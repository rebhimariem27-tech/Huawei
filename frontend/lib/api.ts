// frontend/lib/api.ts
// -----------------------------------------------------------------------------
// Point d'accès unique au backend FastAPI (api/main.py).
// Toute l'URL de base vit ici — les composants n'ont jamais à écrire
// "http://localhost:8000" en dur.
// -----------------------------------------------------------------------------

import type { DesignUpdateResponse, FileEntry, HealthResponse, IngestResponse, QueryResponse, TopologySnapshot } from "./types";
import type { DeviceStatus } from "./types";
import type { CommandProposal, CommandApplyResult } from "./types";
import type { TerminalResponse } from "./types";

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
    body: JSON.stringify({ query, generate_health_report: generateHealthReport, }),
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
import type { ReportEntry } from "./types";

export async function getReports(): Promise<ReportEntry[]> {
  try {
    const res = await fetch(`${API_BASE_URL}/reports`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Reports fetch failed: ${res.status}`);
    return await res.json();
  } catch {
    return [];
  }
}

/**
 * GET /devices/{name}/status — collecte live (SSH/Telnet + parsing VRP).
 * Peut prendre plusieurs secondes (connexion réelle à l'équipement eNSP).
 */
export async function getDeviceStatus(name: string): Promise<DeviceStatus> {
  const res = await fetch(`${API_BASE_URL}/devices/${name}/status`, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Statut équipement indisponible (${res.status})`);
  }
  return res.json();
}
/**
 * POST /devices/{name}/remediate — applique une commande de configuration
 * en direct sur l'équipement (SSH/Telnet, mode config VRP).
 *
 * ⚠ Action modificatrice sur un équipement réel — toujours confirmer
 * avec l'utilisateur avant d'appeler cette fonction.
 */
export async function remediateDevice(
  name: string,
  command: string
): Promise<{ success: boolean; output: string }> {
  const res = await fetch(`${API_BASE_URL}/devices/${name}/remediate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Remédiation échouée (${res.status})`);
  }
  return res.json();
}

export async function requestDesignUpdate(
  request: string,
  apply: boolean = false
): Promise<DesignUpdateResponse> {
  const res = await fetch(`${API_BASE_URL}/design/deploy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request, apply }),
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Déploiement échoué (${res.status})`);
  }

  return res.json();
}

export async function getTopology(): Promise<TopologySnapshot> {
  const res = await fetch(`${API_BASE_URL}/topology`, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Topology fetch failed: ${res.status}`);
  }
  return res.json();
}
export async function proposeCommand(query: string): Promise<CommandProposal> {
  const res = await fetch(`${API_BASE_URL}/commands/propose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Génération échouée (${res.status})`);
  }
  return res.json();
}

export async function applyCommands(
  deviceName: string,
  commands: string[]
): Promise<CommandApplyResult[]> {
  const res = await fetch(`${API_BASE_URL}/commands/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_name: deviceName, commands }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Application échouée (${res.status})`);
  }
  return res.json();
}
export async function runTerminalCommands(
  deviceName: string,
  commands: string[]
): Promise<TerminalResponse> {
  const res = await fetch(`${API_BASE_URL}/devices/${deviceName}/terminal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commands }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Terminal échoué (${res.status})`);
  }
  return res.json();
}