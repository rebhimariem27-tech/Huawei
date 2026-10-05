"use client";

import { useEffect, useState } from "react";
import { getReports, API_BASE_URL } from "@/lib/api";
import type { ReportEntry } from "@/lib/types";

function formatSize(bytes: number): string {
  return `${(bytes / 1024).toFixed(0)} Ko`;
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

export default function ReportsPanel() {
  const [reports, setReports] = useState<ReportEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getReports().then((data) => {
      setReports(data);
      setLoading(false);
    });
  }, []);

  return (
    <div className="chat-frame card glass-panel" style={{ padding: "1.5rem" }}>
      <div className="panel-title" style={{ marginBottom: "1rem" }}>
        Rapports de santé générés
      </div>

      {loading && <p style={{ color: "var(--text-dim)" }}>Chargement...</p>}

      {!loading && reports.length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>
          Aucun rapport de santé généré pour le moment.
        </p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
        {reports.map((r) => (
          <div
            key={r.filename}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              border: "1px solid var(--border)",
              borderLeft: "3px solid var(--brand-red)",
              borderRadius: "var(--radius)",
              background: "var(--surface-raised)",
              padding: "0.75rem 1rem",
            }}
          >
            <div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: "0.85rem" }}>
                {r.filename}
              </div>
              <div style={{ fontSize: "0.75rem", color: "var(--text-faint)" }}>
                {formatDate(r.created_at)} · {formatSize(r.size_bytes)}
              </div>
            </div>
            <a
              href={`${API_BASE_URL}/reports/${r.filename}`}
              download
              target="_blank"
              rel="noopener noreferrer"
              className="badge badge--live"
              style={{ textDecoration: "none" }}
            >
              ⭳ Télécharger
            </a>
          </div>
        ))}
      </div>
    </div>
  );
}