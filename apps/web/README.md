# Assured — Web (Synthetic Demo)

Multimodal AI insurance customer-service demo UI. React 18 + TypeScript + Vite.
All data is **synthetic**.

## Requirements

- Node 20+ (tested with Node 26 / npm 11)
- The demo backend running (default `http://localhost:8000`)

## Run

```bash
npm install
npm run dev
```

Open the printed local URL (default http://localhost:5173).

To build for production:

```bash
npm run build      # type-checks then bundles to dist/
npm run preview    # serves the built dist/
```

## Configuration

Copy `.env.example` to `.env` and adjust if the backend is not on localhost:8000.

| Variable        | Default                  | Purpose                    |
| --------------- | ------------------------ | -------------------------- |
| `VITE_API_BASE` | `http://localhost:8000`  | REST base URL              |
| `VITE_WS_BASE`  | `ws://localhost:8000`    | WebSocket base URL         |

## Routes

- `/` — Customer chat + verification + voice
- `/admin` — Operations dashboard (conversations, tickets, tools, evaluations, providers)

## Notes

- **Voice input** uses the browser Web Speech API (`SpeechRecognition` /
  `webkitSpeechRecognition`). Available in Chromium and Safari. When it is not
  available the mic button is disabled with an explanatory tooltip. Recognized
  text is sent to `/ws/voice` as `{type:"utterance_text"}`; returned audio
  chunks are played in a queue, and starting to speak while audio plays triggers
  **barge-in**.
- If the backend reports a mock TTS provider (`/health` → `providers.tts`), the
  UI notes that voice responses are placeholder audio unless Piper TTS is
  configured.
- The demo-customer selector only **prefills** the verification form; it never
  auto-verifies. Demo customers verify with `policy_number` + `zip_code`, or
  `date_of_birth`, or OTP `123456`.
- The admin trace view shows the structured execution trace (selected agent,
  detected intent, tools invoked, retrieved sources, latencies) — never hidden
  chain-of-thought.
