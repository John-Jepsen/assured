import { useCallback, useEffect, useRef, useState } from "react";
import { openVoiceSocket } from "./api";
import type { VoiceSocket } from "./api";
import type { Source, WsVoiceMessage, WsVoiceMetrics } from "./types";
import {
  base64WavToUrl,
  getSpeechRecognitionCtor,
  isSpeechRecognitionAvailable,
  type SpeechRecognitionLike,
} from "./speech";

export interface VoiceCallbacks {
  onUserFinal: (text: string) => void;
  onAssistantToken: (token: string) => void;
  onAssistantDone: (
    answer: string,
    sources: Source[],
    conversationId: string,
    escalated: boolean,
  ) => void;
  onError: (message: string) => void;
  getConversationId: () => string | undefined;
}

export interface VoiceState {
  available: boolean;
  listening: boolean;
  interim: string;
  playing: boolean;
  metrics: WsVoiceMetrics | null;
  connected: boolean;
  toggleListening: () => void;
  stop: () => void;
}

/**
 * Encapsulates the browser Web Speech API + /ws/voice lifecycle: recognizes
 * speech client-side, streams recognized text to the server, plays returned
 * audio in a queue, and supports barge-in.
 */
export function useVoice(callbacks: VoiceCallbacks): VoiceState {
  const available = isSpeechRecognitionAvailable();

  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [playing, setPlaying] = useState(false);
  const [metrics, setMetrics] = useState<WsVoiceMetrics | null>(null);
  const [connected, setConnected] = useState(false);

  const socketRef = useRef<VoiceSocket | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const queueRef = useRef<string[]>([]);
  const playingRef = useRef(false);
  const cbRef = useRef(callbacks);

  useEffect(() => {
    cbRef.current = callbacks;
  }, [callbacks]);

  const clearQueue = useCallback(() => {
    queueRef.current.forEach((url) => URL.revokeObjectURL(url));
    queueRef.current = [];
  }, []);

  const stopPlayback = useCallback(() => {
    const el = audioRef.current;
    if (el) {
      el.pause();
      el.removeAttribute("src");
      el.load();
    }
    clearQueue();
    playingRef.current = false;
    setPlaying(false);
  }, [clearQueue]);

  const playNext = useCallback(() => {
    const url = queueRef.current.shift();
    if (!url) {
      playingRef.current = false;
      setPlaying(false);
      return;
    }
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }
    const el = audioRef.current;
    el.src = url;
    el.onended = () => {
      URL.revokeObjectURL(url);
      playNext();
    };
    el.onerror = () => {
      URL.revokeObjectURL(url);
      playNext();
    };
    playingRef.current = true;
    setPlaying(true);
    void el.play().catch(() => {
      // Autoplay may be blocked until a user gesture; skip this chunk.
      playNext();
    });
  }, []);

  const enqueueAudio = useCallback(
    (base64: string) => {
      try {
        const url = base64WavToUrl(base64);
        queueRef.current.push(url);
        if (!playingRef.current) {
          playNext();
        }
      } catch {
        // Ignore undecodable audio.
      }
    },
    [playNext],
  );

  const ensureSocket = useCallback((): VoiceSocket => {
    if (socketRef.current) return socketRef.current;
    const sock = openVoiceSocket({
      onOpen: () => setConnected(true),
      onClose: () => {
        setConnected(false);
        socketRef.current = null;
      },
      onError: () => cbRef.current.onError("Voice connection error."),
      onMessage: (msg: WsVoiceMessage) => {
        switch (msg.type) {
          case "transcript":
            // Server-confirmed transcript; interim UI already reflects it.
            break;
          case "token":
            cbRef.current.onAssistantToken(msg.token);
            break;
          case "audio":
            enqueueAudio(msg.data);
            break;
          case "metrics":
            setMetrics(msg);
            break;
          case "done":
            cbRef.current.onAssistantDone(
              msg.answer,
              msg.sources ?? [],
              msg.conversation_id,
              msg.trace?.escalated ?? false,
            );
            break;
          case "interrupted":
            stopPlayback();
            break;
          case "error":
            cbRef.current.onError(msg.message);
            break;
        }
      },
    });
    socketRef.current = sock;
    return sock;
  }, [enqueueAudio, stopPlayback]);

  const startRecognition = useCallback(() => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      cbRef.current.onError("Speech recognition is not available.");
      return;
    }
    const sock = ensureSocket();
    const recognition = new Ctor();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setListening(true);
      // Barge-in: if audio is playing when the user starts talking, interrupt.
      if (playingRef.current) {
        sock.sendBargeIn();
        stopPlayback();
      }
    };
    recognition.onerror = (ev) => {
      if (ev.error !== "no-speech" && ev.error !== "aborted") {
        cbRef.current.onError(`Speech recognition error: ${ev.error}`);
      }
    };
    recognition.onend = () => {
      setListening(false);
      setInterim("");
      recognitionRef.current = null;
    };
    recognition.onresult = (ev) => {
      let interimText = "";
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i += 1) {
        const result = ev.results[i];
        const alt = result[0];
        if (result.isFinal) {
          finalText += alt.transcript;
        } else {
          interimText += alt.transcript;
        }
      }
      if (interimText) setInterim(interimText);
      if (finalText.trim()) {
        const text = finalText.trim();
        setInterim("");
        cbRef.current.onUserFinal(text);
        sock.sendUtterance(text, cbRef.current.getConversationId());
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      // start() throws if already started; ignore.
    }
  }, [ensureSocket, stopPlayback]);

  const stopRecognition = useCallback(() => {
    const rec = recognitionRef.current;
    if (rec) {
      try {
        rec.stop();
      } catch {
        // ignore
      }
    }
    setListening(false);
    setInterim("");
  }, []);

  const toggleListening = useCallback(() => {
    if (listening) {
      stopRecognition();
    } else {
      startRecognition();
    }
  }, [listening, startRecognition, stopRecognition]);

  const stop = useCallback(() => {
    stopRecognition();
    stopPlayback();
    socketRef.current?.close();
    socketRef.current = null;
  }, [stopPlayback, stopRecognition]);

  useEffect(() => {
    return () => {
      stopRecognition();
      stopPlayback();
      socketRef.current?.close();
      socketRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    available,
    listening,
    interim,
    playing,
    metrics,
    connected,
    toggleListening,
    stop,
  };
}
