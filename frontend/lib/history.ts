import type { Confidence } from "./types";

export const HISTORY_STORAGE_KEY = "huawei-rag-history";

export interface ConversationHistoryEntry {
  id: string;
  question: string;
  answer: string;
  createdAt: string;
  confidence?: Confidence;
  confidenceScore?: number;
  healthReportRequested: boolean;
  isError?: boolean;
  status?: "pending" | "done" | "error";
}

export function makeHistoryId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
