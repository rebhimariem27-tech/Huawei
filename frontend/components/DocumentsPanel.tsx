"use client";

import { useMemo, useState } from "react";
import type { FileEntry } from "@/lib/types";

type SortMode = "recent" | "oldest" | "name" | "chunks";

interface DocumentsPanelProps {
  files: FileEntry[];
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

export default function DocumentsPanel({ files }: DocumentsPanelProps) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortMode>("recent");

  const totalChunks = useMemo(
    () => files.reduce((sum, f) => sum + (f.chunks_indexed ?? 0), 0),
    [files]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list = files.filter((f) => f.filename.toLowerCase().includes(q));

    switch (sort) {
      case "recent":
        list = list.sort(
          (a, b) => new Date(b.ingested_at).getTime() - new Date(a.ingested_at).getTime()
        );
        break;
      case "oldest":
        list = list.sort(
          (a, b) => new Date(a.ingested_at).getTime() - new Date(b.ingested_at).getTime()
        );
        break;
      case "name":
        list = list.sort((a, b) => a.filename.localeCompare(b.filename));
        break;
      case "chunks":
        list = list.sort((a, b) => (b.chunks_indexed ?? 0) - (a.chunks_indexed ?? 0));
        break;
    }
    return list;
  }, [files, query, sort]);

  return (
    <div className="chat-frame card glass-panel" style={{ padding: "1.5rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "0.75rem",
          marginBottom: "1.1rem",
        }}
      >
        <div>
          <div className="panel-title" style={{ marginBottom: "0.3rem" }}>
            Documents indexés
          </div>
          <div style={{ color: "var(--text-faint)", fontSize: "0.8rem" }}>
            {files.length} document{files.length > 1 ? "s" : ""} · {totalChunks} chunks au total dans Qdrant
          </div>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <span className="badge badge--live">{files.length} fichiers</span>
          <span className="badge">{totalChunks} chunks</span>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          gap: "0.6rem",
          marginBottom: "1.1rem",
          flexWrap: "wrap",
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Rechercher un document..."
          style={{
            flex: 1,
            minWidth: "200px",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            background: "var(--surface-raised)",
            color: "var(--text)",
            padding: "0.55rem 0.8rem",
            fontSize: "0.85rem",
            fontFamily: "var(--font-sans)",
          }}
        />

        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortMode)}
          style={{
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            background: "var(--surface-raised)",
            color: "var(--text)",
            padding: "0.55rem 0.8rem",
            fontSize: "0.82rem",
            fontFamily: "var(--font-sans)",
            cursor: "pointer",
          }}
        >
          <option value="recent">Plus récents</option>
          <option value="oldest">Plus anciens</option>
          <option value="name">Nom (A → Z)</option>
          <option value="chunks">Chunks (décroissant)</option>
        </select>
      </div>

      {filtered.length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>
          {files.length === 0
            ? "Aucun document indexé pour le moment. Utilise la zone d'upload à droite."
            : "Aucun document ne correspond à ta recherche."}
        </p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
        {filtered.map((file) => (
          <div
            key={file.file_id}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              border: "1px solid var(--border)",
              borderLeft: "3px solid var(--brand-red)",
              borderRadius: "var(--radius)",
              background: "var(--surface-raised)",
              padding: "0.75rem 1rem",
              gap: "0.75rem",
              flexWrap: "wrap",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", minWidth: 0 }}>
              <span
                style={{
                  width: "11px",
                  height: "11px",
                  borderRadius: "3px",
                  flexShrink: 0,
                  background: "linear-gradient(180deg, var(--brand-red-bright), var(--brand-red))",
                  boxShadow: "0 0 0 4px color-mix(in srgb, var(--brand-red) 14%, transparent)",
                }}
              />
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    fontWeight: 600,
                    fontSize: "0.88rem",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {file.filename}
                </div>
                <div style={{ fontSize: "0.75rem", color: "var(--text-faint)" }}>
                  {formatDate(file.ingested_at)}
                </div>
              </div>
            </div>

            <span className="badge">{file.chunks_indexed ?? 0} chunks</span>
          </div>
        ))}
      </div>
    </div>
  );
}