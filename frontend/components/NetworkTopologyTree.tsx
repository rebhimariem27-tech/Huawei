"use client";

import { Router, Network, Laptop } from "lucide-react";

export type TopoNodeState = "online" | "warning" | "offline" | "unknown";

export interface TopoNode {
  id: string;
  label: string;
  role?: string;
  ip?: string;
  protocol?: string;
  kind?: "router" | "switch" | "host";
  state: TopoNodeState;
  children?: TopoNode[];
}

const STATE_COLOR: Record<TopoNodeState, string> = {
  online: "var(--accent-live)",
  warning: "var(--accent-warn)",
  offline: "var(--accent-critical)",
  unknown: "var(--text-faint)",
};

const STATE_LABEL: Record<TopoNodeState, string> = {
  online: "Up",
  warning: "Warning",
  offline: "Down",
  unknown: "Unknown",
};

const KIND_ICON = { router: Router, switch: Network, host: Laptop };

function NodeBox({ node }: { node: TopoNode }) {
  const Icon = KIND_ICON[node.kind ?? "switch"];
  const color = STATE_COLOR[node.state];

  return (
    <div className="topo-node">
      <div className="topo-node__box" style={{ borderColor: color }}>
        <Icon size={22} strokeWidth={1.6} color="var(--text)" />
        <span
          className="topo-node__dot"
          style={{
            background: color,
            boxShadow: `0 0 0 3px color-mix(in srgb, ${color} 22%, transparent)`,
          }}
        />
      </div>
      <div className="topo-node__label">{node.label}</div>
      {node.role && <div className="topo-node__role">{node.role}</div>}
      {(node.ip || node.protocol) && (
        <div className="topo-node__meta">
          {node.ip && <span>{node.ip}</span>}
          {node.protocol && <span>{node.protocol}</span>}
        </div>
      )}
    </div>
  );
}

function TreeLevel({ nodes }: { nodes: TopoNode[] }) {
  return (
    <ul className="topo-tree">
      {nodes.map((node) => (
        <li key={node.id} style={{ ["--topo-link-color" as string]: STATE_COLOR[node.state] }}>
          <NodeBox node={node} />
          {node.children && node.children.length > 0 && <TreeLevel nodes={node.children} />}
        </li>
      ))}
    </ul>
  );
}

/**
 * Diagramme de topologie en arbre (style org-chart), inspiré du rendu
 * "Topologie Réseau Live" attendu par le client.
 *
 * EXTENSIBILITÉ : passe simplement un `root.children` plus profond quand
 * topology.yaml grandit (ex: switches d'accès sous S1, PC sous chaque
 * switch d'accès) — le rendu s'adapte automatiquement, aucune valeur
 * n'est codée en dur ici.
 */
interface NetworkTopologyTreeProps {
  root: TopoNode;
  compact?: boolean;
  showLegend?: boolean;
}

export default function NetworkTopologyTree({
  root,
  compact = false,
  showLegend = true,
}: NetworkTopologyTreeProps) {
  return (
    <div className={`topo-tree-wrap${compact ? " topo-tree-wrap--compact" : ""}`}>
      <ul className="topo-tree topo-tree--root">
        <li>
          <NodeBox node={root} />
          {root.children && root.children.length > 0 && <TreeLevel nodes={root.children} />}
        </li>
      </ul>

      {showLegend && (
        <div className="topo-legend">
          {(["online", "offline", "warning", "unknown"] as TopoNodeState[]).map((s) => (
            <span key={s} className="topo-legend__item">
              <span className="topo-legend__dot" style={{ background: STATE_COLOR[s] }} />
              {STATE_LABEL[s]}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}