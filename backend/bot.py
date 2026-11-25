# backend/bot.py
import os
import time
from datetime import datetime
import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel
import uvicorn

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    AudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    LLMRunFrame,
    MetricsFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

import database

load_dotenv(override=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TRANSCRIPT = []
AGENT_LOGS = []
URGENT_ALERTS = []
AGENT_STATE = {"status": "idle"}  # idle | listening | thinking | speaking
METRICS = {"llm_ttfb_ms": None, "tts_ttfb_ms": None}
CURRENT_CALL_OUTCOME = {"value": "Inquiry"}


def log_tool(tool_name: str, args: dict, summary: str, ok: bool = True):
    AGENT_LOGS.insert(
        0,
        {
            "id": f"log_{int(time.time() * 1000)}",
            "time": datetime.now().strftime("%H:%M:%S"),
            "tool": tool_name,
            "args": args,
            "summary": summary,
            "ok": ok,
        },
    )
    del AGENT_LOGS[30:]


class SlotPayload(BaseModel):
    slot: str


class StatusPayload(BaseModel):
    status: str


# ---------------------------------------------------------
# 1. Dashboard REST API Endpoints (Two-Way Controls)
# ---------------------------------------------------------
@app.get("/api/state")
async def get_dashboard_state():
    return {
        "clinic": database.CLINIC_INFO,
        "doctors": database.get_all_doctors(),
        "appointments": database.get_all_appointments(),
        "transcript": TRANSCRIPT,
        "agent_logs": AGENT_LOGS,
        "call_history": database.get_call_history(),
        "urgent_alerts": URGENT_ALERTS,
        "agent_state": AGENT_STATE["status"],
        "metrics": METRICS,
    }


@app.post("/api/doctors/{doctor_id}/slots")
async def api_add_slot(doctor_id: str, payload: SlotPayload):
    return database.add_slot(doctor_id, payload.slot)


@app.delete("/api/doctors/{doctor_id}/slots")
async def api_remove_slot(doctor_id: str, slot: str):
    return database.remove_slot(doctor_id, slot)


@app.post("/api/appointments/{apt_id}/status")
async def api_update_status(apt_id: str, payload: StatusPayload):
    res = database.update_appointment_status(apt_id, payload.status)
    if res.get("success"):
        log_tool(
            "front_desk_update",
            {"id": apt_id, "status": payload.status},
            f"Updated appointment to {payload.status}",
        )
    return res


@app.post("/api/alerts/clear")
async def api_clear_alerts():
    URGENT_ALERTS.clear()
    return {"status": "cleared"}


# ---------------------------------------------------------
# 2. Raw PCM Audio Serializer (16kHz Mono Int16)
# ---------------------------------------------------------
class RawPCMSerializer(FrameSerializer):
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, AudioRawFrame):
            return frame.audio
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
        return None


# ---------------------------------------------------------
# 3. Live Transcript, State & Latency Telemetry
# ---------------------------------------------------------
class TranscriptAndTelemetry(FrameProcessor):
    def __init__(self, role: str):
        super().__init__()
        self.role = role

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            AGENT_STATE["status"] = "listening"
        elif isinstance(frame, UserStoppedSpeakingFrame):
            AGENT_STATE["status"] = "thinking"
        elif isinstance(frame, BotStartedSpeakingFrame):
            AGENT_STATE["status"] = "speaking"
        elif isinstance(frame, BotStoppedSpeakingFrame):
            AGENT_STATE["status"] = "listening"

        if isinstance(frame, MetricsFrame):
            for item in getattr(frame, "data", []):
                val = getattr(item, "value", None)
                proc = str(getattr(item, "processor", "")).lower()
                if isinstance(val, (int, float)) and val > 0:
                    ms = int(round(val * 1000))
                    if "llm" in proc or "google" in proc:
                        METRICS["llm_ttfb_ms"] = ms
                    elif "tts" in proc or "elevenlabs" in proc:
                        METRICS["tts_ttfb_ms"] = ms

        if self.role == "user" and isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                TRANSCRIPT.append({"role": "caller", "text": text})
                AGENT_STATE["status"] = "thinking"

        elif self.role == "assistant" and isinstance(frame, TextFrame):
            text = frame.text
            if text:
                if TRANSCRIPT and TRANSCRIPT[-1]["role"] == "agent":
                    TRANSCRIPT[-1]["text"] += text
                else:
                    TRANSCRIPT.append({"role": "agent", "text": text.lstrip()})

        await self.push_frame(frame, direction)


# ---------------------------------------------------------
# 4. AI Tools (Function Calling)
# ---------------------------------------------------------
async def check_doctor_availability(params: FunctionCallParams, doctor_name: str = ""):
    """Check available appointment slots, consultation fees, room numbers, and clinic hours.
    Args:
        doctor_name: Optional name or specialty (e.g., 'Dr. Priya Sharma' or 'Cardiology'). Leave empty to list all doctors and clinic info.
    """
    result = database.check_doctor_availability(doctor_name)
    count = len(result.get("doctors", []))
    log_tool(
        "check_doctor_availability",
        {"doctor_name": doctor_name or "All"},
        f"Returned {count} matching doctor(s)",
        ok="error" not in result,
    )
    await params.result_callback(result)


async def get_my_appointments(params: FunctionCallParams, phone_number: str):
    """Look up existing appointments for a returning caller using their phone number.
    Args:
        phone_number: The caller's phone number.
    """
    result = database.get_appointments_by_phone(phone_number)
    log_tool(
        "get_my_appointments",
        {"phone": phone_number},
        f"Found {len(result.get('appointments', []))} record(s)" if result.get("found") else "No records found",
        ok=result.get("found", False),
    )
    await params.result_callback(result)


async def book_appointment(
    params: FunctionCallParams,
    doctor_name: str,
    slot: str,
    phone_number: str,
    patient_name: str = "Caller",
    reason: str = "General consultation",
):
    """Book a doctor appointment for the caller.
    Args:
        doctor_name: The doctor's name (e.g., 'Dr. Priya Sharma').
        slot: The exact time slot (e.g., '10:00 AM').
        phone_number: The caller's confirmed phone number.
        patient_name: The caller's full name.
        reason: Brief reason for the visit (e.g., 'annual checkup', 'skin rash').
    """
    result = database.book_appointment(doctor_name, slot, phone_number, patient_name, reason)
    ok = bool(result.get("success"))
    if ok:
        CURRENT_CALL_OUTCOME["value"] = "Booked"
    log_tool(
        "book_appointment",
        {"doctor": doctor_name, "slot": slot, "patient": patient_name, "reason": reason},
        "Appointment confirmed" if ok else result.get("message", "Booking failed"),
        ok=ok,
    )
    await params.result_callback(result)


async def reschedule_appointment(
    params: FunctionCallParams,
    phone_number: str,
    new_slot: str,
    new_doctor_name: str = "",
):
    """Reschedule an existing appointment to a new time slot (and optionally a new doctor).
    Args:
        phone_number: The caller's confirmed phone number.
        new_slot: The new desired time slot (e.g., '2:00 PM').
        new_doctor_name: Optional new doctor name if switching doctors.
    """
    result = database.reschedule_appointment(phone_number, new_slot, new_doctor_name)
    ok = bool(result.get("success"))
    if ok:
        CURRENT_CALL_OUTCOME["value"] = "Rescheduled"
    log_tool(
        "reschedule_appointment",
        {"phone": phone_number, "new_slot": new_slot, "doctor": new_doctor_name or "Same"},
        result.get("message", "Rescheduled"),
        ok=ok,
    )
    await params.result_callback(result)


async def cancel_appointment(params: FunctionCallParams, phone_number: str):
    """Cancel an existing appointment using the caller's phone number.
    Args:
        phone_number: The caller's confirmed phone number.
    """
    result = database.cancel_appointment(phone_number)
    ok = bool(result.get("success"))
    if ok:
        CURRENT_CALL_OUTCOME["value"] = "Cancelled"
    log_tool(
        "cancel_appointment",
        {"phone": phone_number},
        (
            f"Cancelled {result.get('doctor_name', '')} ({result.get('slot', '')})"
            if ok
            else result.get("message", "Failed")
        ),
        ok=ok,
    )
    await params.result_callback(result)


async def flag_urgent_case(params: FunctionCallParams, symptoms: str, caller_info: str = "Unknown"):
    """Trigger an urgent triage alert on the clinic dashboard if the caller reports emergency symptoms.
    Args:
        symptoms: Brief description of the emergency symptoms reported.
        caller_info: Caller name or phone number if known.
    """
    alert = {
        "id": f"alert_{int(time.time() * 1000)}",
        "time": datetime.now().strftime("%I:%M %p"),
        "symptoms": symptoms,
        "caller_info": caller_info,
    }
    URGENT_ALERTS.insert(0, alert)
    CURRENT_CALL_OUTCOME["value"] = "Urgent Triage"
    log_tool("flag_urgent_case", {"symptoms": symptoms}, "Escalated urgent alert to front desk", ok=True)
    await params.result_callback({"flagged": True, "instruction": "Advise caller to call emergency services immediately."})


SYSTEM_PROMPT = """
You are a warm, calm, and highly professional clinic receptionist for Nexov Healthcare.
Your responses will be spoken aloud, so NEVER use emojis, markdown, bullet points, or symbols. Speak in short, natural, conversational sentences.

You can help callers with:
1. Checking doctor availability, consultation fees, room numbers, and clinic hours (check_doctor_availability).
2. Looking up existing appointments for returning callers (get_my_appointments).
3. Booking a new appointment (book_appointment).
4. Rescheduling an existing appointment (reschedule_appointment).
5. Cancelling an existing appointment (cancel_appointment).
6. Flagging medical emergencies (flag_urgent_case).

IMPORTANT REALISM RULES:
- EMERGENCY TRIAGE: If a caller mentions chest pain, severe bleeding, trouble breathing, stroke symptoms, or any life-threatening emergency, immediately call flag_urgent_case and firmly advise them to hang up and call emergency services (108 or 911) right away. Do not book a routine slot.
- NO DEAD AIR: Before calling a tool, say a brief natural acknowledgment like "Let me check that for you" or "One moment while I pull up the schedule."
- BOOKING DETAILS: When booking, politely ask for the caller's full name, a brief reason for their visit, and their phone number.
- PHONE CONFIRMATION: Before calling book_appointment, reschedule_appointment, or cancel_appointment, you MUST read the caller's phone number back digit by digit and ask them to confirm it is correct.
- Start the conversation by greeting the caller warmly as Nexov Healthcare and asking how you can help them today.
"""


# ---------------------------------------------------------
# 5. WebSocket Endpoint for Real-Time Voice
# ---------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Caller connected via WebSocket!")
    TRANSCRIPT.clear()
    AGENT_STATE["status"] = "thinking"
    CURRENT_CALL_OUTCOME["value"] = "Inquiry"
    call_start_ts = time.time()
    call_start_str = datetime.now().strftime("%b %d, %I:%M %p")

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            serializer=RawPCMSerializer(),
        ),
    )

    stt = ElevenLabsRealtimeSTTService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
    )

    async with aiohttp.ClientSession() as session:
        tts = ElevenLabsHttpTTSService(
            api_key=os.environ["ELEVENLABS_API_KEY"],
            aiohttp_session=session,
            sample_rate=16000,
            settings=ElevenLabsHttpTTSService.Settings(
                voice="EXAVITQu4vr4xnSDxMaL",
                model="eleven_flash_v2_5",
            ),
        )

        llm = GoogleLLMService(
            api_key=os.environ["GEMINI_API_KEY"],
            settings=GoogleLLMService.Settings(
                model="gemini-3.5-flash-lite",
                system_instruction=SYSTEM_PROMPT,
            ),
        )

        tools = [
            check_doctor_availability,
            get_my_appointments,
            book_appointment,
            reschedule_appointment,
            cancel_appointment,
            flag_urgent_case,
        ]
        for fn in tools:
            llm.register_direct_function(fn)

        context = LLMContext(
            messages=[
                {
                    "role": "user",
                    "content": "The caller has just connected. Greet them warmly as Nexov Healthcare and ask how you can help.",
                }
            ],
            tools=tools,
        )
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
        )

        user_telemetry = TranscriptAndTelemetry(role="user")
        assistant_telemetry = TranscriptAndTelemetry(role="assistant")

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                user_telemetry,
                user_aggregator,
                llm,
                assistant_telemetry,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
            ),
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Sending initial AI greeting...")
            await task.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Caller disconnected.")
            AGENT_STATE["status"] = "idle"
            duration = max(1, int(time.time() - call_start_ts))
            if TRANSCRIPT:
                database.save_call_record(
                    call_start_str,
                    duration,
                    CURRENT_CALL_OUTCOME["value"],
                    list(TRANSCRIPT),
                )
            await task.cancel()

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7860)