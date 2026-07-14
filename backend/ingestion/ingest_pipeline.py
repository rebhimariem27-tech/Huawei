"""
ingestion/ingest_pipeline.py
=============================
Pipeline d'ingestion complet : PDF → Qdrant.

METADATA FILTERING :
    Chaque chunk indexé porte source_file = nom du PDF (sans chemin).
    Ex : "TP2&3RLE.pdf", "Test1.pdf"
    
    Le documentalist_agent filtre par source_file pour chercher
    uniquement dans les chunks du PDF concerné.
    
    Qdrant filter : FieldCondition(key="source_file", match=MatchValue(value="Test1.pdf"))
"""

from __future__ import annotations

import sys
import time
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from datetime import datetime

# ---------------------------------------------------------------------------
# CHEMINS — Configuration des imports
# ---------------------------------------------------------------------------
BACKEND_ROOT   = Path(__file__).resolve().parent.parent
INGESTION_DIR  = BACKEND_ROOT / "ingestion"
VECTORSTORE_DIR = BACKEND_ROOT / "vectorstore"

for p in [str(BACKEND_ROOT), str(INGESTION_DIR), str(VECTORSTORE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------------------------
from pdf_extractor   import PDFExtractor, ExtractionConfig
from vision_analyzer import VisionAnalyzer, enrich_chunks_with_vision
from chunker         import Chunker, ChunkerConfig, chunks_to_json

from collection_setup import CollectionManager, COLLECTION_NAME
from Embedder         import Embedder


# ---------------------------------------------------------------------------
# 1. TYPES
# ---------------------------------------------------------------------------

@dataclass
class IngestConfig:
    use_vision_api:   bool         = False
    groq_api_key:     Optional[str] = None
    image_dpi:        int          = 200
    min_text_length:  int          = 20
    max_chunk_chars:  int          = 800
    min_chunk_chars:  int          = 80
    overlap_chars:    int          = 150
    qdrant_host:      str          = "localhost"
    qdrant_port:      int          = 6333
    collection_name:  str          = COLLECTION_NAME
    force_reindex:    bool         = False
    save_chunks_json: bool         = True


@dataclass
class IngestReport:
    source_file:     str
    status:          str   = "pending"
    started_at:      str   = ""
    finished_at:     str   = ""
    duration_sec:    float = 0.0
    pages_count:     int   = 0
    raw_chunks:      int   = 0
    images_found:    int   = 0
    images_analyzed: int   = 0
    final_chunks:    int   = 0
    chunks_by_type:  dict  = field(default_factory=dict)
    points_indexed:  int   = 0
    points_errors:   int   = 0
    error_message:   str   = ""

    def to_dict(self) -> dict:
        return {
            "source_file":     self.source_file,
            "status":          self.status,
            "started_at":      self.started_at,
            "finished_at":     self.finished_at,
            "duration_sec":    round(self.duration_sec, 2),
            "pages_count":     self.pages_count,
            "raw_chunks":      self.raw_chunks,
            "images_found":    self.images_found,
            "images_analyzed": self.images_analyzed,
            "final_chunks":    self.final_chunks,
            "chunks_by_type":  self.chunks_by_type,
            "points_indexed":  self.points_indexed,
            "points_errors":   self.points_errors,
            "error_message":   self.error_message,
        }

    def display(self) -> None:
        icon = {"success": "✅", "skipped": "⏭", "error": "❌"}.get(self.status, "❓")
        print(f"\n{'='*60}")
        print(f"  RAPPORT D'INGESTION {icon}")
        print(f"{'='*60}")
        print(f"  Fichier     : {Path(self.source_file).name}")
        print(f"  Status      : {self.status}")
        print(f"  Durée       : {self.duration_sec:.1f}s")
        print(f"  Pages       : {self.pages_count}")
        print(f"  Chunks bruts: {self.raw_chunks}")
        print(f"  Images      : {self.images_found} trouvées, {self.images_analyzed} analysées")
        print(f"  Chunks final: {self.final_chunks}")
        for t, c in sorted(self.chunks_by_type.items()):
            print(f"    {t:15s}: {c}")
        print(f"  Indexés     : {self.points_indexed} ✓  Erreurs: {self.points_errors}")
        if self.error_message:
            print(f"  Erreur      : {self.error_message}")
        print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# 2. PIPELINE
# ---------------------------------------------------------------------------

class IngestPipeline:
    """
    Pipeline d'ingestion complet PDF → Qdrant.

    METADATA FILTERING :
        source_file est stocké comme NOM DE FICHIER SEUL (sans chemin).
        "F:/docs/Test1.pdf" → source_file = "Test1.pdf"
        
        Les agents filtrent ensuite :
        qdrant.search(query, source_file="Test1.pdf")
    """

    def __init__(self, config: Optional[IngestConfig] = None):
        self.config = config or IngestConfig()
        self._init_components()

    def _init_components(self) -> None:
        if self.config.groq_api_key:
            os.environ["GROQ_API_KEY"] = self.config.groq_api_key

        self.extractor = PDFExtractor(ExtractionConfig(
            extract_images=True,
            extract_text=True,
            image_dpi=self.config.image_dpi,
            min_text_length=self.config.min_text_length,
        ))
        self.analyzer = VisionAnalyzer(mock_mode=not self.config.use_vision_api)
        self.chunker  = Chunker(ChunkerConfig(
            max_chunk_chars=self.config.max_chunk_chars,
            min_chunk_chars=self.config.min_chunk_chars,
            overlap_chars=self.config.overlap_chars,
        ))
        self.collection_manager = CollectionManager(
            host=self.config.qdrant_host,
            port=self.config.qdrant_port,
        )
        self.embedder = Embedder(
            host=self.config.qdrant_host,
            port=self.config.qdrant_port,
        )

        print(f"[IngestPipeline] Initialisé")
        print(f"  Vision API : {'Groq' if self.config.use_vision_api else 'Mock'}")
        print(f"  Qdrant     : {self.config.qdrant_host}:{self.config.qdrant_port}")
        print(f"  Collection : {self.config.collection_name}")

    def ingest(self, pdf_path: str | Path) -> IngestReport:
        """
        Ingère un PDF dans Qdrant.

        IMPORTANT : source_file = nom du fichier UNIQUEMENT (pas le chemin complet).
        Cela permet le filtrage metadata cohérent quel que soit l'OS ou le chemin.
        """
        pdf_path   = Path(pdf_path)
        # NOM SEUL — clé du metadata filtering
        pdf_name   = pdf_path.name

        report     = IngestReport(source_file=pdf_name, started_at=datetime.now().isoformat())
        start_time = time.time()

        print(f"\n{'='*60}")
        print(f"  INGESTION : {pdf_name}")
        print(f"  source_file (metadata) : '{pdf_name}'")
        print(f"{'='*60}")

        try:
            if not pdf_path.exists():
                raise FileNotFoundError(f"Fichier introuvable : {pdf_path}")
            if pdf_path.suffix.lower() != ".pdf":
                raise ValueError(f"Pas un PDF : {pdf_path}")

            self.collection_manager.setup()

            # Vérification doublon par nom de fichier
            if not self.config.force_reindex and self._already_indexed(pdf_name):
                print(f"  ⏭ '{pdf_name}' déjà indexé (--force pour réindexer)")
                report.status      = "skipped"
                report.finished_at = datetime.now().isoformat()
                report.duration_sec = time.time() - start_time
                return report

            if self.config.force_reindex:
                print(f"  🔄 Réindexation forcée...")
                self.embedder.delete_by_source(pdf_name)

            # ── Étape 1 : Extraction ──────────────────────────────────
            print(f"\n  [1/4] Extraction PDF...")
            raw_chunks = self.extractor.extract(str(pdf_path))

            # METADATA FILTERING : on normalise source_file sur tous les chunks
            for chunk in raw_chunks:
                chunk.source_file = pdf_name

            report.pages_count  = len({c.page_number for c in raw_chunks})
            report.raw_chunks   = len(raw_chunks)
            report.images_found = sum(
                1 for c in raw_chunks
                if getattr(c.chunk_type, "value", str(c.chunk_type)) == "image"
            )
            print(f"        {report.raw_chunks} chunks | "
                  f"{report.pages_count} pages | {report.images_found} images")

            # ── Étape 2 : Vision ──────────────────────────────────────
            print(f"\n  [2/4] Vision [{'Groq' if self.config.use_vision_api else 'Mock'}]...")
            enriched = enrich_chunks_with_vision(
                raw_chunks, self.analyzer
            )
            report.images_analyzed = sum(
                1 for c in enriched
                if getattr(c.chunk_type, "value", "") == "image"
                and c.metadata.get("vision_description")
            )

            # ── Étape 3 : Chunking ────────────────────────────────────
            print(f"\n  [3/4] Chunking...")
            final_chunks = self.chunker.chunk(enriched)
            report.final_chunks  = len(final_chunks)
            report.chunks_by_type = dict(Counter(
                getattr(fc.chunk_type, "value", str(fc.chunk_type))
                for fc in final_chunks
            ))

            # Sauvegarde JSON
            if self.config.save_chunks_json:
                json_path = pdf_path.parent / f"{pdf_path.stem}_chunks.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(chunks_to_json(final_chunks), f, ensure_ascii=False, indent=2)
                print(f"        → {json_path.name}")

            # ── Étape 4 : Indexation ──────────────────────────────────
            print(f"\n  [4/4] Embedding + Indexation Qdrant...")
            stats               = self.embedder.embed_and_store(
                final_chunks, collection_name=self.config.collection_name
            )
            report.points_indexed = stats["success"]
            report.points_errors  = stats["errors"]
            report.status         = "success"

        except Exception as e:
            report.status        = "error"
            report.error_message = str(e)
            print(f"\n  ❌ Erreur : {e}")
            import traceback; traceback.print_exc()
        finally:
            report.finished_at  = datetime.now().isoformat()
            report.duration_sec = time.time() - start_time

        report.display()
        return report

    def ingest_folder(self, folder_path: str | Path, pattern: str = "*.pdf") -> list[IngestReport]:
        folder    = Path(folder_path)
        pdf_files = sorted(folder.glob(pattern))
        if not pdf_files:
            print(f"[IngestPipeline] Aucun PDF dans {folder}")
            return []

        print(f"\n[IngestPipeline] Batch : {len(pdf_files)} PDFs")
        reports     = []
        total_start = time.time()

        for i, f in enumerate(pdf_files, 1):
            print(f"\n[{i}/{len(pdf_files)}] {f.name}")
            reports.append(self.ingest(f))

        duration      = time.time() - total_start
        success_count = sum(1 for r in reports if r.status == "success")
        total_indexed = sum(r.points_indexed for r in reports)

        print(f"\n{'='*60}")
        print(f"  BATCH terminé — {success_count}/{len(reports)} succès")
        print(f"  {total_indexed} points indexés en {duration:.1f}s")
        print(f"{'='*60}\n")

        batch_path = folder / "ingest_report.json"
        with open(batch_path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in reports], f, ensure_ascii=False, indent=2)
        return reports

    def _already_indexed(self, pdf_name: str) -> bool:
        """Vérifie si pdf_name est déjà dans Qdrant (filtre sur source_file)."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        try:
            results = self.embedder.client.scroll(
                collection_name=self.config.collection_name,
                scroll_filter=Filter(must=[
                    FieldCondition(key="source_file", match=MatchValue(value=pdf_name))
                ]),
                limit=1, with_payload=False, with_vectors=False,
            )
            return len(results[0]) > 0
        except Exception:
            return False

    def get_collection_stats(self) -> dict:
        return self.collection_manager.get_info()

    def list_indexed_files(self) -> list[str]:
        """Liste tous les PDFs indexés dans Qdrant."""
        stats = self.collection_manager.get_info()
        if "error" in stats:
            return []
        try:
            all_points, _ = self.embedder.client.scroll(
                collection_name=self.config.collection_name,
                limit=1000, with_payload=True, with_vectors=False,
            )
            sources = {p.payload.get("source_file", "") for p in all_points if p.payload}
            return sorted(sources - {""})
        except Exception:
            return []


# ---------------------------------------------------------------------------
# 3. POINT D'ENTRÉE
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("Usage : python ingest_pipeline.py <chemin.pdf|dossier> [options]")
        print()
        print("Options :")
        print("  --api          → vision Groq (GROQ_API_KEY requis)")
        print("  --force        → forcer la réindexation")
        print("  --batch        → ingérer tous les PDFs du dossier")
        print("  --list         → lister les PDFs déjà indexés")
        sys.exit(1)

    target     = sys.argv[1]
    use_api    = "--api"   in sys.argv
    force      = "--force" in sys.argv
    batch_mode = "--batch" in sys.argv
    list_mode  = "--list"  in sys.argv

    config = IngestConfig(
        use_vision_api=use_api,
        groq_api_key=os.getenv("GROQ_API_KEY"),
        force_reindex=force,
    )
    pipeline = IngestPipeline(config=config)

    if list_mode:
        files = pipeline.list_indexed_files()
        print(f"\nPDFs indexés ({len(files)}) :")
        for f in files:
            print(f"  • {f}")
        sys.exit(0)

    if batch_mode:
        pipeline.ingest_folder(target)
    else:
        pipeline.ingest(target)

    stats = pipeline.get_collection_stats()
    print(f"Collection '{stats.get('name')}' → {stats.get('points_count')} points\n")