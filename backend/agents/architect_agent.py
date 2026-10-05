"""
agents/architect_agent.py
==========================
Agent Architecte — Premier agent du pipeline LangGraph.

CORRECTIONS v2 :
    1. SYSTEM_PROMPT nettoyé — plus de code Python dans le prompt
    2. Topologie injectée dynamiquement via f-string au moment de l'appel
    3. Modèle Groq corrigé — "meta-llama/llama-4-scout-17b-16e-instruct" (disponible)
    4. DEVICE_INVENTORY_NAMES chargé depuis topology.yaml (dynamique)
    5. Agent instancié une seule fois, pas à chaque appel LangGraph

RÔLE DANS LE PIPELINE :
    User Question
         ↓
    [Architecte]  ← ON EST ICI
         ↓        → produit ArchitectPlan
    [Documentaliste]
         ↓
    [Validateur]
         ↓
    Réponse finale

RESPONSABILITÉS :
    1. COMPRENDRE l'intention (configuration, diagnostic, audit...)
    2. PLANIFIER la stratégie de recherche (RAG seul, terrain, hybride)
    3. EXTRACTION DES ENTITÉS (RÈGLE STRICTE) :

IMPORTANT :
Tu dois extraire uniquement les entités explicitement présentes dans la question utilisateur.

INTERDICTIONS :
- Ne jamais ajouter un équipement simplement parce qu'il existe dans la topologie.
- Ne jamais déduire un VLAN depuis une interface.
- Ne jamais compléter avec des équipements probables.

Équipements :
- Extraire uniquement les noms cités explicitement (ex: S1, R1).

VLAN :
- Extraire uniquement si un numéro VLAN apparaît explicitement.
- Exemples valides :
  VLAN10
  VLAN 20

- Ne jamais considérer :
  Vlanif1
  Vlanif10
  interface VLAN
  SVI

comme un VLAN.

Protocoles :
- Extraire uniquement les protocoles écrits dans la question.

Interfaces :
- Extraire uniquement les interfaces écrites dans la question.
    4. ENRICHIR la query pour maximiser la pertinence Qdrant
"""

from __future__ import annotations

import os
import json
import re
import sys
from dataclasses import dataclass, field
import ipaddress
from enum import Enum
from pathlib import Path
from backend.llm.fallback_client import LLMFallbackClient

from groq import Groq
sys.path.insert(0, str(Path(__file__).parent.parent))  # backend/
from logging_config import get_logger
from dotenv import load_dotenv
load_dotenv()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 1. TYPES DE DONNÉES
# ---------------------------------------------------------------------------
class QueryIntent(str, Enum):
    CONFIGURATION = "configuration"   # "Comment configurer X ?"
    DIAGNOSTIC    = "diagnostic"      # "Pourquoi X ne fonctionne pas ?"
    AUDIT         = "audit"           # "Compare la config avec le manuel"
    EXPLANATION   = "explanation"     # "Explique comment fonctionne X"
    OPTIMIZATION  = "optimization"    # "Optimise cette architecture"
    STATUS_CHECK  = "status_check"    # "Quel est l'état de X ?"
    COMPARISON    = "comparison"      # "Compare X et Y"
    DESIGN_UPDATE = "design_update"   # "Ajoute un switch / déploie une topologie"
    HEALTH_REPORT = "health_report"
    UNKNOWN       = "unknown"


class SearchStrategy(str, Enum):
    RAG_ONLY    = "rag_only"     # manuels Qdrant uniquement
    DEVICE_ONLY = "device_only"  # équipements uniquement
    HYBRID      = "hybrid"       # RAG + équipements (cas le plus fréquent)
    COMPARISON  = "comparison"   # comparer explicitement RAG vs terrain


@dataclass
class ArchitectPlan:
    """
    Plan de recherche produit par l'Agent Architecte.
    Transmis via l'état LangGraph au Documentaliste.
    """
    original_query:    str
    intent:            QueryIntent    = QueryIntent.UNKNOWN
    intent_confidence: float          = 0.0
    intent_reasoning:  str            = ""
    mentioned_devices: list[str]      = field(default_factory=list)
    mentioned_vlans:   list[str]      = field(default_factory=list)
    mentioned_protos:  list[str]      = field(default_factory=list)
    mentioned_ifaces:  list[str]      = field(default_factory=list)
    strategy:          SearchStrategy = SearchStrategy.HYBRID
    target_devices:    list[str]      = field(default_factory=list)
    chunk_types:       list[str]      = field(default_factory=list)
    needs_live_data:   bool           = False
    needs_images:      bool           = False
    enriched_query:    str            = ""
    sub_queries:       list[str]      = field(default_factory=list)
    complexity:        str            = "medium"
    estimated_sources: int            = 3
    design_device_name: str           = ""
    design_device_type: str           = ""
    design_device_ip:   str           = ""
    design_parent_device: str         = ""
    design_parent_interface: str      = ""
    design_link_mode:    str          = ""
    topology_yaml_block: str          = ""
    remediation_commands: list[str]   = field(default_factory=list)
    deployment_notes:    list[str]    = field(default_factory=list)

    def to_prompt_context(self) -> str:
        lines = [
            f"PLAN DE RECHERCHE :",
            f"  Intention   : {self.intent.value} ({self.intent_confidence:.0%})",
            f"  Stratégie   : {self.strategy.value}",
            f"  Équipements : {self.target_devices or 'tous'}",
            f"  Types Qdrant: {self.chunk_types}",
            f"  Données live: {'OUI' if self.needs_live_data else 'NON'}",
            f"  Images      : {'OUI' if self.needs_images else 'NON'}",
            f"  Query enrichie: {self.enriched_query}",
        ]
        if self.mentioned_protos:
            lines.append(f"  Protocoles  : {self.mentioned_protos}")
        if self.mentioned_vlans:
            lines.append(f"  VLANs       : {self.mentioned_vlans}")
        if self.sub_queries:
            lines.append(f"  Sub-queries : {self.sub_queries}")
        if self.intent == QueryIntent.DESIGN_UPDATE:
            lines.append(f"  Nouveau nœud : {self.design_device_name} ({self.design_device_type})")
            lines.append(f"  IP mgmt      : {self.design_device_ip}")
            lines.append(f"  Parent       : {self.design_parent_device or 'N/A'}")
            if self.design_parent_interface:
                lines.append(f"  Interface    : {self.design_parent_interface}")
            if self.design_link_mode:
                lines.append(f"  Lien         : {self.design_link_mode}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "original_query":    self.original_query,
            "intent":            self.intent.value,
            "intent_confidence": self.intent_confidence,
            "intent_reasoning":  self.intent_reasoning,
            "strategy":          self.strategy.value,
            "target_devices":    self.target_devices,
            "chunk_types":       self.chunk_types,
            "needs_live_data":   self.needs_live_data,
            "needs_images":      self.needs_images,
            "enriched_query":    self.enriched_query,
            "sub_queries":       self.sub_queries,
            "mentioned_devices": self.mentioned_devices,
            "mentioned_vlans":   self.mentioned_vlans,
            "mentioned_protos":  self.mentioned_protos,
            "complexity":        self.complexity,
            "estimated_sources": self.estimated_sources,
            "design_device_name": self.design_device_name,
            "design_device_type": self.design_device_type,
            "design_device_ip": self.design_device_ip,
            "design_parent_device": self.design_parent_device,
            "design_parent_interface": self.design_parent_interface,
            "design_link_mode": self.design_link_mode,
            "topology_yaml_block": self.topology_yaml_block,
            "remediation_commands": self.remediation_commands,
            "deployment_notes": self.deployment_notes,
        }


# ---------------------------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------------------------

# CORRECTION : modèle disponible sur Groq (vérifié juillet 2026)
GROQ_MODEL="openai/gpt-oss-120b"
MAX_TOKENS  = 1536
TEMPERATURE = 0.1

# Entités Huawei reconnues pour l'extraction par règles
HUAWEI_PROTOCOLS = {
    "OSPF", "BGP", "RIP", "LACP", "VLAN", "STP", "RSTP",
    "MSTP", "LLDP", "VRP", "SNMP", "SSH", "TELNET", "VRRP",
    "DHCP", "ACL", "NAT", "QOS", "MPLS",
}
HUAWEI_IFACE_PATTERN = re.compile(
    r"(GigabitEthernet|GE|FastEthernet|FE|Ethernet|"
    r"Vlanif|Eth-Trunk|LoopBack)\s*[\d/\.]+",
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# 3. CHARGEMENT DE LA TOPOLOGIE (dynamique)
# ---------------------------------------------------------------------------

def load_topology_context(topology_file: str = "topology.yaml") -> tuple[str, set[str]]:
    """
    Charge la topologie depuis topology.yaml et retourne :
    - Un texte descriptif à injecter dans le prompt LLM
    - Un set de noms d'équipements pour l'extraction d'entités

    POURQUOI CETTE FONCTION ?
        La topologie n'est plus hardcodée dans le code.
        Si tu ajoutes S4 dans topology.yaml, l'agent le reconnaît
        automatiquement sans modifier une seule ligne Python.

    Returns:
        (topology_text, device_names_set)
    """
    # Chercher le fichier dans plusieurs emplacements
    candidates = [
        Path(topology_file),
        Path(__file__).parent / "topology.yaml",
        Path("topology.yaml"),
    ]

    yaml_file = next((p for p in candidates if p.exists()), None)

    if yaml_file is None:
        # Fallback : description générique si pas de fichier
        default_text = (
            "Topologie non définie. "
            "Créez topology.yaml dans le dossier agents/."
        )
        return default_text, set()

    try:
        import yaml
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        return f"Erreur lecture topology.yaml : {e}", set()

    devices      = data.get("devices", [])
    network_name = data.get("network_name", "Réseau eNSP")
    subnet       = data.get("management_subnet", "N/A")

    # Construction du texte descriptif pour le prompt
    lines = [
        f"Réseau : {network_name}",
        f"Subnet management : {subnet}",
        "Équipements disponibles :",
    ]
    device_names = set()
    for dev in devices:
        name  = dev.get("name", "?").upper()
        dtype = dev.get("type", "?")
        ip    = dev.get("mgmt_ip", "?")
        iface = dev.get("mgmt_interface", "?")
        desc  = dev.get("description", "")
        lines.append(f"  - {name} ({dtype}) : {ip} sur {iface}"
                     + (f" — {desc}" if desc else ""))
        device_names.add(name)

    topology_text = "\n".join(lines)
    return topology_text, device_names


# ---------------------------------------------------------------------------
# 4. PROMPTS
# ---------------------------------------------------------------------------

# CORRECTION : prompt propre, sans aucun code Python dedans.
# La topologie est injectée via .format() au moment de l'appel API,
# pas stockée dans la chaîne elle-même.
ARCHITECT_SYSTEM_PROMPT_TEMPLATE = """Tu es l'Agent Architecte d'un système RAG \
multimodal spécialisé en infrastructure réseau Huawei.

Ta mission : analyser la question d'un ingénieur réseau et produire un plan \
de recherche structuré en JSON.

TOPOLOGIE ACTUELLE DU RÉSEAU :
{topology_context}

BASE DE CONNAISSANCES QDRANT :
  Types de chunks disponibles : text, code_cli, image_desc, table, heading

RÈGLES D'ANALYSE :
1. INTENTION : identifie parmi [configuration, diagnostic, audit, explanation, \
optimization, status_check, comparison, design_update, health_report, unknown] 
   - Utilise 'health_report' si l'utilisateur demande une prédiction, une analyse de santé, un rapport de panne ou l'état de fatigue du matériel.
   - Utilise 'status_check' pour toute question sur l'état RÉEL actuel d'un équipement
     ou d'un protocole (ex: "est-ce que OSPF est configuré sur R1 ?", "quel est l'état
     des interfaces de S1 ?"). Ce type de question attend une réponse FACTUELLE sur le
     terrain, pas une comparaison avec le manuel.
   - Utilise 'audit' ou 'comparison' uniquement si la question demande explicitement
     une vérification de conformité vs le manuel (ex: "est-ce conforme au manuel ?",
     "quels sont les écarts ?").
2. ENTITÉS : extrais les équipements, VLANs (ex: VLAN3), protocoles, interfaces
3. STRATÉGIE :
   - "rag_only"    → question théorique sur les manuels uniquement
   - "device_only" → état actuel d'un équipement (pas besoin des manuels)
   - "hybrid"      → combine manuels Qdrant + état équipement (cas fréquent)
   - "comparison"  → compare explicitement théorie vs terrain
4. CHUNK_TYPES : choisis parmi [text, code_cli, image_desc, table, heading].
   - RÈGLE IMPORTANTE : si un protocole précis est mentionné (OSPF, BGP, VRRP, STP,
     VLAN...), inclut TOUJOURS "text" en plus de "code_cli"/"table" — les exigences
     de ce protocole sont souvent décrites en prose dans le manuel, jamais uniquement
     dans des commandes CLI. Ne restreins jamais à ["code_cli", "table"] seuls quand
     un protocole est mentionné.
5. SUB_QUERIES : si un protocole est mentionné dans la question, génère au moins :
   - une sub-query ciblant l'état terrain (ex: "Huawei display ospf peer",
     "Huawei OSPF neighbor state")
   - une sub-query ciblant la configuration attendue dans le manuel (ex: "Huawei OSPF
     area configuration standard")
6. NEEDS_LIVE_DATA : mets toujours true si la question porte sur l'état actuel d'un
   équipement ou d'un protocole (status_check, diagnostic, health_report, ou toute
   mention explicite de protocole).
7. ENRICHISSEMENT : reformule enriched_query pour maximiser la pertinence Qdrant
8. DESIGN_UPDATE : si l'utilisateur demande d'ajouter/déployer un équipement ou une
    extension de topologie, remplis aussi design_device_name, design_device_type,
    design_device_ip, design_parent_device, topology_yaml_block,
    remediation_commands et deployment_notes.

RÉPONDS UNIQUEMENT EN JSON valide, sans markdown ni commentaires."""

def build_architect_prompt(query: str) -> str:
    """Construit le prompt utilisateur avec le format JSON attendu."""
    return f"""Question de l'ingénieur : "{query}"

Analyse et retourne ce JSON exactement :
{{
  "intent": "string",
  "intent_confidence": float,
  "intent_reasoning": "string",
  "mentioned_devices": ["S1", ...],
  "mentioned_vlans": ["VLAN3", ...],
  "mentioned_protocols": ["OSPF", ...],
  "mentioned_interfaces": ["GE0/0/1", ...],
  "strategy": "string",
  "target_devices": ["S1", ...],
  "chunk_types": ["code_cli", "text", ...],
  "needs_live_data": boolean,
  "needs_images": boolean,
  "enriched_query": "string",
  "sub_queries": ["string", ...],
  "complexity": "low|medium|high",
    "estimated_sources": integer,
    "design_device_name": "string",
    "design_device_type": "switch|router",
    "design_device_ip": "string",
    "design_parent_device": "string",
            "design_parent_interface": "string",
            "design_link_mode": "string",
    "topology_yaml_block": "string",
    "remediation_commands": ["string", ...],
    "deployment_notes": ["string", ...]
}}"""


# ---------------------------------------------------------------------------
# 5. AGENT ARCHITECTE
# ---------------------------------------------------------------------------

class ArchitectAgent:
    """
    Agent Architecte — analyse l'intention et planifie la recherche.

    CORRECTIONS v2 :
    - topology_text chargé une fois à l'init, injecté dans chaque appel LLM
    - device_names chargé dynamiquement depuis topology.yaml
    - Modèle Groq corrigé
    - Prompt système propre (sans code Python)

    USAGE :
        agent = ArchitectAgent()
        plan  = agent.analyze("Pourquoi S1 ne répond pas au ping ?")
        print(plan.to_prompt_context())
    """

    def __init__(
        self,
        api_key:       str | None = None,
        topology_file: str           = "topology.yaml",
    ):
        # Chargement dynamique de la topologie
        self._topology_text, self._device_names = load_topology_context(topology_file)
        logger.info("Topologie chargée : %s", self._device_names or "non définie")
        # Client Groq
        resolved_key = api_key or os.getenv("GROQ_API_KEY")
        if resolved_key:
            
            self._llm = LLMFallbackClient(groq_api_key=resolved_key)
            self._use_llm = True
            print(f"[ArchitectAgent] LLM actif — {GROQ_MODEL}")
        else:
            self._llm     = None
            self._use_llm = False
            print("[ArchitectAgent] Mode règles (GROQ_API_KEY manquant)")

    # ------------------------------------------------------------------
    # POINT D'ENTRÉE
    # ------------------------------------------------------------------

    def analyze(self, query: str) -> ArchitectPlan:
        """
        Analyse une question et retourne un ArchitectPlan.

        Essaie le LLM en premier, fallback sur les règles si indisponible.
        """
        short = query[:60] + "..." if len(query) > 60 else query
        print(f"\n[ArchitectAgent] Analyse : '{short}'")

        q_lower = query.lower()
        health_keywords = ["rapport", "santé", "prédiction", "prédire", "obsolescence", "fatigué", "panne", "échéance"]
        should_force_health = any(kw in q_lower for kw in health_keywords)

        plan = None

        if self._use_llm:
            try:
                plan = self._analyze_with_llm(query)
                if should_force_health and plan.intent != QueryIntent.HEALTH_REPORT:
                    print(f"  [Security] Forçage de l'intention : status_check -> health_report")
                    plan.intent = QueryIntent.HEALTH_REPORT
                    plan.strategy = SearchStrategy.HYBRID # On a besoin du RAG + Terrain
                    plan.needs_live_data = True           # On a besoin des données SSH
                
                print(f"  → Intent   : {plan.intent.value} ({plan.intent_confidence:.0%})")
                print(f"  → Stratégie: {plan.strategy.value}")
                print(f"  → Devices  : {plan.target_devices or 'tous'}")
            except Exception as e:
                logger.warning("LLM échoué (%s), fallback règles", e)

        if plan is None:
            plan = self._analyze_with_rules(query)
            print(f"  → [RÈGLES] Intent : {plan.intent.value}")

        if plan.intent == QueryIntent.DESIGN_UPDATE:
            plan = self._build_design_update_plan(query, plan)

        return plan

    # ------------------------------------------------------------------
    # ANALYSE LLM
    # ------------------------------------------------------------------

    def _analyze_with_llm(self, query: str) -> ArchitectPlan:
        """
        Analyse via Groq.

        CORRECTION : la topologie est injectée ici via .format()
        au moment de l'appel API, pas stockée dans le template.
        Le LLM reçoit un prompt propre, sans code Python.
        """
        # Injection dynamique de la topologie dans le prompt système
        system_content = ARCHITECT_SYSTEM_PROMPT_TEMPLATE.format(
            topology_context=self._topology_text
        )

        llm_response = self._llm.complete(
    system_prompt=system_content,
    user_prompt=build_architect_prompt(query),
    temperature=TEMPERATURE,
    max_tokens=MAX_TOKENS,
    json_mode=True,
)

        raw_text = llm_response.content.strip()
        # Nettoyage des balises markdown si le modèle en ajoute
        raw_text = re.sub(r"```(?:json)?\s*|\s*```", "", raw_text).strip()

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON invalide : {e}\nRaw: {raw_text[:300]}")

        return self._dict_to_plan(query, data)

    def _dict_to_plan(self, query: str, data: dict) -> ArchitectPlan:
        """Convertit le dict JSON du LLM en ArchitectPlan typé."""

        # Intent → enum
        try:
            intent = QueryIntent(data.get("intent", "unknown").lower())
        except ValueError:
            intent = QueryIntent.UNKNOWN

        # Strategy → enum
        try:
            strategy = SearchStrategy(data.get("strategy", "hybrid").lower())
        except ValueError:
            strategy = SearchStrategy.HYBRID
        # Sécurité réseau : un status_check doit comparer avec les exigences
        if intent in (
    QueryIntent.DIAGNOSTIC,
    QueryIntent.OPTIMIZATION,
    QueryIntent.HEALTH_REPORT,

):  
            strategy = SearchStrategy.HYBRID

        # Validation des devices (seulement ceux de la topologie)
        raw_devices = data.get("target_devices", [])

        target_devices = [
    d.upper()
    for d in raw_devices
    if d.upper() in self._device_names
] if self._device_names else [
    d.upper()
    for d in raw_devices
]
        # Validation des devices mentionnés par le LLM
        raw_mentioned_devices = data.get("mentioned_devices", [])

        mentioned_devices = [
    d.upper()
    for d in raw_mentioned_devices
    if (
        d.upper() in self._device_names
        and re.search(
            rf"\b{re.escape(d)}\b",
            query,
            re.IGNORECASE
        )
    )
]
        # Suppression des doublons
        mentioned_devices = list(dict.fromkeys(mentioned_devices))
        if mentioned_devices:
            raw_mentioned_devices = mentioned_devices
        else:
            raw_mentioned_devices = []
        # Chunk types par défaut selon l'intention
        chunk_types = data.get("chunk_types") or self._default_chunk_types(intent)

        # Query enrichie
        enriched = data.get("enriched_query", "").strip()

        # Suppression des équipements inventés par le LLM
        for device in self._device_names:

            if not re.search(
        rf"\b{device}\b",
        query,
        re.IGNORECASE
    ):
                enriched = re.sub(
            rf"\b{device}\b",
            "",
            enriched,
            flags=re.IGNORECASE
        )

        enriched = re.sub(
    r"\s+",
    " ",
    enriched
).strip()
        if not enriched:
            enriched = self._enrich_query(query)
        return ArchitectPlan(
            original_query=query,
            intent=intent,
            intent_confidence=float(data.get("intent_confidence", 0.8)),
            intent_reasoning=data.get("intent_reasoning", ""),
            mentioned_devices=mentioned_devices,
            mentioned_vlans=self._validate_vlans(data.get("mentioned_vlans", [])),
            mentioned_protos=data.get("mentioned_protocols", []),
            mentioned_ifaces=data.get("mentioned_interfaces", []),
            strategy=strategy,
            target_devices=target_devices,
            chunk_types=chunk_types,
            needs_live_data=bool(data.get("needs_live_data", False)),
            needs_images=bool(data.get("needs_images", False)),
            enriched_query=enriched,
            sub_queries=data.get("sub_queries", []),
            complexity=data.get("complexity", "medium"),
            estimated_sources=int(data.get("estimated_sources", 3)),
        )

    # ------------------------------------------------------------------
    # ANALYSE PAR RÈGLES (fallback sans LLM)
    # ------------------------------------------------------------------

    def _analyze_with_rules(self, query: str) -> ArchitectPlan:
        """Analyse heuristique — fonctionne sans clé API."""
        q = query.lower()

        intent  = self._detect_intent(q)
        devices = self._extract_devices(query)
        vlans   = self._extract_vlans(query)
        protos  = self._extract_protocols(query)
        ifaces  = self._extract_interfaces(query)

        strategy = self._decide_strategy(intent, devices)

        # Targets : devices mentionnés ET dans la topologie
        if self._device_names:
            targets = [d for d in devices if d in self._device_names]
        else:
            targets = devices

        # Si pas de device spécifié et stratégie nécessite terrain → tous
        if not targets and strategy in (
            SearchStrategy.HYBRID,
            SearchStrategy.DEVICE_ONLY,
            SearchStrategy.COMPARISON,
        ):
            targets = list(self._device_names) if self._device_names else []

        chunk_types = self._default_chunk_types(intent)
        if any(kw in q for kw in ["schéma", "topologie", "diagram", "topology"]):
            if "image_desc" not in chunk_types:
                chunk_types.append("image_desc")

        needs_live = intent in (
            QueryIntent.DIAGNOSTIC,
            QueryIntent.OPTIMIZATION,
            QueryIntent.HEALTH_REPORT,
            QueryIntent.STATUS_CHECK,

        )

        return ArchitectPlan(
            original_query=query,
            intent=intent,
            intent_confidence=0.7,
            intent_reasoning="Analyse par règles heuristiques (mode fallback)",
            mentioned_devices=devices,
            mentioned_vlans=vlans,
            mentioned_protos=protos,
            mentioned_ifaces=ifaces,
            strategy=strategy,
            target_devices=targets,
            chunk_types=chunk_types,
            needs_live_data=needs_live,
            needs_images="image_desc" in chunk_types,
            enriched_query=self._enrich_query(query),
            sub_queries=self._build_sub_queries(query, intent, protos, devices, vlans, ifaces),
            complexity=self._estimate_complexity(query),
            estimated_sources=5 if needs_live else 3,
        )

    # ------------------------------------------------------------------
    # HELPERS — Règles
    # ------------------------------------------------------------------

    def _detect_intent(self, q: str) -> QueryIntent:
        rules = [
            (QueryIntent.DESIGN_UPDATE, ["ajoute", "ajouter", "déployer", "deploy", "mettre en place", "nouveau switch", "nouveau routeur", "topology.yaml", "assistant de déploiement", "design update"]),
            (QueryIntent.HEALTH_REPORT, ["rapport", "santé", "prédiction", "prédire","fatigue",
                "vieillissement", "obsolescence", "health"]),
            (QueryIntent.DIAGNOSTIC,    ["pourquoi", "erreur", "problème", "down",
                                          "fail", "cannot", "not working", "issue",
                                          "ne marche pas", "ne fonctionne pas","panne"]),
            (QueryIntent.AUDIT,         ["audit", "comparer", "compare", "conformit",
                                          "vérif", "check", "verify", "conforme"]),
            (QueryIntent.STATUS_CHECK,  ["état", "status", "running", "actif",
                                          "fonctionne", "up", "is it", "est-ce que"]),
            (QueryIntent.OPTIMIZATION,  ["optimis", "améliorer", "improve",
                                          "performance", "bottleneck", "goulot"]),
            (QueryIntent.EXPLANATION,   ["expliquer", "explain", "qu'est-ce",
                                          "what is", "comment fonctionne", "c'est quoi"]),
            (QueryIntent.CONFIGURATION, ["configurer", "configure", "how to",
                                          "comment", "setup", "créer", "mettre en place"]),
            
            (QueryIntent.COMPARISON,    ["différence", "vs", "versus",
                                          "compare", "quelle est la diff"]),
            ]
        for intent, keywords in rules:
            if any(kw in q for kw in keywords):
                return intent
        return QueryIntent.UNKNOWN

    def _extract_devices(self, query: str) -> list[str]:
        """Extrait les noms d'équipements — utilise la topologie dynamique."""
        found = []
        # Recherche dans les devices de la topologie (priorité)
        for name in self._device_names:
            if re.search(rf"\b{re.escape(name)}\b", query, re.IGNORECASE):
                found.append(name.upper())
        # Fallback : patterns génériques (S\d+, R\d+)
        if not found:
            for m in re.finditer(r"\b([SR]\d+)\b", query, re.IGNORECASE):
                found.append(m.group(1).upper())
        return list(dict.fromkeys(found))  # déduplique en préservant l'ordre
    def _validate_vlans(self, vlans: list[str]) -> list[str]:
        """
    Garde uniquement les vrais VLAN.
    Exemple accepté:
        VLAN10
        vlan 20

    Refuse:
        Vlanif1
        Interface Vlanif
    """

        valid = []

        for vlan in vlans:

            match = re.search(
            r"VLAN\s*(\d+)",
            vlan,
            re.IGNORECASE
        )

            if match:
                valid.append(
                f"VLAN{match.group(1)}"
            )

        return list(dict.fromkeys(valid))
    def _extract_vlans(self, query: str) -> list[str]:
        return [f"VLAN{m}" for m in re.findall(r"VLAN\s*(\d+)", query, re.IGNORECASE)]

    def _extract_protocols(self, query: str) -> list[str]:
        return [
            p for p in HUAWEI_PROTOCOLS
            if re.search(rf"\b{p}\b", query, re.IGNORECASE)
        ]

    def _extract_interfaces(self, query: str) -> list[str]:
        return [m.group(0) for m in HUAWEI_IFACE_PATTERN.finditer(query)]

    def _decide_strategy(
    self,
    intent: QueryIntent,
    devices: list[str]
) -> SearchStrategy:
        if intent in (
        QueryIntent.DIAGNOSTIC,
        QueryIntent.OPTIMIZATION,
        QueryIntent.HEALTH_REPORT,
        QueryIntent.DESIGN_UPDATE,
    ):
            return SearchStrategy.HYBRID


        if intent == QueryIntent.STATUS_CHECK:

            if devices:
                return SearchStrategy.DEVICE_ONLY

            return SearchStrategy.HYBRID


        if intent == QueryIntent.CONFIGURATION:

            if devices:
                return SearchStrategy.HYBRID

            return SearchStrategy.RAG_ONLY


        if intent == QueryIntent.AUDIT:
            return SearchStrategy.COMPARISON


        if intent == QueryIntent.EXPLANATION:
            return SearchStrategy.RAG_ONLY


        if intent == QueryIntent.COMPARISON:

            if devices:
                return SearchStrategy.HYBRID

            return SearchStrategy.RAG_ONLY


        return SearchStrategy.RAG_ONLY
    def _default_chunk_types(self, intent: QueryIntent) -> list[str]:
        mapping = {
            QueryIntent.CONFIGURATION: ["code_cli", "text", "heading"],
            QueryIntent.DIAGNOSTIC:    ["code_cli", "text", "table"],
            QueryIntent.AUDIT:         ["code_cli", "text", "table", "image_desc"],
            QueryIntent.EXPLANATION:   ["text", "image_desc", "heading"],
            QueryIntent.OPTIMIZATION:  ["code_cli", "text", "image_desc"],
            QueryIntent.HEALTH_REPORT: ["code_cli", "text", "table"],
            QueryIntent.STATUS_CHECK:  ["code_cli", "table", "text"],
            QueryIntent.COMPARISON:    ["code_cli", "text", "image_desc", "table"],
            QueryIntent.DESIGN_UPDATE: ["code_cli", "text", "table"],
            QueryIntent.UNKNOWN:       ["code_cli", "text"],
        }
        return mapping.get(intent, ["code_cli", "text"])

    def _enrich_query(self, query: str) -> str:
        """Enrichissement simple de la query sans LLM."""
        parts = [query]
        if "huawei" not in query.lower():
            parts.append("Huawei VRP")
        if not any(w in query.lower() for w in ["réseau", "network", "switch", "router"]):
            parts.append("network configuration")
        return " ".join(parts)

    def _build_sub_queries(
    self,
    query: str,
    intent: QueryIntent,
    protos: list[str],
    devices: list[str],
    vlans: list[str] = None,
    ifaces: list[str] = None,
) -> list[str]:
        """
    Génère des sous-requêtes optimisées pour le moteur RAG.

    Priorité :
    1. Protocole précis
    2. VLAN / Interface
    3. Contexte de panne
    4. Bonnes pratiques selon intention

    Retourne maximum 6 requêtes.
    """

        sub = []

        vlans = vlans or []
        ifaces = ifaces or []

        q = query.lower()


    # ==========================================================
    # 1) REQUETES PAR PROTOCOLE
    # ==========================================================

        protocol_library = {

            "OSPF": {
            QueryIntent.STATUS_CHECK: [
            "Huawei display ospf peer",
            "Huawei OSPF process area configuration",
            ],
            QueryIntent.CONFIGURATION: [
                "Huawei VRP OSPF configuration",
                "Huawei OSPF area configuration",
            ],

            QueryIntent.DIAGNOSTIC: [
                "Huawei OSPF troubleshooting",
                "Huawei display ospf peer",
                "Huawei OSPF neighbor state",
            ],

            QueryIntent.AUDIT: [
                "Huawei OSPF best practices",
                "Huawei OSPF configuration standards",
            ],
        },


        "BGP": {
            QueryIntent.STATUS_CHECK: [
            "Huawei display bgp peer",
            "Huawei BGP process configuration",
        ],

            QueryIntent.CONFIGURATION: [
                "Huawei VRP BGP configuration",
            ],

            QueryIntent.DIAGNOSTIC: [
                "Huawei BGP troubleshooting",
                "Huawei display bgp peer",
                "Huawei BGP neighbor state",
            ],

            QueryIntent.AUDIT: [
                "Huawei BGP security best practices",
            ],
        },


        "VRRP": {

            QueryIntent.STATUS_CHECK: [
            "Huawei display VRRP peer",
            "Huawei VRRP process configuration",
        ],

            QueryIntent.CONFIGURATION: [
                "Huawei VRP VRRP configuration",
            ],

            QueryIntent.DIAGNOSTIC: [
                "Huawei VRRP troubleshooting",
                "Huawei VRRP master backup state",
            ],
        },


        "RSTP": {

            QueryIntent.STATUS_CHECK: [
            "Huawei display RSTP peer",
            "Huawei RSTP process configuration",
        ],

            QueryIntent.DIAGNOSTIC: [
                "Huawei RSTP troubleshooting",
                "Huawei spanning tree topology issue",
            ],

            QueryIntent.CONFIGURATION: [
                "Huawei RSTP configuration",
            ],
        },

    }


        for proto in protos:

            proto = proto.upper()

            if proto in protocol_library:

                queries = protocol_library[proto].get(intent, [])

                sub.extend(queries)



    # ==========================================================
    # 2) REQUETES VLAN
    # ==========================================================

        if vlans:

            for vlan in vlans:

                if intent == QueryIntent.CONFIGURATION:

                    sub.append(
                    f"Huawei {vlan} configuration"
                )


                elif intent == QueryIntent.DIAGNOSTIC:

                    sub.extend([
                    "Huawei VLAN troubleshooting",
                    "Huawei trunk VLAN configuration",
                    "Huawei dot1q troubleshooting",
                ])



                elif intent == QueryIntent.AUDIT:

                    sub.extend([
                    "Huawei VLAN best practices",
                    "Huawei VLAN security standards",
                ])




    # ==========================================================
    # 3) REQUETES INTERFACES
    # ==========================================================

        if ifaces:

            if intent == QueryIntent.DIAGNOSTIC:

                sub.extend([
                "Huawei interface troubleshooting",
                "Huawei display interface command",
                "Huawei interface down troubleshooting",
            ])


            elif intent == QueryIntent.CONFIGURATION:

                sub.extend([
                "Huawei interface configuration",
                "Huawei interface description configuration",
            ])




    # ==========================================================
    # 4) CONTEXTE DETECTE DANS LA QUESTION
    # ==========================================================


        context_rules = {

        "ping": [
            "Huawei ping troubleshooting",
            "Huawei ICMP connectivity troubleshooting",
        ],


        "route": [
            "Huawei routing troubleshooting",
            "Huawei display ip routing-table",
        ],


        "arp": [
            "Huawei ARP troubleshooting",
            "Huawei display arp",
        ],


        "dhcp": [
            "Huawei DHCP troubleshooting",
            "Huawei DHCP configuration",
        ],


        "mac": [
            "Huawei MAC address table troubleshooting",
            "Huawei display mac-address",
        ],


        "latence": [
            "Huawei network performance troubleshooting",
        ],


        "perte": [
            "Huawei packet loss troubleshooting",
        ],

    }



        for keyword, queries in context_rules.items():

            if keyword in q:
                sub.extend(queries)




    # ==========================================================
    # 5) CAS GENERIQUES SELON INTENTION
    # ==========================================================


        if intent == QueryIntent.DIAGNOSTIC:

            sub.append(
            "Huawei VRP network troubleshooting methodology"
        )


        elif intent == QueryIntent.AUDIT:

            sub.extend([
            "Huawei configuration audit checklist",
            "Huawei network best practices",
        ])


        elif intent == QueryIntent.OPTIMIZATION:

            sub.extend([
            "Huawei network performance optimization",
            "Huawei configuration tuning best practices",
        ])



        elif intent == QueryIntent.EXPLANATION:

            sub.append(
            "Huawei VRP architecture explanation"
        )

        elif intent == QueryIntent.DESIGN_UPDATE:

            sub.extend([
            "Huawei switch trunk configuration",
            "Huawei topology.yaml device definition",
        ])



    # ==========================================================
    # 6) SI AUCUN ELEMENT TECHNIQUE TROUVE
    # ==========================================================

        if not sub:

            sub.extend([
            "Huawei VRP configuration guide",
            "Huawei network troubleshooting guide",
        ])




    # ==========================================================
    # 7) NETTOYAGE
    # ==========================================================

        cleaned = []

        for item in sub:

            item = item.strip()

            if item and item not in cleaned:

                cleaned.append(item)



    # Maximum 6 requêtes pour éviter de saturer le RAG

        return cleaned[:6]
    def _estimate_complexity(self, query: str) -> str:
        if len(query) < 50:
            return "low"
        if len(query) > 150:
            return "high"
        return "medium"

    def _build_design_update_plan(self, query: str, base_plan: ArchitectPlan) -> ArchitectPlan:
        topology = self._load_topology_data()
        devices = topology.get("devices", []) if isinstance(topology, dict) else []
        device_names = [str(dev.get("name", "")).upper() for dev in devices if dev.get("name")]

        new_device_name = self._extract_new_device_name(query, device_names)
        new_device_type = self._infer_device_type(query, new_device_name)
        new_device_ip = self._next_free_mgmt_ip(topology)
        parent_device = self._infer_parent_device(query, device_names, new_device_name)
        parent_interface = self._infer_parent_interface(query)
        link_mode = self._infer_link_mode(query)
        topology_yaml_block = self._render_device_yaml(new_device_name, new_device_type, new_device_ip, parent_device, parent_interface, link_mode)
        remediation_commands = self._build_remediation_commands(query, parent_interface, new_device_name, link_mode)

        deployment_notes = [
            f"Topologie prête pour l'ajout de {new_device_name}.",
            f"IP de management réservée : {new_device_ip}",
        ]
        if parent_device:
            deployment_notes.append(f"Switch parent cible : {parent_device}")

        return ArchitectPlan(
            original_query=query,
            intent=QueryIntent.DESIGN_UPDATE,
            intent_confidence=max(base_plan.intent_confidence, 0.92),
            intent_reasoning=base_plan.intent_reasoning or "Déploiement réseau détecté par règles de design.",
            mentioned_devices=base_plan.mentioned_devices,
            mentioned_vlans=base_plan.mentioned_vlans,
            mentioned_protos=base_plan.mentioned_protos,
            mentioned_ifaces=base_plan.mentioned_ifaces,
            strategy=SearchStrategy.RAG_ONLY,
            target_devices=[parent_device] if parent_device else [],
            chunk_types=["text", "code_cli", "table"],
            needs_live_data=False,
            needs_images=False,
            enriched_query=f"Déployer {new_device_name} dans topology.yaml et générer la configuration trunk pour {parent_device or 'le switch parent'}",
            sub_queries=[
                "Huawei switch trunk configuration",
                "Huawei topology.yaml device definition",
            ],
            complexity="medium",
            estimated_sources=2,
            design_device_name=new_device_name,
            design_device_type=new_device_type,
            design_device_ip=new_device_ip,
            design_parent_device=parent_device,
            design_parent_interface=parent_interface,
            design_link_mode=link_mode,
            topology_yaml_block=topology_yaml_block,
            remediation_commands=remediation_commands,
            deployment_notes=deployment_notes,
        )

    def _load_topology_data(self) -> dict:
        candidates = [
            Path("topology.yaml"),
            Path(__file__).parent / "topology.yaml",
            Path(__file__).parent.parent / "topology.yaml",
        ]
        yaml_file = next((p for p in candidates if p.exists()), None)
        if yaml_file is None:
            return {}
        try:
            import yaml
            with open(yaml_file, encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}
        except Exception:
            return {}

    def _extract_new_device_name(self, query: str, existing_names: list[str]) -> str:
        candidates = re.findall(r"\b([SR]\d+)\b", query, re.IGNORECASE)
        for candidate in candidates:
            normalized = candidate.upper()
            if normalized not in existing_names:
                return normalized
        if candidates:
            return candidates[-1].upper()
        index = 1
        while f"S{index}" in existing_names:
            index += 1
        return f"S{index}"

    def _infer_device_type(self, query: str, device_name: str) -> str:
        q = query.lower()
        if any(word in q for word in ["routeur", "router", "l3", "layer 3"]):
            return "router"
        if any(word in q for word in ["switch", "commutateur", "topologie"]):
            return "switch"
        return "switch" if device_name.upper().startswith("S") else "router"

    def _infer_parent_device(self, query: str, existing_names: list[str], new_device_name: str) -> str:
        for name in existing_names:
            if name.upper() == new_device_name.upper():
                continue
            if re.search(rf"\b{re.escape(name)}\b", query, re.IGNORECASE):
                return name
        for name in existing_names:
            if name.upper().startswith("S"):
                return name
        return existing_names[0] if existing_names else ""

    def _infer_parent_interface(self, query: str) -> str:
        patterns = [
            r"\b(?:ge|gigabitethernet)\s*(?:0/0/)?(\d+)\b",
            r"\b(?:xge|10ge|fortygigabitethernet)\s*(?:0/0/)?(\d+)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                return f"GigabitEthernet0/0/{match.group(1)}"
        return ""

    def _infer_link_mode(self, query: str) -> str:
        q = query.lower()
        if "trunk" in q:
            return "trunk"
        if "access" in q:
            return "access"
        return "trunk" if any(word in q for word in ["uplink", "liaison", "branch", "connect", "branché"]) else "access"

    def _next_free_mgmt_ip(self, topology: dict) -> str:
        subnet = topology.get("management_subnet", "192.168.56.0/24")
        network = ipaddress.ip_network(subnet, strict=False)
        used_hosts: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for device in topology.get("devices", []) or []:
            raw_ip = str(device.get("mgmt_ip", "")).strip()
            if not raw_ip:
                continue
            try:
                host = ipaddress.ip_address(raw_ip)
            except ValueError:
                continue
            if host in network:
                used_hosts.append(host)

        if used_hosts:
            next_host = int(max(used_hosts)) + 1
            candidate = ipaddress.ip_address(next_host)
            if candidate in network and candidate not in used_hosts:
                return str(candidate)

        for host in network.hosts():
            host_ip = str(host)
            if host_ip not in {str(item) for item in used_hosts}:
                return host_ip
        return str(next(network.hosts()))

    def _render_device_yaml(self, name: str, device_type: str, ip: str, parent_device: str = "", parent_interface: str = "", link_mode: str = "") -> str:
        lines = [
            f"  - name: {name}",
            f"    type: {device_type}",
            f"    protocol: ssh",
            f"    mgmt_ip: {ip}",
        ]
        if parent_device:
            lines.append(f"    parent_device: {parent_device}")
        if parent_interface:
            lines.append(f"    parent_interface: {parent_interface}")
        if link_mode:
            lines.append(f"    link_mode: {link_mode}")
        lines.append(f"    description: \"Nouveau {device_type} ajouté par l'assistant\"")
        return "\n".join(lines)

    def _build_remediation_commands(self, query: str, parent_interface: str, new_device_name: str, link_mode: str) -> list[str]:
        interface_name = parent_interface or "GigabitEthernet 0/0/10"
        port_mode = "trunk" if link_mode == "trunk" else "access"
        commands = [
            "system-view",
            f"interface {interface_name}",
            f" port link-type {port_mode}",
            " port trunk allow-pass vlan all",
            f" description Connecté vers {new_device_name}",
        ]
        if not any(word in query.lower() for word in ["trunk", "liaison", "uplink"]):
            commands[2] = " port link-type access"
        return commands


# ---------------------------------------------------------------------------
# 6. NŒUD LANGGRAPH
# ---------------------------------------------------------------------------

# CORRECTION : agent instancié UNE SEULE FOIS au niveau module
# pas à chaque appel de architect_node (meilleure performance)
_agent_instance: ArchitectAgent | None = None


def get_architect_agent(api_key: str | None = None) -> ArchitectAgent:
    """Retourne l'instance singleton de l'agent."""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = ArchitectAgent(api_key=api_key)
    return _agent_instance


def architect_node(state: dict) -> dict:
    """
    Nœud LangGraph pour l'Agent Architecte.

    STATE INPUT  : query, api_key
    STATE OUTPUT : plan, intent, strategy, step
    """
    query   = state.get("query", "")
    api_key = state.get("api_key") or os.getenv("GROQ_API_KEY")

    if not query:
        return {
            **state,
            "plan":     {},
            "intent":   "unknown",
            "strategy": "rag_only",
            "step":     "architect_error",
            "error":    "Query vide",
        }

    agent = get_architect_agent(api_key)
    plan  = agent.analyze(query)

    return {
        **state,
        "plan":     plan.to_dict(),
        "intent":   plan.intent.value,
        "strategy": plan.strategy.value,
        "step":     "architect_done",
    }


# ---------------------------------------------------------------------------
# 7. POINT D'ENTRÉE — Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    print("=" * 60)
    print("  TEST ARCHITECT AGENT v2")
    print("=" * 60)

    agent = ArchitectAgent()

    test_queries = [
        "Comment configurer un trunk entre S1 et le routeur ?",
        "Pourquoi R1 ne peut pas pinguer via OSPF ?",
        "Compare la configuration VLAN de S1 avec les best practices",
        "Montre-moi la topologie Layer 3 switching du TP",
        "Quel est l'état des interfaces de S1 en ce moment ?",
        "Explique comment fonctionne le routage inter-VLAN sur Huawei",
    ]

    for i, query in enumerate(test_queries, 1):
        print(f"\n{'─'*60}")
        print(f"  TEST {i} : {query}")
        print(f"{'─'*60}")

        plan = agent.analyze(query)
        print(f"\n{plan.to_prompt_context()}")
        print(f"\n  Entités :")
        print(f"    Devices : {plan.mentioned_devices}")
        print(f"    VLANs   : {plan.mentioned_vlans}")
        print(f"    Protos  : {plan.mentioned_protos}")
        if plan.sub_queries:
            print(f"    Sub-q   : {plan.sub_queries}")

    print(f"\n{'='*60}")
    print("  Tests terminés ✓")
    print("  → Prochaine étape : validator_agent.py")
    print(f"{'='*60}\n") 