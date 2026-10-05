"""
agents/validator_agent.py
==========================
Agent Validateur — Troisième agent du pipeline LangGraph.

CORRECTIONS v2 :
    1. Extraction VLAN par équipement depuis le RAG
       (association VLAN → switch mentionné dans le même paragraphe)
    2. Mapping nom PDF → équipement réel
       "S1-CORE-SWITCH" → "S1", "R1-GW-OFFICE" → "R1"
    3. Comparaison VLANs RAG vs VLANs réels par équipement
    4. Vérification hostname : nom dans le PDF vs nom réel configuré
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from backend.llm.fallback_client import LLMFallbackClient
from groq import Groq

# Imports
_BACKEND = Path(__file__).parent.parent
for _p in [str(_BACKEND), str(_BACKEND / "vectorstore"), str(_BACKEND / "agents")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from qdrant_connection import get_qdrant_client
from documentalist_agent import DocumentalistResult


# ---------------------------------------------------------------------------
# 1. TYPES
# ---------------------------------------------------------------------------

class ConfidenceLevel(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING  = "warning"
    INFO     = "info"


@dataclass
class ValidationAlert:
    severity:  AlertSeverity
    category:  str
    message:   str
    device:    str = ""
    detail:    str = ""

    def to_text(self) -> str:
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
        return (
            f"{icon.get(self.severity.value, '⚪')} "
            f"[{self.severity.value.upper()}] {self.message}"
            + (f"\n   Équipement : {self.device}" if self.device else "")
            + (f"\n   Détail     : {self.detail}"  if self.detail  else "")
        )


@dataclass
class ValidatorResult:
    final_answer:      str
    confidence:        ConfidenceLevel = ConfidenceLevel.MEDIUM
    confidence_score:  float           = 0.0
    sources_used:      list[dict]      = field(default_factory=list)
    devices_consulted: list[str]       = field(default_factory=list)
    alerts:            list[ValidationAlert] = field(default_factory=list)
    discrepancies:     list[str]       = field(default_factory=list)
    recommendations:   list[str]       = field(default_factory=list)
    validation_checks: dict            = field(default_factory=dict)
    errors:            list[str]       = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "final_answer":      self.final_answer,
            "confidence":        self.confidence.value,
            "confidence_score":  round(self.confidence_score, 2),
            "sources_used":      self.sources_used,
            "devices_consulted": self.devices_consulted,
            "alerts":            [a.to_text() for a in self.alerts],
            "discrepancies":     self.discrepancies,
            "recommendations":   self.recommendations,
            "validation_checks": self.validation_checks,
            "errors":            self.errors,
        }

    def to_display(self) -> str:
        lines = [
            f"{'='*60}",
            f"  RÉPONSE FINALE",
            f"{'='*60}",
            f"  Confiance : {self.confidence.value.upper()} ({self.confidence_score:.0%})",
            f"  Sources   : {len(self.sources_used)} chunks RAG",
            f"  Devices   : {self.devices_consulted or 'aucun'}",
            "",
            self.final_answer,
        ]
        if self.alerts:
            lines += ["", "  ALERTES :"]
            for a in self.alerts:
                lines.append(f"    {a.to_text()}")
        if self.discrepancies:
            lines += ["", "  ÉCARTS THÉORIE vs TERRAIN :"]
            for d in self.discrepancies:
                lines.append(f"    ⚠ {d}")
        if self.recommendations:
            lines += ["", "  RECOMMANDATIONS :"]
            for r in self.recommendations:
                lines.append(f"    → {r}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------------------------

GROQ_MODEL  = "openai/gpt-oss-120b"
MAX_TOKENS  = 2048
TEMPERATURE = 0.1
CPU_THRESHOLD   = 70.0
MEM_THRESHOLD   = 80.0
MIN_RAG_SCORE   = 0.35
MIN_CHUNKS_GOOD = 3


# ---------------------------------------------------------------------------
# 3. PROMPTS
# ---------------------------------------------------------------------------
VALIDATOR_SYSTEM_PROMPT = """Tu es l'Agent Validateur d'un système RAG multimodal \
spécialisé en infrastructure réseau Huawei.

Tu reçois :
1. La question originale de l'ingénieur, ainsi que son INTENTION et sa STRATÉGIE
2. Une réponse préliminaire du Documentaliste (RÉDACTION uniquement)
3. Les sources RAG et données terrain (RÉDACTION uniquement)
4. Des CONSTATS D'ÉTAT — faits factuels sur les protocoles mentionnés (configuré ou non,
   actif ou non), à utiliser librement pour répondre aux questions d'état réel
5. Les écarts détectés automatiquement — LA SEULE liste de non-conformités valide
6. Les alertes CRITICAL/WARNING détectées automatiquement — les seuls problèmes de santé valides
7. Les alertes INFO — purement informatives, jamais des problèmes à corriger

═══════════════════════════════════════════════════
MODE DE RÉPONSE — ADAPTE-TOI À L'INTENTION DE LA QUESTION :
═══════════════════════════════════════════════════

MODE "ÉTAT RÉEL" (intention = status_check, diagnostic, ou toute question factuelle
sur ce qui tourne actuellement sur un équipement, ex: "est-ce que OSPF est configuré",
"quel est l'état des interfaces") :
- Réponds FACTUELLEMENT à partir des CONSTATS D'ÉTAT et des données terrain fournies.
- Une réponse positive est parfaitement valide et attendue : "OSPF est configuré sur R1
  et fonctionne normalement (2 voisins en FULL)" est une bonne réponse même s'il n'y a
  aucun écart à signaler.
- Ne te limite PAS à la liste d'écarts pour ce mode — les constats d'état ne sont pas
  des écarts, ce sont des réponses directes à la question posée.

MODE "CONFORMITÉ / AUDIT" (intention = audit, comparison, ou question explicite sur la
conformité vs le manuel, ex: "est-ce conforme ?", "quels sont les écarts ?") :
- RÈGLE ABSOLUE — PÉRIMÈTRE STRICT : tu ne rapportes JAMAIS un écart, une non-conformité,
  ou une action corrective qui n'apparaît pas explicitement dans la liste "ÉCARTS DÉTECTÉS"
  ou dans les alertes CRITICAL/WARNING fournies plus bas.
- Le contexte RAG et terrain sert uniquement à RÉDIGER et ILLUSTRER les écarts déjà
  détectés (ex: citer le numéro de source), jamais à en découvrir de nouveaux.
- N'attribue jamais une exigence VLAN ou interface à un équipement qui n'est pas
  explicitement cité avec cette exigence dans les écarts fournis.
- S'il n'y a aucun écart ni alerte CRITICAL/WARNING, dis clairement que la configuration
  observée est conforme sur les points vérifiés, sans en inventer.

DANS LES DEUX MODES :
- Les alertes INFO restent des observations neutres, jamais des problèmes à corriger.
- Sois précis et technique — terminologie Huawei VRP correcte.
- Cite les sources : [Manuel p.X] ou [S1 SSH live].
- NE CITE JAMAIS d'URL, de lien externe, ou de document que tu n'as pas reçu dans le contexte fourni.
- Réponds en français.

FORMAT DE SORTIE — OBLIGATOIRE :
- Utilise TOUJOURS ce format Markdown, sans exception :

  **Résumé**
  - Réponds directement à la question de l'utilisateur.
- Ne décris jamais ton raisonnement.
- Ne mentionne jamais l'intention détectée.
- Ne mentionne jamais le mode ("ÉTAT RÉEL" ou "CONFORMITÉ/AUDIT").
- Ne dis jamais "La question concerne...", "Cette question relève de...", ou "Le mode est...".
- Commence immédiatement par la réponse technique.
  **Constats d'état**
  | Équipement | Constat |
  |---|---|
  | S1 | ... |
  (si aucun constat, omets entièrement cette section, ne mets pas de tableau vide)

  **Écarts détectés**
  | Équipement | Écart | Commande corrective | Source |
  |---|---|---|---|
  | S1 | Hostname incorrect (attendu S1-CORE-SWITCH, réel S1) | `sysname S1-CORE-SWITCH` | Manuel p.45 |
  (si aucun écart : écris UNIQUEMENT la phrase "Aucun écart détecté." — pas de tableau)

  **Alertes informatives**
  | Équipement | Alerte |
  |---|---|
  | S1 | 24 interfaces DOWN, non documentées |
  (si aucune alerte, omets la section)

  **Recommandations**
  - [action priorisée] (liste à puces, PAS un tableau)

- Respecte STRICTEMENT la syntaxe Markdown GFM (ligne de séparation `|---|---|`).
- N'ajoute jamais de ligne ou colonne vide pour "remplir" un tableau.
- N'utilise JAMAIS de tableau pour le Résumé ou les Recommandations."""

def build_validator_prompt(
    original_query:      str,
    intent:                 str,
    strategy:             str,
    preliminary_answer:  str,
    rag_context:         str,
    device_context:      str,
    protocol_facts_text:     str,
    actionable_alerts_text: str,
    info_alerts_text:    str,
    discrepancies_text:  str,
) -> str:
    return f"""QUESTION : "{original_query}"
INTENTION DÉTECTÉE : {intent}
STRATÉGIE : {strategy}

RÉPONSE PRÉLIMINAIRE (contexte de rédaction uniquement) :
{preliminary_answer}

SOURCES RAG (contexte de rédaction uniquement, ne pas en tirer de nouveaux écarts) :
{rag_context[:2000] if rag_context else "Aucune."}

DONNÉES TERRAIN (contexte de rédaction uniquement) :
{device_context[:1500] if device_context else "Aucune."}

═══════════════════════════════════════════════════════
CONSTATS D'ÉTAT (réponses factuelles aux questions sur l'état réel — à utiliser
librement en MODE "ÉTAT RÉEL", à ignorer en MODE "CONFORMITÉ/AUDIT") :
═══════════════════════════════════════════════════════
{protocol_facts_text if protocol_facts_text else "Aucun constat d'état généré pour cette question."}

═══════════════════════════════════════════════════════
SEULE SOURCE DE VÉRITÉ POUR LES PROBLÈMES À RAPPORTER (mode conformité/audit) :
═══════════════════════════════════════════════════════

ALERTES ACTIONNABLES (CRITICAL/WARNING) :
{actionable_alerts_text if actionable_alerts_text else "Aucune."}

ÉCARTS DÉTECTÉS (théorie vs terrain) :
{discrepancies_text if discrepancies_text else "Aucun écart détecté."}

ALERTES INFORMATIVES (à mentionner sans les traiter comme des problèmes) :
{info_alerts_text if info_alerts_text else "Aucune."}

Détermine d'abord si la question relève du MODE "ÉTAT RÉEL" ou du MODE "CONFORMITÉ/AUDIT"
selon l'intention détectée, puis rédige la réponse finale en conséquence."""
# ---------------------------------------------------------------------------
# 4. EXTRACTION VLAN PAR ÉQUIPEMENT
# ---------------------------------------------------------------------------

# Mapping noms PDF → noms réels des équipements
# Le PDF peut utiliser S1-CORE-SWITCH mais la topologie utilise S1
DEVICE_NAME_ALIASES: dict[str, str] = {
    "s1-core-switch": "S1",
    "s1-core":        "S1",
    "s2-core-switch": "S2",
    "s2-core":        "S2",
    "r1-gw-office":   "R1",
    "r1-gw":          "R1",
    "r2-gw":          "R2",
    "lsw1":           "S1",   # eNSP utilise LSW1 pour les switches Layer 2
    "lsw2":           "S2",
}


def normalize_device_name(raw_name: str) -> str:
    """
    Normalise le nom d'un équipement depuis le PDF vers le nom réel.

    Exemples :
        "S1-CORE-SWITCH" → "S1"
        "LSW1"           → "S1"
        "R1-GW-OFFICE"   → "R1"
        "S1"             → "S1"  (déjà normalisé)
    """
    key = raw_name.lower().strip()
    if key in DEVICE_NAME_ALIASES:
        return DEVICE_NAME_ALIASES[key]

    # Pattern générique : S1, S2, R1, R2, etc.
    m = re.match(r"^([sr]\d+)", key, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    return raw_name.upper()


def extract_vlans_per_device(rag_text: str) -> dict[str, set[int]]:
    """
    Extrait les VLANs associés à chaque équipement depuis le texte RAG.

    STRATÉGIE :
        1. Découper le texte en paragraphes/sections
        2. Pour chaque section, identifier le ou les équipements mentionnés
        3. Extraire les VLANs de cette section et les associer à ces équipements
        4. Normaliser les noms d'équipements

    Formats gérés :
        "Le switch S1 doit avoir VLAN 10, 20 et 99"
        "S1-CORE-SWITCH : vlan batch 10 20 99"
        "• VLAN 10 : Ingénierie" (dans section Switch S1)
        "S2 : VLAN 30, 40"

    Returns:
        {"S1": {10, 20, 99}, "S2": {30, 40}, "ALL": {10, 20, 30, 40, 99}}
        "ALL" contient tous les VLANs mentionnés sans association spécifique
    """
    device_vlans: dict[str, set[int]] = {}

    # Pattern de détection des noms d'équipements
    device_pattern = re.compile(
        r"\b((?:S|R|LSW|SW|GW)\d+(?:[-_][A-Z0-9]+)*)\b",
        re.IGNORECASE
    )
    
    # Pattern de détection des VLANs
    def extract_vlans_from_text(text: str) -> set[int]:
        """Extrait tous les IDs VLAN d'un texte."""
        vids = set()

        # Pattern 1 : "vlan batch 10 20 99" → capture la séquence complète
        for m in re.finditer(r"\bvlan\s+batch\s+([\d\s]+)", text, re.IGNORECASE):
            for v in m.group(1).split():
                try:
                    vid = int(v)
                    if 1 < vid < 4095:
                        vids.add(vid)
                except ValueError:
                    pass

        # Pattern 2 : "VLAN 10", "VLAN10", "vlan 10"
        for m in re.finditer(r"\bvlan\s*(\d{1,4})\b", text, re.IGNORECASE):
            vid = int(m.group(1))
            if 1 < vid < 4095:
                vids.add(vid)

        # Pattern 3 : "Vlanif 99" dans les adresses IP
        for m in re.finditer(r"\bvlanif\s*(\d{1,4})\b", text, re.IGNORECASE):
            vid = int(m.group(1))
            if 1 < vid < 4095:
                vids.add(vid)

        # Pattern 4 : listes séparées par virgules/et après "VLAN"
        # "VLANs 10, 20 et 99"
        for m in re.finditer(
            r"\bvlans?\s+([\d,\s]+(?:et\s+\d+)?)",
            text, re.IGNORECASE
        ):
            for v in re.findall(r"\d+", m.group(1)):
                vid = int(v)
                if 1 < vid < 4095:
                    vids.add(vid)

        return vids

    # ── Analyse par paragraphe ──────────────────────────────────────────
    # Découper en paragraphes ou sections
    lines = rag_text.split("\n")

    active_devices = set()
    lines_since_device = 999

    WINDOW_SIZE = 10
    for i, line in enumerate(lines):

        stripped = line.strip()

        if not stripped:
            lines_since_device += 1
            continue


    # Détection équipement
        devices = set()

        for m in device_pattern.finditer(stripped):
            devices.add(
            normalize_device_name(m.group(1))
        )


        if devices:
            active_devices = devices
            lines_since_device = 0
        else:
            lines_since_device += 1


    # Extraction VLAN ligne courante
        vlans = extract_vlans_from_text(stripped)

        if not vlans:
            continue

        
    # Association avec contexte actif
        if (
        active_devices
        and lines_since_device <= WINDOW_SIZE
            ):
            for dev in active_devices:
                if re.match(r"^R\d+$", dev):
                    continue    
                device_vlans.setdefault(
                dev,
                set()
            ).update(vlans)
        
    return device_vlans

def extract_expected_static_routes(rag_text: str) -> dict[str, list[dict]]:
    """
    Extrait les routes statiques attendues par équipement depuis le manuel.

    Formats gérés :
        "Routage R1 : ... ip route-static 10.10.10.0 255.255.255.0 192.168.56.11"
        "ip route-static <réseau> <masque> <next-hop>"

    STRATÉGIE : même logique de proximité que extract_vlans_per_device —
    associe la route au device mentionné dans les lignes précédentes.
    """
    device_routes: dict[str, list[dict]] = {}
    device_pattern = re.compile(r"\b((?:S|R|LSW|SW|GW)\d+(?:[-_][A-Z0-9]+)*)\b", re.IGNORECASE)
    route_pattern = re.compile(
        r"ip\s+route-static\s+(\d{1,3}(?:\.\d{1,3}){3})\s+"
        r"(\d{1,3}(?:\.\d{1,3}){3})\s+"
        r"(\d{1,3}(?:\.\d{1,3}){3})",
        re.IGNORECASE
    )

    active_devices, lines_since_device, WINDOW_SIZE = set(), 999, 10
    for line in rag_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines_since_device += 1
            continue

        devices_on_line = {normalize_device_name(m.group(1)) for m in device_pattern.finditer(stripped)}
        if devices_on_line:
            active_devices, lines_since_device = devices_on_line, 0
        else:
            lines_since_device += 1

        for m in route_pattern.finditer(stripped):
            route = {"network": m.group(1), "mask": m.group(2), "next_hop": m.group(3)}
            if active_devices and lines_since_device <= WINDOW_SIZE:
                for dev in active_devices:
                    device_routes.setdefault(dev, []).append(route)

    return device_routes

def extract_expected_hostnames(rag_text: str) -> dict[str, str]:
    """
    Extrait les hostnames attendus depuis le RAG.

    Formats gérés :
        "sysname S1-CORE-SWITCH"
        "Le switch doit être nommé : S1-CORE-SWITCH"
        "hostname S1-CORE-SWITCH"
        "nommé S1-CORE-SWITCH"

    Returns:
        {"S1": "S1-CORE-SWITCH", "R1": "R1-GW-OFFICE"}
        Clé = nom normalisé, Valeur = hostname exact attendu
    """
    expected = {}
    patterns = [
        r"sysname\s+([\w\-]+)",
        r"hostname\s+([\w\-]+)",
        r"nommé\s*[:\-]?\s*([\w\-]+)",
        r"named?\s*[:\-]?\s*([\w\-]+)",
        r"nom\s+(?:doit\s+être|est)\s*[:\-]?\s*([\w\-]+)",
        r":\s*\*\*([\w\-]+)\*\*",      # format markdown **S1-CORE-SWITCH**
        r":\s+`([\w\-]+)`",             # format code `S1-CORE-SWITCH`
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, rag_text, re.IGNORECASE):
            hostname  = m.group(1).strip()
            # Normaliser pour trouver l'équipement correspondant
            normalized = normalize_device_name(hostname)
            if re.match(r"^[SR]\d+$", normalized):
                expected[normalized] = hostname

    return expected
IFACE_PATTERN = re.compile(
    r"\b(GigabitEthernet|GE|FastEthernet|FE|Ethernet|Eth|Vlanif|Eth-Trunk)\s*([\d/\.]+)\b",
    re.IGNORECASE
)

IFACE_PREFIX_EXPANSION = {"ge": "GigabitEthernet", "fe": "FastEthernet", "eth": "Ethernet"}


def normalize_iface_name(prefix: str, numbers: str) -> str:
    key = prefix.lower().replace("-", "")
    full_prefix = IFACE_PREFIX_EXPANSION.get(key, prefix)
    return f"{full_prefix}{numbers}"


def extract_expected_interfaces(rag_text: str) -> dict[str, set[str]]:
    """Même logique de proximité que extract_vlans_per_device : associe
    une interface citée dans le manuel au device mentionné juste avant.
    Priorité à la co-occurrence sur la même ligne (ex: 'R1 (GE 0/0/0)')."""
    device_ifaces: dict[str, set[str]] = {}
    device_pattern = re.compile(r"\b((?:S|R|LSW|SW|GW)\d+(?:[-_][A-Z0-9]+)*)\b", re.IGNORECASE)
    active_devices, lines_since_device, WINDOW_SIZE = set(), 999, 3   # réduit de 10 à 3

    for line in rag_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines_since_device += 1
            continue

        devices_on_line = {normalize_device_name(m.group(1)) for m in device_pattern.finditer(stripped)}
        ifaces_on_line  = {normalize_iface_name(m.group(1), m.group(2)) for m in IFACE_PATTERN.finditer(stripped)}

        # PRIORITÉ : device ET interface sur la MÊME ligne → association certaine
        if devices_on_line and ifaces_on_line:
            for dev in devices_on_line:
                device_ifaces.setdefault(dev, set()).update(ifaces_on_line)
            active_devices, lines_since_device = devices_on_line, 0
            continue

        if devices_on_line:
            active_devices, lines_since_device = devices_on_line, 0
        else:
            lines_since_device += 1

        if ifaces_on_line and active_devices and lines_since_device <= WINDOW_SIZE:
            for dev in active_devices:
                device_ifaces.setdefault(dev, set()).update(ifaces_on_line)

    return device_ifaces

# ---------------------------------------------------------------------------
# 5. VÉRIFICATIONS AUTOMATIQUES
# ---------------------------------------------------------------------------

class AutoChecker:
    """Vérifications automatiques sans LLM."""

    def check_rag_quality(
        self,
        doc_result: DocumentalistResult,
    ) -> tuple[float, list[ValidationAlert]]:
        """Évalue la qualité des chunks RAG."""
        alerts     = []
        all_chunks = (
            doc_result.rag_text_results +
            doc_result.rag_image_results +
            doc_result.rag_table_results
        )

        if not all_chunks:
            alerts.append(ValidationAlert(
                severity=AlertSeverity.WARNING,
                category="missing_data",
                message="Aucun chunk RAG trouvé dans Qdrant",
                detail="Indexez d'abord le PDF avec ingest_pipeline.py",
            ))
            return 0.0, alerts

        avg_score = sum(r.score for r in all_chunks) / len(all_chunks)

        if len(all_chunks) < 2:
            alerts.append(ValidationAlert(
                severity=AlertSeverity.INFO,
                category="missing_data",
                message=f"Seulement {len(all_chunks)} chunk(s) RAG trouvé(s)",
            ))
        if avg_score < MIN_RAG_SCORE:
            alerts.append(ValidationAlert(
                severity=AlertSeverity.WARNING,
                category="missing_data",
                message=f"Pertinence RAG faible (score moyen : {avg_score:.2f})",
            ))

        quality = min(1.0, avg_score * (len(all_chunks) / MIN_CHUNKS_GOOD))
        return quality, alerts

    def check_device_health(
        self,
        doc_result: DocumentalistResult,
    ) -> list[ValidationAlert]:
        """Vérifie CPU, interfaces down, OSPF instable."""
        alerts = []
        full_rag = "\n".join(
            r.content for r in
            doc_result.rag_text_results + doc_result.rag_table_results + doc_result.rag_image_results
        )

        if doc_result.manual_reference_text_structured:
            full_rag = doc_result.manual_reference_text_structured
        elif doc_result.manual_reference_text:
            full_rag += "\n" + doc_result.manual_reference_text
        else:
            full_rag = "\n".join(
        r.content for r in
        doc_result.rag_text_results + doc_result.rag_table_results + doc_result.rag_image_results
    )
        expected_active = extract_expected_interfaces(full_rag)
        has_reference = bool(full_rag.strip())
        for device_name, data in doc_result.device_data.items():
            if not data.ospf_peers and "ospf" in doc_result.rag_context.lower():
                alerts.append(ValidationAlert(
                severity=AlertSeverity.WARNING,
                category="missing_data",
                message=f"Aucune donnée OSPF collectée pour {device_name}",
                device=device_name,
                detail="Impossible de confirmer/infirmer l'état OSPF — vérifier la collecte terrain",
            ))

            if data.cpu_usage and data.cpu_usage > CPU_THRESHOLD:
                alerts.append(ValidationAlert(
                    severity=AlertSeverity.WARNING,
                    category="performance",
                    message=f"CPU élevé sur {device_name} : {data.cpu_usage:.1f}%",
                    device=device_name,
                ))

            down = [
                i["name"] for i in data.interfaces
                if "down" in i.get("phy_state", "").lower()
                and "NULL"     not in i.get("name", "")
                and "LoopBack" not in i.get("name", "")
            ]
            if down:
                expected = expected_active.get(device_name, set())

                if not expected and has_reference:
                # Filet de sécurité : si l'extraction n'a rien trouvé pour ce
                # device alors qu'on a du texte de référence, on ne veut pas
                # masquer un vrai problème → on garde l'ancien comportement.
                    alerts.append(ValidationAlert(
                    severity=AlertSeverity.CRITICAL,
                    category="anomaly",
                    message=f"Interface(s) DOWN sur {device_name} (aucune référence détectée — vérifier par précaution)",
                    device=device_name,
                    detail=f"Interfaces : {', '.join(down[:5])}",
                ))
                else:
                    critical_down = [i for i in down if i in expected]
                    unused_down   = [i for i in down if i not in expected]

                    if critical_down:
                        alerts.append(ValidationAlert(
                        severity=AlertSeverity.CRITICAL,
                        category="anomaly",
                        message=f"Interface(s) DOWN sur {device_name} (documentées comme actives)",
                        device=device_name,
                        detail=f"Interfaces : {', '.join(critical_down)}",
                    ))
                    if unused_down:
                        alerts.append(ValidationAlert(
                        severity=AlertSeverity.INFO,
                        category="info",
                        message=f"{len(unused_down)} interface(s) DOWN sur {device_name} — non documentées, probablement inutilisées",
                        device=device_name,
                        detail=f"Interfaces : {', '.join(unused_down[:5])}",
                    ))

            bad_peers = [p for p in data.ospf_peers if "full" not in p.get("state", "").lower()]
            if bad_peers:
                alerts.append(ValidationAlert(
                severity=AlertSeverity.CRITICAL,
                category="anomaly",
                message=f"OSPF instable sur {device_name}",
                device=device_name,
                detail=f"Voisins non-FULL : {[p.get('router_id') for p in bad_peers]}",
            ))

        return alerts
    def check_discrepancies(
        self,
        doc_result: DocumentalistResult,
    ) -> list[str]:
        """
        Détecte les écarts entre théorie (RAG) et terrain (SSH).

        VÉRIFICATIONS :
        1. VLANs manquants par équipement
           (extrait les VLANs attendus par switch depuis le RAG)
        2. Hostname incorrect
           (compare le nom attendu dans le PDF avec le nom réel)
        3. Interfaces down non attendues
        """
        discrepancies = []

        if (not doc_result.rag_text_results
            and not doc_result.rag_table_results
            and not doc_result.manual_reference_text):
            return discrepancies

        # Texte RAG complet
        full_rag = "\n".join(
            r.content for r in
            doc_result.rag_text_results +
            doc_result.rag_table_results +
            doc_result.rag_image_results
        )
        if doc_result.manual_reference_text_structured:
            full_rag = doc_result.manual_reference_text_structured
        elif doc_result.manual_reference_text:
            full_rag += "\n" + doc_result.manual_reference_text
        else:
            full_rag = "\n".join(
        r.content for r in
        doc_result.rag_text_results + doc_result.rag_table_results + doc_result.rag_image_results
    )
        # ── 1. VLANs par équipement ─────────────────────────────────────
        rag_vlans_per_device = extract_vlans_per_device(full_rag)

        print(f"\n[Validator] VLANs extraits du RAG par équipement :")
        for dev, vids in rag_vlans_per_device.items():
            if vids:
                print(f"  {dev} → {sorted(vids)}")

        for device_name, data in doc_result.device_data.items():
    # VLANs configurés sur l'équipement réel (BRUT, avant filtrage)
            raw_real_vlans = {int(v.get("id", 0)) for v in data.vlans if v.get("id")}
            real_vlans = raw_real_vlans - {1}   # exclure VLAN 1 (natif)

            expected_vlans = rag_vlans_per_device.get(
    device_name,
    set()
)
            expected_vlans -= {1}

            print(f"\n[Validator] {device_name} :")
            print(f"  VLANs bruts collectés (SSH)   : {sorted(raw_real_vlans)}")   # ← ajouté
            print(f"  VLANs attendus (RAG)          : {sorted(expected_vlans)}")
            print(f"  VLANs réels hors natif (SSH)  : {sorted(real_vlans)}")
            if not expected_vlans:
                continue

            # Les routeurs n'héritent jamais des VLAN globaux
            if re.match(r"^R\d+$", device_name):

                if device_name not in rag_vlans_per_device:

                    print(
            f"[Validator] {device_name} ignoré pour VLAN"
        )

                    continue

            # VLANs manquants sur l'équipement
            missing = expected_vlans - real_vlans
            if missing:
                missing_list = sorted(missing)
                discrepancies.append(
                    f"{device_name} : VLANs {missing_list} requis par le manuel "
                    f"mais ABSENTS de la configuration réelle.\n"
                    f"   Commande corrective : "
                    f"[{device_name}] vlan batch {' '.join(str(v) for v in missing_list)}"
                )

            # VLANs en trop (non documentés)
            extra = real_vlans - expected_vlans
            extra -= {1}
            if extra:
                discrepancies.append(
                    f"{device_name} : VLANs {sorted(extra)} présents sur "
                    f"l'équipement mais NON documentés dans le manuel."
                )

        # ── 2. Vérification des hostnames ────────────────────────────────
        expected_hostnames = extract_expected_hostnames(full_rag)
        print(f"\n[Validator] Hostnames attendus (RAG) : {expected_hostnames}")

        for device_name, data in doc_result.device_data.items():
            # Hostname réel depuis la config ou version
            real_hostname = (
                data.version_info.get("hostname", "") or
                data.device_name
            )

            # Cherche dans la config brute
            config_text = data.raw_outputs.get("display current-configuration", "")
            sysname_match = re.search(r"sysname\s+([\w\-]+)", config_text, re.IGNORECASE)
            if sysname_match:
                real_hostname = sysname_match.group(1).strip()

            expected_hostname = expected_hostnames.get(device_name, "")

            print(f"[Validator] {device_name} : "
                  f"hostname réel='{real_hostname}' "
                  f"attendu='{expected_hostname}'")

            if expected_hostname and real_hostname:
                if real_hostname.upper() != expected_hostname.upper():
                    discrepancies.append(
                        f"{device_name} : Hostname incorrect.\n"
                        f"   Attendu  (manuel) : '{expected_hostname}'\n"
                        f"   Réel     (SSH)    : '{real_hostname}'\n"
                        f"   Commande corrective : "
                        f"[{real_hostname}] sysname {expected_hostname}"
                    )

        # ── 3. Interfaces down non attendues ─────────────────────────────
        for device_name, data in doc_result.device_data.items():
            down = [
                i["name"] for i in data.interfaces
                if "down" in i.get("phy_state", "").lower()
                and "NULL"     not in i.get("name", "")
                and "LoopBack" not in i.get("name", "")
            ]
            if down:
                # Vérifier si ces interfaces sont mentionnées dans le manuel
                for iface in down:
                    if iface.lower() in full_rag.lower():
                        discrepancies.append(
                            f"{device_name} : Interface {iface} est DOWN "
                            f"alors que le manuel la mentionne comme active."
                        )
        # ── 4. Routes statiques attendues ─────────────────────────────
        expected_routes = extract_expected_static_routes(full_rag)
        print(f"\n[Validator] Routes statiques attendues (RAG) : {expected_routes}")

        for device_name, routes in expected_routes.items():
            data = doc_result.device_data.get(device_name)
            if not data:
                continue
            routing_table_text = data.raw_outputs.get("display ip routing-table", "")
            for route in routes:
                if route["network"] not in routing_table_text:
                    discrepancies.append(
                        f"{device_name} : Route statique {route['network']}/{route['mask']} "
                        f"vers {route['next_hop']} requise par le manuel mais ABSENTE.\n"
                        f"   Commande corrective : "
                        f"[{device_name}] ip route-static {route['network']} "
                        f"{route['mask']} {route['next_hop']}"
                    )

        return discrepancies

    def compute_confidence(
        self,
        rag_quality:   float,
        has_devices:   bool,
        alerts:        list[ValidationAlert],
        discrepancies: list[str],
    ) -> tuple[float, ConfidenceLevel]:
        score = rag_quality
        if has_devices:
            score += 0.2
        for a in alerts:
            if a.severity == AlertSeverity.CRITICAL:
                score -= 0.1
            elif a.severity == AlertSeverity.WARNING:
                score -= 0.05
        score -= len(discrepancies) * 0.05
        score  = max(0.0, min(1.0, score))
        level  = (ConfidenceLevel.HIGH   if score >= 0.75 else
                  ConfidenceLevel.MEDIUM if score >= 0.45 else
                  ConfidenceLevel.LOW)
        return score, level
    def check_protocol_status(
    self,
    doc_result: DocumentalistResult,
) -> list[str]:
        """
    Produit des CONSTATS FACTUELS (positifs ou négatifs) sur les protocoles
    explicitement mentionnés dans la question, à partir des données terrain.
    """
        print(f"[DEBUG] mentioned_protos reçus par Validator : {doc_result.mentioned_protos}")
        facts = []
        protos = {p.upper() for p in doc_result.mentioned_protos}
        if not protos:
            return facts

    # Regex de détection du bloc de config par protocole (config active)
        CONFIG_MARKERS = {
        "OSPF":  r"^ospf\s+(\d+)",
        "BGP":   r"^bgp\s+(\d+)",
        "VRRP":  r"vrrp\s+vrid\s+(\d+)",
        "STP":   r"stp\s+(?:enable|mode)",
        "RSTP":  r"stp\s+mode\s+rstp",
        "MSTP":  r"stp\s+mode\s+mstp",
        "LLDP":  r"lldp\s+enable",
        "RIP":   r"^rip\s+(\d+)",
        "DHCP":  r"dhcp\s+enable",
        "ACL":   r"^acl\s+(?:number\s+)?(\d+)",
        "VRRP":  r"vrrp\s+vrid",
    }

        for device_name, data in doc_result.device_data.items():
            config_text = data.raw_outputs.get("display current-configuration", "")

            for proto in protos:

                if proto == "OSPF":
                    m = re.search(r"^ospf\s+(\d+)", config_text, re.IGNORECASE | re.MULTILINE)
                    if m:
                        detail = self._peer_summary(data.ospf_peers, "état")
                        facts.append(f"{device_name} : OSPF est configuré (processus {m.group(1)}) — {detail}.")
                    else:
                        facts.append(f"{device_name} : OSPF n'est PAS configuré.")

                elif proto == "BGP":
                    m = re.search(r"^bgp\s+(\d+)", config_text, re.IGNORECASE | re.MULTILINE)
                    if m:
                        detail = self._peer_summary(data.bgp_peers, "état", ok_state="established")
                        facts.append(f"{device_name} : BGP est configuré (AS {m.group(1)}) — {detail}.")
                    else:
                        facts.append(f"{device_name} : BGP n'est PAS configuré.")

                elif proto == "VRRP":
                    if data.vrrp_groups:
                        resume = ", ".join(
                        f"groupe {g['vrid']} sur {g['interface']} en état {g['state']}"
                        for g in data.vrrp_groups
                    )
                        facts.append(f"{device_name} : VRRP est configuré — {resume}.")
                    elif re.search(r"vrrp\s+vrid", config_text, re.IGNORECASE):
                        facts.append(f"{device_name} : VRRP est configuré mais aucun groupe actif détecté (vérifier la collecte).")
                    else:
                        facts.append(f"{device_name} : VRRP n'est PAS configuré.")

                elif proto in ("STP", "RSTP", "MSTP"):
                    if data.stp_ports:
                        forwarding = [p for p in data.stp_ports if p["state"] == "FORWARDING"]
                        facts.append(
                        f"{device_name} : {proto} est actif — {len(data.stp_ports)} port(s) suivi(s), "
                        f"{len(forwarding)} en FORWARDING."
                    )
                    else:
                        facts.append(f"{device_name} : Aucune donnée {proto} disponible (commande non collectée ou protocole inactif).")

                elif proto == "LLDP":
                    if data.lldp_neighbors:
                        facts.append(
                        f"{device_name} : LLDP est actif — {len(data.lldp_neighbors)} voisin(s) découvert(s) "
                        f"({', '.join(n['neighbor_device'] for n in data.lldp_neighbors[:3])})."
                    )
                    else:
                        facts.append(f"{device_name} : Aucun voisin LLDP détecté (protocole possiblement désactivé).")

                elif proto in CONFIG_MARKERS:
                # Fallback générique : présence/absence dans la config, sans détail de voisinage
                    pattern = CONFIG_MARKERS[proto]
                    if re.search(pattern, config_text, re.IGNORECASE | re.MULTILINE):
                        facts.append(f"{device_name} : {proto} apparaît configuré dans la configuration active (vérification détaillée non automatisée).")
                    else:
                        facts.append(f"{device_name} : {proto} n'apparaît PAS dans la configuration active.")

                else:
                # Protocole mentionné mais totalement non instrumenté (ex: MPLS, QOS, NAT...)
                    facts.append(
                    f"{device_name} : constat automatique non disponible pour {proto} — "
                    f"consultez la configuration brute ou le manuel pour ce protocole."
                )

        return facts


    def _peer_summary(self, peers: list[dict], key_field: str, ok_state: str = "full") -> str:
        """Résume un état de voisinage (OSPF/BGP) — factorisé pour éviter la duplication."""
        if not peers:
            return "aucun voisin détecté actuellement"
        ok    = [p for p in peers if ok_state in p.get("state", "").lower()]
        not_ok = [p for p in peers if ok_state not in p.get("state", "").lower()]
        detail = f"{len(ok)} voisin(s) en état {ok_state.upper()}"
        if not_ok:
            detail += f", {len(not_ok)} voisin(s) hors {ok_state.upper()}"
        return detail

# ---------------------------------------------------------------------------
# 6. AGENT VALIDATEUR
# ---------------------------------------------------------------------------

class ValidatorAgent:
    """
    Agent Validateur — vérifie et finalise la réponse.

    USAGE :
        agent  = ValidatorAgent()
        result = agent.validate(query, doc_result)
        print(result.to_display())
    """

    def __init__(self, api_key: Optional[str] = None):
        resolved_key = api_key or os.getenv("GROQ_API_KEY")
        if resolved_key:
            self._llm = LLMFallbackClient(groq_api_key=resolved_key)  # adapte le nom de variable si différent
            self._use_llm = True
            print(f"[ValidatorAgent] LLM actif — {GROQ_MODEL}")
        else:
            self._llm     = None
            self._use_llm = False
            print("[ValidatorAgent] Mode sans LLM")

        self._checker = AutoChecker()

        try:
            self._qdrant = get_qdrant_client()
        except Exception:
            self._qdrant = None

    def validate(
        self,
        query:      str,
        doc_result: DocumentalistResult,
        intent:     str = "",
        strategy:   str = "",

    ) -> ValidatorResult:
        """Valide et finalise la réponse du Documentaliste."""
        print(f"\n[ValidatorAgent] Validation : '{query[:50]}'")

        # Étape 1 : vérifications automatiques
        print("  [1/3] Vérifications automatiques...")
        rag_quality, rag_alerts = self._checker.check_rag_quality(doc_result)
        device_alerts           = self._checker.check_device_health(doc_result)
        discrepancies           = self._checker.check_discrepancies(doc_result)
        protocol_facts          = self._checker.check_protocol_status(doc_result)   # ← AJOUT
        all_alerts              = rag_alerts + device_alerts

        print(f"    ✓ {len(all_alerts)} alerte(s) | "
              f"{len(discrepancies)} écart(s) | "
              f"qualité RAG={rag_quality:.2f}")

        # Étape 2 : confiance
        print("  [2/3] Calcul confiance...")
        confidence_score, confidence_level = self._checker.compute_confidence(
            rag_quality=rag_quality,
            has_devices=len(doc_result.device_data) > 0,
            alerts=all_alerts,
            discrepancies=discrepancies,
        )
        print(f"    ✓ {confidence_level.value.upper()} ({confidence_score:.0%})")

        # Étape 3 : finalisation LLM
        print("  [3/3] Finalisation LLM...")

        # CORRECTION : on sépare les alertes actionnables (CRITICAL/WARNING)
        # des alertes purement informatives (INFO). Le LLM ne doit jamais
        # traiter une alerte INFO comme un problème à corriger — sans cette
        # séparation, il mélangeait les deux et proposait des commandes
        # correctives pour des observations neutres (ex: interfaces down
        # non documentées, donc probablement inutilisées).
        actionable_alerts = [a for a in all_alerts if a.severity != AlertSeverity.INFO]
        info_alerts       = [a for a in all_alerts if a.severity == AlertSeverity.INFO]

        actionable_alerts_text = "\n".join(a.to_text() for a in actionable_alerts)
        info_alerts_text       = "\n".join(a.to_text() for a in info_alerts)
        discrepancies_text     = "\n".join(f"⚠ {d}" for d in discrepancies)
        protocol_facts_text    = "\n".join(f". {f}" for f in protocol_facts)   # ← AJOUT

        if self._use_llm and doc_result.full_context:
            final_answer = self._finalize_with_llm(
                query, doc_result, intent, strategy,protocol_facts_text, actionable_alerts_text, info_alerts_text, discrepancies_text
            )
        else:
            final_answer = self._finalize_without_llm(
                query, doc_result, all_alerts, discrepancies, protocol_facts
            )

        recommendations = self._build_recommendations(
            all_alerts, discrepancies, confidence_level
        )
        sources = self._extract_sources(doc_result)

        result = ValidatorResult(
            final_answer=final_answer,
            confidence=confidence_level,
            confidence_score=confidence_score,
            sources_used=sources,
            devices_consulted=list(doc_result.device_data.keys()),
            alerts=all_alerts,
            discrepancies=discrepancies,
            recommendations=recommendations,
            validation_checks={
                "rag_quality":     round(rag_quality, 2),
                "rag_chunks":      doc_result.total_rag_chunks,
                "devices_checked": len(doc_result.device_data),
                "alerts_count":    len(all_alerts),
                "discrepancies":   len(discrepancies),
            },
        )

        print(f"\n[ValidatorAgent] ✓")
        print(f"  Confiance    : {confidence_level.value} ({confidence_score:.0%})")
        print(f"  Alertes      : {len(all_alerts)}")
        print(f"  Écarts       : {len(discrepancies)}")
        return result
    
    def _finalize_with_llm(
        self,
        query:                  str,
        doc_result:             DocumentalistResult,
        intent:                 str,
        strategy:               str,
        protocol_facts_text:    str,
        actionable_alerts_text: str,
        info_alerts_text:       str,
        discrepancies_text:     str,
    ) -> str:
        prompt = build_validator_prompt(
            original_query=query,
            intent=intent,
            strategy=strategy,
            preliminary_answer=doc_result.full_context,
            rag_context=doc_result.rag_context[:2000],
            device_context=doc_result.device_context[:4000],
            actionable_alerts_text=actionable_alerts_text,
            info_alerts_text=info_alerts_text,
            discrepancies_text=discrepancies_text,
            protocol_facts_text=protocol_facts_text,
        )
        try:
            llm_response = self._llm.complete(
        system_prompt=VALIDATOR_SYSTEM_PROMPT,
        user_prompt=prompt,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
            return llm_response.content.strip()
        except Exception as e:
            print(f"  ⚠ LLM échoué ({e})")
            return doc_result.full_context or "Erreur lors de la génération de la réponse."
    def _finalize_without_llm(
        self,
        query:        str,
        doc_result:   DocumentalistResult,
        alerts:       list[ValidationAlert],
        discrepancies: list[str],
        protocol_facts=None,
    ) -> str:
        parts = [f"RÉPONSE À : {query}\n"]
        if doc_result.full_context:
            parts.append(doc_result.full_context)
        if alerts:
            parts += ["\nALERTES :"] + [a.to_text() for a in alerts]
        if protocol_facts:
            parts += ["\nCONSTATS D'ÉTAT :"] + [f"  . {f}" for f in protocol_facts]
        if discrepancies:
            parts += ["\nÉCARTS THÉORIE/TERRAIN :"] + [f"  ⚠ {d}" for d in discrepancies]
        return "\n".join(parts)

    def _build_recommendations(
        self,
        alerts:        list[ValidationAlert],
        discrepancies: list[str],
        confidence:    ConfidenceLevel,
    ) -> list[str]:
        recs = []
        for a in alerts:
            if a.severity == AlertSeverity.CRITICAL:
                if "DOWN" in a.message:
                    recs.append(
                        f"Vérifier le câblage/config de {a.device} — "
                        f"interfaces DOWN détectées"
                    )
                if "OSPF" in a.message:
                    recs.append(f"Corriger OSPF sur {a.device} — voisinage non établi")
            if "CPU" in a.message:
                recs.append(f"Surveiller CPU sur {a.device}")
        if any("VLAN" in d for d in discrepancies):
            recs.append(
                "Configurer les VLANs manquants avec 'vlan batch X Y Z' "
                "et vérifier les interfaces en mode access/trunk"
            )
        if any("Hostname" in d for d in discrepancies):
            recs.append(
                "Corriger les hostnames avec la commande 'sysname <NOM_ATTENDU>'"
            )
        if confidence == ConfidenceLevel.LOW:
            recs.append("Indexer davantage de documentation dans Qdrant")
        return recs

    def _extract_sources(self, doc_result: DocumentalistResult) -> list[dict]:
        sources = []
        for r in (doc_result.rag_text_results +
                  doc_result.rag_image_results +
                  doc_result.rag_table_results):
            sources.append({
                "chunk_id":    r.chunk_id,
                "type":        r.chunk_type,
                "page":        r.page_number,
                "score":       round(r.score, 3),
                "source_file": r.source_file,
                "section":     r.section_title,
                "preview":     r.preview(60),
            })
        for name, data in doc_result.device_data.items():
            sources.append({
                "chunk_id":    f"device_{name}",
                "type":        "device_data",
                "device":      name,
                "access_mode": data.access_mode,
                "collected_at": data.collected_at,
            })
        return sources


# ---------------------------------------------------------------------------
# 7. NŒUD LANGGRAPH
# ---------------------------------------------------------------------------

_validator_instance: Optional[ValidatorAgent] = None


def get_validator_agent(api_key: Optional[str] = None) -> ValidatorAgent:
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = ValidatorAgent(api_key=api_key)
    return _validator_instance


def validator_node(state: dict) -> dict:
    print("###### VALIDATOR_NODE VERSION TEST 123 ######")
    query   = state.get("query", "")
    intent   = state.get("intent", "")
    strategy = state.get("strategy", "")
    api_key = state.get("api_key") or os.getenv("GROQ_API_KEY")

    if intent == "design_update":
        plan = state.get("plan", {}) or {}
        device_name = plan.get("design_device_name", "nouvel équipement")
        device_type = plan.get("design_device_type", "switch")
        management_ip = plan.get("design_device_ip", "")
        parent_device = plan.get("design_parent_device", "")
        topology_yaml_block = plan.get("topology_yaml_block", "")
        remediation_commands = plan.get("remediation_commands", []) or []
        deployment_notes = plan.get("deployment_notes", []) or []

        summary_lines = [
            f"**Résumé**",
            f"- Déploiement préparé pour {device_name} ({device_type}).",
            f"- IP de management proposée : {management_ip}.",
        ]
        if parent_device:
            summary_lines.append(f"- Équipement parent cible : {parent_device}.")

        if topology_yaml_block:
            summary_lines.extend([
                "",
                "**Bloc YAML**",
                "```yaml",
                topology_yaml_block,
                "```",
            ])

        if remediation_commands:
            summary_lines.extend([
                "",
                "**Commandes VRP**",
                "```bash",
                "\n".join(remediation_commands),
                "```",
            ])

        if deployment_notes:
            summary_lines.extend(["", "**Notes**"])
            summary_lines.extend([f"- {note}" for note in deployment_notes])

        result = ValidatorResult(
            final_answer="\n".join(summary_lines),
            confidence=ConfidenceLevel.HIGH,
            confidence_score=0.96,
            sources_used=[],
            devices_consulted=[d for d in [parent_device, device_name] if d],
            validation_checks={
                "intent": intent,
                "topology_update_ready": True,
            },
        )

        return {
            **state,
            "final_answer": result.final_answer,
            "confidence": result.confidence.value,
            "confidence_score": result.confidence_score,
            "alerts": [],
            "discrepancies": [],
            "recommendations": [],
            "sources_used": result.sources_used,
            "devices_consulted": result.devices_consulted,
            "validation_checks": result.validation_checks,
            "step": "validator_done",
        }

    doc_result = DocumentalistResult()
    doc_result.full_context   = state.get("full_context", "")
    doc_result.rag_context    = state.get("rag_context", "")
    doc_result.device_context = state.get("device_context", "")
    doc_result.total_rag_chunks = state.get("rag_chunks_count", 0)
    doc_result.manual_reference_text = state.get("manual_reference_text", "")
    doc_result.manual_reference_text_structured = state.get("manual_reference_text_structured", "")
    # Récupération des résultats RAG depuis l'état
    doc_result.rag_text_results  = state.get("rag_text_results", [])
    doc_result.rag_image_results = state.get("rag_image_results", [])
    doc_result.rag_table_results = state.get("rag_table_results", [])
    doc_result.mentioned_protos  = state.get("mentioned_protos", [])   # ← AJOUT
    doc_result.mentioned_vlans   = state.get("mentioned_vlans", [])    # ← AJOUT
    doc_result.mentioned_devices = state.get("mentioned_devices", []) # ← AJOUT
    # Récupération des données terrain
    doc_result.device_data = state.get("device_data", {})
    print("\n[DEBUG DEVICE DATA]")
    for name, data in doc_result.device_data.items():
        print(
        name,
        "type=",
        type(data),
        "stp_ports=",
        getattr(data, "stp_ports", "ABSENT")
    )
    agent  = get_validator_agent(api_key)
    result = agent.validate(query=query, doc_result=doc_result, intent=intent, strategy=strategy)

    return {
        **state,
        "final_answer":      result.final_answer,
        "confidence":        result.confidence.value,
        "confidence_score":  result.confidence_score,
        "alerts":            [a.to_text() for a in result.alerts],
        "discrepancies":     result.discrepancies,
        "recommendations":   result.recommendations,
        "sources_used":      result.sources_used,
        "devices_consulted": result.devices_consulted,
        "validation_checks": result.validation_checks,
        "step":              "validator_done",
    }


# ---------------------------------------------------------------------------
# 8. POINT D'ENTRÉE — Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from documentalist_agent import DocumentalistAgent

    if len(sys.argv) > 1 and sys.argv[1].endswith(".pdf"):
        pdf_file = sys.argv[1]
        use_api  = "--api" in sys.argv
    else:
        pdf_file = None
        use_api  = "--api" in sys.argv

    print("=" * 60)
    print("  TEST VALIDATOR AGENT v2 — VLAN + Hostname matching")
    print("=" * 60)

    doc_agent = DocumentalistAgent()
    val_agent = ValidatorAgent()

    test_cases = [
        {
            "query": "La configuration de S1 est-elle conforme au guide de configuration ?",
            "plan": {
                "intent":          "audit",
                "strategy":        "comparison",
                "enriched_query":  "VLAN configuration S1 switch Huawei standard compliance",
                "chunk_types":     ["code_cli", "text", "table"],
                "needs_live_data": True,
                "needs_images":    False,
                "target_devices":  ["S1"],
                "target_file":     Path(pdf_file).name if pdf_file else None,
                "sub_queries":     [
                    "VLAN 10 20 99 configuration switch",
                    "sysname hostname Huawei standard",
                ],
            }
        },
    ]

    for i, tc in enumerate(test_cases, 1):
        print(f"\n{'─'*60}")
        print(f"  TEST {i} : {tc['query']}")
        print(f"{'─'*60}")

        doc_result = doc_agent.collect(query=tc["query"], plan=tc["plan"])
        result     = val_agent.validate(query=tc["query"], doc_result=doc_result)
        print(result.to_display())

        print(f"\n  Checks :")
        for k, v in result.validation_checks.items():
            print(f"    {k:20s}: {v}")

    print(f"\n{'='*60}")
    print("  Tests terminés ✓")
    print(f"{'='*60}\n")