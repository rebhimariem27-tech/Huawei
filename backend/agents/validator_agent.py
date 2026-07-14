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

GROQ_MODEL  = "llama-3.3-70b-versatile"
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
1. La question originale de l'ingénieur
2. Une réponse préliminaire du Documentaliste
3. Les sources RAG (chunks PDF)
4. Les données terrain des équipements eNSP
5. Les écarts détectés automatiquement (VLANs manquants, hostnames incorrects...)

Ta mission : valider, enrichir et finaliser la réponse.

RÈGLES :
- Sois précis et technique — terminologie Huawei VRP correcte
- Cite les sources : [Manuel p.X] ou [S1 SSH live]
- Si des VLANs ou hostnames sont non conformes, explique PRÉCISÉMENT
  ce qui manque et comment le corriger avec les commandes VRP exactes
- Réponds en français"""


def build_validator_prompt(
    original_query:     str,
    preliminary_answer: str,
    rag_context:        str,
    device_context:     str,
    alerts_text:        str,
    discrepancies_text: str,
) -> str:
    return f"""QUESTION : "{original_query}"

RÉPONSE PRÉLIMINAIRE :
{preliminary_answer}

SOURCES RAG :
{rag_context[:2000] if rag_context else "Aucune."}

DONNÉES TERRAIN :
{device_context[:1500] if device_context else "Aucune."}

ALERTES AUTOMATIQUES :
{alerts_text if alerts_text else "Aucune alerte."}

ÉCARTS DÉTECTÉS (théorie vs terrain) :
{discrepancies_text if discrepancies_text else "Aucun écart détecté."}

Valide et finalise la réponse. Pour chaque écart, fournis les commandes VRP \
exactes pour corriger la configuration."""


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
                alerts.append(ValidationAlert(
                    severity=AlertSeverity.CRITICAL,
                    category="anomaly",
                    message=f"Interface(s) DOWN sur {device_name}",
                    device=device_name,
                    detail=f"Interfaces : {', '.join(down[:5])}",
                ))

            bad_peers = [
                p for p in data.ospf_peers
                if "full" not in p.get("state", "").lower()
            ]
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
        if doc_result.manual_reference_text:
            full_rag += "\n" + doc_result.manual_reference_text

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
            self._client  = Groq(api_key=resolved_key)
            self._use_llm = True
            print(f"[ValidatorAgent] LLM actif — {GROQ_MODEL}")
        else:
            self._client  = None
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
    ) -> ValidatorResult:
        """Valide et finalise la réponse du Documentaliste."""
        print(f"\n[ValidatorAgent] Validation : '{query[:50]}'")

        # Étape 1 : vérifications automatiques
        print("  [1/3] Vérifications automatiques...")
        rag_quality, rag_alerts = self._checker.check_rag_quality(doc_result)
        device_alerts           = self._checker.check_device_health(doc_result)
        discrepancies           = self._checker.check_discrepancies(doc_result)
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
        alerts_text       = "\n".join(a.to_text() for a in all_alerts)
        discrepancies_text = "\n".join(f"⚠ {d}" for d in discrepancies)

        if self._use_llm and doc_result.full_context:
            final_answer = self._finalize_with_llm(
                query, doc_result, alerts_text, discrepancies_text
            )
        else:
            final_answer = self._finalize_without_llm(
                query, doc_result, all_alerts, discrepancies
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
        query:             str,
        doc_result:        DocumentalistResult,
        alerts_text:       str,
        discrepancies_text: str,
    ) -> str:
        prompt = build_validator_prompt(
            original_query=query,
            preliminary_answer=doc_result.full_context,
            rag_context=doc_result.rag_context[:2000],
            device_context=doc_result.device_context[:1500],
            alerts_text=alerts_text,
            discrepancies_text=discrepancies_text,
        )
        try:
            response = self._client.chat.completions.create(
                model=GROQ_MODEL,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                messages=[
                    {"role": "system", "content": VALIDATOR_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"  ⚠ LLM échoué ({e})")
            return doc_result.full_context or "Erreur lors de la génération de la réponse."

    def _finalize_without_llm(
        self,
        query:        str,
        doc_result:   DocumentalistResult,
        alerts:       list[ValidationAlert],
        discrepancies: list[str],
    ) -> str:
        parts = [f"RÉPONSE À : {query}\n"]
        if doc_result.full_context:
            parts.append(doc_result.full_context)
        if alerts:
            parts += ["\nALERTES :"] + [a.to_text() for a in alerts]
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
    query   = state.get("query", "")
    api_key = state.get("api_key") or os.getenv("GROQ_API_KEY")

    doc_result = DocumentalistResult()
    doc_result.full_context   = state.get("full_context", "")
    doc_result.rag_context    = state.get("rag_context", "")
    doc_result.device_context = state.get("device_context", "")
    doc_result.total_rag_chunks = state.get("rag_chunks_count", 0)
    doc_result.manual_reference_text = state.get("manual_reference_text", "")
    # Récupération des résultats RAG depuis l'état
    doc_result.rag_text_results  = state.get("rag_text_results", [])
    doc_result.rag_image_results = state.get("rag_image_results", [])
    doc_result.rag_table_results = state.get("rag_table_results", [])

    # Récupération des données terrain
    doc_result.device_data = state.get("device_data", {})

    agent  = get_validator_agent(api_key)
    result = agent.validate(query=query, doc_result=doc_result)

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