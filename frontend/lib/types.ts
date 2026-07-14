// frontend/lib/types.ts
// -----------------------------------------------------------------------------
// Types miroir des modèles Pydantic de api/schemas.py.
// Garde ce fichier synchronisé si tu changes un schéma côté API.
// -----------------------------------------------------------------------------

export type Confidence = "high" | "medium" | "low";

export interface QueryResponse {
  final_answer: string;
  intent: string;
  strategy: string;
  confidence: Confidence;
  confidence_score: number;
  rag_chunks_count: number;
  alerts: string[];
  discrepancies: string[];
  recommendations: string[];
  sources_used: Record<string, unknown>[];
  validation_checks: Record<string, unknown>;
  report_filename: string | null;   // ← ajouté
}

export interface IngestResponse {
  filename: string;
  file_id: string;
  status: "success" | "error";
  chunks_indexed: number | null;
  message: string;
}

export interface FileEntry {
  file_id: string;
  filename: string;
  ingested_at: string;
  chunks_indexed: number | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  qdrant_connected: boolean;
  groq_configured: boolean;
  ingestion_available: boolean;
  devices_reachable: number | null;
  devices_total: number | null;
  timestamp: string;
}