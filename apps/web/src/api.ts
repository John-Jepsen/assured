// Centralized REST + WebSocket access for the demo app.
import type {
  AdminConversationDetail,
  AdminConversationsResponse,
  ChatResponse,
  DemoCustomersResponse,
  EvaluationsResponse,
  HealthResponse,
  PaymentsConfig,
  TelephonyStatus,
  TicketsResponse,
  ToolsResponse,
  VerifyRequest,
  VerifyResponse,
  WsChatMessage,
  WsVoiceMessage,
} from "./types";

// Empty string ("") means "same origin" (Docker: nginx proxies /api and /ws to the
// API). Undefined means local dev → default to the API on :8000.
const RAW_API = import.meta.env.VITE_API_BASE as string | undefined;
export const API_BASE: string = RAW_API === undefined ? "http://localhost:8000" : RAW_API;

const RAW_WS = import.meta.env.VITE_WS_BASE as string | undefined;
export const WS_BASE: string =
  RAW_WS === undefined
    ? "ws://localhost:8000"
    : RAW_WS === ""
      ? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`
      : RAW_WS;

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`POST ${path} failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

// ---- REST ----

export const api = {
  health: () => getJson<HealthResponse>("/health"),

  chat: (message: string, conversationId?: string, channel?: string) =>
    postJson<ChatResponse>("/api/chat", {
      message,
      conversation_id: conversationId,
      channel,
    }),

  verify: (req: VerifyRequest) => postJson<VerifyResponse>("/api/verify", req),

  demoCustomers: () =>
    getJson<DemoCustomersResponse>("/api/admin/demo-customers"),

  conversations: () =>
    getJson<AdminConversationsResponse>("/api/admin/conversations"),

  conversationDetail: (id: string) =>
    getJson<AdminConversationDetail>(`/api/admin/conversations/${id}`),

  tickets: () => getJson<TicketsResponse>("/api/admin/tickets"),

  tools: () => getJson<ToolsResponse>("/api/admin/tools"),

  evaluations: () => getJson<EvaluationsResponse>("/api/admin/evaluations"),

  paymentsConfig: () => getJson<PaymentsConfig>("/api/payments/config"),

  telephonyStatus: () => getJson<TelephonyStatus>("/api/telephony/status"),
};

// ---- WebSockets ----

export interface ChatSocketHandlers {
  onMessage: (msg: WsChatMessage) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: Event) => void;
}

/**
 * Opens the /api/ws/chat socket, sends the message once connected, and streams
 * events back through handlers. Returns the socket so the caller can close it.
 */
export function openChatSocket(
  message: string,
  conversationId: string | undefined,
  handlers: ChatSocketHandlers,
): WebSocket {
  const ws = new WebSocket(`${WS_BASE}/api/ws/chat`);
  ws.onopen = () => {
    handlers.onOpen?.();
    ws.send(
      JSON.stringify({ message, conversation_id: conversationId }),
    );
  };
  ws.onmessage = (ev: MessageEvent) => {
    try {
      const parsed = JSON.parse(ev.data as string) as WsChatMessage;
      handlers.onMessage(parsed);
    } catch {
      // Ignore malformed frames.
    }
  };
  ws.onclose = () => handlers.onClose?.();
  ws.onerror = (err) => handlers.onError?.(err);
  return ws;
}

export interface VoiceSocketHandlers {
  onMessage: (msg: WsVoiceMessage) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: Event) => void;
}

export interface VoiceSocket {
  ws: WebSocket;
  sendUtterance: (text: string, conversationId?: string) => void;
  sendBargeIn: () => void;
  close: () => void;
}

/**
 * Opens the /ws/voice socket. The browser handles STT via the Web Speech API,
 * so we send recognized text via {type:"utterance_text"}.
 */
export function openVoiceSocket(handlers: VoiceSocketHandlers): VoiceSocket {
  const ws = new WebSocket(`${WS_BASE}/ws/voice`);
  ws.onopen = () => handlers.onOpen?.();
  ws.onmessage = (ev: MessageEvent) => {
    try {
      const parsed = JSON.parse(ev.data as string) as WsVoiceMessage;
      handlers.onMessage(parsed);
    } catch {
      // Ignore malformed frames.
    }
  };
  ws.onclose = () => handlers.onClose?.();
  ws.onerror = (err) => handlers.onError?.(err);

  const safeSend = (payload: unknown) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  };

  return {
    ws,
    sendUtterance: (text: string, conversationId?: string) =>
      safeSend({
        type: "utterance_text",
        text,
        conversation_id: conversationId,
      }),
    sendBargeIn: () => safeSend({ type: "barge_in" }),
    close: () => {
      safeSend({ type: "close" });
      ws.close();
    },
  };
}
