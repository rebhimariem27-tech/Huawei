"use client";

import { useEffect, useState } from "react";
import { postQuery, API_BASE_URL } from "@/lib/api";
import { HISTORY_STORAGE_KEY, makeHistoryId, type ConversationHistoryEntry } from "@/lib/history";
import type { Confidence, QueryResponse } from "@/lib/types";
import MarkdownResponse from "@/components/MarkdownResponse";
const DEFAULT_AUDIT_QUERY =
  "Générez un rapport de santé complet du réseau, avec tous les écarts entre la configuration théorique et la configuration réelle.";

const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: "confiance haute",
  medium: "confiance moyenne",
  low: "confiance basse",
};

const CONFIDENCE_DOT: Record<Confidence, string> = {
  high: "status-dot--live",
  medium: "status-dot--warn",
  low: "status-dot--critical",
};

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function loadHistory(): ConversationHistoryEntry[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
  return raw ? (JSON.parse(raw) as ConversationHistoryEntry[]) : [];
}

function pushHistoryEntry(entry: ConversationHistoryEntry) {
  if (typeof window === "undefined") return;
  const current = loadHistory();
  const next = [entry, ...current].slice(0, 20);
  window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new Event("huawei-history-updated"));
}

function updateHistoryEntry(id: string, patch: Partial<ConversationHistoryEntry>) {
  if (typeof window === "undefined") return;
  const current = loadHistory();
  const next = current.map((e) => (e.id === id ? { ...e, ...patch } : e));
  window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new Event("huawei-history-updated"));
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AuditsPanel() {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pastAudits, setPastAudits] = useState<ConversationHistoryEntry[]>([]);

  useEffect(() => {
    const load = () => setPastAudits(loadHistory().filter((e) => e.healthReportRequested));
    load();
    window.addEventListener("huawei-history-updated", load);
    window.addEventListener("storage", load);
    return () => {
      window.removeEventListener("huawei-history-updated", load);
      window.removeEventListener("storage", load);
    };
  }, []);

  async function runAudit() {
    if (running) return;
    setRunning(true);
    setError(null);
    setResult(null);

    const historyId = makeHistoryId();
    pushHistoryEntry({
      id: historyId,
      question: DEFAULT_AUDIT_QUERY,
      answer: "Audit en cours...",
      createdAt: new Date().toISOString(),
      healthReportRequested: true,
      status: "pending",
    });

    try {
      const res = await postQuery(DEFAULT_AUDIT_QUERY, true);
      setResult(res);
      updateHistoryEntry(historyId, {
        answer: res.final_answer,
        confidence: res.confidence,
        confidenceScore: res.confidence_score,
        status: "done",
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "L'audit a échoué.";
      setError(message);
      updateHistoryEntry(historyId, {
        answer: message,
        isError: true,
        status: "error",
      });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="chat-frame card glass-panel" style={{ padding: "1.5rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "0.75rem",
          marginBottom: "1.2rem",
        }}
      >
        <div>
          <div className="panel-title" style={{ marginBottom: "0.3rem" }}>
            Audits de conformité
          </div>
          <div style={{ color: "var(--text-faint)", fontSize: "0.8rem" }}>
            Compare la configuration théorique (manuels) à la configuration réelle des équipements
          </div>
        </div>

        <button type="button" className="btn-primary" onClick={runAudit} disabled={running}>
          {running ? "Audit en cours..." : "Lancer un audit complet"}
        </button>
      </div>

      {error && (
        <div
          style={{
            border: "1px solid var(--accent-critical)",
            borderRadius: "var(--radius)",
            background: "color-mix(in srgb, var(--accent-critical) 8%, var(--surface))",
            padding: "0.8rem 1rem",
            fontSize: "0.85rem",
            color: "var(--accent-critical)",
            marginBottom: "1.1rem",
          }}
        >
          {error}
        </div>
      )}

      {running && !result && (
        <div style={{ color: "var(--text-faint)", fontSize: "0.85rem", marginBottom: "1.1rem" }}>
          Architecte → Documentaliste → Validateur en cours d'exécution...
        </div>
      )}

      {result && <AuditResult result={result} />}

      <div style={{ marginTop: "1.6rem" }}>
        <div style={{ fontSize: "0.9rem", fontWeight: 700, marginBottom: "0.7rem" }}>
          Historique des audits ({pastAudits.length})
        </div>

        {pastAudits.length === 0 ? (
          <p style={{ color: "var(--text-dim)" }}>
            Aucun audit lancé pour le moment — utilise le bouton ci-dessus ou coche « générer un
            rapport de santé » dans le Chat Assistant.
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
            {pastAudits.map((audit) => {
              const status = audit.status === "pending" ? "pending" : audit.isError ? "error" : "done";
              return (
                <div
                  key={audit.id}
                  style={{
                    border: "1px solid var(--border)",
                    borderLeft: `3px solid ${
                      status === "done"
                        ? "var(--accent-live)"
                        : status === "error"
                        ? "var(--accent-critical)"
                        : "var(--accent-warn)"
                    }`,
                    borderRadius: "var(--radius)",
                    background: "var(--surface-raised)",
                    padding: "0.75rem 1rem",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "0.6rem" }}>
                    <span style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                      {formatDate(audit.createdAt)}
                    </span>
                    <span
                      className={`pill ${
                        status === "done" ? "pill--green" : status === "error" ? "pill--neutral" : "pill--amber"
                      }`}
                    >
                      {status === "done" ? audit.confidence ?? "OK" : status === "error" ? "Erreur" : "En cours"}
                    </span>
                  </div>
                  <div
                    style={{
                      marginTop: "0.4rem",
                      fontSize: "0.8rem",
                      color: "var(--text-dim)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                    }}
                  >
                    {audit.answer}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function AuditResult({ result }: { result: QueryResponse }) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderLeft: `3px solid var(--brand-red)`,
        borderRadius: "var(--radius)",
        background: "var(--surface-raised)",
        padding: "1rem 1.1rem",
        marginBottom: "1.1rem",
      }}
    >
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "0.8rem" }}>
        <span className="badge">
          <span className={`status-dot ${CONFIDENCE_DOT[result.confidence]}`} />
          {CONFIDENCE_LABEL[result.confidence]} · {Math.round(result.confidence_score * 100)}%
        </span>
        <span className="badge">{result.rag_chunks_count} chunks RAG</span>
        <span className="badge">{result.devices_consulted.length} équipement(s)</span>
      </div>

      <div
  className="audit-answer-body"
  style={{
    fontSize: "0.86rem",
    lineHeight: 1.6,
    marginBottom: "0.9rem",
    maxHeight: "400px",
    overflowY: "auto",
  }}
>
  <MarkdownResponse content={result.final_answer} />
</div>
      {result.alerts.length > 0 && (
        <MetaList label="Alertes" items={result.alerts} color="var(--accent-warn)" />
      )}
      {result.discrepancies.length > 0 && (
        <MetaList label="Écarts théorie / terrain" items={result.discrepancies} color="var(--accent-critical)" />
      )}
      {result.recommendations.length > 0 && (
        <MetaList label="Recommandations" items={result.recommendations} color="var(--accent-live)" />
      )}

      {result.report_filename && (
        <a
          href={`${API_BASE_URL}/reports/${result.report_filename}`}
          download
          target="_blank"
          rel="noopener noreferrer"
          className="badge badge--live"
          style={{ width: "fit-content", textDecoration: "none", marginTop: "0.6rem", display: "inline-flex" }}
        >
          ⭳ télécharger le rapport PDF
        </a>
      )}
    </div>
  );
}

function MetaList({ label, items, color }: { label: string; items: string[]; color: string }) {
  return (
    <div
      className="meta-list"
      style={{
        border: "1px solid var(--border)",
        borderLeft: `2px solid ${color}`,
        borderRadius: "var(--radius-sm)",
        background: "var(--surface)",
        padding: "0.55rem 0.7rem",
        fontFamily: "var(--font-mono)",
        fontSize: "0.72rem",
        color: "var(--text-faint)",
        marginTop: "0.6rem",
      }}
    >
      <div style={{ color, marginBottom: "0.3rem", letterSpacing: "0.05em", fontWeight: 600 }}>
        {label.toUpperCase()}
      </div>
      <ul style={{ margin: 0, paddingLeft: "1.1rem", color: "var(--text-dim)" }}>
        {items.map((item, i) => (
          <li key={i} style={{ marginBottom: "0.2rem" }}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}