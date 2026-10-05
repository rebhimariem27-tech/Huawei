"use client";

import { useState } from "react";
import { requestDesignUpdate } from "@/lib/api";
import type { DesignUpdateResponse } from "@/lib/types";

export default function DeploymentAssistantPanel() {
  const [requestText, setRequestText] = useState(
    "Ajoute S3 comme nouveau switch SSH, réserve la prochaine IP libre et configure le trunk entre S1 et S3."
  );
  const [applyChange, setApplyChange] = useState(false);
  const [result, setResult] = useState<DesignUpdateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit() {
    if (applyChange) {
      const confirmed = window.confirm(
        "Cette action va modifier backend/agents/topology.yaml. Continuer ?"
      );
      if (!confirmed) {
        return;
      }
    }

    setLoading(true);
    setError("");
    try {
      const response = await requestDesignUpdate(requestText, applyChange);
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "Erreur de déploiement");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-frame card glass-panel" style={{ padding: "1.5rem", display: "grid", gap: "1rem" }}>
      <div>
        <div className="panel-title" style={{ marginBottom: "0.3rem" }}>
          Assistant de Déploiement
        </div>
        <div style={{ color: "var(--text-faint)", fontSize: "0.82rem", lineHeight: 1.5 }}>
          Décris l'extension à faire en langage naturel. L'architecte génère le nouveau nœud, le YAML et les commandes VRP.
        </div>
      </div>

      <label style={{ display: "grid", gap: "0.45rem" }}>
        <span style={{ fontSize: "0.76rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Demande
        </span>
        <textarea
          value={requestText}
          onChange={(event) => setRequestText(event.target.value)}
          rows={5}
          style={{
            width: "100%",
            resize: "vertical",
            borderRadius: "0.9rem",
            border: "1px solid var(--border)",
            background: "var(--surface)",
            color: "var(--text)",
            padding: "0.9rem 1rem",
            font: "inherit",
            lineHeight: 1.5,
          }}
        />
      </label>

      <label style={{ display: "flex", alignItems: "center", gap: "0.6rem", color: "var(--text-dim)", fontSize: "0.85rem" }}>
        <input type="checkbox" checked={applyChange} onChange={(event) => setApplyChange(event.target.checked)} />
        Appliquer directement la mise à jour dans topology.yaml
      </label>

      {applyChange && (
        <div style={{ border: "1px solid var(--accent-warn)", color: "var(--text)", borderRadius: "0.9rem", padding: "0.8rem 0.95rem", fontSize: "0.82rem", background: "color-mix(in srgb, var(--accent-warn) 10%, transparent)" }}>
          Le prochain envoi modifiera le fichier topology.yaml après confirmation.
        </div>
      )}

      <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={loading || !requestText.trim()}
          className="badge"
          style={{ cursor: loading ? "default" : "pointer", opacity: loading || !requestText.trim() ? 0.7 : 1 }}
        >
          {loading ? "Génération..." : applyChange ? "Générer et appliquer" : "Générer le plan"}
        </button>
      </div>

      {error && (
        <div style={{ border: "1px solid var(--accent-critical)", color: "var(--accent-critical)", borderRadius: "0.9rem", padding: "0.8rem 0.95rem", fontSize: "0.84rem" }}>
          {error}
        </div>
      )}

      {result && (
        <div style={{ display: "grid", gap: "0.8rem" }}>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <span className={`pill ${result.applied ? "pill--green" : "pill--amber"}`}>
              {result.applied ? "Appliqué" : "Prévisualisation"}
            </span>
            <span className="pill pill--neutral">{result.device_name}</span>
            <span className="pill pill--neutral">{result.management_ip}</span>
          </div>

          <div style={{ display: "grid", gap: "0.45rem" }}>
            <SectionTitle>Bloc YAML</SectionTitle>
            <pre
              style={{
                margin: 0,
                padding: "0.9rem 1rem",
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "0.9rem",
                overflowX: "auto",
                fontSize: "0.82rem",
                lineHeight: 1.55,
                whiteSpace: "pre-wrap",
              }}
            >
              {result.topology_yaml_block}
            </pre>
          </div>

          {result.remediation_commands.length > 0 && (
            <div style={{ display: "grid", gap: "0.45rem" }}>
              <SectionTitle>Commandes VRP</SectionTitle>
              <pre
                style={{
                  margin: 0,
                  padding: "0.9rem 1rem",
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "0.9rem",
                  overflowX: "auto",
                  fontSize: "0.82rem",
                  lineHeight: 1.55,
                  whiteSpace: "pre-wrap",
                }}
              >
                {result.remediation_commands.join("\n")}
              </pre>
            </div>
          )}

          <div style={{ display: "grid", gap: "0.45rem" }}>
            <SectionTitle>Notes</SectionTitle>
            <div style={{ display: "grid", gap: "0.3rem", color: "var(--text-dim)", fontSize: "0.84rem" }}>
              {result.deployment_notes.map((note) => (
                <div key={note}>• {note}</div>
              ))}
            </div>
          </div>

          <div style={{ fontSize: "0.75rem", color: "var(--text-faint)" }}>
            Fichier cible: {result.topology_file}
          </div>
        </div>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: string }) {
  return (
    <div style={{ fontSize: "0.76rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-dim)" }}>
      {children}
    </div>
  );
}
