"""
api/routes/devices.py
=======================
Router FastAPI — accès aux données live des équipements réseau via MCPTools.

ENDPOINTS :
    GET /devices               → liste des noms d'équipements déclarés (topology.yaml)
    GET /devices/{name}/status → collecte live (SSH/Telnet, fallback fichier) + parsing VRP

Le MCPTools est instancié en singleton paresseux (comme le pipeline dans
dependencies.py) pour éviter de recharger topology.yaml à chaque requête.
"""

from __future__ import annotations

import copy
from pathlib import Path
import threading
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_tools import MCPTools, AccessMode
from schemas import TerminalRequest, TerminalResponse , TerminalCommandResult

router = APIRouter(tags=["devices"])

_mcp_singleton: MCPTools | None = None
_monitor_started = False
_monitor_lock = threading.Lock()


class DeviceStatusCache:
    def __init__(self, interval_seconds: int = 15) -> None:
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="device-status-cache", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        self.refresh_all()
        while not self._stop_event.wait(self.interval_seconds):
            self.refresh_all()

    def refresh_all(self) -> None:
        mcp = get_mcp()
        for name in mcp.devices:
            self.refresh_device(name)

    def refresh_device(self, name: str) -> None:
        mcp = get_mcp()
        try:
            data = mcp.get_device_info(name, mode=AccessMode.AUTO)
            payload = data.to_dict()
            sample = {
                "timestamp": payload.get("collected_at") or datetime.now().isoformat(),
                "cpu": payload.get("cpu_usage"),
                "memory": payload.get("memory_usage"),
            }

            with self._lock:
                history = self._cache.get(name.upper(), {}).get("performance_history", [])
                history = [*history, sample]
                cutoff = time.time() - 10 * 60
                filtered_history = []
                for item in history:
                    try:
                        item_ts = datetime.fromisoformat(item["timestamp"]).timestamp()
                    except Exception:
                        item_ts = time.time()
                    if item_ts >= cutoff:
                        filtered_history.append(item)

                payload["performance_history"] = filtered_history[-40:]
                payload["status_error"] = None
                self._cache[name.upper()] = payload
        except Exception as exc:
            with self._lock:
                cached = self._cache.get(name.upper())
                if cached is None:
                    self._cache[name.upper()] = {
                        "device_name": name.upper(),
                        "status_error": str(exc),
                        "performance_history": [],
                    }
                else:
                    cached["status_error"] = str(exc)

    def get_device(self, name: str) -> dict | None:
        with self._lock:
            return self._cache.get(name.upper())


_status_cache = DeviceStatusCache()


def get_mcp() -> MCPTools:
    global _mcp_singleton
    if _mcp_singleton is None:
        _mcp_singleton = MCPTools()
    return _mcp_singleton


def _topology_file_path() -> Path:
    return Path(__file__).resolve().parents[2] / "backend" / "agents" / "topology.yaml"


def _load_topology_snapshot() -> dict:
    path = _topology_file_path()
    try:
        import yaml
    except ImportError:
        raise HTTPException(status_code=500, detail="PyYAML manquant pour lire topology.yaml")

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier topology introuvable : {path}")

    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Impossible de lire topology.yaml : {exc}") from exc

    devices = []
    for device in data.get("devices", []) or []:
        devices.append(copy.deepcopy(device))

    return {
        "network_name": data.get("network_name", "Unknown"),
        "management_subnet": data.get("management_subnet", ""),
        "devices": devices,
    }


def start_device_monitor() -> None:
    global _monitor_started
    with _monitor_lock:
        if _monitor_started:
            return
        _status_cache.start()
        _monitor_started = True


def stop_device_monitor() -> None:
    _status_cache.stop()


class RemediateRequest(BaseModel):
    command: str


@router.get("/devices")
def list_devices():
    """Liste les équipements déclarés dans topology.yaml."""
    mcp = get_mcp()
    return {"devices": mcp.devices}


@router.get("/topology")
def get_topology_snapshot():
    """Retourne la topologie brute telle qu'elle est définie dans topology.yaml."""
    return _load_topology_snapshot()


@router.get("/devices/{name}/status")
def get_device_status(name: str):
    """
    Retourne le dernier état collecté en arrière-plan pour un équipement.

    La collecte live elle-même est faite par un cache backend qui tourne en tâche de fond.
    """
    mcp = get_mcp()
    if name.upper() not in mcp.devices:
        raise HTTPException(
            status_code=404,
            detail=f"Équipement '{name}' introuvable. Disponibles : {mcp.devices}",
        )

    cached = _status_cache.get_device(name)
    if cached is None:
        _status_cache.refresh_device(name)
        cached = _status_cache.get_device(name)

    if cached is None:
        raise HTTPException(status_code=503, detail=f"Impossible de charger l'état de '{name}'.")

    payload = dict(cached)
    payload.setdefault("performance_history", [])
    payload.setdefault("status_error", None)
    return payload


@router.post("/devices/{name}/remediate")
def remediate_device(name: str, body: RemediateRequest):
    """
    Applique UNE commande de configuration sur l'équipement (mode config VRP).

    ⚠ Action destructive/modificatrice — le frontend DOIT confirmer avec
    l'utilisateur avant d'appeler cet endpoint.
    """
    mcp = get_mcp()
    if name.upper() not in mcp.devices:
        raise HTTPException(
            status_code=404,
            detail=f"Équipement '{name}' introuvable. Disponibles : {mcp.devices}",
        )

    result = mcp.push_command(name, body.command)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["output"])
    return result
@router.post("/devices/{name}/terminal", response_model=TerminalResponse)
def run_terminal(name: str, body: TerminalRequest) -> TerminalResponse:
    """
    Exécute une séquence de commandes en mode exec (lecture/navigation)
    sur l'équipement, dans une seule session, et retourne le résultat brut.
    """
    mcp = get_mcp()
    if name.upper() not in mcp.devices:
        raise HTTPException(
            status_code=404,
            detail=f"Équipement '{name}' introuvable. Disponibles : {mcp.devices}",
        )

    result = mcp.run_terminal_session(name, body.commands)
    return TerminalResponse(
        device_name=name.upper(),
        success=result["success"],
        results=[TerminalCommandResult(**r) for r in result["results"]],
        error=result["error"],
    )