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
    3. EXTRAIRE les entités (équipements, VLANs, protocoles)
    4. ENRICHIR la query pour maximiser la pertinence Qdrant
"""

from __future__ import annotations

import os
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from groq import Groq
sys.path.insert(0, str(Path(__file__).parent.parent))  # backend/
from logging_config import get_logger

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
        }


# ---------------------------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------------------------

# CORRECTION : modèle disponible sur Groq (vérifié juillet 2026)
GROQ_MODEL  = "meta-llama/llama-4-scout-17b-16e-instruct"
MAX_TOKENS  = 1024
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
optimization, status_check, comparison, health_report, unknown] 
   - Utilise 'health_report' si l'utilisateur demande une prédiction, une analyse de santé, un rapport de panne ou l'état de fatigue du matériel.
2. ENTITÉS : extrais les équipements, VLANs (ex: VLAN3), protocoles, interfaces
3. STRATÉGIE :
   - "rag_only"    → question théorique sur les manuels uniquement
   - "device_only" → état actuel d'un équipement (pas besoin des manuels)
   - "hybrid"      → combine manuels Qdrant + état équipement (cas fréquent)
   - "comparison"  → compare explicitement théorie vs terrain
4. ENRICHISSEMENT : reformule enriched_query pour maximiser la pertinence Qdrant

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
  "estimated_sources": integer
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
        api_key:       Optional[str] = None,
        topology_file: str           = "topology.yaml",
    ):
        # Chargement dynamique de la topologie
        self._topology_text, self._device_names = load_topology_context(topology_file)
        logger.info("Topologie chargée : %s", self._device_names or "non définie")
        # Client Groq
        resolved_key = api_key or os.getenv("GROQ_API_KEY")
        if resolved_key:
            self._client  = Groq(api_key=resolved_key)
            self._use_llm = True
            print(f"[ArchitectAgent] LLM actif — {GROQ_MODEL}")
        else:
            self._client  = None
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

        response = self._client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user",   "content": build_architect_prompt(query)},
            ],
        )

        raw_text = response.choices[0].message.content.strip()

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
    QueryIntent.STATUS_CHECK,
    QueryIntent.DIAGNOSTIC,
    QueryIntent.AUDIT,
):
            strategy = SearchStrategy.HYBRID

        # Validation des devices (seulement ceux de la topologie)
        raw_devices    = data.get("target_devices", [])
        target_devices = [
            d.upper() for d in raw_devices
            if d.upper() in self._device_names
        ] if self._device_names else [d.upper() for d in raw_devices]

        # Chunk types par défaut selon l'intention
        chunk_types = data.get("chunk_types") or self._default_chunk_types(intent)

        # Query enrichie
        enriched = data.get("enriched_query", "").strip() or self._enrich_query(query)

        return ArchitectPlan(
            original_query=query,
            intent=intent,
            intent_confidence=float(data.get("intent_confidence", 0.8)),
            intent_reasoning=data.get("intent_reasoning", ""),
            mentioned_devices=data.get("mentioned_devices", []),
            mentioned_vlans=data.get("mentioned_vlans", []),
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
            QueryIntent.STATUS_CHECK,
            QueryIntent.AUDIT,
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
            sub_queries=self._build_sub_queries(query, intent, protos, devices),
            complexity=self._estimate_complexity(query),
            estimated_sources=5 if needs_live else 3,
        )

    # ------------------------------------------------------------------
    # HELPERS — Règles
    # ------------------------------------------------------------------

    def _detect_intent(self, q: str) -> QueryIntent:
        rules = [
            (QueryIntent.HEALTH_REPORT, ["rapport", "santé", "prédiction", "prédire", "obsolescence", "health"]),
            (QueryIntent.DIAGNOSTIC,    ["pourquoi", "erreur", "problème", "down",
                                          "fail", "cannot", "not working", "issue",
                                          "ne marche pas", "ne fonctionne pas"]),
            (QueryIntent.CONFIGURATION, ["configurer", "configure", "how to",
                                          "comment", "setup", "créer", "mettre en place"]),
            (QueryIntent.AUDIT,         ["audit", "comparer", "compare", "conformit",
                                          "vérif", "check", "verify", "conforme"]),
            (QueryIntent.STATUS_CHECK,  ["état", "status", "running", "actif",
                                          "fonctionne", "up", "is it", "est-ce que"]),
            (QueryIntent.OPTIMIZATION,  ["optimis", "améliorer", "improve",
                                          "performance", "bottleneck", "goulot"]),
            (QueryIntent.EXPLANATION,   ["expliquer", "explain", "qu'est-ce",
                                          "what is", "comment fonctionne", "c'est quoi"]),
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

    def _extract_vlans(self, query: str) -> list[str]:
        return [f"VLAN{m}" for m in re.findall(r"VLAN\s*(\d+)", query, re.IGNORECASE)]

    def _extract_protocols(self, query: str) -> list[str]:
        return [
            p for p in HUAWEI_PROTOCOLS
            if re.search(rf"\b{p}\b", query, re.IGNORECASE)
        ]

    def _extract_interfaces(self, query: str) -> list[str]:
        return [m.group(0) for m in HUAWEI_IFACE_PATTERN.finditer(query)]

    def _decide_strategy(self, intent: QueryIntent, devices: list[str]) -> SearchStrategy:
        if intent in (QueryIntent.DIAGNOSTIC, QueryIntent.STATUS_CHECK):
            return SearchStrategy.HYBRID
        if intent == QueryIntent.AUDIT:
            return SearchStrategy.COMPARISON
        if intent == QueryIntent.EXPLANATION:
            return SearchStrategy.RAG_ONLY
        if devices:
            return SearchStrategy.HYBRID
        return SearchStrategy.RAG_ONLY

    def _default_chunk_types(self, intent: QueryIntent) -> list[str]:
        mapping = {
            QueryIntent.CONFIGURATION: ["code_cli", "text", "heading"],
            QueryIntent.DIAGNOSTIC:    ["code_cli", "text", "table"],
            QueryIntent.AUDIT:         ["code_cli", "text", "table", "image_desc"],
            QueryIntent.EXPLANATION:   ["text", "image_desc", "heading"],
            QueryIntent.OPTIMIZATION:  ["code_cli", "text", "image_desc"],
            QueryIntent.STATUS_CHECK:  ["code_cli", "table"],
            QueryIntent.COMPARISON:    ["code_cli", "text", "image_desc", "table"],
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
        query:   str,
        intent:  QueryIntent,
        protos:  list[str],
        devices: list[str],
    ) -> list[str]:
        sub = []
        for proto in protos[:2]:
            sub.append(f"{proto} configuration Huawei VRP")
        for dev in devices[:2]:
            sub.append(f"{dev} troubleshooting {intent.value}")
        if intent == QueryIntent.DIAGNOSTIC:
            sub.append("Huawei interface troubleshooting down")
        elif intent == QueryIntent.AUDIT:
            sub.append("Huawei best practices configuration standards")
        return sub[:4]

    def _estimate_complexity(self, query: str) -> str:
        if len(query) < 50:
            return "low"
        if len(query) > 150:
            return "high"
        return "medium"


# ---------------------------------------------------------------------------
# 6. NŒUD LANGGRAPH
# ---------------------------------------------------------------------------

# CORRECTION : agent instancié UNE SEULE FOIS au niveau module
# pas à chaque appel de architect_node (meilleure performance)
_agent_instance: Optional[ArchitectAgent] = None


def get_architect_agent(api_key: Optional[str] = None) -> ArchitectAgent:
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