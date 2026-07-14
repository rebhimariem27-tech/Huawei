"""
vectorstore/embedder.py
=======================
Transformation des FinalChunks en vecteurs et insertion dans Qdrant.

CONCEPT CLÉ — Qu'est-ce qu'un embedding ?
    Un embedding = une représentation numérique du sens d'un texte.
    "VLAN routing topology" → [0.12, -0.34, 0.87, ..., 0.23]  (384 nombres)

    Deux textes sémantiquement proches auront des vecteurs proches
    dans l'espace à 384 dimensions. C'est ce qui permet la recherche
    "trouve-moi des chunks similaires à cette question".

FASTEMBED — Pourquoi ?
    FastEmbed utilise ONNX Runtime au lieu de PyTorch :
    - 5x plus rapide que sentence-transformers
    - 10x moins de RAM
    - Intégration native avec Qdrant (même équipe)
    - Modèle BAAI/bge-small-en-v1.5 : excellent pour textes techniques

STRATÉGIE D'EMBEDDING :
    On n'embed pas juste chunk.content, mais chunk.content_with_context :
    "Section: Lab 1-3\nType: code_cli\n[S1]vlan batch 3 to 7..."
    Le contexte de section améliore la précision de la recherche.

INSERTION EN BATCH :
    On n'insère pas chunk par chunk (trop lent) mais par batches de 32.
    Qdrant optimise les insertions en batch → 10x plus rapide.

FLUX :
    FinalChunk → content_with_context → FastEmbed → vecteur 384D
                                                          ↓
                                              Qdrant PointStruct
                                            (id, vector, payload)
                                                          ↓
                                              Collection Qdrant
"""

from __future__ import annotations

import sys
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Generator

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, SparseVector

# Import depuis collection_setup — source unique de vérité pour le modèle
sys.path.append(str(Path(__file__).parent))
from collection_setup import (
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    SPARSE_MODEL_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    CollectionManager,
)
# Import du type FinalChunk depuis ingestion
sys.path.append(str(Path(__file__).parent.parent / "ingestion"))
from chunker import FinalChunk, FinalChunkType
# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------

BATCH_SIZE = 32


@dataclass
class EmbedderConfig:
    batch_size:    int  = BATCH_SIZE
    show_progress: bool = True
    upsert_mode:   bool = True
    enable_sparse: bool = True   # False = mode dense uniquement (fallback)


# ---------------------------------------------------------------------------
# 2. EMBEDDER
# ---------------------------------------------------------------------------

class Embedder:
    """
    Vectorise les FinalChunks (dense + sparse) et les insère dans Qdrant.

    USAGE :
        embedder = Embedder()
        stats    = embedder.embed_and_store(final_chunks)
    """

    def __init__(
        self,
        host:   str = "localhost",
        port:   int = 6333,
        config: Optional[EmbedderConfig] = None,
    ):
        self.config  = config or EmbedderConfig()
        self.client  = QdrantClient(host=host, port=port)
        self._dense_model  = None   # chargé au premier appel
        self._sparse_model = None   # chargé au premier appel
        print(f"[Embedder] Connecté à Qdrant → {host}:{port}")

    # ------------------------------------------------------------------
    # CHARGEMENT DES MODÈLES (lazy)
    # ------------------------------------------------------------------

    def _get_dense_model(self):
        """Charge BAAI/bge-small-en-v1.5 (lazy loading)."""
        if self._dense_model is None:
            print(f"[Embedder] Chargement modèle dense : {EMBEDDING_MODEL}")
            from fastembed import TextEmbedding
            self._dense_model = TextEmbedding(model_name=EMBEDDING_MODEL)
            print("[Embedder] Modèle dense chargé ✓")
        return self._dense_model

    def _get_sparse_model(self):
        """Charge Qdrant/bm25 pour les vecteurs sparse (lazy loading)."""
        if self._sparse_model is None:
            print(f"[Embedder] Chargement modèle sparse : {SPARSE_MODEL_NAME}")
            from fastembed import SparseTextEmbedding
            self._sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)
            print("[Embedder] Modèle sparse BM25 chargé ✓")
        return self._sparse_model

    # ------------------------------------------------------------------
    # EMBEDDING PUBLIC
    # ------------------------------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        """Embedding dense d'un texte — utilisé par hybrid_search."""
        model = self._get_dense_model()
        return list(self._get_dense_model().embed([text]))[0].tolist()

    def embed_text_sparse(self, text: str) -> SparseVector:
        """Embedding sparse BM25 d'un texte — utilisé par hybrid_search."""
        model  = self._get_sparse_model()
        result = list(model.embed([text]))[0]
        return SparseVector(
            indices=result.indices.tolist(),
            values=result.values.tolist(),
        )

    def embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
        """Embedding dense par batch."""
        model = self._get_dense_model()
        return [e.tolist() for e in model.embed(texts)]

    def embed_texts_sparse_batch(self, texts: list[str]) -> list[SparseVector]:
        """Embedding sparse BM25 par batch."""
        model   = self._get_sparse_model()
        results = list(model.embed(texts))
        return [
            SparseVector(
                indices=r.indices.tolist(),
                values=r.values.tolist(),
            )
            for r in results
        ]

    # ------------------------------------------------------------------
    # INSERTION PRINCIPALE
    # ------------------------------------------------------------------

    def embed_and_store(
        self,
        chunks:          list[FinalChunk],
        collection_name: str = COLLECTION_NAME,
    ) -> dict:
        """
        Vectorise (dense + sparse) et insère les chunks dans Qdrant.

        Returns:
            Stats {total, success, errors}
        """
        if not chunks:
            print("[Embedder] Aucun chunk à indexer.")
            return {"total": 0, "success": 0, "errors": 0}

        # Détection du support sparse dans la collection
        sparse_enabled = self.config.enable_sparse and self._collection_has_sparse(
            collection_name
        )

        print(f"\n[Embedder] Indexation de {len(chunks)} chunks...")
        print(f"  Collection : {collection_name}")
        print(f"  Dense      : {EMBEDDING_MODEL}")
        print(f"  Sparse BM25: {'✓ activé' if sparse_enabled else '✗ désactivé (collection sans sparse)'}")
        print(f"  Batch size : {self.config.batch_size}")

        # Vérification collection
        collections = [c.name for c in self.client.get_collections().collections]
        if collection_name not in collections:
            print(f"[Embedder] ✗ Collection '{collection_name}' introuvable.")
            print("  → Lance : python collection_setup.py --reset")
            return {"total": len(chunks), "success": 0, "errors": len(chunks)}

        stats   = {"total": len(chunks), "success": 0, "errors": 0}
        batches = list(self._make_batches(chunks, self.config.batch_size))
        print(f"  Batches    : {len(batches)}\n")

        for batch_idx, batch in enumerate(batches, start=1):
            try:
                inserted = self._process_batch(
                    batch, collection_name, batch_idx,
                    len(batches), sparse_enabled,
                )
                stats["success"] += inserted
            except Exception as e:
                print(f"  ✗ Batch {batch_idx} échoué : {e}")
                stats["errors"] += len(batch)

        self._print_stats(stats, collection_name)
        return stats

    def _process_batch(
        self,
        batch:          list[FinalChunk],
        collection_name: str,
        batch_idx:      int,
        total_batches:  int,
        sparse_enabled: bool,
    ) -> int:
        """
        Traite un batch : double embedding + upsert.

        STRUCTURE DU VECTEUR NOMMÉ :
            Qdrant attend un dict pour les collections multi-vecteurs :
            {
                "dense":  [0.12, -0.34, ...],   # liste de floats
                "sparse": SparseVector(          # vecteur creux
                    indices=[42, 187, 903],
                    values =[1.5, 0.8, 1.1]
                )
            }
        """
        texts = [chunk.content_with_context for chunk in batch]

        # Embedding dense (obligatoire)
        dense_vectors = self.embed_texts_batch(texts)

        # Embedding sparse BM25 (si collection le supporte)
        sparse_vectors = None
        if sparse_enabled:
            sparse_vectors = self.embed_texts_sparse_batch(texts)

        # Construction des PointStructs
        points = []
        for i, (chunk, dense_vec) in enumerate(zip(batch, dense_vectors)):

            # ID en UUID object (requis par qdrant-client >= 1.7)
            try:
                point_id =uuid.UUID(chunk.chunk_id)
            except (ValueError, AttributeError):
                point_id = chunk.chunk_id

            # Vecteurs : dense uniquement ou dense + sparse
            if sparse_vectors:
                vector = {
                    DENSE_VECTOR_NAME:  dense_vec,
                    SPARSE_VECTOR_NAME: sparse_vectors[i],
                }
            else:
                vector = {DENSE_VECTOR_NAME: dense_vec}

            point = PointStruct(
                id=point_id,
                vector=vector,
                payload=chunk.to_qdrant_payload(),
            )
            points.append(point)

        # Upsert dans Qdrant
        self.client.upsert(
            collection_name=collection_name,
            points=points,
        )

        if self.config.show_progress:
            types_str = ", ".join(set(c.chunk_type.value for c in batch))
            mode_str  = "dense+sparse" if sparse_vectors else "dense only"
            print(
                f"  Batch {batch_idx}/{total_batches} "
                f"| [{batch[0].chunk_index}→{batch[-1].chunk_index}] "
                f"| {mode_str} "
                f"| types: {types_str} "
                f"| ✓ {len(points)} pts"
            )

        return len(points)

    # ------------------------------------------------------------------
    # UTILITAIRES
    # ------------------------------------------------------------------

    def _collection_has_sparse(self, collection_name: str) -> bool:
        """Vérifie si la collection supporte les vecteurs sparse."""
        try:
            info          = self.client.get_collection(collection_name)
            sparse_config = info.config.params.sparse_vectors
            return sparse_config is not None and SPARSE_VECTOR_NAME in sparse_config
        except Exception:
            return False

    @staticmethod
    def _make_batches(items: list, batch_size: int) -> Generator[list, None, None]:
        for i in range(0, len(items), batch_size):
            yield items[i:i + batch_size]

    def _print_stats(self, stats: dict, collection_name: str) -> None:
        try:
            info        = self.client.get_collection(collection_name)
            total_in_db = info.points_count
        except Exception:
            total_in_db = "?"

        print(f"\n{'='*50}")
        print(f"  RÉSUMÉ INDEXATION")
        print(f"{'='*50}")
        print(f"  Chunks traités : {stats['total']}")
        print(f"  Insérés        : {stats['success']} ✓")
        print(f"  Erreurs        : {stats['errors']}")
        print(f"  Total en base  : {total_in_db} points")
        print(f"{'='*50}\n")

    def get_collection_count(self, collection_name: str = COLLECTION_NAME) -> int:
        try:
            return self.client.get_collection(collection_name).points_count
        except Exception:
            return 0

    def delete_by_source(
        self,
        source_file:     str,
        collection_name: str = COLLECTION_NAME,
    ) -> int:
        """Supprime tous les points d'un fichier source."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        result = self.client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[FieldCondition(
                    key="source_file",
                    match=MatchValue(value=source_file),
                )]
            ),
        )
        print(f"[Embedder] Supprimé les points de '{source_file}'")
        return result.status


# ---------------------------------------------------------------------------
# 3. POINT D'ENTRÉE
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    ingestion_path = Path(__file__).parent.parent / "ingestion"
    sys.path.insert(0, str(ingestion_path))

    from pdf_extractor import PDFExtractor, ExtractionConfig
    from vision_analyzer import VisionAnalyzer, enrich_chunks_with_vision
    from chunker import Chunker

    if len(sys.argv) < 2:
        print("Usage : python embedder.py <chemin.pdf> [--api]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    use_api  = "--api" in sys.argv

    # Setup collection (reset si nécessaire pour avoir le sparse)
    print("=" * 55)
    print("  ÉTAPE 0 — Setup collection Qdrant (dense + sparse)")
    print("=" * 55)
    manager = CollectionManager()
    manager.setup()

    # Extraction
    print("\n" + "=" * 55)
    print("  ÉTAPE 1 — Extraction PDF")
    print("=" * 55)
    extractor  = PDFExtractor(ExtractionConfig(extract_images=True, image_dpi=200))
    raw_chunks = extractor.extract(pdf_path)

    # Vision
    print("\n" + "=" * 55)
    print(f"  ÉTAPE 2 — Vision [{'Groq' if use_api else 'Mock'}]")
    print("=" * 55)
    analyzer        = VisionAnalyzer(mock_mode=not use_api)
    enriched_chunks = enrich_chunks_with_vision(raw_chunks, analyzer)

    # Chunking
    print("\n" + "=" * 55)
    print("  ÉTAPE 3 — Chunking")
    print("=" * 55)
    chunker      = Chunker()
    final_chunks = chunker.chunk(enriched_chunks)

    # Embedding + indexation
    print("\n" + "=" * 55)
    print("  ÉTAPE 4 — Embedding dense + sparse + Indexation Qdrant")
    print("=" * 55)
    embedder = Embedder()
    stats    = embedder.embed_and_store(final_chunks)

    # Vérification
    manager.verify_collection()
    print(f"  Pipeline terminé ✓  —  {stats['success']} chunks indexés\n")