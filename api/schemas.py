"""
api/schemas.py
===============
Modèles Pydantic partagés entre tous les routers.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# /query
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query:       str           = Field(..., min_length=1)
    target_file: Optional[str] = Field(None, description="Filtrer sur un PDF spécifique")
    use_ssh:     bool          = Field(True, description="Activer la collecte SSH eNSP")
    generate_health_report: bool = Field(
        False, description="Coché côté frontend → génère un rapport .md téléchargeable"
    )



class QueryResponse(BaseModel):
    final_answer:      str
    intent:            str
    strategy:          str
    confidence:        str
    confidence_score:  float
    rag_chunks_count:  int
    devices_consulted: list[str]    = []
    alerts:            list[str]    = []
    discrepancies:     list[str]    = []
    recommendations:   list[str]    = []
    sources_used:      list[dict]   = []
    validation_checks: dict         = {}
    report_filename:   Optional[str] = None   # ← ajouté


# ---------------------------------------------------------------------------
# /ingest
# ---------------------------------------------------------------------------

class IngestResponse(BaseModel):
    filename:       str
    file_id:        str
    status:         str
    chunks_indexed: Optional[int]  = None
    chunks_by_type: dict           = {}
    duration_sec:   float          = 0.0
    message:        str


# ---------------------------------------------------------------------------
# /files
# ---------------------------------------------------------------------------

class FileEntry(BaseModel):
    file_id:        str
    filename:       str
    ingested_at:    str
    chunks_indexed: Optional[int] = None


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status:              str
    qdrant_connected:    bool
    groq_configured:     bool
    ingestion_available: bool
    devices_reachable:   Optional[int] = None
    devices_total:       Optional[int] = None
    timestamp:           str
