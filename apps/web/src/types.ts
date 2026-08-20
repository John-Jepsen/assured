// Shared types matching the backend API contract.

export interface Source {
  citation: string;
  document_id: string;
  chunk_id: string;
  score: number;
  snippet: string;
}

export interface TraceToolCall {
  tool_name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  error_code?: string | null;
  result_summary?: string;
  agent?: string;
}

export interface Trace {
  request_id: string;
  conversation_id: string;
  intents: string[];
  agents: string[];
  tool_calls: TraceToolCall[];
  sources: Source[];
  verification_status: string;
  escalated: boolean;
  latencies_ms: Record<string, number>;
}

export interface HealthResponse {
  status: string;
  version: string;
  providers: {
    llm: string;
    stt: string;
    tts: string;
    embedding: string;
    vector_backend: string;
  };
  database: string;
  features: {
    stripe: boolean;
    telephony: boolean;
    payment_provider: string;
  };
}

export interface ChatResponse {
  conversation_id: string;
  answer: string;
  needs_verification: boolean;
  trace: Trace;
}

export interface VerifyRequest {
  conversation_id: string;
  last_name?: string;
  zip_code?: string;
  policy_number?: string;
  date_of_birth?: string;
  otp_code?: string;
}

export interface VerifyResponse {
  status: string;
  verified: boolean;
  matched_factors: string[];
  message: string;
  attempts_remaining: number;
}

export interface DemoCustomer {
  name: string;
  policy_number: string;
  zip_code: string;
  date_of_birth: string;
  scenario: string;
}

export interface DemoCustomersResponse {
  synthetic: boolean;
  customers: DemoCustomer[];
}

export interface AdminConversationSummary {
  id: string;
  channel: string;
  verification_status: string;
  current_agent: string;
  escalated: boolean;
  customer_id: string | null;
  updated_at: string;
}

export interface AdminConversationsResponse {
  conversations: AdminConversationSummary[];
}

export interface TranscriptMessage {
  role: string;
  content: string;
  agent?: string | null;
  intent?: string | null;
  trace?: Trace | null;
  created_at: string;
}

export interface ToolExecution {
  tool_name: string;
  ok: boolean;
  error_code?: string | null;
  arguments?: Record<string, unknown>;
  latency_ms: number;
  result_message?: string;
  conversation_id?: string;
  created_at?: string;
}

export interface AdminConversationDetail {
  conversation: {
    id: string;
    channel: string;
    verification_status: string;
    current_agent: string;
    escalated: boolean;
  };
  transcript: TranscriptMessage[];
  tool_executions: ToolExecution[];
}

export interface Ticket {
  ticket_number: string;
  status: string;
  urgency: string;
  reason: string;
  summary: string;
  handoff: string;
  created_at: string;
}

export interface TicketsResponse {
  tickets: Ticket[];
}

export interface ToolsResponse {
  tool_executions: ToolExecution[];
  avg_latency_ms: number;
}

export interface EvaluationRun {
  suite: string;
  total: number;
  passed: number;
  pass_rate: number;
  created_at: string;
}

export interface EvaluationsResponse {
  runs: EvaluationRun[];
}

export interface PaymentsConfig {
  provider: string;
  test_mode: boolean;
  stripe_configured: boolean;
  note: string;
}

export interface TelephonyStatus {
  provider: string;
  enabled: boolean;
  public_base_url: string;
  note: string;
}

// ---- WebSocket message shapes ----

export interface WsChatMeta {
  type: "meta";
  conversation_id: string;
  trace: Trace;
  needs_verification: boolean;
}
export interface WsChatToken {
  type: "token";
  token: string;
}
export interface WsChatDone {
  type: "done";
  answer: string;
  sources: Source[];
}
export interface WsChatError {
  type: "error";
  message: string;
}
export type WsChatMessage =
  | WsChatMeta
  | WsChatToken
  | WsChatDone
  | WsChatError;

export interface WsVoiceTranscript {
  type: "transcript";
  text: string;
  confidence: number;
}
export interface WsVoiceToken {
  type: "token";
  token: string;
}
export interface WsVoiceAudio {
  type: "audio";
  data: string; // base64 wav
  text: string;
  sample_rate: number;
}
export interface WsVoiceMetrics {
  type: "metrics";
  speech_end_to_transcript_ms?: number;
  transcript_to_first_token_ms?: number;
  speech_end_to_first_audio_ms?: number;
  first_token_to_first_audio_ms?: number;
}
export interface WsVoiceDone {
  type: "done";
  answer: string;
  sources: Source[];
  conversation_id: string;
  trace: Trace;
}
export interface WsVoiceInterrupted {
  type: "interrupted";
}
export interface WsVoiceError {
  type: "error";
  message: string;
}
export type WsVoiceMessage =
  | WsVoiceTranscript
  | WsVoiceToken
  | WsVoiceAudio
  | WsVoiceMetrics
  | WsVoiceDone
  | WsVoiceInterrupted
  | WsVoiceError;

// ---- UI-local types ----

export type VerificationState =
  | "Unverified"
  | "Verifying"
  | "Verified"
  | "Failed";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  sources?: Source[];
  escalated?: boolean;
  ticket?: string | null;
}
