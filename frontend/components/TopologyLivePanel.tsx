"use client";

import { useEffect, useMemo, useState } from "react";
import { getTopology } from "@/lib/api";
import type { TopologySnapshot } from "@/lib/types";
import type { HealthResponse } from "@/lib/types";
import NetworkTopologyTree, { type TopoNode } from "@/components/NetworkTopologyTree";
type NodeState = "online" | "warning" | "offline";

interface TopologyNode {
  id: string;
  label: string;
  role: string;
  ip: string;
  protocol: string;
  interfaceName: string;
  state: NodeState;
}

interface TopologyLivePanelProps {
  health: HealthResponse;
}

export default function TopologyLivePanel({ health }: TopologyLivePanelProps) {
  const [topology, setTopology] = useState<TopologySnapshot | null>(null);

  useEffect(() => {
    let active = true;
    void getTopology()
      .then((snapshot) => {
        if (active) {
          setTopology(snapshot);
        }
      })
      .catch(() => {
        if (active) {
          setTopology(null);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const nodes = useMemo(() => buildNodes(topology, health), [topology, health.device_connectivity]);

  const summary = useMemo(() => {
    const reachable = health.devices_reachable ?? 0;
    const total = health.devices_total ?? (topology?.devices.length ?? 0);
    const ratio = total > 0 ? reachable / total : 0;

    return {
      reachable,
      total,
      ratio,
      label:
        reachable === total && total > 0
          ? "Tous les équipements répondent"
          : total > 0
          ? `${reachable}/${total} équipements joignables`
          : "Aucune donnée eNSP disponible",
    };
  }, [health.devices_reachable, health.devices_total, (topology?.devices.length ?? 0)]);

  return (
    <section id="topologie-live" className="panel card glass-panel topology-panel">
      <div className="panel-header-row">
        <div>
          <div className="panel-title">Topologie Live</div>
          <div className="topology-subtitle">Lab eNSP - TP Huawei</div>
        </div>
        <span className={`pill ${summary.ratio >= 1 ? "pill--green" : summary.ratio > 0 ? "pill--amber" : "pill--neutral"}`}>
          {summary.label}
        </span>
      </div>

      <div className="topology-stage">
        <NetworkTopologyTree root={nodes} compact showLegend={false} />
      </div>
      <div className="topology-footer">
        <StatBlock label="Dispositifs" value={`${summary.total || (topology?.devices.length ?? 0)}`} />
        <StatBlock label="En ligne" value={`${summary.reachable}`} tone={summary.ratio >= 1 ? "live" : "warn"} />
        <StatBlock label="Mode" value={health.qdrant_connected ? "Connecté" : "Hors ligne"} tone={health.qdrant_connected ? "live" : "warn"} />
      </div>

      <div className="topology-hint">
        Les états sont calculés à partir de la santé globale du backend et des équipements déclarés dans la topologie du lab.
      </div>
    </section>
  );
}

function buildNodes(topology: TopologySnapshot | null, health: HealthResponse): TopoNode {
  const devices = topology?.devices ?? [];
  const decorated = devices.map((device) => {
    const reachable = health.device_connectivity?.[device.name];
    const state: NodeState = reachable === true ? "online" : reachable === false ? "offline" : "warning";
    return {
      id: device.name.toLowerCase(),
      label: device.name,
      role: device.description || device.parent_interface || device.mgmt_interface || device.type,
      ip: device.mgmt_ip,
      protocol: device.protocol,
      kind: device.type === "router" ? "router" : "switch",
      state,
      parentDevice: device.parent_device,
      parentInterface: device.parent_interface,
      linkMode: device.link_mode,
    } as const;
  });

  const byName = new Map(decorated.map((device) => [device.label.toUpperCase(), device]));
  const roots = decorated.filter((device) => !device.parentDevice || !byName.has(device.parentDevice.toUpperCase()));

  const root = roots.find((device) => device.kind === "router") ?? roots[0] ?? decorated[0];
  if (!root) {
    return {
      id: "empty",
      label: topology?.network_name ?? "Topologie",
      kind: "router",
      state: "unknown",
      children: [],
    };
  }

  const children = decorated
  .filter((device) => {
    if (device.label.toUpperCase() === root.label.toUpperCase()) return false;
    const parent = (device.parentDevice || "").toUpperCase();
    const parentIsValid = parent && byName.has(parent);
    // Enfant explicite de la racine OU orphelin (pas de parent connu) → rattaché à la racine
    return parent === root.label.toUpperCase() || !parentIsValid;
  })
  .map((device) => toTreeNode(device, decorated));
  return {
    id: root.id,
    label: root.label,
    role: root.role,
    ip: root.ip,
    protocol: root.protocol,
    kind: root.kind,
    state: root.state,
    children,
  };
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
  const children = allDevices
    .filter((candidate) => (candidate.parentDevice || "").toUpperCase() === device.label.toUpperCase())
    .map((candidate) => toTreeNode(candidate, allDevices));

  return {
    id: device.id,
    label: device.label,
    role: device.parentInterface && device.linkMode ? `${device.role} · ${device.parentInterface} · ${device.linkMode}` : device.role,
    ip: device.ip,
    protocol: device.protocol,
    kind: device.kind,
    state: device.state,
    children,
  };
}

function StatBlock({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "live" | "warn" | "neutral";
}) {
  return (
    <div className="topology-stat">
      <span className="topology-stat__label">{label}</span>
      <span className={`topology-stat__value topology-stat__value--${tone}`}>{value}</span>
    </div>
  );
}
