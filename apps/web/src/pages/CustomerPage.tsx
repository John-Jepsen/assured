import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, openChatSocket } from "../api";
import { useHealth } from "../useHealth";
import { useVoice } from "../useVoice";
import type {
  ChatMessage,
  DemoCustomer,
  Source,
  VerificationState,
  VerifyResponse,
  WsChatMessage,
} from "../types";

let msgSeq = 0;
function nextId(): string {
  msgSeq += 1;
  return `m${msgSeq}-${Date.now()}`;
}

function SourcesList({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (!sources || sources.length === 0) return null;
  return (
    <div className="sources">
      <button
        type="button"
        className="sources-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? "▾" : "▸"} Sources ({sources.length})
      </button>
      {open && (
        <ul className="sources-list">
          {sources.map((s, i) => (
            <li key={`${s.document_id}-${s.chunk_id}-${i}`}>
              <span className="source-citation">{s.citation}</span>
              <span className="source-score">
                score {s.score.toFixed(3)}
              </span>
              {s.snippet && (
                <span className="source-snippet">{s.snippet}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CustomerPage() {
  const { health } = useHealth();

  // Verification form state
  const [lastName, setLastName] = useState("");
  const [zipCode, setZipCode] = useState("");
  const [policyNumber, setPolicyNumber] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [otpCode, setOtpCode] = useState("");

  const [verificationState, setVerificationState] =
    useState<VerificationState>("Unverified");
  const [verifyResult, setVerifyResult] = useState<VerifyResponse | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  // Demo customers
  const [customers, setCustomers] = useState<DemoCustomer[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState("");
  const [customersError, setCustomersError] = useState<string | null>(null);

  // Conversation state
  const [conversationId, setConversationId] = useState<string | undefined>(
    undefined,
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [escalated, setEscalated] = useState(false);

  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const activeAssistantId = useRef<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const conversationIdRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    conversationIdRef.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    api
      .demoCustomers()
      .then((res) => setCustomers(res.customers))
      .catch((e) =>
        setCustomersError(
          e instanceof Error ? e.message : "Failed to load demo customers",
        ),
      );
  }, []);

  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    return () => {
      socketRef.current?.close();
    };
  }, []);

  const ttsIsMock = useMemo(() => {
    const tts = health?.providers.tts?.toLowerCase() ?? "";
    return tts.includes("mock") || tts.includes("placeholder");
  }, [health]);

  // ---- Demo customer selection (prefill only, no auto-verify) ----
  const onSelectCustomer = (name: string) => {
    setSelectedCustomer(name);
    const c = customers.find((x) => x.name === name);
    if (!c) return;
    const surname = c.name.trim().split(/\s+/).slice(-1)[0] ?? "";
    setLastName(surname);
    setPolicyNumber(c.policy_number);
    setZipCode(c.zip_code);
    setDateOfBirth(c.date_of_birth);
  };

  // ---- Verification ----
  const onVerify = async () => {
    if (!conversationId) {
      setVerifyError(
        "Start a conversation first (send a message) so verification can be linked.",
      );
      return;
    }
    setVerificationState("Verifying");
    setVerifyError(null);
    try {
      const res = await api.verify({
        conversation_id: conversationId,
        last_name: lastName || undefined,
        zip_code: zipCode || undefined,
        policy_number: policyNumber || undefined,
        date_of_birth: dateOfBirth || undefined,
        otp_code: otpCode || undefined,
      });
      setVerifyResult(res);
      setVerificationState(res.verified ? "Verified" : "Failed");
    } catch (e) {
      setVerificationState("Failed");
      setVerifyError(e instanceof Error ? e.message : "Verification failed");
    }
  };

  // ---- Chat helpers shared by text + voice ----
  const appendUserMessage = useCallback((content: string) => {
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: "user", content },
    ]);
  }, []);

  const startAssistantMessage = useCallback((): string => {
    const id = nextId();
    activeAssistantId.current = id;
    setMessages((prev) => [
      ...prev,
      { id, role: "assistant", content: "", streaming: true },
    ]);
    return id;
  }, []);

  const appendAssistantToken = useCallback((token: string) => {
    const id = activeAssistantId.current;
    if (!id) return;
    setMessages((prev) =>
      prev.map((m) =>
        m.id === id ? { ...m, content: m.content + token } : m,
      ),
    );
  }, []);

  const finalizeAssistantMessage = useCallback(
    (answer: string, sources: Source[], isEscalated: boolean) => {
      const id = activeAssistantId.current;
      if (!id) return;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === id
            ? {
                ...m,
                content: answer || m.content,
                streaming: false,
                sources,
                escalated: isEscalated,
              }
            : m,
        ),
      );
      activeAssistantId.current = null;
    },
    [],
  );

  // ---- Text chat over /api/ws/chat ----
  const sendText = () => {
    const message = input.trim();
    if (!message || streaming) return;
    setChatError(null);
    setInput("");
    appendUserMessage(message);
    startAssistantMessage();
    setStreaming(true);

    const handleMessage = (msg: WsChatMessage) => {
      switch (msg.type) {
        case "meta":
          setConversationId(msg.conversation_id);
          setNeedsVerification(msg.needs_verification);
          if (msg.trace?.escalated) setEscalated(true);
          break;
        case "token":
          appendAssistantToken(msg.token);
          break;
        case "done":
          finalizeAssistantMessage(msg.answer, msg.sources ?? [], escalated);
          setStreaming(false);
          socketRef.current?.close();
          socketRef.current = null;
          break;
        case "error":
          setChatError(msg.message);
          finalizeAssistantMessage("", [], escalated);
          setStreaming(false);
          socketRef.current?.close();
          socketRef.current = null;
          break;
      }
    };

    try {
      socketRef.current = openChatSocket(message, conversationId, {
        onMessage: handleMessage,
        onError: () => {
          setChatError("Chat connection error.");
          finalizeAssistantMessage("", [], escalated);
          setStreaming(false);
        },
        onClose: () => {
          setStreaming(false);
        },
      });
    } catch (e) {
      setChatError(e instanceof Error ? e.message : "Failed to open chat");
      finalizeAssistantMessage("", [], escalated);
      setStreaming(false);
    }
  };

  // ---- Voice ----
  const voice = useVoice({
    getConversationId: () => conversationIdRef.current,
    onUserFinal: (text) => {
      appendUserMessage(text);
      startAssistantMessage();
      setChatError(null);
    },
    onAssistantToken: (token) => appendAssistantToken(token),
    onAssistantDone: (answer, sources, convId, isEscalated) => {
      setConversationId(convId);
      if (isEscalated) setEscalated(true);
      finalizeAssistantMessage(answer, sources, isEscalated);
    },
    onError: (message) => setChatError(message),
  });

  const verifyBadgeClass = `verif-state verif-${verificationState.toLowerCase()}`;

  return (
    <div className="customer-layout">
      <section className="panel side-panel">
        <h2 className="panel-title">Demo customer</h2>
        <p className="hint">
          Prefills the verification form for convenience. It does not verify you.
        </p>
        {customersError ? (
          <p className="error-text">{customersError}</p>
        ) : (
          <select
            className="input"
            value={selectedCustomer}
            onChange={(e) => onSelectCustomer(e.target.value)}
          >
            <option value="">— Select a synthetic customer —</option>
            {customers.map((c) => (
              <option key={c.policy_number} value={c.name}>
                {c.name} · {c.scenario}
              </option>
            ))}
          </select>
        )}

        <h2 className="panel-title" style={{ marginTop: "1.25rem" }}>
          Verification
        </h2>
        <div className="verif-row">
          <span className={verifyBadgeClass}>{verificationState}</span>
          {verifyResult && (
            <span className="attempts">
              {verifyResult.attempts_remaining} attempts left
            </span>
          )}
        </div>

        <label className="field">
          <span>Last name</span>
          <input
            className="input"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="field">
          <span>ZIP code</span>
          <input
            className="input"
            value={zipCode}
            onChange={(e) => setZipCode(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="field">
          <span>Policy number</span>
          <input
            className="input"
            value={policyNumber}
            onChange={(e) => setPolicyNumber(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="field">
          <span>Date of birth</span>
          <input
            className="input"
            placeholder="YYYY-MM-DD"
            value={dateOfBirth}
            onChange={(e) => setDateOfBirth(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="field">
          <span>OTP code (demo: 123456)</span>
          <input
            className="input"
            value={otpCode}
            onChange={(e) => setOtpCode(e.target.value)}
            autoComplete="off"
          />
        </label>

        <button
          type="button"
          className="btn btn-primary"
          onClick={onVerify}
          disabled={verificationState === "Verifying"}
        >
          {verificationState === "Verifying" ? "Verifying…" : "Verify"}
        </button>

        {verifyResult && (
          <div className="verif-result">
            <p>{verifyResult.message}</p>
            {verifyResult.matched_factors.length > 0 && (
              <p className="matched">
                Matched: {verifyResult.matched_factors.join(", ")}
              </p>
            )}
          </div>
        )}
        {verifyError && <p className="error-text">{verifyError}</p>}
      </section>

      <section className="panel chat-panel">
        <div className="banners">
          {needsVerification && verificationState !== "Verified" && (
            <div className="banner banner-warn">
              Verification required before account-specific information can be
              shared. Complete the panel on the left.
            </div>
          )}
          {escalated && (
            <div className="banner banner-escalate">
              Escalated to human support. A ticket was opened — see Admin ›
              Tickets for details.
            </div>
          )}
          {chatError && <div className="banner banner-error">{chatError}</div>}
        </div>

        <div className="transcript" ref={transcriptRef}>
          {messages.length === 0 && (
            <div className="empty-state">
              Ask a question to begin. Try “What does my policy cover?” or use
              the microphone.
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`bubble-row bubble-${m.role}`}>
              <div className={`bubble bubble-${m.role}`}>
                {m.content ||
                  (m.streaming ? (
                    <span className="typing" aria-label="Assistant is typing">
                      <span />
                      <span />
                      <span />
                    </span>
                  ) : (
                    ""
                  ))}
                {m.role === "assistant" && m.streaming && m.content && (
                  <span className="cursor">▋</span>
                )}
                {m.role === "assistant" && m.sources && (
                  <SourcesList sources={m.sources} />
                )}
              </div>
            </div>
          ))}
        </div>

        {voice.interim && (
          <div className="interim">Listening: “{voice.interim}”</div>
        )}
        {voice.metrics && (
          <div className="metrics">
            {typeof voice.metrics.speech_end_to_first_audio_ms === "number" && (
              <span>
                speech→audio{" "}
                {Math.round(voice.metrics.speech_end_to_first_audio_ms)}ms
              </span>
            )}
            {typeof voice.metrics.transcript_to_first_token_ms === "number" && (
              <span>
                transcript→token{" "}
                {Math.round(voice.metrics.transcript_to_first_token_ms)}ms
              </span>
            )}
          </div>
        )}
        {ttsIsMock && (
          <p className="hint subtle">
            Voice responses are placeholder audio unless Piper TTS is configured.
          </p>
        )}

        <div className="composer">
          <input
            className="input composer-input"
            placeholder="Type your message…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") sendText();
            }}
            disabled={streaming}
          />
          <button
            type="button"
            className="btn btn-primary"
            onClick={sendText}
            disabled={streaming || !input.trim()}
          >
            Send
          </button>
          <button
            type="button"
            className={
              voice.listening ? "btn btn-mic mic-active" : "btn btn-mic"
            }
            onClick={voice.toggleListening}
            disabled={!voice.available}
            title={
              voice.available
                ? voice.listening
                  ? "Stop listening"
                  : "Start voice input"
                : "Voice input needs a Chromium/Safari browser or server STT"
            }
            aria-pressed={voice.listening}
          >
            {voice.listening ? "Stop" : "Mic"}
          </button>
        </div>
        {voice.playing && (
          <p className="hint subtle">Playing voice response…</p>
        )}
      </section>
    </div>
  );
}
