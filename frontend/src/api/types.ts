// The shapes the backend sends (docs/PHASE3.md has the events, docs/PHASE4.md the chat view)

export type MemoryStatus = "active" | "superseded" | "deleted";

/** A recalled memory as the `memory` event carries it. */
export interface MemoryRef {
  id: number;
  kind: string;
  content: string;
  valid_from: string | null;
  valid_until: string | null;
}

/** A recalled memory as a reloaded chat carries it, resolved as of now. */
export interface RecalledMemory {
  id: number;
  kind: string;
  /** null for a memory deleted since: an old reply must not bring its text back */
  content: string | null;
  /** recalled as something that was true once */
  past: boolean;
  status: MemoryStatus;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  memories: RecalledMemory[];
}

export interface ChatSummary {
  id: string;
  created_at: string;
  message_count: number;
  preview: string | null;
}

export interface Memory {
  id: number;
  kind: string;
  content: string;
  status: MemoryStatus;
  source_kind: "owner" | "external";
  superseded_by: number | null;
  valid_from: string | null;
  valid_until: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

// The events of a turn ----------------------------------------------------------------------

export interface MemoryEvent {
  type: "memory";
  current: MemoryRef[];
  past: MemoryRef[];
}

export interface TextEvent {
  type: "text";
  text: string;
}

export interface DoneEvent {
  type: "done";
  message_id: number;
  input_tokens: number;
  output_tokens: number;
  steps: number;
  recall_seconds: number;
  total_seconds: number;
}

export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
}

export type StreamEvent = MemoryEvent | TextEvent | DoneEvent | ErrorEvent;
