"""
agents/graph.py
================
Orchestrateur LangGraph — Cerveau du pipeline multi-agents.

RÔLE :
    graph.py connecte les 3 agents en un graphe d'état :

    START
      ↓
    [architect_node]    → analyse l'intention + planifie
      ↓
    [documentalist_node] → collecte RAG + terrain eNSP
      ↓
    [validator_node]    → vérifie + finalise la réponse
      ↓
    END → ValidatorResult

LANGGRAPH — Concepts clés :
    StateGraph : graphe d'état typé — chaque nœud lit et enrichit l'état
    State      : dict partagé entre tous les nœuds
    Node       : fonction (state) → state enrichi
    Edge       : connexion entre nœuds (simple ou conditionnelle)

    L'état est IMMUTABLE entre nœuds — chaque nœud retourne
    un nouveau dict avec les clés ajoutées/modifiées.
    LangGraph fusionne les dicts automatiquement.

USAGE :
    # Simple
    pipeline = RAGPipeline()
    result   = pipeline.run("Comment configurer OSPF sur R1 ?")
    print(result["final_answer"])

    # Avec streaming (pour l'API)
    for event in pipeline.stream("Quel est l'état de S1 ?"):
        print(event)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TypedDict, Annotated, Optional
import operator

# LangGraph
from langgraph.graph import StateGraph, START, END

# Imports agents
sys.path.insert(0, str(Path(__file__).parent))
from architect_agent     import architect_node,     get_architect_agent
from documentalist_agent import documentalist_node, DocumentalistAgent
from validator_agent     import validator_node,     get_validator_agent

from fpdf import FPDF
import re
# ---------------------------------------------------------------------------
# 1. ÉTAT DU GRAPHE
# ---------------------------------------------------------------------------

class RAGState(TypedDict, total=False):
    """
    État partagé entre tous les nœuds du graphe.

    TypedDict : typage strict pour éviter les erreurs silencieuses.
    total=False : tous les champs sont optionnels (remplis progressivement).

    FLUX DE L'ÉTAT :
        START         → query
        Architecte    → + plan, intent, strategy
        Documentaliste→ + doc_result, full_context, rag_context,
                          device_context, rag_chunks_count
        Validateur    → + final_answer, confidence, confidence_score,
                          alerts, discrepancies, recommendations, sources_used
        END           → état complet
    """
    # Entrée
    query:             str
    api_key:           Optional[str]
    rag_text_results:  list
    rag_image_results: list
    rag_table_results: list

    # Architecte
    plan:              dict
    intent:            str
    strategy:          str

    # Documentaliste
    doc_result:        dict
    full_context:      str
    rag_context:       str
    device_context:    str
    rag_chunks_count:  int
    device_data:              dict      # ← AJOUTER
    manual_reference_text:    str       # ← AJOUTER
    manual_reference_text_structured: str   # version sans image_desc, pour extraction
    manual_device_mapping:    dict      # ← AJOUTER (utilisé ailleurs)
    mentioned_protos:         list      # ← AJOUT
    mentioned_vlans:          list      # ← AJOUT
    mentioned_devices:        list      # ← AJOUT

    # Validateur
    final_answer:      str
    confidence:        str
    confidence_score:  float
    alerts:            list
    discrepancies:     list
    recommendations:   list
    sources_used:      list
     # Evidence Layer
    evidence:          list
    validation_status: dict

    # Baseline réseau attendue
    baseline:          dict

    # Méta
    step:              str
    error:             Optional[str]
    risk_requested:    bool


# ---------------------------------------------------------------------------
# 2. CONSTRUCTION DU GRAPHE
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """
    Construit le graphe LangGraph avec les 3 nœuds.

    TOPOLOGIE :
        START → architect → documentalist → validator → END

    EDGES CONDITIONNELS :
        Si l'Architecte détecte une erreur → passe directement au Validateur
        (qui générera une réponse d'erreur structurée)

    Returns:
        StateGraph compilé prêt à être exécuté
    """
    # Création du graphe avec notre état typé
    graph = StateGraph(RAGState)

    # ── Ajout des nœuds ──────────────────────────────────────────────
    graph.add_node("architect",     architect_node)
    graph.add_node("documentalist", documentalist_node)
    graph.add_node("validator",     validator_node)

    # ── Connexions (edges) ────────────────────────────────────────────
    # START → Architecte (toujours)
    graph.add_edge(START, "architect")

    # Architecte → Documentaliste ou Validateur (conditionnel)
    graph.add_conditional_edges(
        "architect",
        _route_after_architect,
        {
            "documentalist": "documentalist",
            "validator":     "validator",   # si query vide ou erreur
        }
    )

    # Documentaliste → Validateur (toujours)
    graph.add_edge("documentalist", "validator")

    # Validateur → END (toujours)
    graph.add_edge("validator", END)

    return graph.compile()


def _route_after_architect(state: RAGState) -> str:
    """
    Fonction de routage après l'Architecte.

    Si erreur → sauter le Documentaliste et aller directement au Validateur.
    Sinon → flux normal vers le Documentaliste.
    """
    if state.get("step") == "architect_error":
        return "validator"
    if state.get("intent") == "design_update":
        return "validator"
    return "documentalist"


# ---------------------------------------------------------------------------
# 3. PIPELINE RAG
# ---------------------------------------------------------------------------

class RAGPipeline:
    """
    Pipeline RAG complet — interface haut niveau pour l'API et les tests.

    Encapsule le graphe LangGraph et expose des méthodes simples.

    USAGE :
        pipeline = RAGPipeline()

        # Exécution synchrone complète
        result = pipeline.run("Comment configurer OSPF sur R1 ?")
        print(result["final_answer"])
        print(result["confidence"])

        # Streaming step-by-step
        for step in pipeline.stream("Quel est l'état de S1 ?"):
            agent = step.get("step", "?")
            print(f"[{agent}] en cours...")

        # Résumé formaté
        pipeline.print_result(result)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.graph   = build_graph()
        print("[RAGPipeline] Graphe LangGraph initialisé ✓")
        print("  Nœuds : architect → documentalist → validator")

    def run(
    self,
    query: str,
    risk_requested: bool = False
) -> RAGState:
        """
        Exécute le pipeline complet de manière synchrone.

        Args:
            query : question de l'ingénieur réseau

        Returns:
            État final avec final_answer, confidence, alerts, etc.
        """
        print(f"\n{'='*65}")
        print(f"  PIPELINE RAG — EXÉCUTION COMPLÈTE")
        print(f"{'='*65}")
        print(f"  Query : '{query}'")
        print(f"{'='*65}\n")

        initial_state: RAGState = {
            "query":   query,
            "api_key": self.api_key,
            "step":    "start",
            "risk_requested": risk_requested,

            "baseline": {},
            "evidence": [],
            "validation_status": {}
        }

        # Exécution du graphe
        final_state = self.graph.invoke(initial_state)
        return final_state

    def stream(self, query: str):
        """
        Exécute le pipeline avec streaming — yield l'état après chaque nœud.

        Utilisé par l'API FastAPI pour le streaming SSE vers le frontend.

        Yields:
            dict avec les nouvelles clés ajoutées par chaque nœud
        """
        initial_state: RAGState = {
            "query":   query,
            "api_key": self.api_key,
            "step":    "start",
            "baseline": {},
            "evidence": [],
            "validation_status": {}
        }

        for event in self.graph.stream(initial_state):
            yield event

    def print_result(self, state: RAGState) -> None:
        """Affiche le résultat final de manière formatée."""
        print(f"\n{'='*65}")
        print(f"  RÉSULTAT FINAL")
        print(f"{'='*65}")
        print(f"  Confiance    : {state.get('confidence', 'N/A').upper()} "
              f"({state.get('confidence_score', 0):.0%})")
        print(f"  Intent       : {state.get('intent', 'N/A')}")
        print(f"  Stratégie    : {state.get('strategy', 'N/A')}")
        print(f"  RAG chunks   : {state.get('rag_chunks_count', 0)}")
        print(f"  Sources      : {len(state.get('sources_used', []))}")
        print(f"  Evidence     : {len(state.get('evidence', []))}")
        alerts = state.get("alerts", [])
        if alerts:
            print(f"\n  ALERTES ({len(alerts)}) :")
            for a in alerts:
                print(f"    {a}")

        discrepancies = state.get("discrepancies", [])
        if discrepancies:
            print(f"\n  ÉCARTS ({len(discrepancies)}) :")
            for d in discrepancies:
                print(f"    ⚠ {d}")

        recs = state.get("recommendations", [])
        if recs:
            print(f"\n  RECOMMANDATIONS :")
            for r in recs:
                print(f"    → {r}")
        evidence = state.get("evidence", [])

        if evidence:
            print("\n  PREUVES :")
            for e in evidence:
                print(f"    ✓ {e}")
        print(f"\n  RÉPONSE :")
        print(f"{'─'*65}")
        answer = state.get("final_answer", "Aucune réponse générée.")
        print(answer)
        print(f"{'='*65}\n")

    def generate_health_report_if_requested(self, state: RAGState) -> Optional[str]:
        """
    Génère le rapport de santé en PDF UNIQUEMENT si risk_requested=True.
    """
        if not state.get("risk_requested"):
            return None

        import datetime
        date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        devices = "_".join(state.get("devices_consulted", [])) or "rapport"
        filename = f"Rapport_Sante_{devices}_{date_str}.pdf"
        filepath = Path(__file__).parent.parent.parent / "reports" / filename
        filepath.parent.mkdir(exist_ok=True)
        print(f"[ReportGen] Génération en cours → {filepath.resolve()}")
        md_content = state.get("final_answer", "Erreur : contenu vide")
        self._render_markdown_to_pdf(md_content, filepath)
        exists = filepath.exists()
        print(f"[ReportGen] Fichier créé : {exists} | taille={filepath.stat().st_size if exists else 0} octets")
  
        return str(filepath)

    def _sanitize_for_pdf(text: str) -> str:
        """Remplace les caractères hors latin-1 (œ, æ, guillemets typographiques, tirets longs...)."""
        replacements = {
        "œ": "oe", "Œ": "OE",
        "æ": "ae", "Æ": "AE",
        "’": "'", "‘": "'",
        "“": '"', "”": '"',
        "–": "-", "—": "-",
        "…": "...",
        "\u00a0": " ",  # espace insécable
    }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        # Filet de sécurité : supprime tout caractère encore hors latin-1
        return text.encode("latin-1", errors="replace").decode("latin-1")
    @staticmethod
    def _render_markdown_to_pdf(md_text: str, filepath: Path) -> None:
        """Convertit un texte Markdown simple (titres, gras, listes) en PDF avec fpdf2."""
        HUAWEI_RED = (207, 10, 44)
        BLACK = (26, 26, 26)
        md_text = RAGPipeline._sanitize_for_pdf(md_text)
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_margins(15, 15, 15)

        def write_bold_line(text: str, size: int = 11):
            """Gère le **gras** inline en alternant les segments."""
            parts = re.split(r"(\*\*.*?\*\*)", text)
            pdf.set_font("Helvetica", size=size)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    pdf.set_font("Helvetica", "B", size)
                    pdf.set_text_color(*HUAWEI_RED)
                    pdf.write(6, part[2:-2])
                    pdf.set_font("Helvetica", size=size)
                    pdf.set_text_color(*BLACK)
                elif part:
                    pdf.write(6, part)
            pdf.ln(6)

        for raw_line in md_text.split("\n"):
            line = raw_line.rstrip()

            if not line:
                pdf.ln(3)
                continue

            if line.startswith("### "):
                pdf.set_font("Helvetica", "B", 12)
                pdf.set_text_color(*HUAWEI_RED)
                pdf.multi_cell(0, 7, line[4:])
                pdf.set_text_color(*BLACK)
            elif line.startswith("## "):
                pdf.set_font("Helvetica", "B", 14)
                pdf.set_text_color(*HUAWEI_RED)
                pdf.multi_cell(0, 8, line[3:])
                pdf.set_text_color(*BLACK)
            elif line.startswith("# "):
                pdf.set_font("Helvetica", "B", 16)
                pdf.set_text_color(*HUAWEI_RED)
                pdf.multi_cell(0, 9, line[2:])
                pdf.set_text_color(*BLACK)
            elif line.startswith("- ") or line.startswith("* "):
                pdf.set_font("Helvetica", size=11)
                pdf.set_text_color(*BLACK)
                pdf.write(6, "  -  ")
                write_bold_line(line[2:])
            elif line.startswith("```"):
                continue  # ignore les balises de bloc de code
            else:
                write_bold_line(line)

        pdf.output(str(filepath))
# ---------------------------------------------------------------------------
# 4. POINT D'ENTRÉE — Tests interactifs
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import os
    import datetime


    print("=" * 65)
    print("  TEST GRAPH.PY — Pipeline LangGraph Complet")
    print("=" * 65)

    pipeline = RAGPipeline()

    

    # Mode interactif si query passée en argument
    if len(sys.argv) > 1:
        user_query = " ".join(sys.argv[1:])
        
    else:
        # Si aucune question n'est donnée, on prend une question de test
        user_query = "S1 semble fatigué. Peux-tu analyser son uptime et me générer un rapport de santé complet ?"
    # Sinon : mode streaming sur le premier test
    print(f"\n🤔 ANALYSE EN COURS : {user_query}\n")
    
    result = pipeline.run(user_query , risk_requested=False)
    
    # 3. AFFICHAGE DU RÉSULTAT DANS LA CONSOLE
    pipeline.print_result(result)
    
    intent = result.get("intent", "")
    q_lower = user_query.lower()
    is_health = "santé" in q_lower or "fatigué" in q_lower or "prédiction" in q_lower

    if intent == "health_report" or is_health:
        date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"Rapport_Sante_S1_{date_str}.md"
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(result.get("final_answer", "Erreur : Contenu vide"))
        
        print("\n" + "⭐" * 50)
        print("📁 RAPPORT GÉNÉRÉ ET ENREGISTRÉ !")
        print(f"📍 CHEMIN : {os.path.abspath(filename)}")
        print("⭐" * 50)
    else:
        print("\nℹ️  Note : Analyse terminée (Aucun fichier créé car ce n'était pas un rapport de santé).")

    # Fin propre du script
    print(f"\n{'='*65}")
    sys.exit(0)