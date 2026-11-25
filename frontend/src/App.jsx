import { useEffect, useRef, useState } from "react";
import {
  Phone,
  PhoneOff,
  Mic,
  MicOff,
  Calendar,
  Clock,
  Stethoscope,
  Activity,
  History,
  Plus,
  X,
  LayoutDashboard,
  AlertTriangle,
  Gauge,
} from "lucide-react";

const API_BASE = "http://127.0.0.1:7860";
const WS_URL = "ws://127.0.0.1:7860/ws";

export default function App() {
  const [doctors, setDoctors] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [transcript, setTranscript] = useState([]);
  const [agentLogs, setAgentLogs] = useState([]);
  const [callHistory, setCallHistory] = useState([]);
  const [urgentAlerts, setUrgentAlerts] = useState([]);
  const [agentState, setAgentState] = useState("idle");
  const [metrics, setMetrics] = useState({ llm_ttfb_ms: null, tts_ttfb_ms: null });
  const [backendOnline, setBackendOnline] = useState(false);

  const [activeTab, setActiveTab] = useState("overview");
  const [expandedCallId, setExpandedCallId] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const [slotInputs, setSlotInputs] = useState({});
  const [highlightId, setHighlightId] = useState(null);
  const [toast, setToast] = useState("");

  const [phoneOpen, setPhoneOpen] = useState(false);
  const [callState, setCallState] = useState("idle");
  const [isMuted, setIsMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [audioLevel, setAudioLevel] = useState(4);

  const wsRef = useRef(null);
  const audioCtxRef = useRef(null);
  const streamRef = useRef(null);
  const processorRef = useRef(null);
  const nextPlayTimeRef = useRef(0);
  const isMutedRef = useRef(false);
  const prevAptCountRef = useRef(0);
  const phoneTranscriptEndRef = useRef(null);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2800);
  };

  const fetchState = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/state`);
      if (!res.ok) return;
      const data = await res.json();
      setBackendOnline(true);
      setDoctors(data.doctors || []);
      setTranscript(data.transcript || []);
      setAgentLogs(data.agent_logs || []);
      setCallHistory(data.call_history || []);
      setUrgentAlerts(data.urgent_alerts || []);
      setAgentState(data.agent_state || "idle");
      setMetrics(data.metrics || { llm_ttfb_ms: null, tts_ttfb_ms: null });

      const newApts = data.appointments || [];
      if (newApts.length > prevAptCountRef.current) {
        const newest = newApts[0];
        setHighlightId(newest.id);
        setTimeout(() => setHighlightId(null), 3000);
      }
      prevAptCountRef.current = newApts.length;
      setAppointments(newApts);
    } catch {
      setBackendOnline(false);
    }
  };

  useEffect(() => {
    fetchState();
    const timer = setInterval(fetchState, 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (callState !== "connected") return;
    const interval = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, [callState]);

  useEffect(() => {
    phoneTranscriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  const formatTime = (secs) => {
    const m = String(Math.floor(secs / 60)).padStart(2, "0");
    const s = String(secs % 60).padStart(2, "0");
    return `${m}:${s}`;
  };

  const handleAddSlot = async (doctorId) => {
    const val = (slotInputs[doctorId] || "").trim();
    if (!val) return;
    const res = await fetch(`${API_BASE}/api/doctors/${doctorId}/slots`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot: val }),
    });
    const data = await res.json();
    if (data.success) {
      setSlotInputs((prev) => ({ ...prev, [doctorId]: "" }));
      showToast(`Added ${val} slot`);
      fetchState();
    } else {
      showToast(data.message || "Could not add slot");
    }
  };

  const handleRemoveSlot = async (doctorId, slot) => {
    await fetch(
      `${API_BASE}/api/doctors/${doctorId}/slots?slot=${encodeURIComponent(slot)}`,
      { method: "DELETE" }
    );
    showToast(`Removed ${slot} slot`);
    fetchState();
  };

  const handleUpdateStatus = async (aptId, status) => {
    const res = await fetch(`${API_BASE}/api/appointments/${aptId}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Marked as ${status}`);
      fetchState();
    }
  };

  const handleClearAlerts = async () => {
    await fetch(`${API_BASE}/api/alerts/clear`, { method: "POST" });
    fetchState();
  };

  const startCall = async () => {
    setPhoneOpen(true);
    setCallState("connecting");
    setSeconds(0);
    setIsMuted(false);
    isMutedRef.current = false;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      streamRef.current = stream;

      const audioCtx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: 16000,
      });
      if (audioCtx.state === "suspended") await audioCtx.resume();
      audioCtxRef.current = audioCtx;
      nextPlayTimeRef.current = audioCtx.currentTime;

      const ws = new WebSocket(WS_URL);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        setCallState("connected");
        const source = audioCtx.createMediaStreamSource(stream);
        const processor = audioCtx.createScriptProcessor(4096, 1, 1);
        processorRef.current = processor;

        processor.onaudioprocess = (e) => {
          if (ws.readyState !== WebSocket.OPEN || isMutedRef.current) {
            setAudioLevel(4);
            return;
          }
          const float32 = e.inputBuffer.getChannelData(0);
          let sum = 0;
          const int16 = new Int16Array(float32.length);
          for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            sum += Math.abs(s);
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
          const avg = sum / float32.length;
          setAudioLevel(Math.min(20, Math.max(4, Math.round(avg * 180))));
          ws.send(int16.buffer);
        };

        source.connect(processor);
        processor.connect(audioCtx.destination);
      };

      ws.onmessage = (event) => {
        if (!(event.data instanceof ArrayBuffer) || !audioCtxRef.current) return;
        const int16 = new Int16Array(event.data);
        if (int16.length === 0) return;

        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
          float32[i] = int16[i] / 32768;
        }

        const ctx = audioCtxRef.current;
        const buffer = ctx.createBuffer(1, float32.length, 16000);
        buffer.getChannelData(0).set(float32);

        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(ctx.destination);

        const startAt = Math.max(ctx.currentTime, nextPlayTimeRef.current);
        src.start(startAt);
        nextPlayTimeRef.current = startAt + buffer.duration;
      };

      ws.onclose = () => {
        cleanupAudio();
        setCallState("ended");
      };

      ws.onerror = () => {
        cleanupAudio();
        setCallState("ended");
      };
    } catch {
      cleanupAudio();
      setCallState("ended");
    }
  };

  const cleanupAudio = () => {
    processorRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close();
    }
    setAudioLevel(4);
  };

  const endCall = () => {
    wsRef.current?.close();
    cleanupAudio();
    setCallState("ended");
    setTimeout(() => setPhoneOpen(false), 1000);
  };

  const toggleMute = () => {
    const next = !isMuted;
    setIsMuted(next);
    isMutedRef.current = next;
  };

  const totalSlots = doctors.reduce((acc, d) => acc + d.slots.length, 0);
  const activeBookings = appointments.filter(
    (a) => a.status === "Booked" || a.status === "Checked In"
  ).length;

  const filteredAppointments = appointments.filter((a) => {
    const matchesStatus = statusFilter === "All" || a.status === statusFilter;
    const q = searchQuery.toLowerCase();
    const matchesQuery =
      !q ||
      a.doctor_name.toLowerCase().includes(q) ||
      (a.patient_name || "").toLowerCase().includes(q) ||
      (a.reason || "").toLowerCase().includes(q) ||
      a.phone_number.toLowerCase().includes(q);
    return matchesStatus && matchesQuery;
  });

  const statusClass = (status) => {
    if (status === "Checked In") return "CheckedIn";
    if (status === "Urgent Triage") return "Urgent";
    return status;
  };

  return (
    <div className="app-shell">
      {toast && <div className="toast">{toast}</div>}

      {urgentAlerts.length > 0 && (
        <div className="urgent-banner">
          <div className="urgent-left">
            <AlertTriangle size={18} />
            <div>
              <strong>Urgent Triage Alert ({urgentAlerts[0].time}):</strong>{" "}
              {urgentAlerts[0].symptoms} &mdash; Caller advised to contact emergency services.
            </div>
          </div>
          <button className="urgent-dismiss" onClick={handleClearAlerts}>
            Dismiss
          </button>
        </div>
      )}

      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">N</div>
          <div>
            <h1>Nexov Healthcare</h1>
            <p>AI Voice Receptionist &amp; Live Operations Desk</p>
          </div>
        </div>

        <div className="topbar-right">
          <span className={`status-chip ${backendOnline ? "online" : ""}`}>
            <span className="status-dot" />
            {backendOnline ? "Voice Engine Online" : "Connecting..."}
          </span>
          <button
            className="call-trigger-btn"
            onClick={startCall}
            disabled={callState === "connecting" || callState === "connected" || !backendOnline}
          >
            <Phone size={16} />
            {callState === "connected" ? "Call in Progress" : "Start Voice Call"}
          </button>
        </div>
      </header>

      <section className="stats-row">
        <div className="stat-card">
          <span className="stat-label">Doctors On Duty</span>
          <span className="stat-value">{doctors.length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Open Slots</span>
          <span className="stat-value">{totalSlots}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Active Visits</span>
          <span className="stat-value">{activeBookings}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Calls Handled</span>
          <span className="stat-value">{callHistory.length}</span>
        </div>
      </section>

      <div className="tab-bar">
        <button
          className={`tab-btn ${activeTab === "overview" ? "active" : ""}`}
          onClick={() => setActiveTab("overview")}
        >
          <LayoutDashboard size={15} />
          Overview
        </button>
        <button
          className={`tab-btn ${activeTab === "activity" ? "active" : ""}`}
          onClick={() => setActiveTab("activity")}
        >
          <Activity size={15} />
          Agent Activity ({agentLogs.length})
        </button>
        <button
          className={`tab-btn ${activeTab === "history" ? "active" : ""}`}
          onClick={() => setActiveTab("history")}
        >
          <History size={15} />
          Call History ({callHistory.length})
        </button>
      </div>

      {activeTab === "overview" && (
        <main className="dashboard-grid">
          <section className="panel">
            <div className="panel-header">
              <h2>
                <Stethoscope size={16} color="#0d9488" />
                Doctors &amp; Live Slots
              </h2>
              <span className="panel-badge">{totalSlots} open</span>
            </div>
            <div className="panel-body">
              <div className="doctor-list">
                {doctors.map((doc) => (
                  <div className="doctor-card" key={doc.id}>
                    <div className="doctor-top">
                      <span className="doctor-name">{doc.name}</span>
                      <span className="doctor-spec">{doc.specialty}</span>
                    </div>
                    <div className="doctor-meta">
                      {doc.room} &middot; Consultation Fee: {doc.fee}
                    </div>
                    <div className="slots-wrap">
                      {doc.slots.length > 0 ? (
                        doc.slots.map((slot) => (
                          <span className="slot-pill" key={slot}>
                            {slot}
                            <button
                              className="slot-remove"
                              onClick={() => handleRemoveSlot(doc.id, slot)}
                              title="Remove slot"
                            >
                              <X size={12} />
                            </button>
                          </span>
                        ))
                      ) : (
                        <span style={{ fontSize: 12.5, color: "#64748b" }}>No open slots</span>
                      )}
                    </div>
                    <div className="add-slot-row">
                      <input
                        className="slot-input"
                        placeholder="Add slot (e.g. 5:30 PM)"
                        value={slotInputs[doc.id] || ""}
                        onChange={(e) =>
                          setSlotInputs((prev) => ({ ...prev, [doc.id]: e.target.value }))
                        }
                        onKeyDown={(e) => e.key === "Enter" && handleAddSlot(doc.id)}
                      />
                      <button className="slot-add-btn" onClick={() => handleAddSlot(doc.id)}>
                        <Plus size={13} />
                        Add
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>
                <Calendar size={16} color="#0d9488" />
                Appointments &amp; Check-In
              </h2>
              <span className="panel-badge">{filteredAppointments.length} shown</span>
            </div>
            <div className="panel-body">
              <div className="filter-bar">
                <input
                  className="search-input"
                  placeholder="Search patient, reason, doctor, or phone..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
                <select
                  className="filter-select"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  <option value="All">All</option>
                  <option value="Booked">Booked</option>
                  <option value="Checked In">Checked In</option>
                  <option value="Completed">Completed</option>
                  <option value="Cancelled">Cancelled</option>
                </select>
              </div>

              {filteredAppointments.length === 0 ? (
                <div className="empty-state">
                  No appointments match. Click <strong>Start Voice Call</strong> to book by voice.
                </div>
              ) : (
                <div className="apt-list">
                  {filteredAppointments.map((apt) => (
                    <div
                      key={apt.id}
                      className={`apt-row ${highlightId === apt.id ? "highlight" : ""}`}
                    >
                      <div className="apt-main">
                        <span className="apt-doc">
                          {apt.patient_name || "Caller"} &rarr; {apt.doctor_name}
                        </span>
                        {apt.reason && <span className="apt-reason">Reason: {apt.reason}</span>}
                        <span className="apt-meta">
                          <Clock size={11} style={{ marginRight: 4, verticalAlign: "middle" }} />
                          {apt.slot} &middot; {apt.phone_number}
                        </span>
                      </div>
                      <div className="apt-actions">
                        <span className={`status-pill ${statusClass(apt.status)}`}>
                          {apt.status}
                        </span>
                        {apt.status === "Booked" && (
                          <>
                            <button
                              className="action-btn"
                              onClick={() => handleUpdateStatus(apt.id, "Checked In")}
                            >
                              Check In
                            </button>
                            <button
                              className="cancel-btn"
                              onClick={() => handleUpdateStatus(apt.id, "Cancelled")}
                            >
                              Cancel
                            </button>
                          </>
                        )}
                        {apt.status === "Checked In" && (
                          <button
                            className="action-btn"
                            onClick={() => handleUpdateStatus(apt.id, "Completed")}
                          >
                            Complete
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        </main>
      )}

      {activeTab === "activity" && (
        <div>
          <div className="telemetry-bar">
            <div className="metric-pill">
              <Gauge size={15} color="#0d9488" />
              LLM TTFB:{" "}
              <strong>{metrics.llm_ttfb_ms ? `${metrics.llm_ttfb_ms} ms` : "—"}</strong>
            </div>
            <div className="metric-pill">
              <Gauge size={15} color="#0d9488" />
              TTS TTFB:{" "}
              <strong>{metrics.tts_ttfb_ms ? `${metrics.tts_ttfb_ms} ms` : "—"}</strong>
            </div>
            <div className="metric-pill">
              Pipeline State: <strong style={{ textTransform: "capitalize" }}>{agentState}</strong>
            </div>
          </div>

          <main className="dashboard-grid">
            <section className="panel">
              <div className="panel-header">
                <h2>Live Tool-Call Inspector</h2>
                <span className="panel-badge">{agentLogs.length} events</span>
              </div>
              <div className="panel-body">
                {agentLogs.length === 0 ? (
                  <div className="empty-state">
                    Tool calls (availability, bookings, triage, lookups) will appear here live.
                  </div>
                ) : (
                  <div className="log-list">
                    {agentLogs.map((log) => (
                      <div key={log.id} className="log-card">
                        <div className="log-top">
                          <span className="log-tool">{log.tool}()</span>
                          <span className="log-time">{log.time}</span>
                        </div>
                        <div className="log-summary">{log.summary}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section className="panel">
              <div className="panel-header">
                <h2>Live Call Transcript</h2>
                <span className="panel-badge">{transcript.length} messages</span>
              </div>
              <div className="panel-body">
                {transcript.length === 0 ? (
                  <div className="empty-state">Start a call to watch the live conversation.</div>
                ) : (
                  <div className="transcript-feed">
                    {transcript.map((item, idx) => (
                      <div key={idx} className={`msg ${item.role}`}>
                        <span className="msg-role">
                          {item.role === "agent" ? "Nexov Reception" : "Caller"}
                        </span>
                        {item.text}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          </main>
        </div>
      )}

      {activeTab === "history" && (
        <section className="panel">
          <div className="panel-header">
            <h2>Recent Call History</h2>
            <span className="panel-badge">{callHistory.length} calls (click to expand)</span>
          </div>
          <div className="panel-body">
            {callHistory.length === 0 ? (
              <div className="empty-state">Completed calls will be logged here automatically.</div>
            ) : (
              <div className="apt-list">
                {callHistory.map((call) => {
                  const isOpen = expandedCallId === call.id;
                  const msgs = isOpen ? call.transcript : call.transcript.slice(-2);
                  return (
                    <div
                      key={call.id}
                      className="log-card clickable"
                      onClick={() => setExpandedCallId(isOpen ? null : call.id)}
                    >
                      <div className="log-top">
                        <strong>{call.started_at}</strong>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span className="log-time">{formatTime(call.duration_sec)}</span>
                          <span className={`status-pill ${statusClass(call.outcome)}`}>
                            {call.outcome}
                          </span>
                        </div>
                      </div>
                      <div style={{ fontSize: 12.5, color: "#475569", marginTop: 8 }}>
                        {msgs.map((m, i) => (
                          <div key={i} style={{ marginBottom: 4 }}>
                            <strong>{m.role === "agent" ? "Nexov" : "Caller"}:</strong> {m.text}
                          </div>
                        ))}
                        {!isOpen && call.transcript.length > 2 && (
                          <span style={{ fontSize: 11.5, color: "#0d9488", fontWeight: 600 }}>
                            Click to view all {call.transcript.length} messages
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      )}

      {phoneOpen && (
        <div className="phone-overlay">
          <div className="phone-shell">
            <div className="phone-top">
              <div className="phone-avatar">NH</div>
              <div className="phone-name">Nexov Reception</div>
              <div className="phone-status">
                {callState === "connecting" && "Connecting..."}
                {callState === "connected" && (
                  <>
                    <span className="live-dot" />
                    Connected &middot; {formatTime(seconds)}
                  </>
                )}
                {callState === "ended" && "Call Ended"}
              </div>
              {callState === "connected" && (
                <span className="agent-state-badge">{agentState}...</span>
              )}
              <div className="wave-bars">
                {[0.6, 1, 0.8, 1.1, 0.7].map((mult, i) => (
                  <div
                    key={i}
                    className="wave-bar"
                    style={{
                      height:
                        callState === "connected"
                          ? `${Math.max(4, Math.round(audioLevel * mult))}px`
                          : "4px",
                    }}
                  />
                ))}
              </div>
            </div>

            <div className="phone-transcript">
              {transcript.length === 0 ? (
                <div style={{ color: "#64748b", fontSize: 12, textAlign: "center", marginTop: 24 }}>
                  {callState === "connected"
                    ? "Try: 'How much is Dr. Priya's fee?' or 'Book 10 AM'"
                    : "Establishing audio line..."}
                </div>
              ) : (
                transcript.map((item, i) => (
                  <div key={i} className={`phone-msg ${item.role}`}>
                    {item.text}
                  </div>
                ))
              )}
              <div ref={phoneTranscriptEndRef} />
            </div>

            <div className="phone-controls">
              <button
                className={`ctrl-btn mute ${isMuted ? "active" : ""}`}
                onClick={toggleMute}
                title={isMuted ? "Unmute" : "Mute"}
              >
                {isMuted ? <MicOff size={20} /> : <Mic size={20} />}
              </button>
              <button className="ctrl-btn end" onClick={endCall} title="End Call">
                <PhoneOff size={20} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}