"use client";

import { useEffect, useMemo, useState } from "react";
import { HISTORY_STORAGE_KEY, type ConversationHistoryEntry } from "@/lib/history";
import MarkdownResponse from "@/components/MarkdownResponse";
type StatusFilter = "all" | "done" | "pending" | "error";

function loadHistory(): ConversationHistoryEntry[] {
  if (typeof window === "undefined") return [];
  const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
  return raw ? (JSON.parse(raw) as ConversationHistoryEntry[]) : [];
}

function saveHistory(entries: ConversationHistoryEntry[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(entries));
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

function statusOf(item: ConversationHistoryEntry): "done" | "pending" | "error" {
  if (item.status === "pending") return "pending";
  if (item.isError) return "error";
  return "done";
}

export default function HistoryFullPanel() {
  const [items, setItems] = useState<ConversationHistoryEntry[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");

  useEffect(() => {
    const load = () => setItems(loadHistory());
    load();
    window.addEventListener("huawei-history-updated", load);
    window.addEventListener("storage", load);
    return () => {
      window.removeEventListener("huawei-history-updated", load);
      window.removeEventListener("storage", load);
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items
      .filter((item) => (filter === "all" ? true : statusOf(item) === filter))
      .filter(
        (item) =>
          item.question.toLowerCase().includes(q) || item.answer.toLowerCase().includes(q)
      );
  }, [items, query, filter]);

  function deleteEntry(id: string) {
    const next = items.filter((i) => i.id !== id);
    setItems(next);
    saveHistory(next);
  }

  function clearAll() {
    setItems([]);
    saveHistory([]);
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
          marginBottom: "1.1rem",
        }}
      >
        <div>
          <div className="panel-title" style={{ marginBottom: "0.3rem" }}>
            Historique des conversations
          </div>
          <div style={{ color: "var(--text-faint)", fontSize: "0.8rem" }}>
            {items.length} conversation{items.length > 1 ? "s" : ""} enregistrée
            {items.length > 1 ? "s" : ""} localement
          </div>
        </div>

        {items.length > 0 && (
          <button
            type="button"
            onClick={clearAll}
            className="badge"
            style={{ cursor: "pointer", color: "var(--accent-critical)" }}
          >
            🗑 Vider l'historique
          </button>
        )}
      </div>

      <div style={{ display: "flex", gap: "0.6rem", marginBottom: "1.1rem", flexWrap: "wrap" }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Rechercher dans les questions ou réponses..."
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
          value={filter}
          onChange={(e) => setFilter(e.target.value as StatusFilter)}
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
          <option value="all">Tous les statuts</option>
          <option value="done">Terminées</option>
          <option value="pending">En cours</option>
          <option value="error">Erreurs</option>
        </select>
      </div>

      {filtered.length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>
          {items.length === 0
            ? "Aucune conversation enregistrée pour le moment. Pose une question dans le Chat Assistant."
            : "Aucune conversation ne correspond à ta recherche."}
        </p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
        {filtered.map((item) => {
          const status = statusOf(item);
          return (
            <article
              key={item.id}
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
                padding: "0.9rem 1.1rem",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: "0.75rem",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: "0.9rem", flex: 1 }}>{item.question}</div>
                <div style={{ display: "flex", gap: "0.4rem", alignItems: "center", flexShrink: 0 }}>
                  <span
                    className={`pill ${
                      status === "done" ? "pill--green" : status === "error" ? "pill--neutral" : "pill--amber"
                    }`}
                  >
                    {status === "done" ? item.confidence ?? "OK" : status === "error" ? "Erreur" : "En cours"}
                  </span>
                  <button
                    type="button"
                    onClick={() => deleteEntry(item.id)}
                    aria-label="Supprimer cette entrée"
                    style={{
                      border: "1px solid var(--border)",
                      borderRadius: "999px",
                      background: "var(--surface)",
                      color: "var(--text-faint)",
                      width: "26px",
                      height: "26px",
                      cursor: "pointer",
                      fontSize: "0.8rem",
                      lineHeight: 1,
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>

              <div
  className="audit-answer-body"
  style={{
    marginTop: "0.5rem",
    color: status === "pending" ? "var(--text-faint)" : "var(--text-dim)",
    fontSize: "0.82rem",
    fontStyle: status === "pending" ? "italic" : "normal",
    lineHeight: 1.5,
  }}
>
  {status === "pending" ? item.answer : <MarkdownResponse content={item.answer} />}
</div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginTop: "0.6rem",
                  color: "var(--text-faint)",
                  fontSize: "0.72rem",
                  fontFamily: "var(--font-mono)",
                }}
              >
                <span>{formatDate(item.createdAt)}</span>
                <span>{item.healthReportRequested ? "Rapport santé demandé" : "Réponse simple"}</span>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}