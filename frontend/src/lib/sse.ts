/**
 * SSE parsing (PROJECT.md Section 7: "native EventSource / fetch stream reader
 * for SSE").
 *
 * `EventSource` is not usable here: `POST /api/v1/chat/stream` is a POST, and
 * `EventSource` only issues GETs. So this reads the response body with a stream
 * reader and parses the wire format by hand.
 *
 * The backend emits *named* events:
 *
 *   event: token
 *   data: {"text": "The daily"}
 *   event: done
 *   data: {"answer": "...", "has_sufficient_evidence": true, "citations": []}
 *
 * The framing rules that matter, and why each is handled:
 *
 *  - Events are separated by a blank line, and a chunk boundary can fall
 *    anywhere - including mid-`data:` line or between `\r\n`. Buffering across
 *    reads is therefore mandatory, not defensive: a partial JSON payload is the
 *    normal case on a slow stream.
 *  - `data:` may repeat within one event, in which case the payloads join with
 *    newlines per the spec. Only one `data:` line is used in practice, but
 *    dropping the accumulation would silently truncate such an event.
 *  - Comment lines (`:` heartbeat) must be skipped, not parsed as fields.
 */

import type { ChatError, ChatFinal } from "@/types/chat";

export type SseEvent =
  | { type: "token"; text: string }
  | { type: "done"; payload: ChatFinal }
  | { type: "error"; payload: ChatError }
  /** Anything the server sent that this client does not model. Not an error. */
  | { type: "unknown"; event: string; data: string };

/** Split on a blank line in any of the three line-ending conventions. */
const EVENT_BOUNDARY = /\r\n\r\n|\n\n|\r\r/;

/**
 * Incrementally parses an SSE byte stream into events.
 *
 * Fed chunks as they arrive; `push` returns every event completed by that
 * chunk. Leftover text stays buffered until the terminator arrives.
 */
export class SseParser {
  private buffer = "";

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];

    let match: RegExpExecArray | null;
    // Re-scan from the start each time: a chunk can complete a boundary that
    // was already half-present in the buffer.
    while ((match = EVENT_BOUNDARY.exec(this.buffer)) !== null) {
      const raw = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const parsed = parseEvent(raw);
      if (parsed) events.push(parsed);
    }
    return events;
  }

  /**
   * Flush a trailing event that was never terminated by a blank line.
   *
   * Servers that close the connection right after the last event are common
   * enough that treating the remainder as a complete event avoids losing the
   * final `done` - which is the one event that carries the citations.
   */
  flush(): SseEvent[] {
    const remainder = this.buffer;
    this.buffer = "";
    if (!remainder.trim()) return [];
    const parsed = parseEvent(remainder);
    return parsed ? [parsed] : [];
  }
}

function parseEvent(raw: string): SseEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of raw.split(/\r\n|\n|\r/)) {
    if (!line || line.startsWith(":")) continue; // blank or heartbeat comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    // A single leading space after the colon is part of the framing, not data.
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
  }

  if (!dataLines.length) return null;
  const data = dataLines.join("\n");

  try {
    const parsed = JSON.parse(data) as Record<string, unknown>;
    switch (eventName) {
      case "token": {
        // Guarded rather than cast: a token event with no text would otherwise
        // render as the literal string "undefined".
        const text = typeof parsed.text === "string" ? parsed.text : "";
        return { type: "token", text };
      }
      case "done":
        return { type: "done", payload: parsed as unknown as ChatFinal };
      case "error":
        return { type: "error", payload: parsed as unknown as ChatError };
      default:
        return { type: "unknown", event: eventName, data };
    }
  } catch {
    // Malformed JSON on a known event is a real protocol fault and is surfaced
    // rather than skipped - a silently dropped `done` would leave the UI
    // streaming forever.
    return { type: "unknown", event: eventName, data };
  }
}

/** Error thrown when the stream ends without a `done` event. */
export class StreamIncompleteError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamIncompleteError";
  }
}
