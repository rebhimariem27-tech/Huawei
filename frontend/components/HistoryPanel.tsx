"use client";

import { useEffect, useState } from "react";
import { HISTORY_STORAGE_KEY, type ConversationHistoryEntry } from "@/lib/history";
import MarkdownResponse from "@/components/MarkdownResponse";
const MAX_VISIBLE_ITEMS = 6;

export default function HistoryPanel() {
  const [items, setItems] = useState<ConversationHistoryEntry[]>([]);

  useEffect(() => {
    const loadHistory = () => {
      if (typeof window === "undefined") return;

      const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
      const parsed = raw ? (JSON.parse(raw) as ConversationHistoryEntry[]) : [];
      setItems(parsed.slice(0, MAX_VISIBLE_ITEMS));
    };

    loadHistory();
    window.addEventListener("huawei-history-updated", loadHistory);
    window.addEventListener("storage", loadHistory);

    return () => {
      window.removeEventListener("huawei-history-updated", loadHistory);
      window.removeEventListener("storage", loadHistory);
    };
  }, []);

  return (
    <section className="panel card glass-panel">
      <div className="panel-header-row">
        <div className="panel-title">Historique récent</div>
        <span className="pill pill--neutral">Local</span>
      </div>

      <div className="history-empty-note">
        Les 6 dernières conversations apparaissent ici après l’envoi d’une question.
      </div>

      <div className="history-list">
        {items.length === 0 ? (
          <div className="history-placeholder">
            Aucune entrée pour le moment.
          </div>
        ) : (
          items.map((item) => (
            <article key={item.id} className="history-item">
              <div className="history-item__top">
                <div className="history-item__title">{item.question}</div>
                <span
                  className={`pill ${
                    item.status === "pending"
                      ? "pill--neutral"
                      : item.isError
                      ? "pill--neutral"
                      : "pill--green"
                  }`}
                >
                  {item.status === "pending"
                    ? "En cours"
                    : item.isError
                    ? "Erreur"
                    : item.confidence ?? "OK"}
                </span>
              </div>
              <div className={`history-item__answer ${item.status === "pending" ? "history-item__answer--pending" : ""}`}>
              {item.answer}
</div>
              <div className="history-item__meta">
                <span>{new Date(item.createdAt).toLocaleString("fr-FR")}</span>
                <span>{item.healthReportRequested ? "Rapport santé demandé" : "Réponse simple"}</span>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
