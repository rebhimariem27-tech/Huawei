"use client";

import { useEffect, useState, useCallback } from "react";
import { getHealth, getTopology } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";
import type { TopologySnapshot } from "@/lib/types";
import NetworkTopologyTree, { type TopoNode } from "@/components/NetworkTopologyTree";
const REFRESH_INTERVAL_MS = 15000;

type NodeState = "online" | "warning" | "offline";

function formatTime(date: Date): string {
  return date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function TopologyFullPanel({ initialHealth }: { initialHealth: HealthResponse }) {
  const [health, setHealth] = useState<HealthResponse>(initialHealth);
  const [topology, setTopology] = useState<TopologySnapshot | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [freshHealth, freshTopology] = await Promise.all([getHealth(), getTopology()]);
      setHealth(freshHealth);
      setTopology(freshTopology);
      setLastRefresh(new Date());
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void getTopology().then(setTopology).catch(() => setTopology(null));
  }, []);

  useEffect(() => {
    const interval = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);
  
  const tree = buildTree(topology, health);
  const nodes = flattenNodes(tree);
  const total = health.devices_total ?? (topology?.devices.length ?? 0);
  const reachable = health.devices_reachable ?? 0;
  const ratio = total > 0 ? reachable / total : 0;

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
            Topologie Live
          </div>
          <div style={{ color: "var(--text-faint)", fontSize: "0.8rem" }}>
            Lab eNSP - TP Huawei · dernière vérification à {formatTime(lastRefresh)}
          </div>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <span className={`pill ${ratio >= 1 ? "pill--green" : ratio > 0 ? "pill--amber" : "pill--neutral"}`}>
            {reachable}/{total} équipements joignables
          </span>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="badge"
            style={{ cursor: refreshing ? "default" : "pointer", opacity: refreshing ? 0.6 : 1 }}
          >
            {refreshing ? "Actualisation..." : "⟳ Actualiser"}
          </button>
        </div>
      </div>

      {/* Diagramme de topologie en arbre */}
      <div style={{ marginBottom: "1.3rem" }}>
        <NetworkTopologyTree root={tree} />
      </div>
      {/* Statistiques globales */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "0.7rem",
          marginBottom: "1.2rem",
        }}
      >
        <StatCard label="Dispositifs" value={`${total}`} />
        <StatCard label="En ligne" value={`${reachable}`} tone={ratio >= 1 ? "live" : "warn"} />
        <StatCard
          label="Qdrant"
          value={health.qdrant_connected ? "Connecté" : "Hors ligne"}
          tone={health.qdrant_connected ? "live" : "warn"}
        />
        <StatCard
          label="API LLM"
          value={health.groq_configured ? "Opérationnel" : "Hors ligne"}
          tone={health.groq_configured ? "live" : "warn"}
        />
      </div>

      {/* Tableau détaillé de connectivité */}
      <div className="panel-title" style={{ fontSize: "0.9rem", marginBottom: "0.6rem" }}>
        Détail de connectivité
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        {nodes.map((node) => (
          <div
            key={`row-${node.id}`}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              background: "var(--surface-raised)",
              padding: "0.6rem 0.9rem",
              fontSize: "0.82rem",
            }}
          >
            <span style={{ fontWeight: 600 }}>{node.label}</span>
            <span style={{ color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>{node.ip}</span>
            <span style={{ color: "var(--text-dim)" }}>{node.protocol}</span>
            <span
              className={`badge ${node.state === "online" ? "badge--live" : node.state === "offline" ? "" : "badge--warn"}`}
            >
              {node.state === "online" ? "OK" : node.state === "offline" ? "KO" : "?"}
            </span>
          </div>
        ))}
      </div>

      <p style={{ marginTop: "1.2rem", color: "var(--text-faint)", fontSize: "0.76rem", lineHeight: 1.5 }}>
        Les états sont recalculés automatiquement toutes les {REFRESH_INTERVAL_MS / 1000} secondes à partir de{" "}
        <code>/health</code>, ou immédiatement via le bouton « Actualiser ».
      </p>
    </div>
  );
}

function StatCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "live" | "warn" | "neutral";
}) {
  const color =
    tone === "live" ? "var(--accent-live)" : tone === "warn" ? "var(--accent-warn)" : "var(--text)";
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--surface-raised)",
        padding: "0.7rem 0.85rem",
      }}
    >
      <div style={{ color: "var(--text-faint)", fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {label}
      </div>
      <div style={{ marginTop: "0.3rem", fontWeight: 700, fontSize: "0.95rem", color }}>{value}</div>
    </div>
  );
}

function buildTree(topology: TopologySnapshot | null, health: HealthResponse): TopoNode {
  const devices = topology?.devices ?? [];
  const mapped = devices.map((device) => {
    const reachable = health.device_connectivity?.[device.name];
    const state: NodeState = reachable === true ? "online" : reachable === false ? "offline" : "warning";
    return {
      id: device.name.toLowerCase(),
      label: device.name,
      role: device.description || device.parent_interface || device.mgmt_interface || device.type,
      ip: device.mgmt_ip,
      protocol: device.protocol,
      kind: device.type === "router" ? ("router" as const) : ("switch" as const),
      state,
      parentDevice: device.parent_device,
      parentInterface: device.parent_interface,
      linkMode: device.link_mode,
    };
  });

  const byName = new Map(mapped.map((device) => [device.label.toUpperCase(), device]));
  const rootCandidate =
    mapped.find((device) => device.kind === "router") ??
    mapped.find((device) => !device.parentDevice || !byName.has(device.parentDevice.toUpperCase())) ??
    mapped[0];

  if (!rootCandidate) {
    return {
      id: "empty",
      label: topology?.network_name ?? "Topologie",
      kind: "router",
      state: "unknown",
      children: [],
    };
  }

  const children = mapped
    .filter((device) => {
      if (device.label.toUpperCase() === rootCandidate.label.toUpperCase()) return false;
      const parent = (device.parentDevice || "").toUpperCase();
      const parentIsValid = parent && byName.has(parent);
      // Enfant explicite de la racine OU orphelin (parent absent/inconnu) → rattaché à la racine
      return parent === rootCandidate.label.toUpperCase() || !parentIsValid;
    })
    .map((device) => toTreeNode(device, mapped));

  return {
    id: rootCandidate.id,
    label: rootCandidate.label,
    role: rootCandidate.role,
    ip: rootCandidate.ip,
    protocol: rootCandidate.protocol,
    kind: rootCandidate.kind,
    state: rootCandidate.state,
    children,
  };
}
function flattenNodes(root: TopoNode): TopoNode[] {
  const result: TopoNode[] = [root];
  for (const child of root.children ?? []) {
    result.push(...flattenNodes(child));
  }
  return result;
}
function toTreeNode(
  device: {
    id: string;
    label: string;
    role: string;
    ip?: string;
    protocol?: string;
    kind: "router" | "switch";
    state: NodeState;
    parentDevice?: string;
    parentInterface?: string;
    linkMode?: string;
  },
  allDevices: Array<{
    id: string;
    label: string;
    role: string;
    ip?: string;
    protocol?: string;
    kind: "router" | "switch";
    state: NodeState;
    parentDevice?: string;
    parentInterface?: string;
    linkMode?: string;
  }>
): TopoNode {
  return {
    id: device.id,
    label: device.label,
    role: device.parentInterface && device.linkMode ? `${device.role} · ${device.parentInterface} · ${device.linkMode}` : device.role,
    ip: device.ip,
    protocol: device.protocol,
    kind: device.kind,
    state: device.state,
    children: allDevices
      .filter((candidate) => (candidate.parentDevice || "").toUpperCase() === device.label.toUpperCase())
      .map((candidate) => toTreeNode(candidate, allDevices)),
  };
}