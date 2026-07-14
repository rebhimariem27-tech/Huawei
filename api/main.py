"""
api/main.py
============
Point d'entrée FastAPI — expose le pipeline LangGraph au frontend Next.js.

ENDPOINTS :
    /query, /query/stream, /ingest, /files → voir routes/
    /health                                → défini ici (dépend de plusieurs ressources transverses)
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent.parent
AGENTS_DIR = ROOT_DIR / "agents"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(AGENTS_DIR))
sys.path.insert(0, str(ROOT_DIR))

from dependencies import INGESTION_AVAILABLE, get_pipeline, get_qdrant  # noqa: E402
from schemas import HealthResponse  # noqa: E402
from routes import api_router  # noqa: E402

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app = FastAPI(
    title="Huawei Multimodal RAG API",
    description="API orchestrant le pipeline LangGraph (Architecte → Documentaliste → Validateur)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.on_event("startup")
def on_startup() -> None:
    print("[API] Démarrage — initialisation du pipeline LangGraph...")
    get_pipeline()
    print("[API] Pipeline prêt ✓")
    if not INGESTION_AVAILABLE:
        print("[API] ⚠ Module d'ingestion introuvable — POST /ingest sera indisponible")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Vérifie la disponibilité de Qdrant, Groq, l'ingestion et les équipements eNSP."""
    import os

    qdrant_ok = False
    client = get_qdrant()
    if client is not None:
        try:
            client.get_collections()
            qdrant_ok = True
        except Exception as e:
            print(f"[Health] Qdrant KO : {e}")

    groq_ok = bool(os.getenv("GROQ_API_KEY"))

    devices_reachable = devices_total = None
    try:
        from mcp_tools import MCPTools
        mcp = MCPTools()
        connectivity = mcp.check_connectivity()
        devices_total = len(connectivity)
        devices_reachable = sum(1 for reachable in connectivity.values() if reachable)
    except Exception as e:
        print(f"[Health] MCPTools indisponible : {e}")

    overall = "ok" if (qdrant_ok and groq_ok) else "degraded"

    return HealthResponse(
        status=overall,
        qdrant_connected=qdrant_ok,
        groq_configured=groq_ok,
        ingestion_available=INGESTION_AVAILABLE,
        devices_reachable=devices_reachable,
        devices_total=devices_total,
        timestamp=datetime.now().isoformat(),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)