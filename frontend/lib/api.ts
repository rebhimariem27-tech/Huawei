// frontend/lib/api.ts
// -----------------------------------------------------------------------------
// Point d'accès unique au backend FastAPI (api/main.py).
// Toute l'URL de base vit ici — les composants n'ont jamais à écrire
// "http://localhost:8000" en dur.
// -----------------------------------------------------------------------------

import type { FileEntry, HealthResponse, IngestResponse, QueryResponse } from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * GET /health — statut Qdrant / Groq / équipements eNSP.
 * N'échoue jamais bruyamment : si l'API est injoignable, retourne un
 * statut "degraded" par défaut plutôt que de faire planter la page.
 */
export async function getHealth(): Promise<HealthResponse> {
  try {
    const res = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
    return await res.json();
  } catch {
    return {
      status: "degraded",
      qdrant_connected: false,
      groq_configured: false,
      ingestion_available: false,
      devices_reachable: null,
      devices_total: null,
      timestamp: new Date().toISOString(),
    };
  }
}

/** GET /files — documents déjà ingérés dans Qdrant. */
export async function getFiles(): Promise<FileEntry[]> {
  try {
    const res = await fetch(`${API_BASE_URL}/files`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Files fetch failed: ${res.status}`);
    return await res.json();
  } catch {
    return [];
  }
}

/** POST /query — exécution synchrone du pipeline LangGraph complet. */
export async function postQuery(query: string,
  generateHealthReport: boolean = false): Promise<QueryResponse> {
  const res = await fetch(`${API_BASE_URL}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, generateHealthReport: generateHealthReport, }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Query failed: ${res.status}`);
  }
  return res.json();
}

/**
 * POST /ingest — upload d'un PDF avec suivi de progression.
 *
 * Utilise XMLHttpRequest plutôt que fetch() car fetch ne remonte pas
 * la progression de l'upload (seulement celle du téléchargement).
 */
export function ingestFile(
  file: File,
  onProgress?: (percent: number) => void
): Promise<IngestResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);

    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    });

    xhr.addEventListener("load", () => {
      try {
        const body = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(body as IngestResponse);
        } else {
          reject(new Error(body?.detail ?? `Échec de l'ingestion (${xhr.status})`));
        }
      } catch {
        reject(new Error("Réponse invalide du serveur d'ingestion."));
      }
    });

    xhr.addEventListener("error", () => {
      reject(new Error("Impossible de joindre l'API pour l'ingestion."));
    });

    xhr.open("POST", `${API_BASE_URL}/ingest`);
    xhr.send(formData);
  });
}