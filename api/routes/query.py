"""
api/routes/query.py
=====================
Router FastAPI — endpoints liés à l'exécution du pipeline LangGraph.

ENDPOINTS :
    POST /query        → exécution synchrone complète (architect → documentalist → validator)
    POST /query/stream  → streaming SSE, un événement par nœud du graphe
                          (utile pour ChatInterface.tsx : affiche la progression
                          "Architecte en cours..." → "Documentaliste..." → "Validateur...")

DÉPENDANCES :
    Utilise get_pipeline() depuis api/dependencies.py (singleton RAGPipeline),
    injecté via Depends() — pas d'import de main.py, donc pas de risque
    d'import circulaire.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from dependencies import get_pipeline
from schemas import QueryRequest, QueryResponse
from graph import RAGPipeline

router = APIRouter(tags=["query"])


# ---------------------------------------------------------------------------
# POST /query — exécution synchrone
# ---------------------------------------------------------------------------

@router.post("/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    pipeline: RAGPipeline = Depends(get_pipeline),
) -> QueryResponse:
    """
    Exécute le pipeline complet de manière synchrone.

    Appelle pipeline.run(query), qui traverse :
        architect → documentalist → validator
    et retourne l'état final du graphe LangGraph.
    """
    try:
        state = pipeline.run(request.query,
            risk_requested=request.generate_health_report,)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline échoué : {e}")

    if state.get("step") == "architect_error":
        raise HTTPException(
            status_code=400,
            detail=state.get("error", "Requête invalide"),
        )
    report_path = pipeline.generate_health_report_if_requested(state)
    return QueryResponse(
        final_answer=state.get("final_answer", "Aucune réponse générée."),
        intent=state.get("intent", "unknown"),
        strategy=state.get("strategy", "unknown"),
        confidence=state.get("confidence", "low"),
        confidence_score=state.get("confidence_score", 0.0),
        rag_chunks_count=state.get("rag_chunks_count", 0),
        devices_consulted=state.get("devices_consulted", []),
        alerts=state.get("alerts", []),
        discrepancies=state.get("discrepancies", []),
        recommendations=state.get("recommendations", []),
        sources_used=state.get("sources_used", []),
        validation_checks=state.get("validation_checks", {}),   # manquait
        report_filename=Path(report_path).name if report_path else None,
    )


# ---------------------------------------------------------------------------
# POST /query/stream — streaming SSE
# ---------------------------------------------------------------------------

@router.post("/query/stream")
def query_stream(
    request: QueryRequest,
    pipeline: RAGPipeline = Depends(get_pipeline),
) -> StreamingResponse:
    """
    Streaming SSE — envoie un événement après chaque nœud du graphe.

    Le frontend (ChatInterface.tsx) peut écouter ces événements pour
    afficher une progression en temps réel plutôt qu'un unique spinner
    bloquant pendant tout le pipeline.

    Format de chaque événement :
        data: {"node": "architect", "step": "architect_done"}

    Le dernier événement (node == "validator") contient en plus
    tous les champs de la réponse finale.
    """

    def event_generator():
        try:
            for event in pipeline.stream(request.query):
                for node_name, node_state in event.items():
                    payload = {
                        "node": node_name,
                        "step": node_state.get("step", node_name),
                    }
                    if node_name == "validator":
                        payload.update({
                            "final_answer": node_state.get("final_answer", ""),
                            "confidence": node_state.get("confidence", "low"),
                            "confidence_score": node_state.get("confidence_score", 0.0),
                            "alerts": node_state.get("alerts", []),
                            "discrepancies": node_state.get("discrepancies", []),
                            "recommendations": node_state.get("recommendations", []),
                            "sources_used": node_state.get("sources_used", []),
                        })
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")