"use client";

import { useState } from "react";
import { proposeCommand, applyCommands } from "@/lib/api";
import type { CommandProposal, CommandApplyResult } from "@/lib/types";

type Stage =
  | { step: "idle" }
  | { step: "loading" }
  | { step: "proposed"; proposal: CommandProposal }
  | { step: "applying" }
  | { step: "done"; results: CommandApplyResult[] }
  | { step: "error"; message: string };

export default function CommandAssistant() {
  const [query, setQuery] = useState("");
  const [stage, setStage] = useState<Stage>({ step: "idle" });

  const handlePropose = async () => {
    if (!query.trim()) return;
    setStage({ step: "loading" });
    try {
      const proposal = await proposeCommand(query);
      if (proposal.commands.length === 0) {
        setStage({ step: "error", message: proposal.explanation || "Aucune commande générée." });
        return;
      }
      setStage({ step: "proposed", proposal });
    } catch (err) {
      setStage({ step: "error", message: err instanceof Error ? err.message : "Erreur inconnue" });
    }
  };

  const handleConfirm = async (proposal: CommandProposal) => {
    setStage({ step: "applying" });
    try {
      const results = await applyCommands(proposal.device_name, proposal.commands);
      setStage({ step: "done", results });
    } catch (err) {
      setStage({ step: "error", message: err instanceof Error ? err.message : "Erreur inconnue" });
    }
  };

  const reset = () => {
    setQuery("");
    setStage({ step: "idle" });
  };

  return (
    <div className="chat-frame card glass-panel" style={{ padding: "1.5rem" }}>
      <div className="panel-title" style={{ marginBottom: "0.8rem" }}>
        Assistant de configuration
      </div>

      <textarea
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="ex : active OSPF sur S3"
        disabled={stage.step === "loading" || stage.step === "applying"}
        rows={2}
        style={{
          width: "100%",
          padding: "0.7rem 0.9rem",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--border)",
          background: "var(--surface)",
          color: "var(--text)",
          fontFamily: "inherit",
          resize: "vertical",
        }}
      />

      {stage.step === "idle" || stage.step === "loading" ? (
        <button
          type="button"
          onClick={handlePropose}
          disabled={stage.step === "loading" || !query.trim()}
          className="badge"
          style={{ marginTop: "0.7rem", cursor: "pointer" }}
        >
          {stage.step === "loading" ? "Génération..." : "Générer les commandes"}
        </button>
      ) : null}

      {stage.step === "error" && (
        <div style={{ marginTop: "0.8rem", color: "var(--accent-critical)", fontSize: "0.85rem" }}>
          ⚠ {stage.message}
          <button type="button" onClick={reset} className="badge" style={{ marginLeft: "0.6rem", cursor: "pointer" }}>
            Réessayer
          </button>
        </div>
      )}

      {stage.step === "proposed" && (
        <div style={{ marginTop: "1rem", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "1rem" }}>
          <div style={{ fontWeight: 700, marginBottom: "0.4rem" }}>
            {stage.proposal.device_name}
            <span
              className="badge"
              style={{
                marginLeft: "0.6rem",
                color:
                  stage.proposal.risk_level === "high"
                    ? "var(--accent-critical)"
                    : stage.proposal.risk_level === "medium"
                    ? "var(--accent-warn)"
                    : "var(--accent-live)",
              }}
            >
              Risque {stage.proposal.risk_level}
            </span>
          </div>

          <p style={{ fontSize: "0.85rem", color: "var(--text-dim)" }}>{stage.proposal.explanation}</p>

          <pre
            style={{
              background: "var(--surface)",
              padding: "0.8rem",
              borderRadius: "var(--radius-sm)",
              fontFamily: "var(--font-mono)",
              fontSize: "0.82rem",
              overflowX: "auto",
            }}
          >
            {stage.proposal.commands.join("\n")}
          </pre>

          <div style={{ display: "flex", gap: "0.6rem", marginTop: "0.8rem" }}>
            <button
              type="button"
              onClick={() => handleConfirm(stage.proposal)}
              className="badge"
              style={{ cursor: "pointer", background: "var(--accent-live)" }}
            >
              ✓ Appliquer sur {stage.proposal.device_name}
            </button>
            <button type="button" onClick={reset} className="badge" style={{ cursor: "pointer" }}>
              Annuler
            </button>
          </div>
        </div>
      )}

      {stage.step === "applying" && (
        <div style={{ marginTop: "0.8rem", color: "var(--text-faint)" }}>Application en cours...</div>
      )}

      {stage.step === "done" && (
        <div style={{ marginTop: "1rem" }}>
          {stage.results.map((r, i) => (
            <div
              key={i}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "0.8rem",
                color: r.success ? "var(--accent-live)" : "var(--accent-critical)",
                marginBottom: "0.3rem",
              }}
            >
              {r.success ? "✓" : "✗"} {r.command}
            </div>
          ))}
          <button type="button" onClick={reset} className="badge" style={{ marginTop: "0.6rem", cursor: "pointer" }}>
            Nouvelle demande
          </button>
        </div>
      )}
    </div>
  );
}