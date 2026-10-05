"""
api/routes/commands.py
========================
Command Assistant — traduit une demande en langage naturel en commandes
VRP Huawei, les propose à l'utilisateur, puis les exécute UNIQUEMENT
après confirmation explicite.

FLUX :
    POST /commands/propose  → LLM génère les commandes (rien n'est exécuté)
    POST /commands/apply    → exécute les commandes proposées via MCPTools.push_command

⚠ /commands/apply ne doit JAMAIS être appelé automatiquement par le
frontend sans une confirmation explicite de l'utilisateur.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException

from llm.fallback_client import LLMFallbackClient, RateLimitedAllProvidersError
from routes.devices import get_mcp
from schemas import (
    CommandApplyRequest,
    CommandApplyResult,
    CommandProposal,
    CommandProposeRequest,
)

router = APIRouter(tags=["commands"])

_llm_singleton: LLMFallbackClient | None = None


def get_llm() -> LLMFallbackClient:
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = LLMFallbackClient()
    return _llm_singleton


SYSTEM_PROMPT = """Tu es un expert réseau Huawei VRP. L'utilisateur te donne une
demande en langage naturel concernant un équipement de son lab eNSP.

Réponds UNIQUEMENT en JSON valide, sans texte autour, avec ce format exact :
{
  "device_name": "<nom exact de l'équipement, en majuscules>",
  "commands": ["<commande VRP 1>", "<commande VRP 2>"],
  "explanation": "<2-3 phrases expliquant ce que font ces commandes>",
  "risk_level": "low|medium|high"
}

Règles :
- Les commandes doivent être valides en mode "system-view" (n'inclus PAS
  "system-view" toi-même, les commandes sont envoyées via send_config_set
  qui gère déjà l'entrée en mode config).
- Si la demande est ambiguë ou l'équipement n'existe pas dans la liste fournie,
  mets "commands": [] et explique pourquoi dans "explanation".
- risk_level "high" pour tout ce qui touche le routing global, les ACL, ou
  pourrait couper l'accès de management (ex: IP de management, VLAN 99, ACL).
"""


def _extract_json(text: str) -> dict:
    """Filet de sécurité si le LLM entoure quand même le JSON de ```json ... ```."""
    cleaned = re.sub(r"^```json|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


@router.post("/commands/propose", response_model=CommandProposal)
def propose_command(request: CommandProposeRequest) -> CommandProposal:
    mcp = get_mcp()
    llm = get_llm()

    user_prompt = (
        f"Équipements disponibles : {mcp.devices}\n\n"
        f"Demande : {request.query}"
    )

    try:
        resp = llm.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=512,
            json_mode=True,
        )
        data = _extract_json(resp.content)
    except RateLimitedAllProvidersError as e:
        raise HTTPException(status_code=503, detail=f"Tous les fournisseurs LLM indisponibles : {e}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Réponse LLM non-JSON : {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Échec génération : {e}")

    device_name = str(data.get("device_name", "")).upper()
    if device_name and device_name not in mcp.devices:
        raise HTTPException(
            status_code=400,
            detail=f"Équipement '{device_name}' inconnu. Disponibles : {mcp.devices}",
        )

    return CommandProposal(
        device_name=device_name,
        commands=data.get("commands", []),
        explanation=data.get("explanation", ""),
        risk_level=data.get("risk_level", "medium"),
    )


@router.post("/commands/apply", response_model=list[CommandApplyResult])
def apply_commands(request: CommandApplyRequest) -> list[CommandApplyResult]:
    mcp = get_mcp()
    if request.device_name.upper() not in mcp.devices:
        raise HTTPException(status_code=404, detail=f"Équipement '{request.device_name}' introuvable")

    result = mcp.push_command(request.device_name, request.commands)

    return [
        CommandApplyResult(
            device_name=request.device_name.upper(),
            command="\n".join(request.commands),
            success=result["success"],
            output=result["output"],
        )
    ]