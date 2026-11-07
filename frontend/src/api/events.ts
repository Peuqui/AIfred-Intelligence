// The event stream of one session (GET /api/sessions/{id}/events). The browser reconnects by
// itself and sends the last id it saw, so no event is lost or doubled.
import { apiUrl } from "./client";

export type SessionEventHandler = (kind: string, data: Record<string, unknown>) => void;

// Every kind the server sends (lib/turn_service.py, lib/reply_speech.py, lib/dictation.py,
// lib/api/sessions.py, lib/debug_bus.py). The browser only hears the kinds listed here.
export const SESSION_EVENT_KINDS = [
  "turn_started",
  "turn_rejected",
  "turn_finished",
  "message_queued",
  "agent_started",
  "token",
  "tool_status",
  "message_added",
  "message_updated",
  "history_replaced",
  "session_config",
  "session_title",
  "debug",
  "notice",
  "tts",
  "tts_pause",
  "tts_speak",
  "stop",
  "speech_regenerating",
  "dictation_question",
  "dictation_text",
  "dictation_finished",
] as const;

export function followSession(sessionId: string, afterId: number, handle: SessionEventHandler): EventSource {
  const source = new EventSource(apiUrl(`sessions/${sessionId}/events?after=${afterId}`));
  for (const kind of SESSION_EVENT_KINDS) {
    source.addEventListener(kind, (event) => handle(kind, JSON.parse((event as MessageEvent).data)));
  }
  return source;
}
