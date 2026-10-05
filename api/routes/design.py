"""
api/routes/design.py
=====================
Assistant de déploiement réseau.

Ce routeur transforme une demande en langage naturel en :
- bloc YAML pour topology.yaml
- commandes VRP de remédiation sur l'équipement existant
- option d'application directe du changement dans topology.yaml
"""

from __future__ import annotations

import copy
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["design"])


class DesignUpdateRequest(BaseModel):
    request: str = Field(..., min_length=1, description="Demande en langage naturel")
    apply: bool = Field(False, description="Appliquer la mise à jour dans topology.yaml")


class DesignUpdateResponse(BaseModel):
    intent: str
    device_name: str
    device_type: str
    management_ip: str
    parent_device: str = ""
    topology_yaml_block: str
    remediation_commands: list[str]
    deployment_notes: list[str]
    topology_file: str
    applied: bool
    message: str
    requires_confirmation: bool = False


def _topology_file_path() -> Path:
    return Path(__file__).resolve().parents[2] / "backend" / "agents" / "topology.yaml"


def _load_topology_file() -> dict:
    path = _topology_file_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier topology introuvable : {path}")

    try:
        import yaml
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"PyYAML manquant : {exc}") from exc

    try:
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Impossible de lire topology.yaml : {exc}") from exc


def _save_topology_file(data: dict) -> None:
    path = _topology_file_path()
    try:
        import yaml
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"PyYAML manquant : {exc}") from exc

    try:
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Impossible d'écrire topology.yaml : {exc}") from exc


@router.post("/design/deploy", response_model=DesignUpdateResponse)
def design_deploy(request: DesignUpdateRequest):
    from architect_agent import QueryIntent, get_architect_agent

    agent = get_architect_agent()
    plan = agent.analyze(request.request)

    if plan.intent != QueryIntent.DESIGN_UPDATE:
        raise HTTPException(
            status_code=400,
            detail="La demande ne correspond pas à un déploiement ou une extension de topologie.",
        )

    topology = _load_topology_file()
    devices = topology.setdefault("devices", [])

    existing_names = {str(device.get("name", "")).upper() for device in devices}
    if plan.design_device_name.upper() in existing_names:
        raise HTTPException(
            status_code=409,
            detail=f"L'équipement {plan.design_device_name} existe déjà dans topology.yaml.",
        )

    if request.apply:
        try:
            new_device = {
                "name": plan.design_device_name,
                "type": plan.design_device_type,
                "protocol": "ssh",
                "mgmt_ip": plan.design_device_ip,
                "parent_device": plan.design_parent_device or None,
                "parent_interface": plan.design_parent_interface or None,
                "link_mode": plan.design_link_mode or None,
                "description": f"Nouveau {plan.design_device_type} ajouté par l'assistant",
            }
            new_device = {key: value for key, value in new_device.items() if value not in (None, "")}
            devices.append(copy.deepcopy(new_device))
            _save_topology_file(topology)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Échec de l'application du déploiement : {exc}") from exc

    message = (
        f"Prévisualisation du déploiement pour {plan.design_device_name}."
        if not request.apply
        else f"topology.yaml mis à jour avec {plan.design_device_name}."
    )

    return DesignUpdateResponse(
        intent=plan.intent.value,
        device_name=plan.design_device_name,
        device_type=plan.design_device_type,
        management_ip=plan.design_device_ip,
        parent_device=plan.design_parent_device,
        topology_yaml_block=plan.topology_yaml_block,
        remediation_commands=plan.remediation_commands,
        deployment_notes=plan.deployment_notes,
        topology_file=str(_topology_file_path()),
        applied=request.apply,
        message=message,
        requires_confirmation=not request.apply,
    )