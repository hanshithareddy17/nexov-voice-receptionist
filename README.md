
# Nexov Healthcare — AI Voice Receptionist & Live Operations Desk

A real-time, full-duplex AI voice receptionist and two-way clinic operations dashboard built with **FastAPI**, **Pipecat**, **Gemini**, **ElevenLabs**, **SQLite**, and **React (Vite)**.

---

## Pipeline Architecture

```text
Browser Mic (16kHz Mono PCM)
  └─▶ WebSocket (/ws)
        └─▶ FastAPIWebsocketTransport + Silero VAD
              └─▶ ElevenLabs Realtime STT
                    └─▶ Gemini (gemini-3.5-flash-lite) + 6 Function-Calling Tools ◀─▶ SQLite (nexov.db)
                          └─▶ ElevenLabs Flash v2.5 TTS (16kHz PCM)
                                └─▶ WebSocket (/ws) ─▶ Browser AudioContext Playback
How it works:
The React frontend captures 16kHz mono PCM audio using the Web Audio API and streams it over a WebSocket to the FastAPI backend. Pipecat orchestrates Silero VAD for barge-in and interruption handling, ElevenLabs Realtime STT for live transcription, Gemini (gemini-3.5-flash-lite) for conversational reasoning and tool execution against SQLite, and ElevenLabs Flash v2.5 TTS for low-latency speech synthesis. A custom telemetry processor tracks live transcripts, agent state (listening / thinking / speaking), and per-turn LLM TTFB and TTS TTFB metrics, syncing them in real time with the clinic dashboard via REST polling.

Key Features
Voice Receptionist & Function Calling
Availability & Clinic Info (check_doctor_availability): Answers questions about doctors, specialties, open slots, room numbers, consultation fees, and clinic hours.

Appointment Booking (book_appointment): Captures patient name, reason for visit, and phone number—enforcing a digit-by-digit phone number confirmation before committing to SQLite.

Returning Patient Lookup (get_my_appointments): Retrieves existing bookings using the caller's phone number.

Rescheduling & Cancellation (reschedule_appointment, cancel_appointment): Atomically frees old slots and updates the live schedule.

Emergency Triage Guardrail (flag_urgent_case): Detects urgent symptoms (e.g., chest pain, trouble breathing), advises calling emergency services immediately, and triggers a high-priority banner on the front-desk dashboard.

Two-Way Clinic Operations Dashboard
Live Slot Management: Front-desk staff can add or remove doctor time slots directly in the UI; changes are immediately reflected in the AI's next voice call.

Patient Check-In Workflow: Filter and search appointments, and transition visits through Booked → Checked In → Completed or Cancelled.

Observability & Telemetry: Inspect live Gemini tool calls, real-time transcripts, and LLM TTFB / TTS TTFB latency in milliseconds.

Expandable Call History: Automatically logs every completed call with duration, outcome tag (Booked, Rescheduled, Cancelled, Urgent Triage, Inquiry), and full expandable transcript.

Project Structure
Plaintext
voice-doctor-assistant/
├── backend/
│   ├── bot.py             # FastAPI server, Pipecat voice pipeline, tools & telemetry
│   ├── database.py        # SQLite persistence layer (doctors, appointments, call history)
│   ├── .env.example       # Environment variable template
│   └── nexov.db           # Auto-generated SQLite database (gitignored)
├── frontend/
│   ├── src/
│   │   ├── App.jsx        # Clinic dashboard & WebSocket voice client
│   │   └── index.css      # Styling
│   ├── index.html
│   └── package.json
├── .gitignore
└── README.md
Local Setup
Prerequisites
Python 3.11+

Node.js 18+

API keys for Google AI Studio (Gemini) and ElevenLabs

1. Backend Setup
Bash
cd backend
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS / Linux:
# source venv/bin/activate

pip install fastapi uvicorn python-dotenv aiohttp loguru pydantic "pipecat-ai[google,elevenlabs,silero,websocket]"
Create backend/.env using backend/.env.example as a template:

Code snippet
GEMINI_API_KEY=your_actual_gemini_key
ELEVENLABS_API_KEY=your_actual_elevenlabs_key
Start the backend server:
python bot.py
The server runs on http://127.0.0.1:7860 and initializes backend/nexov.db automatically.

2. Frontend Setup
In a second terminal:
cd frontend
npm install
npm run dev
Open http://localhost:5173 in your browser, allow microphone permissions, and click Start Voice Call.
