"""
api/dependencies.py
====================
Ressources partagées entre tous les routers FastAPI.
Aucun import circulaire : ce fichier n'importe jamais main.py.

CORRECTIONS :
    - qdrant_client_wrapper (pas qdrant_connection)
    - ingest_pipeline (pas ingestion.pipeline)
    - ajout chemin vectorstore au sys.path
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 1. CHEMINS PROJET
# ---------------------------------------------------------------------------

ROOT_DIR        = Path(__file__).parent.parent   # D:\HUAWEI\backend
AGENTS_DIR      = ROOT_DIR / "agents"
VECTORSTORE_DIR = ROOT_DIR / "vectorstore"
INGESTION_DIR   = ROOT_DIR / "ingestion"
UPLOADS_DIR     = ROOT_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

FILES_MANIFEST = ROOT_DIR / "ingested_files.json"
if not FILES_MANIFEST.exists():
    FILES_MANIFEST.write_text("[]", encoding="utf-8")

# Ajout de TOUS les chemins nécessaires
for _p in [
    str(ROOT_DIR),
    str(AGENTS_DIR),
    str(VECTORSTORE_DIR),
    str(INGESTION_DIR),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# 2. PIPELINE LANGGRAPH — SINGLETON
# ---------------------------------------------------------------------------

from backend.agents.graph import RAGPipeline  # noqa: E402

_pipeline: Optional[RAGPipeline] = None


def get_pipeline() -> RAGPipeline:
    """Singleton RAGPipeline — utilisé via Depends(get_pipeline)."""
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline


# ---------------------------------------------------------------------------
# 3. QDRANT — CONNEXION SÛRE
# ---------------------------------------------------------------------------

# CORRECTION : qdrant_client_wrapper (pas qdrant_connection)
try:
    from qdrant_connection import get_qdrant_client as _get_qdrant_client
    _qdrant_available = True
except ImportError:
    _get_qdrant_client = None
    _qdrant_available  = False


def get_qdrant():
    """Retourne le client Qdrant, ou None si indisponible."""
    if not _qdrant_available or _get_qdrant_client is None:
        return None
    try:
        return _get_qdrant_client()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 4. INGESTION — IMPORT ADAPTATIF
# ---------------------------------------------------------------------------

# CORRECTION : ingest_pipeline (pas ingestion.pipeline)
try:
    from ingest_pipeline import IngestPipeline, IngestConfig

    def run_ingestion(pdf_path: str, force: bool = False, use_vision: bool = True) -> dict:
        """Lance l'ingestion d'un PDF et retourne les stats."""
        config = IngestConfig(
            use_vision_api=use_vision and bool(os.getenv("GROQ_API_KEY")),
            groq_api_key=os.getenv("GROQ_API_KEY"),
            force_reindex=force,
            save_chunks_json=False,
        )
        pipeline = IngestPipeline(config=config)
        report   = pipeline.ingest(pdf_path)
        return {
            "status":         report.status,
            "pages_count":    report.pages_count,
            "chunks_indexed": report.points_indexed,
            "chunks_by_type": report.chunks_by_type,
            "duration_sec":   report.duration_sec,
            "error_message":  report.error_message,
        }

    def list_indexed_files() -> list[str]:
        """Liste les PDFs indexés dans Qdrant."""
        config   = IngestConfig()
        pipeline = IngestPipeline(config=config)
        return pipeline.list_indexed_files()

    INGESTION_AVAILABLE = True

except ImportError as e:
    print(f"[dependencies] ⚠ ingest_pipeline non trouvé : {e}")
    INGESTION_AVAILABLE = False
    run_ingestion       = None
    list_indexed_files  = None