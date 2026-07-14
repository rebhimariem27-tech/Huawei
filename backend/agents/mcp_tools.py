"""
agents/mcp_tools.py
====================
Outils MCP pour l'accès aux équipements Huawei eNSP.

DOUBLE MODE (accès) :
    SSH  : connexion Netmiko live → commandes VRP → données temps réel
    FILE : lecture fichiers .txt exportés → fallback si eNSP éteint
    AUTO : essaie la connexion live (SSH ou Telnet selon l'équipement), fallback FILE

PROTOCOLE (par équipement, via topology.yaml → champ `protocol`) :
    ssh    : Netmiko device_type "huawei"        (ex: S1)
    telnet : Netmiko device_type "huawei_telnet" (ex: R1)

TOPOLOGIE EXTERNE :
    La topologie est définie dans topology.yaml — jamais dans ce fichier.
    Pour ajouter un équipement : éditer topology.yaml uniquement.

    topology.yaml
        ↓
    TopologyLoader.load()
        ↓
    DEVICE_REGISTRY (dict dynamique)
        ↓
    MCPTools.get_device_info("S1")
"""

from __future__ import annotations

import os
import re
import json
import time
import socket
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from datetime import datetime


# ---------------------------------------------------------------------------
# 1. TYPES
# ---------------------------------------------------------------------------

class DeviceType(str, Enum):
    SWITCH = "switch"
    ROUTER = "router"

class AccessMode(str, Enum):
    SSH  = "ssh"    # conservé pour compat CLI (--ssh) => signifie "live" désormais
    FILE = "file"
    AUTO = "auto"


@dataclass
class DeviceInfo:
    """Définition d'un équipement — chargée depuis topology.yaml."""
    name:           str
    device_type:    DeviceType
    mgmt_ip:        str
    mgmt_interface: str
    username:       str = "admin"
    password:       str = "huawei"
    ssh_port:       int = 22
    ssh_timeout:    int = 30
    protocol:       str = "ssh"   # "ssh" ou "telnet" — défini dans topology.yaml
    description:    str = ""


# Commandes collectées par type d'équipement
SWITCH_COMMANDS = [
    "display version",
    "display current-configuration",
    "display vlan",
    "display interface brief",
    "display ip routing-table",
    "display cpu-usage",
    "display stp brief",
]

ROUTER_COMMANDS = [
    "display version",
    "display current-configuration",
    "display ip routing-table",
    "display interface brief",
    "display ospf peer",
    "display ip interface brief",
    "display cpu-usage",
]


# ---------------------------------------------------------------------------
# 2. CHARGEMENT DE LA TOPOLOGIE DEPUIS YAML
# ---------------------------------------------------------------------------

class TopologyLoader:
    """
    Charge la topologie depuis topology.yaml.

    FORMAT topology.yaml :
        devices:
          - name: S1
            type: switch
            mgmt_ip: 192.168.56.11
            mgmt_interface: Vlanif1
            protocol: ssh        # optionnel, défaut "ssh"

          - name: R1
            type: router
            mgmt_ip: 192.168.56.10
            mgmt_interface: GigabitEthernet0/0/0
            protocol: telnet     # R1 utilise Telnet
            port: 23
    """

    def __init__(self, yaml_path: str = "topology.yaml"):
        self.yaml_path = Path(yaml_path)

    def load(self) -> dict[str, DeviceInfo]:
        yaml_file = self._find_yaml()

        if yaml_file is None:
            print("[TopologyLoader] ⚠ topology.yaml introuvable — registre vide")
            print("  Créez topology.yaml dans le dossier agents/")
            return {}

        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML manquant.\n"
                "  → pip install pyyaml"
            )

        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "devices" not in data:
            print(f"[TopologyLoader] ⚠ Aucun équipement dans {yaml_file}")
            return {}

        # Paramètres par défaut (surchargeables par équipement)
        ssh_defaults    = data.get("ssh_defaults", {})
        default_user    = ssh_defaults.get("username", "admin")
        default_pass    = ssh_defaults.get("password", "huawei")
        default_port    = ssh_defaults.get("port", 22)
        default_timeout = ssh_defaults.get("timeout", 30)
        default_proto   = ssh_defaults.get("protocol", "ssh")

        registry = {}
        for dev in data["devices"]:
            name     = dev["name"].upper()
            protocol = dev.get("protocol", default_proto).lower()

            # Port par défaut cohérent avec le protocole si non précisé explicitement
            if "port" in dev:
                port = dev["port"]
            else:
                port = 23 if protocol == "telnet" else default_port

            registry[name] = DeviceInfo(
                name=name,
                device_type=DeviceType(dev.get("type", "switch").lower()),
                mgmt_ip=dev["mgmt_ip"],
                mgmt_interface=dev.get("mgmt_interface", "Vlanif1"),
                username=dev.get("username", default_user),
                password=dev.get("password", default_pass),
                ssh_port=port,
                ssh_timeout=dev.get("timeout", default_timeout),
                protocol=protocol,
                description=dev.get("description", ""),
            )

        network = data.get("network_name", "Unknown")
        print(f"[TopologyLoader] Topologie chargée : '{network}'")
        print(f"  → {len(registry)} équipement(s) : "
              f"{[(k, v.protocol) for k, v in registry.items()]}")
        return registry

    def _find_yaml(self) -> Optional[Path]:
        candidates = [
            self.yaml_path,
            Path("topology.yaml"),
            Path(__file__).parent / "topology.yaml",
        ]
        for path in candidates:
            if path.exists():
                return path
        return None


# ---------------------------------------------------------------------------
# 3. DONNÉES COLLECTÉES
# ---------------------------------------------------------------------------

@dataclass
class DeviceData:
    """Données complètes collectées sur un équipement."""
    device_name:  str
    device_type:  str
    mgmt_ip:      str
    access_mode:  str   # "ssh" | "telnet" | "file"
    collected_at: str
    raw_outputs:  dict[str, str]  = field(default_factory=dict)
    version_info: dict            = field(default_factory=dict)
    interfaces:   list[dict]      = field(default_factory=list)
    vlans:        list[dict]      = field(default_factory=list)
    routes:       list[dict]      = field(default_factory=list)
    ospf_peers:   list[dict]      = field(default_factory=list)
    cpu_usage:    Optional[float] = None
    errors:       list[str]       = field(default_factory=list)
    is_complete:  bool            = True

    def to_text_summary(self) -> str:
        source_label = {
            "ssh":    "live SSH",
            "telnet": "live Telnet",
            "file":   "fichier exporté",
        }.get(self.access_mode, self.access_mode)

        lines = [
            f"=== ÉTAT ÉQUIPEMENT {self.device_name} ===",
            f"Type     : {self.device_type}",
            f"IP mgmt  : {self.mgmt_ip}",
            f"Source   : {self.access_mode.upper()} ({source_label})",
            f"Collecté : {self.collected_at[:19]}",
        ]

        if self.version_info:
            vrp    = self.version_info.get("vrp_version", "N/A")
            hw     = self.version_info.get("hardware", "N/A")
            uptime = self.version_info.get("uptime", "N/A")
            cause  = self.version_info.get("reboot_cause", "N/A")
            lines += [f"VRP      : {vrp}", f"Matériel : {hw}", f"Uptime   : {uptime}", f"Dernier Reboot : {cause}"]

        if self.cpu_usage is not None:
            icon = "⚠" if self.cpu_usage > 70 else "✓"
            lines.append(f"CPU      : {self.cpu_usage:.1f}% {icon}")

        if self.interfaces:
            lines.append(f"\n--- Interfaces ({len(self.interfaces)}) ---")
            for iface in self.interfaces[:8]:
                state = iface.get("state", "?")
                icon  = "✓" if "up" in state.lower() else "✗"
                lines.append(
                    f"  {icon} {iface.get('name','?'):22s} "
                    f"{iface.get('ip',''):16s} {state}"
                )

        if self.vlans:
            lines.append(f"\n--- VLANs ({len(self.vlans)}) ---")
            for v in self.vlans[:6]:
                lines.append(
                    f"  VLAN {v.get('id','?'):5s} {v.get('ports','')[:40]}"
                )

        if self.routes:
            lines.append(f"\n--- Routes ({len(self.routes)}) ---")
            for r in self.routes[:6]:
                lines.append(
                    f"  {r.get('destination','?'):20s} "
                    f"via {r.get('nexthop','?'):15s} "
                    f"[{r.get('protocol','?')}]"
                )

        if self.ospf_peers:
            lines.append(f"\n--- Voisins OSPF ({len(self.ospf_peers)}) ---")
            for p in self.ospf_peers:
                state = p.get("state", "?")
                icon  = "✓" if "full" in state.lower() else "⚠"
                lines.append(
                    f"  {icon} {p.get('router_id','?'):15s} "
                    f"via {p.get('interface','?'):15s} {state}"
                )

        if self.errors:
            lines.append(f"\n⚠ {len(self.errors)} erreur(s) de collecte")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "device_name":  self.device_name,
            "device_type":  self.device_type,
            "mgmt_ip":      self.mgmt_ip,
            "access_mode":  self.access_mode,
            "collected_at": self.collected_at,
            "version_info": self.version_info,
            "interfaces":   self.interfaces,
            "vlans":        self.vlans,
            "routes":       self.routes,
            "ospf_peers":   self.ospf_peers,
            "cpu_usage":    self.cpu_usage,
            "errors":       self.errors,
            "is_complete":  self.is_complete,
        }


# ---------------------------------------------------------------------------
# 4. COLLECTE LIVE (SSH ou TELNET selon device.protocol)
# ---------------------------------------------------------------------------

class RemoteCollector:
    """
    Collecte les données d'un équipement via Netmiko, en SSH ou Telnet
    selon le champ `protocol` défini sur le DeviceInfo (topology.yaml).

    - S1 (switch)  → protocol: ssh    → netmiko device_type "huawei"
    - R1 (router)  → protocol: telnet → netmiko device_type "huawei_telnet"
      (R1 nécessite Telnet car son image VRP AR dans eNSP ne complète pas
       correctement le handshake d'authentification SSH — cf. diagnostic)
    """

    BASE_CONFIG = {
        "global_cmd_verify": False,
        "conn_timeout":      30,
        "global_delay_factor": 2,  # marge supplémentaire, utile pour Telnet/eNSP
    }

    def collect(self, device: DeviceInfo) -> dict[str, str]:
        try:
            from netmiko import ConnectHandler
        except ImportError:
            raise ImportError("pip install netmiko")

        is_telnet = device.protocol == "telnet"
        netmiko_device_type = "huawei_telnet" if is_telnet else "huawei"

        params = {
            **self.BASE_CONFIG,
            "device_type": netmiko_device_type,
            "host":        device.mgmt_ip,
            "username":    device.username,
            "password":    device.password,
            "port":        device.ssh_port,
            "timeout":     device.ssh_timeout,
        }

        commands = (
            SWITCH_COMMANDS if device.device_type == DeviceType.SWITCH
            else ROUTER_COMMANDS
        )

        outputs = {}
        conn    = None
        proto_label = "TELNET" if is_telnet else "SSH"

        try:
            print(f"    [{proto_label}] Connexion {device.name} ({device.mgmt_ip}:{device.ssh_port})...")
            conn = ConnectHandler(**params)

            # Désactiver pagination VRP
            conn.send_command(
                "screen-length 0 temporary",
                expect_string=r"[>\]]",
            )

            for cmd in commands:
                try:
                    print(f"    [{proto_label}] {cmd}...", end=" ", flush=True)
                    out = conn.send_command(
                        cmd,
                        expect_string=r"[>\]]",
                        read_timeout=20,
                    )
                    outputs[cmd] = out.strip()
                    print(f"✓ ({len(out.splitlines())} lignes)")
                    time.sleep(0.3)
                except Exception as e:
                    outputs[cmd] = f"[ERREUR] {e}"
                    print(f"✗")

        except Exception as e:
            raise ConnectionError(
                f"{proto_label} échoué vers {device.name} ({device.mgmt_ip}) : {e}\n"
                f"Vérifiez que eNSP tourne et que {proto_label} est configuré."
            )
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass

        print(f"    [{proto_label}] {device.name} ✓ {len(outputs)} commandes")
        return outputs


# ---------------------------------------------------------------------------
# 5. COLLECTE FICHIER
# ---------------------------------------------------------------------------

class FileCollector:
    """Lit les fichiers .txt exportés depuis les équipements."""

    def __init__(self, exports_dir: str = "device_exports"):
        self.exports_dir = Path(exports_dir)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def collect(self, device: DeviceInfo) -> dict[str, str]:
        name    = device.name.upper()
        outputs = {}

        full_file = self.exports_dir / f"{name}_full.txt"
        if full_file.exists():
            print(f"    [FILE] Lecture {full_file.name}...")
            outputs = self._parse_full_file(full_file.read_text(encoding="utf-8"))
            print(f"    [FILE] {name} ✓ {len(outputs)} sections")
            return outputs

        file_map = {
            "display current-configuration": f"{name}_config.txt",
            "display vlan":                  f"{name}_vlans.txt",
            "display interface brief":       f"{name}_interfaces.txt",
            "display ip routing-table":      f"{name}_routing.txt",
            "display ospf peer":             f"{name}_ospf.txt",
            "display version":               f"{name}_version.txt",
            "display cpu-usage":             f"{name}_cpu.txt",
        }
        for cmd, fname in file_map.items():
            fpath = self.exports_dir / fname
            if fpath.exists():
                outputs[cmd] = fpath.read_text(encoding="utf-8").strip()
                print(f"    [FILE] ✓ {fname}")

        if not outputs:
            simple = self.exports_dir / f"{name}.txt"
            if simple.exists():
                outputs["display current-configuration"] = \
                    simple.read_text(encoding="utf-8")
                print(f"    [FILE] ✓ {simple.name}")

        if not outputs:
            print(f"    [FILE] ⚠ Aucun fichier pour {name}")
            print(f"           Créez : {self.exports_dir}/{name}_full.txt")

        return outputs

    def _parse_full_file(self, content: str) -> dict[str, str]:
        outputs = {}
        pattern = re.compile(r"[\[<][\w\-]+[\]>](.*)", re.MULTILINE)
        sections = pattern.split(content)
        matches  = pattern.findall(content)
        for i, cmd_raw in enumerate(matches):
            cmd = cmd_raw.strip()
            if cmd and i + 1 < len(sections):
                outputs[cmd] = sections[i + 1].strip()
        return outputs

    def create_template(self, device: DeviceInfo) -> None:
        path = self.exports_dir / f"{device.name.upper()}_full.txt"
        if path.exists():
            return
        cmds = (
            SWITCH_COMMANDS if device.device_type == DeviceType.SWITCH
            else ROUTER_COMMANDS
        )
        lines = [
            f"# Export {device.name} ({device.mgmt_ip})",
            f"# Copiez-collez le terminal eNSP ici",
            "",
        ]
        for cmd in cmds:
            lines += [f"[{device.name}]{cmd}", "--- résultat ---", ""]
        path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. PARSEUR VRP
# ---------------------------------------------------------------------------

class VRPParser:
    """Parse les sorties textuelles des commandes VRP Huawei."""

    def parse_version(self, output: str) -> dict:
        result = {}
        for key, pattern in [
            ("vrp_version", r"VRP.*?Version\s+([\d.]+)"),
            ("hardware",    r"Huawei\s+([\w\-]+)\s+(?:Router|Switch)"),
            ("uptime",      r"uptime is\s+(.+)"),
            ("reboot_cause", r"Last reboot reason\s*:\s*(.+)"),
            ("compile_date", r"compiled at\s+(.+)"),
            ("patch_version", r"Patch Version\s*:\s*(.+)"),
        ]:
            m = re.search(pattern, output, re.IGNORECASE)
            if m:
                result[key] = m.group(1).strip()
        return result

    def parse_interfaces(self, output: str) -> list[dict]:
        ifaces = []
        pattern = re.compile(
            r"((?:Gigabit|Fast|Eth|Vlanif|GE|FE|XGE)[\w/\.\-]+)"
            r"\s+(up|down|\*down)\s+(up|down)",
            re.IGNORECASE
        )
        for m in pattern.finditer(output):
            ifaces.append({
                "name":      m.group(1),
                "phy_state": m.group(2),
                "pro_state": m.group(3),
                "state":     f"{m.group(2)}/{m.group(3)}",
                "ip":        "",
            })
        ip_pat = re.compile(
            r"interface\s+([\w/\.\-]+)\s*\n(?:.*\n)*?\s*ip address\s+([\d\.]+)\s+([\d\.]+)",
            re.MULTILINE
        )
        ip_map = {m.group(1): f"{m.group(2)}/{m.group(3)}" for m in ip_pat.finditer(output)}
        for iface in ifaces:
            if iface["name"] in ip_map:
                iface["ip"] = ip_map[iface["name"]]
        return ifaces

    def parse_vlans(self, output: str) -> list[dict]:
        vlans   = []
        pattern = re.compile(r"^(\d+)[ \t]+(common|super|sub)[ \t]*(.*)$", re.MULTILINE)
        for m in pattern.finditer(output):
            vlans.append({
                "id":    m.group(1),
                "type":  m.group(2),
                "ports": m.group(3).strip(),
            })
        return vlans

    def parse_routing_table(self, output: str) -> list[dict]:
        routes  = []
        pattern = re.compile(
            r"([\d\.]+/\d+)\s+(Direct|Static|OSPF|RIP|BGP|ISIS)\s+"
            r"\d+\s+\d+\s+\w+\s+([\d\.]+)\s+([\w/\.\-]+)",
            re.IGNORECASE
        )
        for m in pattern.finditer(output):
            routes.append({
                "destination": m.group(1),
                "protocol":    m.group(2),
                "nexthop":     m.group(3),
                "interface":   m.group(4),
            })
        return routes

    def parse_ospf_peers(self, output: str) -> list[dict]:
        peers   = []
        pattern = re.compile(
            r"([\d\.]+)\s+\d+\s+(Full|Init|2-Way|ExStart|Exchange|Loading|Down)[/\w]*"
            r"\s+\d+\s+([\d\.]+)\s+([\w/\.\-]+)",
            re.IGNORECASE
        )
        for m in pattern.finditer(output):
            peers.append({
                "router_id": m.group(1),
                "state":     m.group(2),
                "address":   m.group(3),
                "interface": m.group(4),
            })
        return peers

    def parse_cpu_usage(self, output: str) -> Optional[float]:
        m = re.search(r"CPU Usage\s*:\s*([\d\.]+)%", output, re.IGNORECASE)
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+)%", output)
        return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# 7. OUTIL PRINCIPAL — MCPTools
# ---------------------------------------------------------------------------

class MCPTools:
    """
    Point d'entrée unique pour tous les outils MCP.

    Charge la topologie depuis topology.yaml au démarrage.
    Chaque équipement se connecte en SSH ou Telnet selon son champ `protocol`.

    USAGE :
        tools = MCPTools()
        data  = tools.get_device_info("S1")   # SSH
        data2 = tools.get_device_info("R1")   # Telnet
        print(data.to_text_summary())

        audit = tools.audit_device("S1", rag_context="...")
    """

    def __init__(
        self,
        topology_file: str        = "topology.yaml",
        exports_dir:   str        = "device_exports",
        default_mode:  AccessMode = AccessMode.AUTO,
    ):
        self.default_mode = default_mode

        loader              = TopologyLoader(topology_file)
        self._registry      = loader.load()

        self._remote_collector = RemoteCollector()
        self._file_collector    = FileCollector(exports_dir)
        self._parser             = VRPParser()

        for device in self._registry.values():
            self._file_collector.create_template(device)

        print(f"[MCPTools] Prêt — mode {default_mode.value}")

    # ------------------------------------------------------------------
    # API PUBLIQUE
    # ------------------------------------------------------------------

    @property
    def devices(self) -> list[str]:
        return list(self._registry.keys())

    def get_device_info(
        self,
        device_name: str,
        mode:        AccessMode = None,
    ) -> DeviceData:
        """
        Collecte et parse toutes les données d'un équipement.

        Mode AUTO : connexion live (SSH ou Telnet selon topology.yaml)
        d'abord, fallback FILE si la connexion échoue.
        """
        mode = mode or self.default_mode
        name = device_name.upper()

        if name not in self._registry:
            available = list(self._registry.keys())
            return DeviceData(
                device_name=name,
                device_type="unknown",
                mgmt_ip="unknown",
                access_mode="error",
                collected_at=datetime.now().isoformat(),
                errors=[
                    f"'{name}' introuvable dans topology.yaml. "
                    f"Disponibles : {available}"
                ],
                is_complete=False,
            )

        device      = self._registry[name]
        raw_outputs = {}
        actual_mode = mode.value

        print(f"\n[MCPTools] Collecte {name} ({device.mgmt_ip}) "
              f"[protocole configuré: {device.protocol}] [mode: {mode.value}]")

        if mode == AccessMode.SSH:  # "SSH" = live, quel que soit le protocole réel
            raw_outputs = self._remote_collector.collect(device)
            actual_mode = device.protocol

        elif mode == AccessMode.FILE:
            raw_outputs = self._file_collector.collect(device)
            actual_mode = "file"

        elif mode == AccessMode.AUTO:
            if self._is_reachable(device.mgmt_ip, device.ssh_port):
                try:
                    raw_outputs = self._remote_collector.collect(device)
                    actual_mode = device.protocol
                    print(f"  → {device.protocol.upper()} réussi ✓")
                except Exception as e:
                    print(f"  → {device.protocol.upper()} échoué ({e}), fallback FILE...")
                    raw_outputs = self._file_collector.collect(device)
                    actual_mode = "file"
            else:
                print(f"  → {device.mgmt_ip}:{device.ssh_port} injoignable, mode FILE")
                raw_outputs = self._file_collector.collect(device)
                actual_mode = "file"

        return self._build_device_data(device, raw_outputs, actual_mode)

    def get_all_devices(
        self,
        mode:    AccessMode = None,
        devices: list[str]  = None,
    ) -> dict[str, DeviceData]:
        targets = [d.upper() for d in devices] if devices else self.devices
        results = {}

        print(f"\n[MCPTools] Collecte complète : {targets}")
        for name in targets:
            try:
                results[name] = self.get_device_info(name, mode=mode)
            except Exception as e:
                print(f"  ✗ {name} : {e}")

        ok = sum(1 for d in results.values() if d.is_complete)
        print(f"[MCPTools] {ok}/{len(targets)} collectés")
        return results

    def audit_device(
        self,
        device_name: str,
        rag_context: str,
        mode:        AccessMode = None,
    ) -> str:
        data   = self.get_device_info(device_name, mode=mode)
        alerts = self._generate_alerts(data)

        report = [
            f"=== AUDIT {device_name} ===",
            "",
            "--- CONFIG RÉELLE (terrain eNSP) ---",
            data.to_text_summary(),
            "",
            "--- RÉFÉRENCE THÉORIQUE (manuels RAG) ---",
            rag_context,
            "",
            "--- POINTS À VÉRIFIER ---",
            "1. VLANs configurés vs manuels",
            "2. Interfaces actives vs topologie attendue",
            "3. Routes OSPF convergées",
            "4. Performance CPU/mémoire dans les normes",
        ]

        if alerts:
            report += ["", "--- ALERTES AUTOMATIQUES ---"] + alerts

        return "\n".join(report)

    def check_connectivity(self) -> dict[str, bool]:
        """Teste la connectivité TCP (port SSH ou Telnet selon config) de tous les équipements."""
        results = {}
        print("\n[MCPTools] Test connectivité...")
        for name, device in self._registry.items():
            reachable     = self._is_reachable(device.mgmt_ip, device.ssh_port)
            results[name] = reachable
            icon          = "✓" if reachable else "✗"
            print(f"  {icon} {name:4s} {device.mgmt_ip:16s} "
                  f"[{device.protocol}:{device.ssh_port}] "
                  f"{'OK' if reachable else 'hors ligne → mode FILE'}")
        return results

    def get_topology_summary(self, mode: AccessMode = None) -> str:
        all_data = self.get_all_devices(mode=mode)
        lines    = [
            "=== TOPOLOGIE eNSP ===",
            f"Collecté le : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Équipements : {len(all_data)}",
            "",
        ]
        for name, data in all_data.items():
            alerts    = self._generate_alerts(data)
            alert_str = f" | {len(alerts)} alerte(s)" if alerts else ""
            status    = "✓" if data.is_complete else "✗"
            lines.append(
                f"{status} {name} ({data.mgmt_ip}) "
                f"[{data.access_mode}]{alert_str}"
            )
        lines.append("")
        for data in all_data.values():
            lines += [data.to_text_summary(), ""]
        return "\n".join(lines)

    def save_exports(
        self,
        device_data: dict[str, DeviceData],
        output_dir:  str = "device_exports",
    ) -> None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, data in device_data.items():
            (out / f"{name}_data.json").write_text(
                json.dumps(data.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            (out / f"{name}_summary.txt").write_text(
                data.to_text_summary(), encoding="utf-8"
            )
            print(f"  ✓ {name} exporté")
        print(f"[MCPTools] Exports → {output_dir}/")

    # ------------------------------------------------------------------
    # HELPERS PRIVÉS
    # ------------------------------------------------------------------

    def _is_reachable(self, ip: str, port: int, timeout: int = 3) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _build_device_data(
        self,
        device:      DeviceInfo,
        raw_outputs: dict[str, str],
        mode:        str,
    ) -> DeviceData:
        errors = []

        def safe(func, *args, default=None):
            try:
                return func(*args)
            except Exception as e:
                errors.append(f"{func.__name__}: {e}")
                return default if default is not None else []

        all_text = "\n".join(raw_outputs.values())

        return DeviceData(
            device_name=device.name,
            device_type=device.device_type.value,
            mgmt_ip=device.mgmt_ip,
            access_mode=mode,
            collected_at=datetime.now().isoformat(),
            raw_outputs=raw_outputs,
            version_info =safe(self._parser.parse_version,        raw_outputs.get("display version", ""),              default={}),
            interfaces   =safe(self._parser.parse_interfaces,      raw_outputs.get("display interface brief", all_text), default=[]),
            vlans        =safe(self._parser.parse_vlans,           raw_outputs.get("display vlan", ""),                  default=[]),
            routes       =safe(self._parser.parse_routing_table,   raw_outputs.get("display ip routing-table", ""),      default=[]),
            ospf_peers   =safe(self._parser.parse_ospf_peers,      raw_outputs.get("display ospf peer", ""),             default=[]),
            cpu_usage    =safe(self._parser.parse_cpu_usage,       raw_outputs.get("display cpu-usage", ""),             default=None),
            errors=errors,
            is_complete=len(raw_outputs) > 0,
        )

    def _generate_alerts(self, data: DeviceData) -> list[str]:
        alerts = []
        if data.cpu_usage and data.cpu_usage > 70:
            alerts.append(f"⚠ CPU élevé : {data.cpu_usage:.1f}% (seuil 70%)")
        down = [
            i["name"] for i in data.interfaces
            if "down" in i.get("phy_state", "").lower()
            and "NULL" not in i["name"]
        ]
        if down:
            alerts.append(f"⚠ Interfaces DOWN : {', '.join(down[:5])}")
        bad_ospf = [
            p for p in data.ospf_peers
            if "full" not in p.get("state", "").lower()
        ]
        if bad_ospf:
            alerts.append(f"⚠ OSPF instable : {len(bad_ospf)} voisin(s) non-FULL")
        return alerts


# ---------------------------------------------------------------------------
# 8. POINT D'ENTRÉE — Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    use_ssh  = "--ssh"  in sys.argv   # "--ssh" = connexion live (SSH ou Telnet selon device)
    use_file = "--file" in sys.argv
    mode     = (AccessMode.SSH  if use_ssh  else
                AccessMode.FILE if use_file else
                AccessMode.AUTO)

    tools = MCPTools(default_mode=mode)
    device_arg = next(
        (a.upper() for a in sys.argv[1:] if a.upper() in tools.devices),
        None
    )

    print("=" * 60)
    print("  TEST MCP TOOLS — eNSP Huawei")
    print(f"  Équipements : {tools.devices}")
    print("=" * 60)

    print("\n── Test 1 : Connectivité ──")
    tools.check_connectivity()

    target = device_arg or tools.devices[0] if tools.devices else None
    if target:
        print(f"\n── Test 2 : Collecte {target} ──")
        data = tools.get_device_info(target)
        print(data.to_text_summary())

        alerts = tools._generate_alerts(data)
        if alerts:
            print(f"\n── Alertes ──")
            for a in alerts:
                print(f"  {a}")
        else:
            print(f"\n── Aucune alerte — équipement nominal ✓")

        print(f"\n── Test 4 : Audit {target} ──")
        mock_rag = (
            "Selon le manuel VRP Huawei, les interfaces trunk doivent être "
            "en état up/up. OSPF doit converger en état FULL. CPU < 70%."
        )
        audit = tools.audit_device(target, rag_context=mock_rag)
        print(audit[:600] + "\n...[tronqué]" if len(audit) > 600 else audit)

    print("\n" + "=" * 60)
    print("  mcp_tools.py ✓")
    print("=" * 60)