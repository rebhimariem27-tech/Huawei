// frontend/lib/types.ts
// -----------------------------------------------------------------------------
// Types miroir des modèles Pydantic de api/schemas.py.
// Garde ce fichier synchronisé si tu changes un schéma côté API.
// -----------------------------------------------------------------------------

export type Confidence = "high" | "medium" | "low";

export interface QueryResponse {
  final_answer: string;
  intent: string;
  strategy: string;
  confidence: Confidence;
  confidence_score: number;
  rag_chunks_count: number;
  devices_consulted: string[];   // ← ajoute cette ligne si absente
  alerts: string[];
  discrepancies: string[];
  recommendations: string[];
  sources_used: Record<string, unknown>[];
  validation_checks: Record<string, unknown>;
  report_filename: string | null;   // ← ajouté
}

export interface IngestResponse {
  filename: string;
  file_id: string;
  status: "success" | "error";
  chunks_indexed: number | null;
  message: string;
}

export interface FileEntry {
  file_id: string;
  filename: string;
  ingested_at: string;
  chunks_indexed: number | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  qdrant_connected: boolean;
  groq_configured: boolean;
  ingestion_available: boolean;
  devices_reachable: number | null;
  devices_total: number | null;
  device_connectivity?: Record<string, boolean> | null;
  timestamp: string;
}
export interface ReportEntry {
  filename: string;
  size_bytes: number;
  created_at: string;
}
export interface DeviceInterfaceInfo {
  name: string;
  phy_state: string;
  pro_state: string;
  state: string;
  ip: string;
}

export interface DeviceVlanInfo {
  id: string;
  type: string;
  ports: string;
}

export interface DeviceRouteInfo {
  destination: string;
  protocol: string;
  nexthop: string;
  interface: string;
}

export interface DeviceOspfPeerInfo {
  router_id: string;
  state: string;
  address: string;
  interface: string;
}

export interface DeviceStatus {
  device_name: string;
  device_type: string;
  mgmt_ip: string;
  access_mode: string;
  collected_at: string;
  status_error?: string | null;
  version_info: {
    vrp_version?: string;
    hardware?: string;
    uptime?: string;
    reboot_cause?: string;
    compile_date?: string;
    patch_version?: string;
  };
  interfaces: DeviceInterfaceInfo[];
  vlans: DeviceVlanInfo[];
  routes: DeviceRouteInfo[];
  ospf_peers: DeviceOspfPeerInfo[];
  cpu_usage: number | null;
  memory_usage: number | null;
  performance_history?: {
    timestamp: string;
    cpu: number | null;
    memory: number | null;
  }[];
  errors: string[];
  is_complete: boolean;
}

export interface DesignUpdateResponse {
  intent: string;
  device_name: string;
  device_type: string;
  management_ip: string;
  parent_device: string;
  parent_interface?: string;
  link_mode?: string;
  topology_yaml_block: string;
  remediation_commands: string[];
  deployment_notes: string[];
  topology_file: string;
  applied: boolean;
  message: string;
  requires_confirmation?: boolean;
}

export interface TopologyDevice {
  name: string;
  type: string;
  protocol?: string;
  mgmt_ip?: string;
  mgmt_interface?: string;
  description?: string;
  parent_device?: string;
  parent_interface?: string;
  link_mode?: string;
  port?: number;
  username?: string;
  password?: string;
  timeout?: number;
}

export interface TopologySnapshot {
  network_name: string;
  management_subnet?: string;
  devices: TopologyDevice[];
}
export interface CommandProposal {
  device_name: string;
  commands: string[];
  explanation: string;
  risk_level: "low" | "medium" | "high";
}

export interface CommandApplyResult {
  device_name: string;
  command: string;
  success: boolean;
  output: string;
}
export interface TerminalCommandResult {
  command: string;
  output: string;
}

export interface TerminalResponse {
  device_name: string;
  success: boolean;
  results: TerminalCommandResult[];
  error: string | null;
}