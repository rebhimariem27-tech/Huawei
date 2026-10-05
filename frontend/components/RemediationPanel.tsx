"use client";

import { useState } from "react";
import { postQuery, remediateDevice } from "@/lib/api";

const DEFAULT_AUDIT_QUERY =
  "Générez un rapport de santé complet du réseau, avec tous les écarts entre la configuration théorique et la configuration réelle.";

interface RemediationItem {
  id: string;
  device: string;
  command: string;
  sourceText: string;
  status: "pending" | "applying" | "applied" | "error";
  output?: string;
}

/**
 * Extrait les commandes correctives au format "Commande corrective : [DEVICE] commande"
 * (avec ou sans backticks autour de la commande — le format réel renvoyé par le
 * Validateur n'en a pas, mais on reste tolérant si le LLM en ajoute).
 *
 * Le lookahead arrête la capture avant la prochaine occurrence de
 * "Commande corrective" (si plusieurs dans le même texte) ou la fin de chaîne.
 */
function extractCommands(texts: string[]): RemediationItem[] {
  const pattern =
    /Commande corrective\s*:\s*`?\[([A-Za-z0-9_-]+)\]\s*([^`]+?)`?(?=\s*Commande corrective|\s*$)/gi;
  const items: RemediationItem[] = [];
  let idx = 0;

  for (const text of texts) {
    let match: RegExpExecArray | null;
    pattern.lastIndex = 0;
    while ((match = pattern.exec(text)) !== null) {
      const command = match[2].trim().replace(/[.;:]+$/, "").trim();
      if (!command) continue;
      items.push({
        id: `${idx++}-${match[1]}`,
        device: match[1].toUpperCase(),
        command,
        sourceText: text,
        status: "pending",
      });
    }
  }
  return items;
}

export default function RemediationPanel() {
  const [analyzing, setAnalyzing] = useState(false);
  const [items, setItems] = useState<RemediationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [hasAnalyzed, setHasAnalyzed] = useState(false);

  async function analyze() {
    setAnalyzing(true);
    setError(null);
    try {
      const res = await postQuery(DEFAULT_AUDIT_QUERY, false);
      const found = extractCommands([
        ...res.discrepancies,
        ...res.alerts,
        ...res.recommendations,
      ]);
      setItems(found);
      setHasAnalyzed(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analyse échouée.");
    } finally {
      setAnalyzing(false);
    }
  }

  async function applyItem(id: string) {
    const item = items.find((i) => i.id === id);
    if (!item) return;

    const confirmed = window.confirm(
      `Appliquer sur ${item.device} :\n\n  ${item.command}\n\n` +
        `Cette commande sera envoyée en direct à l'équipement réel (mode config VRP). Confirmer ?`
    );
    if (!confirmed) return;

    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, status: "applying" } : i)));

    try {
      const result = await remediateDevice(item.device, item.command);
      setItems((prev) =>
        prev.map((i) => (i.id === id ? { ...i, status: "applied", output: result.output } : i))
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Échec de l'application.";
      setItems((prev) => prev.map((i) => (i.id === id ? { ...i, status: "error", output: message } : i)));
    }
  }

  const pendingCount = items.filter((i) => i.status === "pending").length;
  const appliedCount = items.filter((i) => i.status === "applied").length;

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
            Remédiation
          </div>
          <div style={{ color: "var(--text-faint)", fontSize: "0.8rem" }}>
            Détecte les commandes correctives issues du dernier audit et les applique en direct,
            une par une, avec confirmation.
          </div>
        </div>

        <button type="button" className="btn-primary" onClick={analyze} disabled={analyzing}>
          {analyzing ? "Analyse en cours..." : "Analyser les écarts actuels"}
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

      {hasAnalyzed && items.length > 0 && (
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
          <span className="badge">{items.length} action(s) détectée(s)</span>
          {pendingCount > 0 && <span className="badge badge--warn">{pendingCount} en attente</span>}
          {appliedCount > 0 && <span className="badge badge--live">{appliedCount} appliquée(s)</span>}
        </div>
      )}

      {hasAnalyzed && items.length === 0 && !error && (
        <p style={{ color: "var(--text-dim)" }}>
          Aucune commande corrective détectée dans le dernier audit — le réseau semble conforme,
          ou les écarts détectés n'incluent pas de commande exploitable automatiquement.
        </p>
      )}

      {!hasAnalyzed && !analyzing && (
        <p style={{ color: "var(--text-dim)" }}>
          Lance une analyse pour détecter les écarts de configuration et obtenir les commandes
          correctives à appliquer sur S1 et R1.
        </p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
        {items.map((item) => (
          <RemediationRow key={item.id} item={item} onApply={() => applyItem(item.id)} />
        ))}
      </div>

      <div
        style={{
          marginTop: "1.3rem",
          padding: "0.9rem 1.1rem",
          borderRadius: "var(--radius)",
          border: "1px dashed var(--border)",
          color: "var(--text-faint)",
          fontSize: "0.78rem",
          lineHeight: 1.55,
        }}
      >
        ⚠ Chaque application envoie une vraie commande de configuration à l'équipement eNSP via
        SSH/Telnet. Une confirmation est demandée avant chaque envoi — vérifie la commande affichée
        avant de valider.
      </div>
    </div>
  );
}

function RemediationRow({ item, onApply }: { item: RemediationItem; onApply: () => void }) {
  const toneColor =
    item.status === "applied"
      ? "var(--accent-live)"
      : item.status === "error"
      ? "var(--accent-critical)"
      : item.status === "applying"
      ? "var(--accent-warn)"
      : "var(--border)";

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderLeft: `3px solid ${toneColor}`,
        borderRadius: "var(--radius)",
        background: "var(--surface-raised)",
        padding: "0.8rem 1rem",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "0.75rem",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <span className="badge">{item.device}</span>
          <code
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.82rem",
              color: "var(--text)",
            }}
          >
            {item.command}
          </code>
        </div>

        {item.status === "pending" && (
          <button type="button" onClick={onApply} className="badge badge--warn" style={{ cursor: "pointer" }}>
            Appliquer
          </button>
        )}
        {item.status === "applying" && <span className="badge">Envoi en cours...</span>}
        {item.status === "applied" && <span className="badge badge--live">✓ Appliquée</span>}
        {item.status === "error" && (
          <button type="button" onClick={onApply} className="badge" style={{ cursor: "pointer", color: "var(--accent-critical)" }}>
            ✕ Échec — réessayer
          </button>
        )}
      </div>

      <div style={{ marginTop: "0.4rem", fontSize: "0.74rem", color: "var(--text-faint)" }}>
        Source : {item.sourceText}
      </div>

      {item.output && (
        <pre
          style={{
            marginTop: "0.5rem",
            padding: "0.5rem 0.7rem",
            background: "var(--surface)",
            borderRadius: "var(--radius-sm)",
            fontSize: "0.72rem",
            color: item.status === "error" ? "var(--accent-critical)" : "var(--text-dim)",
            whiteSpace: "pre-wrap",
            maxHeight: "140px",
            overflowY: "auto",
          }}
        >
          {item.output}
        </pre>
      )}
    </div>
  );
}