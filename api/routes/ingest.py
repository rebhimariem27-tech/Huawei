"""
api/routes/ingest.py
======================
Router FastAPI — endpoints liés à l'ingestion de documents et à leur listing.

ENDPOINTS :
    POST /ingest → upload d'un PDF Huawei + indexation dans Qdrant (Weeks 1-2)
    GET  /files  → liste des documents déjà ingérés (manifest local)

DÉPENDANCES :
    UPLOADS_DIR, FILES_MANIFEST, INGESTION_AVAILABLE, run_ingestion
    depuis api/dependencies.py — mêmes ressources que main.py, pas de
    duplication ni d'import circulaire.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, Path, UploadFile

from dependencies import (
    FILES_MANIFEST,
    INGESTION_AVAILABLE,
    UPLOADS_DIR,
    run_ingestion,
)
from schemas import FileEntry, IngestResponse

router = APIRouter(tags=["ingest"])


# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------

@router.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)) -> IngestResponse:
    """Upload un PDF Huawei et déclenche l'indexation dans Qdrant (Weeks 1-2)."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés")

    if not INGESTION_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "Module d'ingestion introuvable. Vérifie l'import "
                "'from ingestion.pipeline import run_ingestion' dans api/dependencies.py "
                "et adapte-le au chemin réel de ton module Weeks 1-2."
            ),
        )

    file_id = uuid.uuid4().hex[:12]
    dest_path = UPLOADS_DIR / f"{file_id}_{file.filename}"

    try:
        with dest_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        file.file.close()

    try:
        result = run_ingestion(str(dest_path))
        chunks_indexed = getattr(result, "chunks_indexed", None) or (
            result.get("chunks_indexed") if isinstance(result, dict) else None
        )
        status = "success"
        message = f"{file.filename} indexé avec succès dans Qdrant"
    except Exception as e:
        chunks_indexed = None
        status = "error"
        message = f"Échec de l'indexation : {e}"

    entry = FileEntry(
        file_id=file_id,
        filename=file.filename,
        ingested_at=datetime.now().isoformat(),
        chunks_indexed=chunks_indexed,
    )
    _append_to_manifest(entry)

    if status == "error":
        raise HTTPException(status_code=500, detail=message)

    return IngestResponse(
        filename=file.filename,
        file_id=file_id,
        status=status,
        chunks_indexed=chunks_indexed,
        message=message,
    )


def _append_to_manifest(entry: FileEntry) -> None:
    try:
        manifest = json.loads(FILES_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        manifest = []
    manifest.append(entry.model_dump())
    FILES_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# GET /files
# ---------------------------------------------------------------------------

@router.get("/files", response_model=list[FileEntry])
def list_files() -> list[FileEntry]:
    """Liste les documents déjà ingérés (basé sur le manifest local)."""
    try:
        manifest = json.loads(FILES_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        manifest = []
    return [FileEntry(**entry) for entry in manifest]

from fastapi.responses import FileResponse

@router.get("/reports/{filename}")
def download_report(filename: str):
    filepath = Path("reports") / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Rapport introuvable")
    return FileResponse(filepath, media_type="text/markdown", filename=filename)