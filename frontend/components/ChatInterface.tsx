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
// -----------------------------------------------------------------------------
"use client";

import { useEffect, useRef, useState } from "react";
import { postQuery } from "@/lib/api";
import type { Confidence, QueryResponse } from "@/lib/types";

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

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
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

    setInput("");
    setMessages((prev) => [
      ...prev,
      { id: makeId(), role: "user", content: question },
    ]);
    setIsLoading(true);

    try {
      const result = await postQuery(question, healthReportRequested);
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
      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "error",
          content:
            err instanceof Error
              ? err.message
              : "Le pipeline n'a pas répondu. Vérifie que l'API tourne bien.",
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
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--surface)",
        overflow: "hidden",
      }}
    >
      {/* ---------- Historique ---------- */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "1.25rem",
          display: "flex",
          flexDirection: "column",
          gap: "0.9rem",
        }}
      >
        {messages.length === 0 && <EmptyState onPick={handleSend} />}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {isLoading && <ThinkingBubble />}
      </div>

      {/* ---------- Zone de saisie ---------- */}
      <div
        style={{
          borderTop: "1px solid var(--border)",
          padding: "0.85rem 1rem",
          display: "flex",
          gap: "0.6rem",
          alignItems: "flex-end",
        }}
      >
        <label
  style={{
    display: "flex",
    alignItems: "center",
    gap: "0.4rem",
    fontSize: "0.78rem",
    color: "var(--text-dim)",
    fontFamily: "var(--font-mono)",
    cursor: "pointer",
  }}
>
  <input
    type="checkbox"
    checked={healthReportRequested}
    onChange={(e) => setHealthReportRequested(e.target.checked)}
  />
  générer un rapport de santé (.md)
</label>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Pose ta question réseau (Entrée pour envoyer, Maj+Entrée pour une ligne)"
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            background: "var(--surface-raised)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            color: "var(--text)",
            padding: "0.6rem 0.75rem",
            fontFamily: "var(--font-sans)",
            fontSize: "0.9rem",
            lineHeight: 1.4,
            maxHeight: "8rem",
          }}
        />
        <button
          onClick={() => handleSend(input)}
          disabled={isLoading || !input.trim()}
          style={{
            background: isLoading || !input.trim() ? "var(--surface-raised)" : "var(--accent-live)",
            color: isLoading || !input.trim() ? "var(--text-faint)" : "#0a0e12",
            border: "none",
            borderRadius: "var(--radius)",
            padding: "0.6rem 1.1rem",
            fontFamily: "var(--font-mono)",
            fontSize: "0.82rem",
            fontWeight: 600,
            cursor: isLoading || !input.trim() ? "default" : "pointer",
            whiteSpace: "nowrap",
          }}
        >
          Envoyer
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sous-composants
// ---------------------------------------------------------------------------

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div
      style={{
        margin: "auto",
        textAlign: "center",
        maxWidth: "420px",
        color: "var(--text-dim)",
      }}
    >
      <p style={{ fontFamily: "var(--font-mono)", fontSize: "0.8rem", color: "var(--text-faint)" }}>
        &lt;NetRAG&gt; en attente de commande
      </p>
      <p style={{ fontSize: "0.88rem", marginBottom: "1rem" }}>
        Pose une question sur la configuration, le diagnostic ou l&apos;état
        de ton infrastructure Huawei.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
        {EXAMPLE_QUERIES.map((q) => (
          <button
            key={q}
            onClick={() => onPick(q)}
            style={{
              background: "var(--surface-raised)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              color: "var(--text-dim)",
              padding: "0.5rem 0.75rem",
              fontSize: "0.8rem",
              textAlign: "left",
              cursor: "pointer",
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
        gap: "0.5rem",
        color: "var(--text-faint)",
        fontFamily: "var(--font-mono)",
        fontSize: "0.8rem",
        padding: "0.5rem 0.1rem",
      }}
    >
      <span className="status-dot status-dot--live" />
      architecte → documentaliste → validateur...
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const isError = message.role === "error";

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
            ? "var(--accent-live)"
            : isError
            ? "color-mix(in srgb, var(--accent-critical) 12%, var(--surface))"
            : "var(--surface-raised)",
          color: isUser ? "#0a0e12" : "var(--text)",
          border: isError ? "1px solid var(--accent-critical)" : "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: "0.7rem 0.9rem",
          fontSize: "0.9rem",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
        }}
      >
        {isError && (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.72rem",
              color: "var(--accent-critical)",
              marginBottom: "0.3rem",
            }}
          >
            ERREUR PIPELINE
          </div>
        )}
        {message.content}
      </div>

      {message.meta && <ResponseMeta meta={message.meta} />}
    </div>
  );
}

function ResponseMeta({ meta }: { meta: QueryResponse }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.5rem",
        fontFamily: "var(--font-mono)",
        fontSize: "0.72rem",
        color: "var(--text-faint)",
      }}
    >
      {/* Ligne métadonnées : confiance · intent · stratégie · chunks */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.9rem", alignItems: "center" }}>
        <span style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
          <span className={`status-dot ${CONFIDENCE_DOT[meta.confidence]}`} />
          {CONFIDENCE_LABEL[meta.confidence]} ({Math.round(meta.confidence_score * 100)}%)
        </span>
        <span>intent: {meta.intent}</span>
        <span>stratégie: {meta.strategy}</span>
        <span>{meta.rag_chunks_count} chunks RAG</span>
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
        <div style={{ marginTop: "0.2rem" }}>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL}/reports/${meta.report_filename}`}
            download
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: "var(--accent-live)",
              textDecoration: "underline",
              fontFamily: "var(--font-mono)",
              fontSize: "0.75rem",
              display: "inline-flex",
              alignItems: "center",
              cursor: "pointer"
            }}
          >
            ⭳ télécharger le rapport de santé
          </a>
        </div>
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
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: "0.5rem 0.65rem",
      }}
    >
      <div style={{ color, marginBottom: "0.25rem", letterSpacing: "0.03em" }}>
        {label.toUpperCase()}
      </div>
      <ul style={{ margin: 0, paddingLeft: "1.1rem", color: "var(--text-dim)" }}>
        {items.map((item, i) => (
          <li key={i} style={{ marginBottom: "0.15rem" }}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}