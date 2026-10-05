// frontend/components/ChatInterface.tsx
// -----------------------------------------------------------------------------
// Interface de chat — cœur de l'UI. Envoie la question au pipeline LangGraph
// via postQuery() (POST /query), affiche la réponse finale du Validateur
// avec son score de confiance, ses alertes, ses écarts théorie/terrain et
// ses recommandations.
//
// Pas de streaming ici volontairement : /query/stream existe côté API mais
// EventSource ne supporte pas POST nativement. On pourra le brancher plus
// tard via fetch() + ReadableStream si l'attente perçue devient un problème.
//
// DIRECTION DE DESIGN : la bulle assistant porte un liseré gauche coloré
// selon le niveau de confiance — l'info la plus utile pour un ingénieur
// pressé est visible sans même lire le texte.
// -----------------------------------------------------------------------------
"use client";

import { useEffect, useRef, useState } from "react";
import { postQuery,API_BASE_URL } from "@/lib/api";
import { HISTORY_STORAGE_KEY, makeHistoryId, type ConversationHistoryEntry } from "@/lib/history";
import type { Confidence, QueryResponse } from "@/lib/types";
import MarkdownResponse from "@/components/MarkdownResponse";
type Role = "user" | "assistant" | "error";

interface Message {
  id: string;
  role: Role;
  content: string;
  meta?: QueryResponse;
}

const EXAMPLE_QUERIES = [
  "Comment configurer un trunk VLAN sur S1 ?",
  "Pourquoi R1 ne répond pas au ping via OSPF ?",
  "Quel est l'état actuel des interfaces de S1 ?",
];

const CONFIDENCE_DOT: Record<Confidence, string> = {
  high: "status-dot--live",
  medium: "status-dot--warn",
  low: "status-dot--critical",
};

const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: "confiance haute",
  medium: "confiance moyenne",
  low: "confiance basse",
};

const CONFIDENCE_BORDER: Record<Confidence, string> = {
  high: "var(--accent-live)",
  medium: "var(--accent-warn)",
  low: "var(--accent-critical)",
};

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function pushHistoryEntry(entry: ConversationHistoryEntry) {
  if (typeof window === "undefined") return;

  const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
  const current: ConversationHistoryEntry[] = raw ? (JSON.parse(raw) as ConversationHistoryEntry[]) : [];
  const next = [entry, ...current].slice(0, 20);
  window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new Event("huawei-history-updated"));
}

function updateHistoryEntry(id: string, patch: Partial<ConversationHistoryEntry>) {
  if (typeof window === "undefined") return;

  const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
  if (!raw) return;

  const current: ConversationHistoryEntry[] = JSON.parse(raw) as ConversationHistoryEntry[];
  const next = current.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry));
  window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new Event("huawei-history-updated"));
}

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, isLoading]);
  const [healthReportRequested, setHealthReportRequested] = useState(false);
  async function handleSend(text: string) {
    const question = text.trim();
    if (!question || isLoading) return;

    const historyId = makeHistoryId();
    setInput("");
    setMessages((prev) => [
      ...prev,
      { id: makeId(), role: "user", content: question },
    ]);
    setIsLoading(true);

    pushHistoryEntry({
      id: historyId,
      question,
      answer: "En attente de la réponse du validateur...",
      createdAt: new Date().toISOString(),
      healthReportRequested,
      status: "pending",
    });

    try {
      const result = await postQuery(question, healthReportRequested);
      updateHistoryEntry(historyId, {
        answer: result.final_answer,
        confidence: result.confidence,
        confidenceScore: result.confidence_score,
        status: "done",
      });
      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "assistant",
          content: result.final_answer,
          meta: result,
        },
      ]);
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Le pipeline n'a pas répondu. Vérifie que l'API tourne bien.";

      updateHistoryEntry(historyId, {
        answer: message,
        isError: true,
        status: "error",
      });
      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "error",
          content: message,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend(input);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-panel__header">
        <div>
          <div className="chat-panel__eyebrow">Assistant conversationnel</div>
          <div className="chat-panel__title">Posez votre question réseau</div>
        </div>
        <div className="chat-panel__badge">
          <span className="status-dot status-dot--live" />
          Disponible
        </div>
      </div>

      <div ref={scrollRef} className="chat-panel__history">
        {messages.length === 0 && <EmptyState onPick={handleSend} />}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {isLoading && <ThinkingBubble />}
      </div>

      {/* ---------- Zone de saisie ---------- */}
      <div className="chat-panel__composer">
        <label className="chat-panel__toggle">
          <ToggleSwitch
            checked={healthReportRequested}
            onChange={setHealthReportRequested}
          />
          générer un rapport de santé (.md)
        </label>

        <div className="chat-panel__input-row">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Pose ta question réseau (Entrée pour envoyer, Maj+Entrée pour une ligne)"
            rows={1}
            className="chat-panel__input"
          />
          <button
            className="btn-primary"
            onClick={() => handleSend(input)}
            disabled={isLoading || !input.trim()}
          >
            Envoyer
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sous-composants
// ---------------------------------------------------------------------------

function ToggleSwitch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <span
      role="switch"
      aria-checked={checked}
      tabIndex={0}
      onClick={() => onChange(!checked)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onChange(!checked);
        }
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        width: "32px",
        height: "18px",
        borderRadius: "999px",
        padding: "2px",
        background: checked ? "var(--brand-red)" : "var(--surface)",
        border: `1px solid ${checked ? "var(--brand-red-dim)" : "var(--border-strong)"}`,
        cursor: "pointer",
        transition: "background 0.15s ease, border-color 0.15s ease",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          width: "12px",
          height: "12px",
          borderRadius: "50%",
          background: "#fff",
          transform: checked ? "translateX(14px)" : "translateX(0)",
          transition: "transform 0.15s ease",
          boxShadow: "0 1px 2px rgba(0,0,0,0.4)",
        }}
      />
    </span>
  );
}

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div
      style={{
        margin: "auto",
        textAlign: "center",
        maxWidth: "440px",
        color: "var(--text-dim)",
      }}
    >
      <p
        style={{ fontFamily: "var(--font-mono)", fontSize: "0.8rem", color: "var(--brand-red)", letterSpacing: "0.02em" }}
      >
        &lt;NetRAG&gt; en attente de commande
      </p>
      <p style={{ fontSize: "0.88rem", marginBottom: "1.1rem", color: "var(--text-dim)" }}>
        Pose une question sur la configuration, le diagnostic ou l&apos;état
        de ton infrastructure Huawei.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.45rem" }}>
        {EXAMPLE_QUERIES.map((q) => (
          <button
            key={q}
            onClick={() => onPick(q)}
            style={{
              background: "var(--surface-raised)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              color: "var(--text-dim)",
              padding: "0.55rem 0.8rem",
              fontSize: "0.8rem",
              textAlign: "left",
              cursor: "pointer",
              transition: "border-color 0.15s ease, color 0.15s ease, transform 0.1s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "var(--brand-red-dim)";
              e.currentTarget.style.color = "var(--text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "var(--border)";
              e.currentTarget.style.color = "var(--text-dim)";
            }}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

function ThinkingBubble() {
  return (
    <div
      style={{
        alignSelf: "flex-start",
        display: "flex",
        alignItems: "center",
        gap: "0.55rem",
        color: "var(--text-faint)",
        fontFamily: "var(--font-mono)",
        fontSize: "0.8rem",
        padding: "0.5rem 0.1rem",
      }}
    >
      <span style={{ display: "inline-flex", gap: "3px" }}>
        <TypingDot delay="0s" />
        <TypingDot delay="0.15s" />
        <TypingDot delay="0.3s" />
      </span>
      architecte → documentaliste → validateur...
    </div>
  );
}

function TypingDot({ delay }: { delay: string }) {
  return (
    <span
      style={{
        width: "4px",
        height: "4px",
        borderRadius: "50%",
        background: "var(--brand-red)",
        animation: "pulse-live 1.1s ease-in-out infinite",
        animationDelay: delay,
      }}
    />
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const isError = message.role === "error";
  const confidenceBorder =
    !isUser && !isError && message.meta ? CONFIDENCE_BORDER[message.meta.confidence] : undefined;

  return (
    <div
      style={{
        alignSelf: isUser ? "flex-end" : "flex-start",
        maxWidth: "85%",
        display: "flex",
        flexDirection: "column",
        gap: "0.4rem",
      }}
    >
      <div
        style={{
          background: isUser
            ? "linear-gradient(135deg, var(--brand-red-bright), var(--brand-red))"
            : isError
            ? "color-mix(in srgb, var(--accent-critical) 10%, var(--surface))"
            : "var(--surface-raised)",
          color: isUser ? "#fff" : "var(--text)",
          border: isError
            ? "1px solid var(--accent-critical)"
            : `1px solid ${isUser ? "var(--brand-red-dim)" : "var(--border)"}`,
          borderLeft: confidenceBorder ? `3px solid ${confidenceBorder}` : undefined,
          borderRadius: "var(--radius)",
          padding: "0.7rem 0.9rem",
          fontSize: "0.9rem",
          lineHeight: 1.55,
          whiteSpace: isUser || isError ? "pre-wrap" : "normal",
          boxShadow: isUser ? "var(--shadow-red-glow)" : "none",
        }}
      >
        {isError && (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.72rem",
              color: "var(--accent-critical)",
              marginBottom: "0.3rem",
              fontWeight: 600,
            }}
          >
            ERREUR PIPELINE
          </div>
        )}
        {isUser || isError ? (
  message.content
) : (
  <MarkdownResponse content={message.content} />
)}
      </div>

      {message.meta && <ResponseMeta meta={message.meta} />}
    </div>
  );
}

function ResponseMeta({ meta }: { meta: QueryResponse }) {
  return (
    <div
      className="message-meta"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.55rem",
      }}
    >
      {/* Ligne métadonnées en pills : confiance · intent · stratégie · chunks */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", alignItems: "center" }}>
        <span className="badge">
          <span className={`status-dot ${CONFIDENCE_DOT[meta.confidence]}`} />
          {CONFIDENCE_LABEL[meta.confidence]} · {Math.round(meta.confidence_score * 100)}%
        </span>
        <span className="badge">intent: {meta.intent}</span>
        <span className="badge">stratégie: {meta.strategy}</span>
        <span className="badge">{meta.rag_chunks_count} chunks RAG</span>
      </div>

      {meta.alerts.length > 0 && (
        <MetaList label="Alertes" items={meta.alerts} color="var(--accent-warn)" />
      )}
      {meta.discrepancies.length > 0 && (
        <MetaList
          label="Écarts théorie / terrain"
          items={meta.discrepancies}
          color="var(--accent-critical)"
        />
      )}

      {meta.recommendations.length > 0 && (
        <MetaList
          label="Recommandations"
          items={meta.recommendations}
          color="var(--accent-live)"
        />
      )}
      {meta.report_filename && (
        <a
          href={`${API_BASE_URL}/reports/${meta.report_filename}`}
          download
          target="_blank"
          rel="noopener noreferrer"
          className="badge badge--live"
          style={{
            width: "fit-content",
            color: "var(--accent-live)",
            textDecoration: "none",
            cursor: "pointer",
          }}
        >
          ⭳ télécharger le rapport de santé
        </a>
      )}
    </div>
  );
}

function MetaList({
  label,
  items,
  color,
}: {
  label: string;
  items: string[];
  color: string;
}) {
  return (
    <div
      className="meta-list"
      style={{
        border: "1px solid var(--border)",
        borderLeft: `2px solid ${color}`,
        borderRadius: "var(--radius-sm)",
        background: "var(--surface-raised)",
        padding: "0.55rem 0.7rem",
        fontFamily: "var(--font-mono)",
        fontSize: "0.72rem",
        color: "var(--text-faint)",
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