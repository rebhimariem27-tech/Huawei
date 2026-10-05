"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getDeviceStatus, getHealth, getTopology } from "@/lib/api";
import type { DeviceStatus, HealthResponse, TopologyDevice  } from "@/lib/types";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const REFRESH_INTERVAL_MS = 15000;
const PERFORMANCE_WINDOW_MS = 10 * 60 * 1000;
const PERFORMANCE_MAX_POINTS = 40;

type NodeState = "online" | "warning" | "offline";

type PerformanceSample = {
  timestamp: string;
  cpu: number | null;
  memory: number | null;
};

interface DeviceInfo {
  id: string;
  label: string;
  role: string;
  ip: string;
  protocol: string;
  os: string;
  managementInterface: string;
}



function formatTime(date: Date): string {
  return date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

type LiveState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "loaded"; data: DeviceStatus };

export default function EquipmentsPanel({ initialHealth }: { initialHealth: HealthResponse }) {
  const [health, setHealth] = useState<HealthResponse>(initialHealth);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [liveData, setLiveData] = useState<Record<string, LiveState>>({});
  const [performanceHistory, setPerformanceHistory] = useState<Record<string, PerformanceSample[]>>({});
  const [devicesRaw, setDevicesRaw] = useState<TopologyDevice[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(true);

useEffect(() => {
  getTopology()
    .then((snapshot) => setDevicesRaw(snapshot.devices))
    .catch(() => setDevicesRaw([]))
    .finally(() => setDevicesLoading(false));
}, []);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const fresh = await getHealth();
      setHealth(fresh);
      setLastRefresh(new Date());
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const interval = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  const devices = useMemo(() => {
  return devicesRaw.map((device) => {
    const label = device.name.toUpperCase();
    const reachable = health.device_connectivity?.[label];
    const state: NodeState = reachable === true ? "online" : reachable === false ? "offline" : "warning";
    return {
      id: label.toLowerCase(),
      label,
      role: device.description || `${device.type} ${label}`,
      ip: device.mgmt_ip ?? "—",
      protocol: (device.protocol ?? "ssh").toUpperCase(),
      os: "Huawei VRP",
      managementInterface: device.mgmt_interface ?? "—",
      state,
    };
  });
}, [devicesRaw, health.device_connectivity]);
  const onlineCount = devices.filter((device) => device.state === "online").length;

  const loadLiveData = useCallback(async (label: string, options?: { silent?: boolean }) => {
    const silent = options?.silent ?? false;
    if (!silent) {
      setLiveData((prev) => ({ ...prev, [label]: { status: "loading" } }));
    }
    try {
      const data = await getDeviceStatus(label);
      setLiveData((prev) => ({ ...prev, [label]: { status: "loaded", data } }));
      const backendHistory =
        data.performance_history?.map((sample) => ({
          timestamp: sample.timestamp,
          cpu: sample.cpu,
          memory: sample.memory,
        })) ?? [];
      setPerformanceHistory((prev) => ({
        ...prev,
        [label]: backendHistory.length > 0 ? backendHistory : prev[label] ?? [],
      }));
    } catch (err) {
      setLiveData((prev) => ({
        ...prev,
        [label]: {
          status: "error",
          message: err instanceof Error ? err.message : "Erreur de collecte",
        },
      }));
    }
  }, []);

  const toggleExpand = useCallback((device: DeviceInfo) => {
    setExpanded((current) => (current === device.id ? null : device.id));
  }, []);

  useEffect(() => {
    if (!expanded) {
      return;
    }

    const currentDevice = devices.find((device) => device.id === expanded);
    if (!currentDevice) {
      return;
    }

    loadLiveData(currentDevice.label);
    const interval = setInterval(() => {
      loadLiveData(currentDevice.label, { silent: true });
    }, REFRESH_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [expanded, devices, loadLiveData]);

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
            Équipements réseau
          </div>
          <div style={{ color: "var(--text-faint)", fontSize: "0.8rem" }}>
            Lab eNSP - TP Huawei · dernière vérification à {formatTime(lastRefresh)}
          </div>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <span className={`pill ${onlineCount === devices.length ? "pill--green" : "pill--amber"}`}>
            {onlineCount}/{devices.length} en ligne
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

      <div style={{ display: "flex", flexDirection: "column", gap: "0.7rem" }}>
        {devices.map((device) => {
          const isOpen = expanded === device.id;
          const live = liveData[device.label];

          return (
            <div
              key={device.id}
              style={{
                border: "1px solid var(--border)",
                borderLeft: `3px solid ${
                  device.state === "online"
                    ? "var(--accent-live)"
                    : device.state === "offline"
                    ? "var(--accent-critical)"
                    : "var(--accent-warn)"
                }`,
                borderRadius: "var(--radius)",
                background: "var(--surface-raised)",
                overflow: "hidden",
              }}
            >
              <button
                type="button"
                onClick={() => toggleExpand(device)}
                style={{
                  width: "100%",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "0.75rem",
                  padding: "0.9rem 1.1rem",
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                  color: "var(--text)",
                  font: "inherit",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "0.7rem" }}>
                  <span
                    className={`status-dot ${
                      device.state === "online"
                        ? "status-dot--live"
                        : device.state === "offline"
                        ? "status-dot--critical"
                        : "status-dot--warn"
                    }`}
                  />
                  <div>
                    <div style={{ fontWeight: 700, fontSize: "0.95rem" }}>{device.label}</div>
                    <div style={{ color: "var(--text-faint)", fontSize: "0.76rem" }}>{device.role}</div>
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                  <span className="badge">{device.protocol}</span>
                  <span style={{ fontSize: "0.8rem", color: "var(--text-faint)" }}>{isOpen ? "▲" : "▼"}</span>
                </div>
              </button>

              {isOpen && (
                <div style={{ padding: "0 1.1rem 1.1rem" }}>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                      gap: "0.6rem",
                      marginBottom: "1rem",
                    }}
                  >
                    <DetailField label="Adresse IP" value={device.ip} />
                    <DetailField label="Protocole d'accès" value={device.protocol} />
                    <DetailField label="Système" value={device.os} />
                    <DetailField label="Interface de gestion" value={device.managementInterface} />
                    <DetailField
                      label="Statut"
                      value={
                        device.state === "online" ? "En ligne" : device.state === "offline" ? "Injoignable" : "Inconnu"
                      }
                      tone={device.state === "online" ? "live" : device.state === "offline" ? "critical" : "warn"}
                    />
                  </div>

                  <LiveDataSection
                    label={device.label}
                    live={live}
                    performanceHistory={performanceHistory[device.label] ?? []}
                    onRetry={() => loadLiveData(device.label)}
                  />
                </div>
              )}
            </div>
          );
        })}
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
        Les données live (VLANs, interfaces, CPU, routes) sont collectées à la demande via SSH/Telnet
        lorsque tu déplies un équipement — une connexion réelle à eNSP, donc ça peut prendre quelques
        secondes.
      </div>
    </div>
  );
}

function LiveDataSection({
  label,
  live,
  performanceHistory,
  onRetry,
}: {
  label: string;
  live: LiveState | undefined;
  performanceHistory: PerformanceSample[];
  onRetry: () => void;
}) {
  if (!live || live.status === "idle") return null;

  if (live.status === "loading") {
    return (
      <div style={{ color: "var(--text-faint)", fontSize: "0.82rem", padding: "0.5rem 0" }}>
        Collecte en cours sur {label} (SSH/Telnet)...
      </div>
    );
  }

  if (live.status === "error") {
    return (
      <div
        style={{
          border: "1px solid var(--accent-critical)",
          borderRadius: "var(--radius-sm)",
          background: "color-mix(in srgb, var(--accent-critical) 8%, var(--surface))",
          padding: "0.7rem 0.9rem",
          fontSize: "0.8rem",
          color: "var(--accent-critical)",
        }}
      >
        {live.message}
        <button
          type="button"
          onClick={onRetry}
          className="badge"
          style={{ marginLeft: "0.6rem", cursor: "pointer" }}
        >
          Réessayer
        </button>
      </div>
    );
  }

  const { data } = live;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <span className="badge">
          Source : {data.access_mode === "file" ? "fichier exporté" : `live ${data.access_mode}`}
        </span>
        {data.version_info?.vrp_version && <span className="badge">VRP {data.version_info.vrp_version}</span>}
        {data.version_info?.uptime && <span className="badge">Uptime : {data.version_info.uptime}</span>}
        <span className="badge badge--live">Historique 10 min</span>
      </div>

      <PerformanceTrendCard history={performanceHistory} cpuUsage={data.cpu_usage} memoryUsage={data.memory_usage} />

      {data.interfaces.length > 0 && (
        <LiveTable
          title={`Interfaces (${data.interfaces.length})`}
          rows={[
            ...data.interfaces,
          ]
            .sort((a, b) => {
              const aDown = a.state.toLowerCase().includes("down") ? 1 : 0;
              const bDown = b.state.toLowerCase().includes("down") ? 1 : 0;
              return aDown - bDown;
            })
            .slice(0, 10)
            .map((i) => [i.name, i.ip || "—", i.state])}
          headers={["Interface", "IP", "État"]}
          rowTone={(row) => (row[2].toLowerCase().includes("down") ? "critical" : "live")}
        />
      )}

      {data.vlans.length > 0 && (
        <LiveTable title={`VLANs (${data.vlans.length})`} rows={data.vlans.map((v) => [v.id, v.type, v.ports || "—"])} headers={["ID", "Type", "Ports"]} />
      )}

      {data.routes.length > 0 && (
        <LiveTable
          title={`Routes (${data.routes.length})`}
          rows={data.routes.slice(0, 8).map((r) => [r.destination, r.protocol, r.nexthop])}
          headers={["Destination", "Protocole", "Next-hop"]}
        />
      )}

      {data.ospf_peers.length > 0 && (
        <LiveTable
          title={`Voisins OSPF (${data.ospf_peers.length})`}
          rows={data.ospf_peers.map((p) => [p.router_id, p.state, p.interface])}
          headers={["Router ID", "État", "Interface"]}
          rowTone={(row) => (row[1].toLowerCase().includes("full") ? "live" : "warn")}
        />
      )}

      {data.errors.length > 0 && (
        <div style={{ color: "var(--accent-warn)", fontSize: "0.76rem" }}>
          ⚠ {data.errors.length} erreur(s) de parsing : {data.errors.join(" · ")}
        </div>
      )}
    </div>
  );
}

function PerformanceTrendCard({
  history,
  cpuUsage,
  memoryUsage,
}: {
  history: PerformanceSample[];
  cpuUsage: number | null;
  memoryUsage: number | null;
}) {
  const chartData = useMemo(
    () =>
      history.map((sample) => ({
        label: new Date(sample.timestamp).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }),
        cpu: sample.cpu,
        memory: sample.memory,
      })),
    [history]
  );

  if (chartData.length === 0) {
    return null;
  }

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "linear-gradient(180deg, color-mix(in srgb, var(--surface) 92%, transparent), var(--surface-raised))",
        padding: "0.85rem 0.9rem 0.95rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.8rem", flexWrap: "wrap", marginBottom: "0.7rem" }}>
        <div>
          <div style={{ fontSize: "0.78rem", fontWeight: 700, color: "var(--text)" }}>Performance live</div>
          <div style={{ fontSize: "0.72rem", color: "var(--text-faint)" }}>CPU et mémoire sur les 10 dernières minutes</div>
        </div>
        <div style={{ display: "flex", gap: "0.45rem", flexWrap: "wrap" }}>
          <MetricChip label="CPU" value={formatPercent(cpuUsage)} tone={cpuUsage !== null && cpuUsage > 70 ? "warn" : "live"} />
          <MetricChip
            label="Mémoire"
            value={formatPercent(memoryUsage)}
            tone={memoryUsage !== null && memoryUsage > 80 ? "warn" : "live"}
          />
        </div>
      </div>

      <div style={{ height: 160 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.35} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} tick={{ fill: "var(--text-faint)", fontSize: 11 }} />
            <YAxis
              domain={[0, 100]}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--text-faint)", fontSize: 11 }}
              tickFormatter={(value) => `${value}%`}
              width={34}
            />
            <Tooltip
              formatter={(value, name) => [`${value}%`, name === "cpu" ? "CPU" : "Mémoire"]}
              labelFormatter={(label) => `Mesure ${label}`}
              contentStyle={{
                background: "var(--surface-raised)",
                border: "1px solid var(--border)",
                borderRadius: "0.75rem",
                color: "var(--text)",
                boxShadow: "0 16px 32px rgba(0, 0, 0, 0.18)",
              }}
              labelStyle={{ color: "var(--text-dim)" }}
            />
            <Line type="monotone" dataKey="cpu" name="cpu" stroke="var(--accent-live)" strokeWidth={2.4} dot={false} connectNulls />
            <Line
              type="monotone"
              dataKey="memory"
              name="memory"
              stroke="var(--accent-warn)"
              strokeWidth={2.4}
              dot={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function MetricChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "live" | "warn";
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.45rem",
        borderRadius: "999px",
        border: `1px solid ${
          tone === "warn" ? "color-mix(in srgb, var(--accent-warn) 45%, var(--border))" : "color-mix(in srgb, var(--accent-live) 40%, var(--border))"
        }`,
        background:
          tone === "warn"
            ? "color-mix(in srgb, var(--accent-warn) 12%, var(--surface))"
            : "color-mix(in srgb, var(--accent-live) 10%, var(--surface))",
        padding: "0.35rem 0.65rem",
      }}
    >
      <span style={{ fontSize: "0.66rem", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-faint)" }}>{label}</span>
      <span style={{ fontSize: "0.84rem", fontWeight: 700, color: "var(--text)" }}>{value}</span>
    </div>
  );
}

function formatPercent(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(1)}%`;
}

function LiveTable({
  title,
  headers,
  rows,
  rowTone,
}: {
  title: string;
  headers: string[];
  rows: string[][];
  rowTone?: (row: string[]) => "live" | "critical" | "warn" | undefined;
}) {
  return (
    <div>
      <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "var(--text-dim)", marginBottom: "0.4rem" }}>
        {title}
      </div>
      <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${headers.length}, 1fr)`,
            background: "var(--surface)",
            padding: "0.4rem 0.7rem",
            fontSize: "0.7rem",
            color: "var(--text-faint)",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {headers.map((h) => (
            <span key={h}>{h}</span>
          ))}
        </div>
        {rows.map((row, i) => {
          const tone = rowTone?.(row);
          const color = tone === "critical" ? "var(--accent-critical)" : tone === "warn" ? "var(--accent-warn)" : "var(--text)";
          return (
            <div
              key={i}
              style={{
                display: "grid",
                gridTemplateColumns: `repeat(${headers.length}, 1fr)`,
                padding: "0.4rem 0.7rem",
                fontSize: "0.78rem",
                fontFamily: "var(--font-mono)",
                borderTop: "1px solid var(--border)",
                color,
              }}
            >
              {row.map((cell, j) => (
                <span key={j} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {cell}
                </span>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DetailField({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "live" | "warn" | "critical" | "neutral";
}) {
  const color =
    tone === "live"
      ? "var(--accent-live)"
      : tone === "warn"
      ? "var(--accent-warn)"
      : tone === "critical"
      ? "var(--accent-critical)"
      : "var(--text)";

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--surface)",
        padding: "0.55rem 0.7rem",
      }}
    >
      <div style={{ color: "var(--text-faint)", fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {label}
      </div>
      <div style={{ marginTop: "0.25rem", fontSize: "0.84rem", fontWeight: 600, color, fontFamily: "var(--font-mono)" }}>{value}</div>
    </div>
  );
}
