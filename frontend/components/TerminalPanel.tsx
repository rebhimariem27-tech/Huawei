"use client";

import { useEffect, useState } from "react";
import { getTopology, runTerminalCommands } from "@/lib/api";
import type { TerminalCommandResult, TopologyDevice } from "@/lib/types";

export default function TerminalPanel() {
  const [devices, setDevices] = useState<TopologyDevice[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [command, setCommand] = useState("");
  const [history, setHistory] = useState<TerminalCommandResult[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTopology().then((snapshot) => {
      setDevices(snapshot.devices);
      if (snapshot.devices.length > 0) setSelected(snapshot.devices[0].name.toUpperCase());
    });
  }, []);

  const handleRun = async () => {
    if (!command.trim() || !selected) return;
    setRunning(true);
    setError(null);
    try {
      const res = await runTerminalCommands(selected, [command.trim()]);
      if (!res.success) {
        setError(res.error ?? "Échec de la connexion à l'équipement.");
      } else {
        setHistory((prev) => [...prev, ...res.results]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur inconnue");
    } finally {
      setCommand("");
      setRunning(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !running) {
      e.preventDefault();
      handleRun();
    }
  };

  return (
    <div className="chat-frame card glass-panel" style={{ padding: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <div className="panel-title">Terminal CLI</div>
        <select
          value={selected}
          onChange={(e) => {
            setSelected(e.target.value);
            setHistory([]);
          }}
          style={{
            padding: "0.4rem 0.7rem",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--border)",
            background: "var(--surface)",
            color: "var(--text)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {devices.map((d) => (
            <option key={d.name} value={d.name.toUpperCase()}>
              {d.name.toUpperCase()} — {d.mgmt_ip}
            </option>
          ))}
        </select>
      </div>

      <div
        style={{
          background: "#0c0e10",
          color: "#4ade80",
          fontFamily: "var(--font-mono)",
          fontSize: "0.82rem",
          borderRadius: "var(--radius-sm)",
          padding: "1rem",
          minHeight: "320px",
          maxHeight: "480px",
          overflowY: "auto",
          whiteSpace: "pre-wrap",
        }}
      >
        {history.length === 0 && (
          <div style={{ color: "#6b7280" }}>
            [{selected}] en attente de commande... (ex: display current-configuration)
          </div>
        )}
        {history.map((entry, i) => (
          <div key={i} style={{ marginBottom: "0.8rem" }}>
            <div style={{ color: "#e5e7eb" }}>
              [{selected}] {entry.command}
            </div>
            <div>{entry.output}</div>
          </div>
        ))}
        {running && <div style={{ color: "#6b7280" }}>Exécution en cours...</div>}
      </div>

      {error && (
        <div style={{ marginTop: "0.6rem", color: "var(--accent-critical)", fontSize: "0.82rem" }}>⚠ {error}</div>
      )}

      <div style={{ display: "flex", gap: "0.6rem", marginTop: "0.8rem" }}>
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="display current-configuration"
          disabled={running || !selected}
          style={{
            flex: 1,
            padding: "0.6rem 0.9rem",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--border)",
            background: "var(--surface)",
            color: "var(--text)",
            fontFamily: "var(--font-mono)",
          }}
        />
        <button
          type="button"
          onClick={handleRun}
          disabled={running || !command.trim()}
          className="badge"
          style={{ cursor: "pointer" }}
        >
          Exécuter
        </button>
      </div>
    </div>
  );
}