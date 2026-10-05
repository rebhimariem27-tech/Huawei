"use client";

import { useState } from "react";
import ChatInterface from "@/components/ChatInterface";
import ReportsPanel from "@/components/ReportsPanel";
import DocumentsPanel from "@/components/DocumentsPanel";
import TopologyFullPanel from "@/components/TopologyFullPanel";
import HistoryFullPanel from "@/components/HistoryFullPanel";
import EquipmentsPanel from "@/components/EquipmentsPanel";
import DashboardOverviewPanel from "@/components/DashboardOverviewPanel";
import AuditsPanel from "@/components/AuditsPanel";
import DeploymentAssistantPanel from "@/components/DeploymentAssistantPanel";
import RemediationPanel from "@/components/RemediationPanel";
import CommandAssistant from "@/components/CommandAssistant";   
import HistoryPanel from "@/components/HistoryPanel";
import TopologyLivePanel from "@/components/TopologyLivePanel";
import UploadZone from "@/components/UploadZone";
import { ThemeToggle } from "@/components/ThemeToggle";
import type { FileEntry, HealthResponse } from "@/lib/types";
import TerminalPanel from "@/components/TerminalPanel";
const NAV_ITEMS = [
  { key: "chat", label: "Chat Assistant", icon: "chat" },
  { key: "dashboard", label: "Tableau de bord", icon: "dashboard" },
  { key: "audits", label: "Audits", icon: "shield" },
  { key: "documents", label: "Documents", icon: "doc" },
  { key: "topologie", label: "Topologie Live", icon: "topology" },
  { key: "equipements", label: "Équipements", icon: "devices" },
  { key: "rapports", label: "Rapports", icon: "reports" },
  { key: "deploiement", label: "Déploiement", icon: "repair" },
  { key: "remediation", label: "Remédiation", icon: "repair" },
  { key: "commandes", label: "Assistant Commandes", icon: "repair" },
  { key: "terminal", label: "Terminal CLI", icon: "repair" },
  { key: "historique", label: "Historique", icon: "history" },
  { key: "parametres", label: "Paramètres", icon: "settings" },
] as const;

type ViewKey = (typeof NAV_ITEMS)[number]["key"];

const AGENT_ITEMS = [
  { label: "Architecture", state: "3 agents actifs", tone: "violet" },
  { label: "Ingestion", state: "Qdrant synchronisé", tone: "green" },
  { label: "Validation", state: "Règles de conformité", tone: "amber" },
] as const;

const RAG_SOURCES = [
  { name: "Campus_Network_Guide.pdf", page: "Page 45, 47, 48" },
  { name: "Huawei_Switch_Config_Guide.pdf", page: "Page 112, 113" },
  { name: "VLAN_Configuration_Best_Practices.pdf", page: "Page 23" },
  { name: "Network_Topology_Diagrams.pdf", page: "Page 5 (Schéma)" },
  { name: "Interface_Status_Commands.txt", page: "Commandes CLI" },
] as const;

interface DashboardShellProps {
  health: HealthResponse;
  files: FileEntry[];
}

export default function DashboardShell({ health, files }: DashboardShellProps) {
  const [active, setActive] = useState<ViewKey>("chat");

  const total = health.devices_total ?? 0;
  const reachable = health.devices_reachable ?? 0;
  const ratio = total > 0 ? reachable / total : 0;
  const confidenceValue = health.status === "ok" ? 92 : 74;

  return (
    <main className="dashboard-shell">
      <aside className="sidebar card glass-panel">
        <div className="brand-lockup">
          <div className="brand-mark">
            <HuaweiGlyph />
          </div>
          <div>
            <div className="brand-title">AI Network Auditor</div>
            <div className="brand-subtitle">RAG Multimodal</div>
          </div>
        </div>

        <nav className="nav-stack" aria-label="Navigation principale">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`nav-item ${active === item.key ? "nav-item--active" : ""}`}
              onClick={() => setActive(item.key)}
            >
              <NavIcon kind={item.icon} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        <section className="system-card">
          <div className="section-label">Statut du système</div>
          <div className="system-list">
            <StatusRow
              label="Agents"
              value={health.status === "ok" ? "En ligne" : "Dégradé"}
              tone={health.status === "ok" ? "live" : "warn"}
            />
            <StatusRow
              label="Connexion eNSP"
              value={health.qdrant_connected ? "Connecté" : "Déconnecté"}
              tone={health.qdrant_connected ? "live" : "warn"}
            />
            <StatusRow
              label="Base de connaissances"
              value={health.ingestion_available ? "À jour" : "En attente"}
              tone={health.ingestion_available ? "live" : "warn"}
            />
            <StatusRow
              label="API LLM"
              value={health.groq_configured ? "Opérationnel" : "Hors ligne"}
              tone={health.groq_configured ? "live" : "warn"}
            />
          </div>
        </section>

        <section className="agents-card card-soft">
          <div className="agents-header">
            <div>
              <div className="section-label">Architecture Multi-Agents</div>
              <div className="agents-count">{AGENT_ITEMS.length} agents actifs</div>
            </div>
            <button type="button" className="mini-link">
              Voir les agents
            </button>
          </div>
          <div className="agent-stack">
            {AGENT_ITEMS.map((agent) => (
              <div key={agent.label} className={`agent-chip agent-chip--${agent.tone}`}>
                <div className="agent-dot" />
                <div>
                  <div className="agent-name">{agent.label}</div>
                  <div className="agent-state">{agent.state}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </aside>

      <section className="workspace">
        <header className="topbar card glass-panel">
          <div className="topbar-brand">
            <HuaweiGlyph compact />
            <div>
              <div className="topbar-title">AI Network Auditor</div>
              <div className="topbar-subtitle">RAG Multimodal</div>
            </div>
          </div>

          <div className="topbar-right">
            <button type="button" className="icon-chip" aria-label="Notifications">
              <BellIcon />
            </button>
            <ThemeToggle />
            <div className="profile-chip">
              <div className="profile-avatar">M</div>
              <div className="profile-text">
                <div className="profile-name">Mariem REBHI</div>
                <div className="profile-role">Ingénieur Réseau</div>
              </div>
              <ChevronIcon />
            </div>
          </div>
        </header>

        <div className="content-grid">
          <section className="center-column">
            {active === "chat" && (
              <>
                <div className="hero card glass-panel">
                  <div className="hero-title-row">
                    <SparkIcon />
                    <div>
                      <h1>Assistant IA Réseau</h1>
                      <p>
                        Posez vos questions sur votre réseau. L’IA s’appuie sur vos documents et la configuration live.
                      </p>
                    </div>
                  </div>

                  <div className="hero-cta-row">
                    <BadgeStat
                      label="Conformité"
                      value={total > 0 ? `${Math.round(ratio * 100)}%` : "0%"}
                      tone={ratio >= 0.95 ? "live" : ratio > 0 ? "warn" : "muted"}
                    />
                    <BadgeStat
                      label="Équipements"
                      value={total > 0 ? `${reachable}/${total}` : "0/0"}
                      tone={ratio >= 0.95 ? "live" : "warn"}
                    />
                    <BadgeStat label="Sources" value={`${files.length + 1}`} tone="muted" />
                  </div>
                </div>

                <div className="chat-frame card glass-panel">
                  <ChatInterface />
                </div>
              </>
            )}

            {active === "rapports" && <ReportsPanel />}
            {active === "documents" && <DocumentsPanel files={files} />}
            {active === "topologie" && <TopologyFullPanel initialHealth={health} />}
            {active === "historique" && <HistoryFullPanel />}
            {active === "equipements" && <EquipmentsPanel initialHealth={health} />}
            {active === "dashboard" && (
              <DashboardOverviewPanel health={health} files={files} onNavigate={setActive} />
            )}
            {active === "audits" && <AuditsPanel />}
            {active === "deploiement" && <DeploymentAssistantPanel />}
            {active === "remediation" && <RemediationPanel />}
            {active === "commandes" && <CommandAssistant />}
            {active === "terminal" && <TerminalPanel />}
            {active !== "chat" &&
              active !== "rapports" &&
              active !== "documents" &&
              active !== "topologie" &&
              active !== "historique" &&
              active !== "equipements" &&
              active !== "dashboard" &&
              active !== "audits" &&
              active !== "deploiement" &&
              active !== "remediation" &&
              active !== "commandes" &&   /* ← AJOUT */
              active !== "terminal" &&   /* ← AJOUT */
 (
              <div className="chat-frame card glass-panel" style={{ padding: "2rem", color: "var(--text-dim)" }}>
                Cette section n’est pas encore implémentée.
              </div>
            )}
          </section>

          <aside className="right-rail">
            <section className="panel card glass-panel">
              <div className="panel-title">Upload de Documents</div>
              <UploadZone />
              <div className="document-pills">
                {files.slice(0, 3).map((file) => (
                  <IndexedDoc key={file.file_id} file={file} />
                ))}
              </div>
              <button type="button" className="panel-link">
                Voir tous les documents
              </button>
            </section>

            <section className="panel card glass-panel">
              <div className="panel-header-row">
                <div className="panel-title">Contexte RAG Actuel</div>
                <span className="pill pill--amber">Hybrid Search</span>
              </div>
              <div className="sources-list">
                {RAG_SOURCES.map((source) => (
                  <SourceRow key={source.name} source={source.name} page={source.page} />
                ))}
              </div>
              <div className="confidence-meter">
                <div className="confidence-label-row">
                  <span>Score de confiance des sources</span>
                  <span>{confidenceValue}%</span>
                </div>
                <div className="meter-track">
                  <div className="meter-fill" style={{ width: `${confidenceValue}%` }} />
                </div>
              </div>
            </section>

            <section className="panel card glass-panel connection-panel">
              <div className="panel-header-row">
                <div className="panel-title">Connexion eNSP</div>
                <span className={`pill ${health.qdrant_connected ? "pill--green" : "pill--neutral"}`}>
                  {health.qdrant_connected ? "Connecté" : "Offline"}
                </span>
              </div>
              <div className="connection-summary">
                <div className="connection-count">
                  {total > 0 ? `${reachable} équipements connectés` : "Aucun équipement détecté"}
                </div>
                <a href="#topologie-live" className="panel-action panel-action--anchor">
                  Voir la topologie
                </a>
              </div>
            </section>

            <TopologyLivePanel health={health} />

            <HistoryPanel />
          </aside>
        </div>
      </section>
    </main>
  );
}

function HuaweiGlyph({ compact = false }: { compact?: boolean }) {
  return (
    <svg width={compact ? 20 : 24} height={compact ? 20 : 24} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 8.5C6.3 5.6 9.1 4 12 4c2.9 0 5.7 1.6 8 4.5" stroke="var(--brand-red)" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M4 15.5C6.3 18.4 9.1 20 12 20c2.9 0 5.7-1.6 8-4.5" stroke="var(--brand-red)" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="12" r="2.2" fill="var(--brand-red)" />
      <circle cx="5.8" cy="8.5" r="1.2" fill="var(--brand-red)" />
      <circle cx="18.2" cy="8.5" r="1.2" fill="var(--brand-red)" />
      <circle cx="5.8" cy="15.5" r="1.2" fill="var(--brand-red)" />
      <circle cx="18.2" cy="15.5" r="1.2" fill="var(--brand-red)" />
    </svg>
  );
}

function BellIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path d="M9 2.5a3 3 0 0 0-3 3v1.1c0 .6-.2 1.1-.6 1.6L4 9.3a1.4 1.4 0 0 0 1.1 2.2h7.8A1.4 1.4 0 0 0 14 9.3l-1.4-1.1c-.4-.5-.6-1-.6-1.6V5.5a3 3 0 0 0-3-3Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M7.5 13.8c.3.8 1 1.2 1.5 1.2s1.2-.4 1.5-1.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function ChevronIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M3.5 5.25 7 8.75l3.5-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
      <path d="M14 3.5l2.1 5.5L22 11.1l-5.9 2.1L14 18.7l-2.1-5.5L6 11.1l5.9-2.1L14 3.5Z" stroke="var(--brand-red)" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

function NavIcon({ kind }: { kind: string }) {
  const common = {
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };

  switch (kind) {
    case "dashboard":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M3 3.5h5.5V8H3zM9.5 3.5H15V7H9.5zM3 9.5h5.5V15H3zM9.5 8h5.5v7H9.5z" />
        </svg>
      );
    case "shield":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M9 2.4 14.2 4.1v3.7c0 3.4-2 6.4-5.2 7.8-3.2-1.4-5.2-4.4-5.2-7.8V4.1z" />
        </svg>
      );
    case "doc":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M5 2.5h5.3L13 5.2v10.3H5zM10.2 2.5V5.3H13" />
        </svg>
      );
    case "topology":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M6 5.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0ZM15 5.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0ZM9 12.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Z" />
          <path {...common} d="M5 6.3 8.2 10M12 6.3 9.8 10M5 6.5h7" />
        </svg>
      );
    case "devices":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M3.5 4.5h11v6h-11zM5.2 13.5h7.6" />
        </svg>
      );
    case "reports":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M5.2 2.5h6l2.8 2.8v10.2h-8.8zM11.2 2.5v3h3" />
        </svg>
      );
    case "repair":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="m10.6 3.2 4.2 4.2-7.1 7.1H3.5v-4.2z" />
        </svg>
      );
    case "history":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M9 4.1v5l3 2" />
          <path {...common} d="M4.1 6.2A6 6 0 1 1 3.5 9H2" />
        </svg>
      );
    case "settings":
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M7.1 3.1h3.8l.4 1.9 1.7 1 1.8-.5 1.9 3.2-1.5 1.2v2l1.5 1.2-1.9 3.2-1.8-.5-1.7 1-.4 1.9H7.1l-.4-1.9-1.7-1-1.8.5-1.9-3.2 1.5-1.2v-2L1.3 8.7 3.2 5.5 5 6l1.7-1z" />
          <circle cx="9" cy="9" r="2.2" {...common} />
        </svg>
      );
    default:
      return (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path {...common} d="M3 4h12v10H3z" />
        </svg>
      );
  }
}

function StatusRow({ label, value, tone }: { label: string; value: string; tone: "live" | "warn" }) {
  return (
    <div className="status-row">
      <span className={`status-bullet status-bullet--${tone}`} />
      <span className="status-row-label">{label}</span>
      <span className={`status-row-value status-row-value--${tone}`}>{value}</span>
    </div>
  );
}

function BadgeStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "live" | "warn" | "muted";
}) {
  return (
    <div className={`stat-pill stat-pill--${tone}`}>
      <span className="stat-pill-label">{label}</span>
      <span className="stat-pill-value">{value}</span>
    </div>
  );
}

function IndexedDoc({ file }: { file: FileEntry }) {
  return (
    <div className="indexed-doc">
      <span className="indexed-doc-icon" />
      <div className="indexed-doc-body">
        <div className="indexed-doc-name">{file.filename}</div>
        <div className="indexed-doc-meta">
          {file.chunks_indexed ?? 0} chunks · {new Date(file.ingested_at).toLocaleDateString("fr-FR")}
        </div>
      </div>
    </div>
  );
}

function SourceRow({ source, page }: { source: string; page: string }) {
  return (
    <div className="source-row">
      <div className="source-file">{source}</div>
      <div className="source-page">{page}</div>
    </div>
  );
}