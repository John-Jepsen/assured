import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useHealth } from "../useHealth";
import type {
  AdminConversationDetail,
  AdminConversationSummary,
  EvaluationRun,
  PaymentsConfig,
  TelephonyStatus,
  Ticket,
  ToolExecution,
} from "../types";

type TabKey =
  | "conversations"
  | "tickets"
  | "tools"
  | "evaluations"
  | "providers";

const TABS: { key: TabKey; label: string }[] = [
  { key: "conversations", label: "Conversations" },
  { key: "tickets", label: "Tickets" },
  { key: "tools", label: "Tool Activity" },
  { key: "evaluations", label: "Evaluations" },
  { key: "providers", label: "Providers & Features" },
];

function fmtTime(v?: string): string {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString();
}

function OkBadge({ ok }: { ok: boolean }) {
  return (
    <span className={ok ? "pill pill-ok" : "pill pill-bad"}>
      {ok ? "ok" : "fail"}
    </span>
  );
}

function BoolBadge({ value }: { value: boolean }) {
  return (
    <span className={value ? "pill pill-ok" : "pill pill-neutral"}>
      {value ? "enabled" : "disabled"}
    </span>
  );
}

// ---- Conversations tab ----
function ConversationsTab() {
  const [rows, setRows] = useState<AdminConversationSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminConversationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.conversations();
      setRows(res.conversations);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load conversations");
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [load]);

  const openDetail = useCallback(async (id: string) => {
    setSelected(id);
    setLoading(true);
    setDetail(null);
    try {
      const res = await api.conversationDetail(id);
      setDetail(res);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load detail");
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="admin-split">
      <div className="admin-list">
        <div className="admin-list-head">
          <h3>Conversations</h3>
          <button className="btn btn-ghost" onClick={load} type="button">
            Refresh
          </button>
        </div>
        {error && <p className="error-text">{error}</p>}
        {rows.length === 0 && !error && (
          <p className="empty-state">No conversations yet.</p>
        )}
        <ul className="conv-list">
          {rows.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className={
                  selected === c.id ? "conv-item active" : "conv-item"
                }
                onClick={() => openDetail(c.id)}
              >
                <span className="conv-id">{c.id.slice(0, 8)}</span>
                <span className="conv-meta">
                  <span className="tag">{c.channel}</span>
                  <span className="tag">{c.verification_status}</span>
                  {c.escalated && <span className="tag tag-warn">escalated</span>}
                </span>
                <span className="conv-sub">
                  {c.current_agent} · {fmtTime(c.updated_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="admin-detail">
        {!selected && (
          <p className="empty-state">Select a conversation to inspect it.</p>
        )}
        {loading && <p className="empty-state">Loading…</p>}
        {detail && (
          <div>
            <div className="detail-head">
              <h3>Conversation {detail.conversation.id.slice(0, 8)}</h3>
              <div className="detail-tags">
                <span className="tag">{detail.conversation.channel}</span>
                <span className="tag">
                  {detail.conversation.verification_status}
                </span>
                <span className="tag">{detail.conversation.current_agent}</span>
                {detail.conversation.escalated && (
                  <span className="tag tag-warn">escalated</span>
                )}
              </div>
            </div>

            <h4 className="section-label">Transcript</h4>
            <div className="detail-transcript">
              {detail.transcript.map((m, i) => (
                <div key={i} className={`d-msg d-${m.role}`}>
                  <div className="d-msg-head">
                    <span className="d-role">{m.role}</span>
                    {m.agent && <span className="tag">agent: {m.agent}</span>}
                    {m.intent && (
                      <span className="tag">intent: {m.intent}</span>
                    )}
                    <span className="d-time">{fmtTime(m.created_at)}</span>
                  </div>
                  <div className="d-content">{m.content}</div>
                  {m.trace && (
                    <TraceView trace={m.trace} />
                  )}
                </div>
              ))}
            </div>

            <h4 className="section-label">Tool executions</h4>
            <ToolTable tools={detail.tool_executions} />
          </div>
        )}
      </div>
    </div>
  );
}

function TraceView({
  trace,
}: {
  trace: NonNullable<AdminConversationDetail["transcript"][number]["trace"]>;
}) {
  const [open, setOpen] = useState(false);
  const latencies = Object.entries(trace.latencies_ms ?? {});
  return (
    <div className="trace-box">
      <button
        type="button"
        className="sources-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? "▾" : "▸"} Execution trace
      </button>
      {open && (
        <div className="trace-body">
          <div className="trace-line">
            <strong>Agents:</strong> {trace.agents.join(" → ") || "—"}
          </div>
          <div className="trace-line">
            <strong>Intents:</strong> {trace.intents.join(", ") || "—"}
          </div>
          <div className="trace-line">
            <strong>Verification:</strong> {trace.verification_status}
            {trace.escalated ? " · escalated" : ""}
          </div>
          {trace.tool_calls.length > 0 && (
            <div className="trace-line">
              <strong>Tool calls:</strong>
              <ul className="trace-tools">
                {trace.tool_calls.map((t, i) => (
                  <li key={i}>
                    <OkBadge ok={t.ok} /> {t.tool_name}
                    {t.agent ? ` (${t.agent})` : ""}
                    {t.error_code ? ` — ${t.error_code}` : ""}
                    {t.result_summary ? ` — ${t.result_summary}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {trace.sources.length > 0 && (
            <div className="trace-line">
              <strong>Sources:</strong>
              <ul className="trace-tools">
                {trace.sources.map((s, i) => (
                  <li key={i}>
                    {s.citation} · score {s.score.toFixed(3)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {latencies.length > 0 && (
            <div className="trace-line">
              <strong>Latencies (ms):</strong>{" "}
              {latencies.map(([k, v]) => `${k}=${Math.round(v)}`).join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ToolTable({ tools }: { tools: ToolExecution[] }) {
  if (!tools || tools.length === 0) {
    return <p className="empty-state">No tool executions.</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Tool</th>
            <th>Status</th>
            <th>Error</th>
            <th>Latency</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {tools.map((t, i) => (
            <tr key={i}>
              <td>{t.tool_name}</td>
              <td>
                <OkBadge ok={t.ok} />
              </td>
              <td>{t.error_code ?? "—"}</td>
              <td>{Math.round(t.latency_ms)}ms</td>
              <td>{t.result_message ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- Tickets tab ----
function TicketsTab() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.tickets();
      setTickets(res.tickets);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load tickets");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="admin-single">
      <div className="admin-list-head">
        <h3>Support tickets</h3>
        <button className="btn btn-ghost" onClick={load} type="button">
          Refresh
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      {tickets.length === 0 && !error && (
        <p className="empty-state">No tickets.</p>
      )}
      {tickets.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Status</th>
                <th>Urgency</th>
                <th>Reason</th>
                <th>Summary</th>
                <th>Handoff</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <tr key={t.ticket_number}>
                  <td>{t.ticket_number}</td>
                  <td>{t.status}</td>
                  <td>
                    <span className={`pill pill-${t.urgency.toLowerCase()}`}>
                      {t.urgency}
                    </span>
                  </td>
                  <td>{t.reason}</td>
                  <td>{t.summary}</td>
                  <td>{t.handoff}</td>
                  <td>{fmtTime(t.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- Tool Activity tab ----
function ToolActivityTab() {
  const [tools, setTools] = useState<ToolExecution[]>([]);
  const [avg, setAvg] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.tools();
      setTools(res.tool_executions);
      setAvg(res.avg_latency_ms);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load tools");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="admin-single">
      <div className="admin-list-head">
        <h3>Tool activity</h3>
        <button className="btn btn-ghost" onClick={load} type="button">
          Refresh
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="stat-row">
        <div className="stat-card">
          <span className="stat-value">{Math.round(avg)}ms</span>
          <span className="stat-label">avg latency</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{tools.length}</span>
          <span className="stat-label">executions</span>
        </div>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Tool</th>
              <th>Status</th>
              <th>Error</th>
              <th>Latency</th>
              <th>Conversation</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((t, i) => (
              <tr key={i}>
                <td>{t.tool_name}</td>
                <td>
                  <OkBadge ok={t.ok} />
                </td>
                <td>{t.error_code ?? "—"}</td>
                <td>{Math.round(t.latency_ms)}ms</td>
                <td>{t.conversation_id?.slice(0, 8) ?? "—"}</td>
                <td>{fmtTime(t.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---- Evaluations tab ----
function EvaluationsTab() {
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.evaluations();
      setRuns(res.runs);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load evaluations");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="admin-single">
      <div className="admin-list-head">
        <h3>Evaluation runs</h3>
        <button className="btn btn-ghost" onClick={load} type="button">
          Refresh
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      {runs.length === 0 && !error && (
        <p className="empty-state">No evaluation runs.</p>
      )}
      {runs.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Suite</th>
                <th>Passed</th>
                <th>Total</th>
                <th>Pass rate</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r, i) => {
                const pct = Math.round(r.pass_rate * 100);
                return (
                  <tr key={i}>
                    <td>{r.suite}</td>
                    <td>{r.passed}</td>
                    <td>{r.total}</td>
                    <td>
                      <div className="rate-cell">
                        <div className="rate-bar">
                          <div
                            className="rate-fill"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span>{pct}%</span>
                      </div>
                    </td>
                    <td>{fmtTime(r.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- Providers & Features tab ----
function ProvidersTab() {
  const { health, error: healthError, refresh } = useHealth();
  const [payments, setPayments] = useState<PaymentsConfig | null>(null);
  const [telephony, setTelephony] = useState<TelephonyStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([
        api.paymentsConfig(),
        api.telephonyStatus(),
      ]);
      setPayments(p);
      setTelephony(t);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load config");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="admin-single">
      <div className="admin-list-head">
        <h3>Providers &amp; features</h3>
        <button
          className="btn btn-ghost"
          onClick={() => {
            refresh();
            load();
          }}
          type="button"
        >
          Refresh
        </button>
      </div>
      {(error || healthError) && (
        <p className="error-text">{error ?? healthError}</p>
      )}

      <div className="card-grid">
        <div className="info-card">
          <h4>Health</h4>
          {health ? (
            <ul className="kv">
              <li>
                <span>status</span>
                <span>{health.status}</span>
              </li>
              <li>
                <span>version</span>
                <span>{health.version}</span>
              </li>
              <li>
                <span>database</span>
                <span>{health.database}</span>
              </li>
            </ul>
          ) : (
            <p className="empty-state">Unavailable</p>
          )}
        </div>

        <div className="info-card">
          <h4>Providers</h4>
          {health ? (
            <ul className="kv">
              <li>
                <span>llm</span>
                <span>{health.providers.llm}</span>
              </li>
              <li>
                <span>stt</span>
                <span>{health.providers.stt}</span>
              </li>
              <li>
                <span>tts</span>
                <span>{health.providers.tts}</span>
              </li>
              <li>
                <span>embedding</span>
                <span>{health.providers.embedding}</span>
              </li>
              <li>
                <span>vector</span>
                <span>{health.providers.vector_backend}</span>
              </li>
            </ul>
          ) : (
            <p className="empty-state">Unavailable</p>
          )}
        </div>

        <div className="info-card">
          <h4>Features</h4>
          {health ? (
            <ul className="kv">
              <li>
                <span>stripe</span>
                <BoolBadge value={health.features.stripe} />
              </li>
              <li>
                <span>telephony</span>
                <BoolBadge value={health.features.telephony} />
              </li>
              <li>
                <span>payment provider</span>
                <span>{health.features.payment_provider}</span>
              </li>
            </ul>
          ) : (
            <p className="empty-state">Unavailable</p>
          )}
        </div>

        <div className="info-card">
          <h4>Payments</h4>
          {payments ? (
            <ul className="kv">
              <li>
                <span>provider</span>
                <span>{payments.provider}</span>
              </li>
              <li>
                <span>test mode</span>
                <BoolBadge value={payments.test_mode} />
              </li>
              <li>
                <span>stripe configured</span>
                <BoolBadge value={payments.stripe_configured} />
              </li>
              <li className="kv-note">{payments.note}</li>
            </ul>
          ) : (
            <p className="empty-state">Unavailable</p>
          )}
        </div>

        <div className="info-card">
          <h4>Telephony</h4>
          {telephony ? (
            <ul className="kv">
              <li>
                <span>provider</span>
                <span>{telephony.provider}</span>
              </li>
              <li>
                <span>enabled</span>
                <BoolBadge value={telephony.enabled} />
              </li>
              <li>
                <span>public base</span>
                <span>{telephony.public_base_url || "—"}</span>
              </li>
              <li className="kv-note">{telephony.note}</li>
            </ul>
          ) : (
            <p className="empty-state">Unavailable</p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AdminPage() {
  const [tab, setTab] = useState<TabKey>("conversations");

  return (
    <div className="admin-layout">
      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={tab === t.key ? "tab active" : "tab"}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="panel admin-panel">
        {tab === "conversations" && <ConversationsTab />}
        {tab === "tickets" && <TicketsTab />}
        {tab === "tools" && <ToolActivityTab />}
        {tab === "evaluations" && <EvaluationsTab />}
        {tab === "providers" && <ProvidersTab />}
      </div>
    </div>
  );
}
