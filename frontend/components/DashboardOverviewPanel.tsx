"use client";

import { useEffect, useMemo, useState } from "react";
import { getReports } from "@/lib/api";
import { HISTORY_STORAGE_KEY, type ConversationHistoryEntry } from "@/lib/history";
import type { FileEntry, HealthResponse, ReportEntry } from "@/lib/types";

type ViewKey =
  | "chat"
  | "dashboard"
  | "audits"
  | "documents"
  | "topologie"
  | "equipements"
  | "rapports"
  | "remediation"
  | "historique"
  | "parametres";

interface DashboardOverviewPanelProps {
  health: HealthResponse;
  files: FileEntry[];
  onNavigate: (view: ViewKey) => void;
}

function loadHistory(): ConversationHistoryEntry[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
  return raw ? (JSON.parse(raw) as ConversationHistoryEntry[]) : [];
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DashboardOverviewPanel({ health, files, onNavigate }: DashboardOverviewPanelProps) {
  const [reports, setReports] = useState<ReportEntry[]>([]);
  const [history, setHistory] = useState<ConversationHistoryEntry[]>([]);
  const [loadingReports, setLoadingReports] = useState(true);

  useEffect(() => {
    getReports().then((data) => {
      setReports(data);
      setLoadingReports(false);
    });

    const load = () => setHistory(loadHistory());
    load();
    window.addEventListener("huawei-history-updated", load);
    window.addEventListener("storage", load);
    return () => {
      window.removeEventListener("huawei-history-updated", load);
      window.removeEventListener("storage", load);
    };
  }, []);

  const total = health.devices_total ?? 0;
  const reachable = health.devices_reachable ?? 0;
  const ratio = total > 0 ? reachable / total : 0;

  const totalChunks = useMemo(
    () => files.reduce((sum, f) => sum + (f.chunks_indexed ?? 0), 0),
    [files]
  );

  const errorCount = history.filter((h) => h.isError).length;
  const recentActivity = history.slice(0, 5);
  const recentReports = reports.slice(0, 3);

  return (
    <div className="chat-frame card glass-panel" style={{ padding: "1.5rem" }}>
      <div className="panel-title" style={{ marginBottom: "1.2rem" }}>
        Vue d'ensemble
      </div>

      {/* KPI cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "0.8rem",
          marginBottom: "1.4rem",
        }}
      >
        <KpiCard
          label="Conformité"
          value={total > 0 ? `${Math.round(ratio * 100)}%` : "N/A"}
          tone={ratio >= 0.95 ? "live" : ratio > 0 ? "warn" : "neutral"}
          onClick={() => onNavigate("topologie")}
        />
        <KpiCard
          label="Équipements en ligne"
          value={total > 0 ? `${reachable}/${total}` : "0/0"}
          tone={ratio >= 0.95 ? "live" : "warn"}
          onClick={() => onNavigate("equipements")}
        />
        <KpiCard
          label="Documents indexés"
          value={`${files.length}`}
          sub={`${totalChunks} chunks`}
          onClick={() => onNavigate("documents")}
        />
        <KpiCard
          label="Rapports générés"
          value={loadingReports ? "…" : `${reports.length}`}
          onClick={() => onNavigate("rapports")}
        />
        <KpiCard
          label="Conversations"
          value={`${history.length}`}
          sub={errorCount > 0 ? `${errorCount} erreur(s)` : undefined}
          tone={errorCount > 0 ? "warn" : undefined}
          onClick={() => onNavigate("historique")}
        />
      </div>

      {/* Statut système condensé */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: "0.6rem",
          marginBottom: "1.4rem",
        }}
      >
        <StatusChip label="Base Qdrant" ok={health.qdrant_connected} />
        <StatusChip label="API LLM" ok={health.groq_configured} />
        <StatusChip label="Ingestion" ok={health.ingestion_available} />
        <StatusChip label="Statut global" ok={health.status === "ok"} />
      </div>

      {/* Deux colonnes : activité récente + derniers rapports */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1.3fr) minmax(0, 1fr)",
          gap: "1rem",
        }}
      >
        <div>
          <div style={{ fontSize: "0.88rem", fontWeight: 700, marginBottom: "0.6rem" }}>
            Activité récente
          </div>

          {recentActivity.length === 0 ? (
            <p style={{ color: "var(--text-faint)", fontSize: "0.82rem" }}>
              Aucune conversation pour le moment.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {recentActivity.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onNavigate("historique")}
                  style={{
                    textAlign: "left",
                    border: "1px solid var(--border)",
                    borderLeft: `3px solid ${
                      item.status === "pending"
                        ? "var(--accent-warn)"
                        : item.isError
                        ? "var(--accent-critical)"
                        : "var(--accent-live)"
                    }`,
                    borderRadius: "var(--radius-sm)",
                    background: "var(--surface-raised)",
                    padding: "0.6rem 0.8rem",
                    cursor: "pointer",
                    color: "var(--text)",
                    font: "inherit",
                  }}
                >
                  <div
                    style={{
                      fontSize: "0.82rem",
                      fontWeight: 600,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.question}
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "var(--text-faint)", marginTop: "0.2rem" }}>
                    {formatDate(item.createdAt)}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div>
          <div style={{ fontSize: "0.88rem", fontWeight: 700, marginBottom: "0.6rem" }}>
            Derniers rapports
          </div>

          {recentReports.length === 0 ? (
            <p style={{ color: "var(--text-faint)", fontSize: "0.82rem" }}>
              Aucun rapport généré pour le moment.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {recentReports.map((r) => (
                <button
                  key={r.filename}
                  type="button"
                  onClick={() => onNavigate("rapports")}
                  style={{
                    textAlign: "left",
                    border: "1px solid var(--border)",
                    borderLeft: "3px solid var(--brand-red)",
                    borderRadius: "var(--radius-sm)",
                    background: "var(--surface-raised)",
                    padding: "0.6rem 0.8rem",
                    cursor: "pointer",
                    color: "var(--text)",
                    font: "inherit",
                  }}
                >
                  <div
                    style={{
                      fontSize: "0.78rem",
                      fontFamily: "var(--font-mono)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {r.filename}
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "var(--text-faint)", marginTop: "0.2rem" }}>
                    {formatDate(r.created_at)}
                  </div>
                </button>
              ))}
            </div>
          )}

          <button
            type="button"
            onClick={() => onNavigate("rapports")}
            className="panel-link"
            style={{ marginTop: "0.7rem", width: "100%" }}
          >
            Voir tous les rapports
          </button>
        </div>
      </div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  sub,
  tone,
  onClick,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "live" | "warn" | "neutral";
  onClick?: () => void;
}) {
  const color =
    tone === "live" ? "var(--accent-live)" : tone === "warn" ? "var(--accent-warn)" : "var(--text)";

  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        textAlign: "left",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--surface-raised)",
        padding: "0.8rem 0.9rem",
        cursor: onClick ? "pointer" : "default",
        font: "inherit",
        transition: "border-color 0.14s ease, transform 0.1s ease",
      }}
      onMouseEnter={(e) => {
        if (onClick) e.currentTarget.style.borderColor = "var(--brand-red-dim)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--border)";
      }}
    >
      <div
        style={{
          color: "var(--text-faint)",
          fontSize: "0.7rem",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}
      >
        {label}
      </div>
      <div style={{ marginTop: "0.35rem", fontSize: "1.25rem", fontWeight: 700, color }}>{value}</div>
      {sub && <div style={{ marginTop: "0.15rem", fontSize: "0.74rem", color: "var(--text-faint)" }}>{sub}</div>}
    </button>
  );
}

function StatusChip({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.5rem",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--surface-raised)",
        padding: "0.55rem 0.75rem",
      }}
    >
      <span className={`status-dot ${ok ? "status-dot--live" : "status-dot--critical"}`} />
      <span style={{ fontSize: "0.8rem", color: "var(--text-dim)" }}>{label}</span>
      <span
        style={{
          marginLeft: "auto",
          fontSize: "0.74rem",
          fontWeight: 600,
          color: ok ? "var(--accent-live)" : "var(--accent-critical)",
        }}
      >
        {ok ? "OK" : "KO"}
      </span>
    </div>
  );
}