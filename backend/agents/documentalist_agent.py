"""
agents/documentalist_agent.py
==============================
Agent Documentaliste — Deuxième agent du pipeline LangGraph.

RÔLE DANS LE PIPELINE :
    [Architecte]  → produit ArchitectPlan
         ↓
    [Documentaliste]  ← ON EST ICI
         ↓
    [Validateur]

RESPONSABILITÉS :
    1. CHERCHER dans Qdrant (RAG) selon le plan de l'Architecte
    2. COLLECTER les données live des équipements (si nécessaire)
    3. ASSEMBLER le contexte complet pour le Validateur

SORTIE :
    DocumentalistResult — contexte complet (RAG + terrain)
"""

from __future__ import annotations

import os
import re
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
from groq import Groq
from backend.llm.fallback_client import LLMFallbackClient

# Imports vectorstore
sys.path.insert(0, str(Path(__file__).parent.parent / "vectorstore"))
from hybrid_search import SearchResult
from qdrant_connection import get_qdrant_client

# Import MCP tools
sys.path.insert(0, str(Path(__file__).parent))
from mcp_tools import MCPTools, AccessMode, DeviceData ,PROTOCOL_COMMANDS  


# ---------------------------------------------------------------------------
# 1. TYPES DE DONNÉES
# ---------------------------------------------------------------------------
LAST_UPLOAD_FILE = Path(__file__).parent.parent.parent / "state" / "last_uploaded.json"
@dataclass
class DocumentalistResult:
    """
    Résultat complet du Documentaliste.

    Contient tout ce dont le Validateur a besoin :
    - Chunks RAG pertinents (théorie des manuels)
    - Données terrain des équipements (pratique eNSP)
    - Contexte fusionné prêt pour le LLM
    """
    # Résultats RAG par type
    rag_text_results:  list[SearchResult]    = field(default_factory=list)
    rag_image_results: list[SearchResult]    = field(default_factory=list)
    rag_table_results: list[SearchResult]    = field(default_factory=list)

    # Données terrain eNSP
    device_data:       dict[str, DeviceData] = field(default_factory=dict)

    # Contextes formatés pour le LLM
    rag_context:       str = ""  # texte des chunks Qdrant
    device_context:    str = ""  # résumé des équipements
    full_context:      str = ""  # fusion RAG + terrain

    # Métriques
    total_rag_chunks:  int       = 0
    total_devices:     int       = 0
    search_queries:    list[str] = field(default_factory=list)
    errors:            list[str] = field(default_factory=list)
    
    
    # Dump complet d'un manuel explicitement nommé dans la question
    # (ex: "selon Test2.pdf") — utilisé par check_discrepancies() côté
    # Validateur pour un audit exhaustif, indépendant du score sémantique
    # du retrieval top-k (qui peut manquer des sections du même document).
    manual_reference_text: str = ""
    manual_reference_text_structured: str = ""
    manual_device_mapping: dict[str,list] = field(default_factory=dict)
    mentioned_devices: list[str] = field(default_factory=list)
    mentioned_vlans:   list[str] = field(default_factory=list)   # ← AJOUT
    mentioned_protos:  list[str] = field(default_factory=list)   # ← AJOUT
    def to_dict(self) -> dict:
        return {
        "rag_chunks_count": self.total_rag_chunks,
        "devices_collected": self.total_devices,

        "search_queries": self.search_queries,

        "has_images": len(self.rag_image_results) > 0,
        "has_tables": len(self.rag_table_results) > 0,

        "errors": self.errors,

        # AJOUT IMPORTANT
        "device_data": self.device_data,

        # Ajoute aussi ces champs si le Validator les utilise
        "rag_text_results": self.rag_text_results,
        "rag_image_results": self.rag_image_results,
        "rag_table_results": self.rag_table_results,

        "full_context": self.full_context,
        "rag_context": self.rag_context,
        "device_context": self.device_context,
        "manual_reference_text": self.manual_reference_text,
        "manual_reference_text_structured": self.manual_reference_text_structured,
        "manual_device_mapping":self.manual_device_mapping,
        "mentioned_devices": self.mentioned_devices,
        "mentioned_vlans":   self.mentioned_vlans,
        "mentioned_protos":  self.mentioned_protos,
    }
# ---------------------------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------------------------

GROQ_MODEL  = "openai/gpt-oss-120b"
MAX_TOKENS  = 2048
TEMPERATURE = 0.2


# ---------------------------------------------------------------------------
# 3. PROMPTS
# ---------------------------------------------------------------------------

DOCUMENTALIST_SYSTEM_PROMPT = """Tu es l'Agent Documentaliste d'un système RAG \
multimodal spécialisé en infrastructure réseau Huawei.

Tu reçois :
1. Un plan de recherche de l'Agent Architecte
2. Des chunks extraits de la base Qdrant (manuels PDF)
3. Des données live des équipements eNSP (config réelle)

Ta mission : synthétiser ces informations en une réponse structurée et précise.

RÈGLES :
- Cite TOUJOURS tes sources (numéro de chunk, page, équipement)
- Distingue clairement ce qui vient des manuels vs du terrain
- Si une information manque, dis-le explicitement
- Utilise la terminologie technique Huawei VRP correcte
- Réponds en français"""


def build_documentalist_prompt(
    original_query: str,
    architect_plan: str,
    rag_context:    str,
    device_context: str,
) -> str:
    return f"""QUESTION ORIGINALE : "{original_query}"

PLAN DE L'ARCHITECTE :
{architect_plan}

CONTEXTE DOCUMENTAIRE (manuels PDF indexés dans Qdrant) :
{rag_context if rag_context else "Aucun chunk pertinent trouvé dans Qdrant."}

CONTEXTE TERRAIN (équipements eNSP) :
{device_context if device_context else "Aucune donnée terrain disponible."}

Synthétise ces informations pour répondre à la question.
Indique clairement ce qui vient des manuels et ce qui vient du terrain.
Si tu détectes des écarts entre théorie et terrain, signale-les."""


# ---------------------------------------------------------------------------
# 4. AGENT DOCUMENTALISTE
# ---------------------------------------------------------------------------

class DocumentalistAgent:
    """
    Agent Documentaliste — collecte et synthétise les informations.

    USAGE :
        agent  = DocumentalistAgent()
        result = agent.collect(
            query="Comment configurer trunk sur S1 ?",
            plan=architect_plan_dict,
        )
        print(result.full_context)
    """

    def __init__(
        self,
        api_key:       Optional[str] = None,
        topology_file: str           = "topology.yaml",
        top_k:         int           = 5,
    ):
        self.top_k = top_k

        # Client Groq
        resolved_key  = api_key or os.getenv("GROQ_API_KEY")
        if resolved_key:
            
            self._llm = LLMFallbackClient(groq_api_key=resolved_key)  # adapte le nom de variable si différent
            self._use_llm = True
            print(f"[DocumentalistAgent] LLM actif — {GROQ_MODEL}")
        else:
            self._llm     = None
            self._use_llm = False
            print("[DocumentalistAgent] Mode sans LLM")

        # Client Qdrant
        try:
            self._qdrant = get_qdrant_client()
            print("[DocumentalistAgent] Qdrant connecté ✓")
        except Exception as e:
            self._qdrant = None
            print(f"[DocumentalistAgent] ⚠ Qdrant : {e}")

        # MCP Tools
        try:
            self._mcp = MCPTools(topology_file=topology_file)
            print(f"[DocumentalistAgent] MCP prêt — {self._mcp.devices}")
        except Exception as e:
            self._mcp = None
            print(f"[DocumentalistAgent] ⚠ MCP : {e}")

    # ------------------------------------------------------------------
    # POINT D'ENTRÉE PRINCIPAL
    # ------------------------------------------------------------------

    def collect(self, query: str, plan: dict) -> DocumentalistResult:
        """
        Collecte toutes les informations nécessaires pour répondre.

        ÉTAPES :
        1. Recherche RAG dans Qdrant (selon chunk_types du plan)
        2. Collecte terrain via MCPTools (si needs_live_data)
        3. Formatage des contextes
        4. Synthèse LLM

        Args:
            query : question originale de l'utilisateur
            plan  : dict produit par ArchitectAgent

        Returns:
            DocumentalistResult avec tout le contexte assemblé
        """
        print(f"\n[DocumentalistAgent] '{query[:60]}'")
        result = DocumentalistResult()

        # Étape 1 : RAG
        print("  [1/3] Recherche RAG...")
        self._search_rag(query, plan, result)

        # Étape 2 : Terrain
        needs_live = plan.get("needs_live_data", False)
        strategy   = plan.get("strategy", "rag_only")
        result.mentioned_devices = plan.get("mentioned_devices", [])
        result.mentioned_vlans   = plan.get("mentioned_vlans", [])   # ← AJOUT
        result.mentioned_protos  = plan.get("mentioned_protos", [])  # ← AJOUT
        if needs_live or strategy in ("hybrid", "comparison", "device_only"):
            print("  [2/3] Collecte terrain eNSP...")
            self._collect_terrain(plan, result)
        else:
            print("  [2/3] Terrain non requis")

        # Étape 3 : Assemblage
        print("  [3/3] Assemblage contexte...")
        self._build_contexts(result)

        # Synthèse LLM
        if self._use_llm and (result.rag_context or result.device_context):
            print("  [+]  Synthèse LLM...")
            result.full_context = self._synthesize_with_llm(
                query, plan, result
            )
        else:
            result.full_context = self._build_raw_context(result)

        # Métriques finales
        result.total_rag_chunks = (
            len(result.rag_text_results) +
            len(result.rag_image_results) +
            len(result.rag_table_results)
        )
        result.total_devices = len(result.device_data)

        print(f"\n[DocumentalistAgent] ✓")
        print(f"  RAG chunks  : {result.total_rag_chunks}")
        print(f"  Équipements : {result.total_devices}")
        print(f"  Contexte    : {len(result.full_context)} chars")

        return result

    # ------------------------------------------------------------------
    # DÉTECTION D'UN PDF NOMMÉ EXPLICITEMENT DANS LA QUESTION
    # ------------------------------------------------------------------
    def _extract_manual_devices(self,text):

        devices={}

        blocks=re.split(
        r"\n(?=S\d+)",
        text,
        flags=re.I
    )


        for block in blocks:

            name=re.search(
            r"\b(S\d+)\b",
            block,
            re.I
        )


            if name:

                devices[
                name.group(1).upper()
            ] = block


        return devices
    def _detect_referenced_pdf(self, query: str) -> Optional[str]:
        """
        Détecte si l'utilisateur nomme explicitement un fichier PDF.

        EXEMPLES qui matchent :
            "selon Test2.pdf"                          → "Test2.pdf"
            "conforme au manuel Test2.pdf concernant..." → "Test2.pdf"

        Returns:
            Le nom du fichier (avec extension .pdf), ou None.
        """
        import re
        match = re.search(r"([\w\-]+\.pdf)", query, re.IGNORECASE)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # ÉTAPE 1 : RECHERCHE RAG
    # ------------------------------------------------------------------

    def _search_rag(
        self,
        query:  str,
        plan:   dict,
        result: DocumentalistResult,
    ) -> None:
        """Effectue la recherche dans Qdrant selon le plan."""
        if not self._qdrant:
            result.errors.append("Qdrant non disponible")
            return

        enriched_query = plan.get("enriched_query", query)
        chunk_types    = plan.get("chunk_types", ["text", "code_cli"])
        sub_queries    = plan.get("sub_queries", [])
        needs_images   = plan.get("needs_images", False)

        # Toutes les queries à exécuter
        all_queries           = [enriched_query] + sub_queries[:3]

        # CORRECTIF : quand on va comparer avec le terrain (needs_live_data),
        # on ajoute systématiquement une requête "audit" par équipement ciblé.
        # SANS CA : une question de diagnostic/config ("pourquoi ça marche pas ?")
        # ne ramène que des chunks liés au symptôme (OSPF, ping...) et JAMAIS
        # la section du manuel qui décrit le sysname/OSPF attendus — donc
        # check_discrepancies() côté Validateur n'a rien à comparer, même
        # si le manuel contient bien l'information.
        if plan.get("needs_live_data") and plan.get("target_devices"):
            for dev in plan.get("target_devices", [])[:2]:
                all_queries.extend([
                     f"{dev} sysname hostname nom équipement",
            f"{dev} VLAN vlanif management VLAN 10 VLAN 20 VLAN 99",
            f"{dev} interface GigabitEthernet access trunk configuration",
            f"{dev} adresse IP configuration attendue manuel",
            f"{dev} sécurité SSH stelnet utilisateur"])

        mentioned_protos = plan.get("mentioned_protos", [])
        if mentioned_protos:
            for proto in mentioned_protos[:2]:
                all_queries.append(f"Huawei {proto} configuration manuel exigences")
                for dev in plan.get("target_devices", [])[:2]:
                    all_queries.append(f"{dev} {proto} état configuration")
        result.search_queries = all_queries

        seen_text  = set()
        seen_image = set()
        seen_table = set()

        for q in all_queries:
            try:
                print(f"    → '{q[:50]}'")

                # Texte + CLI
                text_types = [t for t in chunk_types
                              if t in ("text", "code_cli", "heading")]
                if text_types:
                    for r in self._qdrant.search(q, top_k=self.top_k,
                                                 chunk_types=text_types):
                        if r.chunk_id not in seen_text:
                            result.rag_text_results.append(r)
                            seen_text.add(r.chunk_id)

                # Images
                if needs_images or "image_desc" in chunk_types:
                    for r in self._qdrant.search(q, top_k=3,
                                                 chunk_types=["image_desc"]):
                        if r.chunk_id not in seen_image:
                            result.rag_image_results.append(r)
                            seen_image.add(r.chunk_id)

                # Tableaux
                if "table" in chunk_types:
                    for r in self._qdrant.search(q, top_k=2,
                                                 chunk_types=["table"]):
                        if r.chunk_id not in seen_table:
                            result.rag_table_results.append(r)
                            seen_table.add(r.chunk_id)
                
            except Exception as e:
                result.errors.append(f"RAG search ({q[:30]}): {e}")
                print(f"    ✗ {e}")

        # Tri par score + limite
        # NOTE : on garde top_k + 3 ici (au lieu de top_k strict) pour laisser
        # une chance aux chunks "audit" ajoutés ci-dessus de survivre au tri,
        # même s'ils sont un peu moins bien scorés que ceux liés au symptôme.
        result.rag_text_results.sort(key=lambda r: r.score, reverse=True)
        result.rag_image_results.sort(key=lambda r: r.score, reverse=True)
        result.rag_table_results.sort(key=lambda r: r.score, reverse=True)
        result.rag_text_results  = result.rag_text_results[: self.top_k + 3]
        result.rag_image_results = result.rag_image_results[:3]
        result.rag_table_results = result.rag_table_results[:2]

        total = (len(result.rag_text_results) +
                 len(result.rag_image_results) +
                 len(result.rag_table_results))
        print(f"    ✓ {total} chunks "
              f"(text:{len(result.rag_text_results)} "
              f"img:{len(result.rag_image_results)} "
              f"table:{len(result.rag_table_results)})")

        # ------------------------------------------------------------
        # DUMP COMPLET SI UN MANUEL EST NOMMÉ EXPLICITEMENT DANS LA QUESTION
        # ------------------------------------------------------------
        # "selon Test2.pdf", "conforme au manuel Test2.pdf" → on ne se fie
        # plus au score sémantique du top-k pour ce document précis, on
        # récupère TOUT son contenu indexé. Sans ça, une question qui ne
        # ressemble qu'à UNE section du manuel (ex: "ses VLANs") ne ramène
        # jamais les autres sections pertinentes (nommage, autres VLANs...).
        referenced_pdf = self._detect_referenced_pdf(query)
        if not referenced_pdf and (plan.get("needs_live_data") or plan.get("strategy") in ("hybrid", "comparison","rag_only")):
            referenced_pdf = self._get_last_uploaded_pdf()

        if referenced_pdf and self._qdrant:
            try:
                full_doc_chunks = self._qdrant.get_by_source(referenced_pdf, limit=700)
                extracted_texts = []
                extracted_texts_structured = []

                for c in full_doc_chunks:
                    # Sécurité : vérifier 'content' ou 'text' selon votre indexation
                    txt = ""
                    chunk_type = ""
                    if isinstance(c, dict):
                        txt = c.get("content") or c.get("text") or ""
                        chunk_type = c.get("chunk_type", "")
                    elif hasattr(c, "content"):
                        txt = c.content
                        chunk_type = getattr(c, "chunk_type", "")

                    if txt:
                        extracted_texts.append(txt)
                        # EXCLU de la version structurée : les descriptions
                        # d'image (vision) mentionnent souvent plusieurs
                        # équipements/interfaces dans la même phrase sans lien
                        # explicite — ça fausse l'extraction par proximité
                        # de ligne. Le texte réglementaire structuré du
                        # manuel suffit pour VLANs/interfaces/hostnames.
                        if chunk_type != "image_desc":
                            extracted_texts_structured.append(txt)

                result.manual_reference_text = "\n".join(extracted_texts)
                result.manual_reference_text_structured = "\n".join(extracted_texts_structured)
                result.manual_device_mapping = (
                    self._extract_manual_devices(
                        result.manual_reference_text
                    )
                )
                print(f"    ✓ Manuel '{referenced_pdf}' détecté → Dump complet récupéré "
                f"({len(extracted_texts)} chunks, {len(result.manual_reference_text)} chars, "
                f"{len(extracted_texts_structured)} chunks structurés)")
            except Exception as e:
                print(f"    ✗ Erreur dump manuel : {e}")
    # ------------------------------------------------------------------
    # ÉTAPE 2 : COLLECTE TERRAIN
    # ------------------------------------------------------------------

    def _collect_terrain(
        self,
        plan:   dict,
        result: DocumentalistResult,
    ) -> None:
        """Collecte les données des équipements via MCPTools."""
        if not self._mcp:
            result.errors.append("MCPTools non disponible")
            return

        targets = plan.get("target_devices", []) or self._mcp.devices
        print(f"    → Équipements : {targets}")
        mentioned_protos = plan.get("mentioned_protos", [])
        extra_commands = [
        PROTOCOL_COMMANDS[p.upper()]
        for p in mentioned_protos
        if p.upper() in PROTOCOL_COMMANDS
    ]
        extra_commands = list(dict.fromkeys(extra_commands))  # dédoublonnage

        for name in targets:
            try:
                data = self._mcp.get_device_info(name, mode=AccessMode.AUTO , extra_commands=extra_commands)
                result.device_data[name] = data
                src = "SSH" if data.access_mode == "ssh" else "fichier"
                print(f"    ✓ {name} [{src}]")
            except Exception as e:
                result.errors.append(f"MCP ({name}): {e}")
                print(f"    ✗ {name} : {e}")

        print(f"    ✓ {len(result.device_data)} équipement(s)")

    # ------------------------------------------------------------------
    # ÉTAPE 3 : ASSEMBLAGE
    # ------------------------------------------------------------------

    def _build_contexts(self, result: DocumentalistResult) -> None:
        """Formate les données brutes en contextes textuels."""

        # Contexte RAG
        parts = []
        if result.rag_text_results:
            parts.append("=== TEXTE & COMMANDES CLI ===")
            for i, r in enumerate(result.rag_text_results, 1):
                parts.append(
                    f"[Source {i}] p.{r.page_number} | "
                    f"{r.chunk_type} | score={r.score:.2f} | "
                    f"{r.section_title or 'N/A'}\n{r.content}\n---"
                )

        if result.rag_image_results:
            parts.append("\n=== SCHÉMAS & TOPOLOGIES ===")
            for i, r in enumerate(result.rag_image_results, 1):
                parts.append(
                    f"[Schéma {i}] p.{r.page_number} | "
                    f"score={r.score:.2f}\n{r.content}\n---"
                )

        if result.rag_table_results:
            parts.append("\n=== TABLEAUX ===")
            for i, r in enumerate(result.rag_table_results, 1):
                parts.append(
                    f"[Table {i}] p.{r.page_number} | "
                    f"score={r.score:.2f}\n{r.content}\n---"
                )

        result.rag_context = "\n".join(parts)

        # Contexte terrain
        if result.device_data:
            result.device_context = "\n\n".join(
                data.to_text_summary()
                for data in result.device_data.values()
            )

    def _build_raw_context(self, result: DocumentalistResult) -> str:
        """Contexte brut sans LLM."""
        parts = []
        if result.rag_context:
            parts.append("=== DOCUMENTATION (RAG) ===\n" + result.rag_context)
        if result.device_context:
            parts.append("=== TERRAIN (eNSP) ===\n" + result.device_context)
        if result.manual_reference_text:

            parts.append(
        "=== MANUEL PDF COMPLET ===\n"
        +
        result.manual_reference_text
    )
        return "\n\n".join(parts) if parts else "Aucune information trouvée."

    # ------------------------------------------------------------------
    # SYNTHÈSE LLM
    # ------------------------------------------------------------------

    def _synthesize_with_llm(
        self,
        query:  str,
        plan:   dict,
        result: DocumentalistResult,
    ) -> str:
        """Synthétise les informations collectées via Groq."""
        manual_section = (
    f"\n\n=== MANUEL COMPLET ===\n{result.manual_reference_text[:5000]}"
    if result.manual_reference_text else ""
)
        prompt = build_documentalist_prompt(
    original_query=query,
    architect_plan=(
        f"Intention: {plan.get('intent', 'unknown')}\n"
        f"Stratégie: {plan.get('strategy', 'hybrid')}\n"
        f"Équipements: {plan.get('target_devices', [])}"
    ),

    rag_context=result.rag_context[:3000] + manual_section,
    device_context=result.device_context[:2000],
)

        try:
            llm_response = self._llm.complete(
        system_prompt=DOCUMENTALIST_SYSTEM_PROMPT,
        user_prompt=prompt,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
            return llm_response.content.strip()
        except Exception as e:
            print(f"  ⚠ LLM échoué ({e}), fallback brut")
        return self._build_raw_context(result)
    def _get_last_uploaded_pdf(self) -> Optional[str]:
        """
    Fallback : si l'utilisateur ne cite aucun PDF dans sa question,
    on utilise le dernier fichier uploadé/ingéré (le plus récent).
        """
        try:
            if LAST_UPLOAD_FILE.exists():
                data = json.loads(LAST_UPLOAD_FILE.read_text(encoding="utf-8"))
                filename = data.get("filename")
                if filename:
                    print(f"    ℹ Aucun PDF cité — fallback sur dernier upload : '{filename}'")
                return filename
        except Exception as e:
            print(f"    ⚠ Lecture last_uploaded.json échouée : {e}")
        return None  


# ---------------------------------------------------------------------------
# 5. NŒUD LANGGRAPH
# ---------------------------------------------------------------------------

def documentalist_node(state: dict) -> dict:
    """
    Nœud LangGraph pour l'Agent Documentaliste.

    STATE INPUT  : query, plan, api_key
    STATE OUTPUT : doc_result, full_context, rag_context,
                   device_context, rag_chunks_count, step
    """
    query   = state.get("query", "")
    plan    = state.get("plan", {})
    api_key = state.get("api_key") or os.getenv("GROQ_API_KEY")

    agent  = DocumentalistAgent(api_key=api_key)
    result = agent.collect(query=query, plan=plan)
    print(f"[DEBUG-DOC] mentioned_protos envoyés par Documentalist : {result.mentioned_protos}")
    return {
        **state,
        "doc_result":       result.to_dict(),
        "full_context":     result.full_context,
        "rag_context":      result.rag_context,
        "device_context":   result.device_context,
        "device_data":      result.device_data,   # <-- AJOUTER
        "mentioned_protos":  result.mentioned_protos,   # ← AJOUT
        "mentioned_vlans":   result.mentioned_vlans,    # ← AJOUT
        "mentioned_devices": result.mentioned_devices,  # ← AJOUT
        "rag_text_results":  result.rag_text_results,
        "rag_image_results": result.rag_image_results,
        "rag_table_results": result.rag_table_results,
        "rag_chunks_count": result.total_rag_chunks,
        "manual_reference_text": result.manual_reference_text,
        "manual_reference_text_structured": result.manual_reference_text_structured,
        "step":             "documentalist_done",
    }


# ---------------------------------------------------------------------------
# 6. POINT D'ENTRÉE — Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    print("=" * 60)
    print("  TEST DOCUMENTALIST AGENT")
    print("=" * 60)

    agent = DocumentalistAgent()

    test_cases = [
        {
            "query": "Comment configurer un trunk VLAN sur S1 ?",
            "plan": {
                "intent":          "configuration",
                "strategy":        "rag_only",
                "enriched_query":  "trunk VLAN configuration Huawei VRP switch",
                "chunk_types":     ["code_cli", "text"],
                "needs_live_data": False,
                "needs_images":    False,
                "target_devices":  [],
                "sub_queries":     ["VLAN trunk port link-type Huawei"],
            }
        },
        {
            "query": "Quel est l'état actuel de S1 ?",
            "plan": {
                "intent":          "status_check",
                "strategy":        "hybrid",
                "enriched_query":  "S1 switch interface status Huawei VRP",
                "chunk_types":     ["code_cli", "table"],
                "needs_live_data": True,
                "needs_images":    False,
                "target_devices":  ["S1"],
                "sub_queries":     [],
            }
        },
    ]

    for i, tc in enumerate(test_cases, 1):
        print(f"\n{'─'*60}")
        print(f"  TEST {i} : {tc['query']}")
        print(f"{'─'*60}")

        result = agent.collect(query=tc["query"], plan=tc["plan"])

        print(f"\n  RAG chunks  : {result.total_rag_chunks}")
        print(f"  Équipements : {result.total_devices}")
        print(f"  Queries     : {result.search_queries}")
        if result.errors:
            print(f"  Erreurs     : {result.errors}")

        preview = result.full_context[:400]
        print(f"\n  Aperçu :\n{preview}" +
              ("..." if len(result.full_context) > 400 else ""))

    print(f"\n{'='*60}")
    print("  Tests terminés ✓")
    print("  → Prochaine étape : validator_agent.py")
    print(f"{'='*60}\n")