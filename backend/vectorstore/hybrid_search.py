
"""
vectorstore/hybrid_search.py
=============================
Recherche hybride dense + sparse (BM25) avec Reciprocal Rank Fusion.
 
VERSION 2 — Vraie recherche hybride
 
TROIS MODES DE RECHERCHE :
 
    1. DENSE ONLY (fallback)
       query → embedding dense → cosine similarity → top-K
 
    2. SPARSE ONLY (BM25)
       query → tokenisation BM25 → correspondance exacte → top-K
       Idéal pour : commandes CLI exactes, noms d'interfaces
 
    3. HYBRIDE (dense + sparse + RRF)  ← MODE PAR DÉFAUT
       query → dense + sparse en parallèle → RRF fusion → top-K
       Meilleur des deux mondes
 
RECIPROCAL RANK FUSION (RRF) :
    Formule : score = 1/(k + rang)  avec k=60
    
    Exemple avec 3 résultats :
        Dense  rang 1 → 1/(60+1) = 0.0164
        Dense  rang 2 → 1/(60+2) = 0.0161
        Sparse rang 1 → 1/(60+1) = 0.0164  (même chunk !)
        
        Chunk apparaissant dans les deux → scores additionnés → monte en tête
        → Les résultats cohérents entre dense et sparse sont favorisés
 
POURQUOI RRF PLUTÔT QU'UNE MOYENNE DES SCORES ?
    Les scores cosine (dense) et BM25 (sparse) ont des échelles différentes.
    Impossible de les comparer directement.
    RRF utilise les RANGS (positions) au lieu des scores → échelle uniforme.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchValue,
    Range,
    SparseVector,
    NamedVector,
    NamedSparseVector,
    SearchRequest,
    ScoredPoint,
)
sys.path.append(str(Path(__file__).parent.parent))
from collection_setup import (
    COLLECTION_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
)
from Embedder import Embedder


# ---------------------------------------------------------------------------
# 1. TYPES DE DONNÉES
# ---------------------------------------------------------------------------
@dataclass
class SearchResult:
    """Résultat d'une recherche dans Qdrant."""
    score:         float
    chunk_id:      str
    content:       str
    chunk_type:    str
    page_number:   int
    source_file:   str
    section_title: str
    metadata:      dict
    search_mode:   str = "hybrid"   # "dense" | "sparse" | "hybrid"

    def preview(self, max_chars: int = 120) -> str:
        text = self.content.replace("\n", " ").strip()
        return text[:max_chars] + "..." if len(text) > max_chars else text

    def display(self) -> str:
        return (
            f"  [{self.chunk_type:12s}] score={self.score:.4f} "
            f"| mode={self.search_mode:6s} "
            f"| p.{self.page_number} "
            f"| {self.preview(70)}"
        )


@dataclass
class SearchConfig:
    """Paramètres de recherche."""
    top_k:           int   = 5
    score_threshold: float = 0.0    # RRF scores sont petits (~0.01-0.03)
    rrf_k:           int   = 60     # constante RRF (standard = 60)
    with_payload:    bool  = True
    with_vectors:    bool  = False
    # Poids pour la fusion hybride
    dense_weight:    float = 0.7    # 70% dense, 30% sparse
    sparse_weight:   float = 0.3


# ---------------------------------------------------------------------------
# 2. MOTEUR DE RECHERCHE HYBRIDE
# ---------------------------------------------------------------------------

class HybridSearch:
    """
    Moteur de recherche hybride dense + sparse (BM25) + RRF.

    USAGE :
        searcher = HybridSearch()

        # Recherche hybride (recommandée)
        results = searcher.search("display ip routing-table Huawei")

        # Forcer dense uniquement
        results = searcher.search_dense("topologie VLAN Layer 3")

        # Forcer sparse uniquement (correspondance exacte)
        results = searcher.search_sparse("port link-type trunk")

        # Multimodal (texte + images + tables)
        results = searcher.search_multimodal("configuration VLAN")
    """

    def __init__(
        self,
        host:   str = "localhost",
        port:   int = 6333,
        config: Optional[SearchConfig] = None,
    ):
        self.config   = config or SearchConfig()
        self.client   = QdrantClient(host=host, port=port)
        self.embedder = Embedder(host=host, port=port)

        # Vérification du support sparse
        self._sparse_available = self._check_sparse_support()
        mode = "dense + sparse BM25" if self._sparse_available else "dense only"
        print(f"[HybridSearch] Connecté → {host}:{port} | mode: {mode}")

    def _check_sparse_support(self) -> bool:
        """Vérifie si la collection courante supporte les vecteurs sparse."""
        try:
            info   = self.client.get_collection(COLLECTION_NAME)
            sparse = info.config.params.sparse_vectors
            return sparse is not None and SPARSE_VECTOR_NAME in sparse
        except Exception:
            return False

    # ------------------------------------------------------------------
    # RECHERCHE PRINCIPALE — HYBRIDE
    # ------------------------------------------------------------------

    def search(
        self,
        query:       str,
        top_k:       Optional[int]        = None,
        chunk_types: Optional[list[str]]  = None,
        source_file: Optional[str]        = None,
        page_range:  Optional[tuple]      = None,
        section:     Optional[str]        = None,
        collection:  str                  = COLLECTION_NAME,
    ) -> list[SearchResult]:
        """
        Recherche hybride dense + sparse avec RRF.

        Si la collection ne supporte pas le sparse → fallback dense.

        Args:
            query       : question en langage naturel
            top_k       : nombre de résultats
            chunk_types : ["text", "code_cli", "image_desc", "table", "heading"]
            source_file : filtrer par PDF
            page_range  : (page_min, page_max)
            section     : filtrer par titre de section

        Returns:
            Liste de SearchResult triée par score RRF décroissant
        """
        k      = top_k or self.config.top_k
        filt   = self._build_filter(chunk_types, source_file, page_range, section)

        if self._sparse_available:
            return self._search_hybrid(query, k, filt, collection)
        else:
            return self._search_dense(query, k, filt, collection)

    def search_dense(
        self,
        query:      str,
        top_k:      int    = 5,
        chunk_types: Optional[list[str]] = None,
        collection: str    = COLLECTION_NAME,
    ) -> list[SearchResult]:
        """Recherche dense uniquement."""
        filt = self._build_filter(chunk_types, None, None, None)
        return self._search_dense(query, top_k, filt, collection)

    def search_sparse(
        self,
        query:      str,
        top_k:      int    = 5,
        chunk_types: Optional[list[str]] = None,
        collection: str    = COLLECTION_NAME,
    ) -> list[SearchResult]:
        """
        Recherche sparse BM25 uniquement.
        Idéale pour les commandes CLI exactes.
        """
        if not self._sparse_available:
            print("[HybridSearch] ⚠ Sparse non disponible, fallback dense")
            return self.search_dense(query, top_k, chunk_types, collection)
        filt = self._build_filter(chunk_types, None, None, None)
        return self._search_sparse_only(query, top_k, filt, collection)

    # ------------------------------------------------------------------
    # IMPLÉMENTATIONS INTERNES
    # ------------------------------------------------------------------

    def _search_hybrid(
        self,
        query:      str,
        top_k:      int,
        filt:       Optional[Filter],
        collection: str,
    ) -> list[SearchResult]:
        """
        Recherche hybride via query_points avec prefetch.

        Qdrant supporte nativement la fusion hybride via :
            - prefetch : exécute plusieurs recherches en parallèle
            - fusion   : RRF ou DBSF pour combiner les résultats

        STRUCTURE :
            query_points(
                prefetch=[
                    Prefetch(query=dense_vector, using="dense"),
                    Prefetch(query=sparse_vector, using="sparse"),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
            )
        """
        try:
            from qdrant_client.models import Prefetch, FusionQuery, Fusion

            # Génération des deux types de vecteurs
            dense_vec  = self.embedder.embed_text(query)
            sparse_vec = self.embedder.embed_text_sparse(query)

            response = self.client.query_points(
                collection_name=collection,
                prefetch=[
                    Prefetch(
                        query=dense_vec,
                        using=DENSE_VECTOR_NAME,
                        limit=top_k * 3,   # cherche plus large pour la fusion
                        filter=filt,
                    ),
                    Prefetch(
                        query=sparse_vec,
                        using=SPARSE_VECTOR_NAME,
                        limit=top_k * 3,
                        filter=filt,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=top_k,
                with_payload=self.config.with_payload,
                with_vectors=self.config.with_vectors,
            )

            results = [
                self._to_search_result(r, "hybrid")
                for r in response.points
            ]
            return results

        except (ImportError, Exception) as e:
            # Fallback sur RRF manuel si Prefetch non disponible
            print(f"[HybridSearch] Prefetch non dispo ({e}), RRF manuel...")
            return self._search_hybrid_manual_rrf(query, top_k, filt, collection)

    def _search_hybrid_manual_rrf(
        self,
        query:      str,
        top_k:      int,
        filt:       Optional[Filter],
        collection: str,
    ) -> list[SearchResult]:
        """
        RRF manuel : deux recherches séparées + fusion des rangs.
        Fallback si l'API Prefetch n'est pas disponible.
        """
        # Recherche dense
        dense_results  = self._search_dense(query, top_k * 2, filt, collection)
        # Recherche sparse
        sparse_results = self._search_sparse_only(query, top_k * 2, filt, collection)

        # Fusion RRF
        scores: dict[str, float] = {}
        data:   dict[str, SearchResult] = {}
        k = self.config.rrf_k

        for rank, result in enumerate(dense_results):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0)
            scores[result.chunk_id] += self.config.dense_weight * (1.0 / (k + rank + 1))
            data[result.chunk_id]    = result

        for rank, result in enumerate(sparse_results):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0)
            scores[result.chunk_id] += self.config.sparse_weight * (1.0 / (k + rank + 1))
            if result.chunk_id not in data:
                data[result.chunk_id] = result

        # Tri par score RRF décroissant
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        results    = []
        for chunk_id in sorted_ids[:top_k]:
            result             = data[chunk_id]
            result.score       = round(scores[chunk_id], 6)
            result.search_mode = "hybrid_rrf"
            results.append(result)

        return results

    def _search_dense(
        self,
        query:      str,
        top_k:      int,
        filt:       Optional[Filter],
        collection: str,
    ) -> list[SearchResult]:
        """Recherche dense via query_points."""
        dense_vec = self.embedder.embed_text(query)
        try:
            response = self.client.query_points(
                collection_name=collection,
                query=dense_vec,
                using=DENSE_VECTOR_NAME,
                query_filter=filt,
                limit=top_k,
                score_threshold=self.config.score_threshold,
                with_payload=self.config.with_payload,
                with_vectors=self.config.with_vectors,
            )
            return [self._to_search_result(r, "dense") for r in response.points]
        except AttributeError:
            # Fallback qdrant-client < 1.7
            raw = self.client.search(
                collection_name=collection,
                query_vector=NamedVector(name=DENSE_VECTOR_NAME, vector=dense_vec),
                query_filter=filt,
                limit=top_k,
                score_threshold=self.config.score_threshold,
                with_payload=self.config.with_payload,
            )
            return [self._to_search_result(r, "dense") for r in raw]

    def _search_sparse_only(
        self,
        query:      str,
        top_k:      int,
        filt:       Optional[Filter],
        collection: str,
    ) -> list[SearchResult]:
        """Recherche sparse BM25 uniquement."""
        sparse_vec = self.embedder.embed_text_sparse(query)
        try:
            response = self.client.query_points(
                collection_name=collection,
                query=sparse_vec,
                using=SPARSE_VECTOR_NAME,
                query_filter=filt,
                limit=top_k,
                with_payload=self.config.with_payload,
                with_vectors=self.config.with_vectors,
            )
            return [self._to_search_result(r, "sparse") for r in response.points]
        except Exception as e:
            print(f"[HybridSearch] Sparse search error: {e}")
            return []

    # ------------------------------------------------------------------
    # RECHERCHE MULTIMODALE
    # ------------------------------------------------------------------

    def search_multimodal(
        self,
        query:      str,
        top_k:      int = 5,
        collection: str = COLLECTION_NAME,
    ) -> dict[str, list[SearchResult]]:
        """
        Recherche hybride multimodale : texte + images + tableaux.

        Retourne un dict avec les résultats séparés par type.
        Utilisé par l'agent Documentaliste.
        """
        text_results = self.search(
            query=query, top_k=top_k,
            chunk_types=["text", "code_cli", "heading"],
            collection=collection,
        )
        image_results = self.search(
            query=query, top_k=3,
            chunk_types=["image_desc"],
            collection=collection,
        )
        table_results = self.search(
            query=query, top_k=2,
            chunk_types=["table"],
            collection=collection,
        )
        return {
            "text_results":  text_results,
            "image_results": image_results,
            "table_results": table_results,
        }

    def get_similar_chunks(
        self,
        chunk_id:   str,
        top_k:      int = 5,
        collection: str = COLLECTION_NAME,
    ) -> list[SearchResult]:
        """Trouve les chunks similaires à un chunk existant."""
        try:
            points = self.client.retrieve(
                collection_name=collection,
                ids=[chunk_id],
                with_vectors=True,
            )
            if not points:
                return []

            vectors = points[0].vector
            dense_vec = (
                vectors.get(DENSE_VECTOR_NAME)
                if isinstance(vectors, dict)
                else vectors
            )
            if dense_vec is None:
                return []

            response = self.client.query_points(
                collection_name=collection,
                query=dense_vec,
                using=DENSE_VECTOR_NAME,
                limit=top_k + 1,
                with_payload=True,
            )
            return [
                self._to_search_result(r, "dense")
                for r in response.points
                if str(r.id) != chunk_id
            ][:top_k]
        except Exception as e:
            print(f"[HybridSearch] get_similar error: {e}")
            return []

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _build_filter(
        self,
        chunk_types: Optional[list[str]],
        source_file: Optional[str],
        page_range:  Optional[tuple],
        section:     Optional[str],
    ) -> Optional[Filter]:
        """Construit le filtre Qdrant."""
        must = []

        if chunk_types:
            if len(chunk_types) == 1:
                must.append(FieldCondition(
                    key="chunk_type",
                    match=MatchValue(value=chunk_types[0]),
                ))
            else:
                from qdrant_client.models import Filter as QFilter
                must.append(QFilter(should=[
                    FieldCondition(key="chunk_type", match=MatchValue(value=ct))
                    for ct in chunk_types
                ]))

        if source_file:
            must.append(FieldCondition(
                key="source_file",
                match=MatchValue(value=source_file),
            ))

        if page_range:
            must.append(FieldCondition(
                key="page_number",
                range=Range(gte=page_range[0], lte=page_range[1]),
            ))

        if section:
            must.append(FieldCondition(
                key="section_title",
                match=MatchValue(value=section),
            ))

        return Filter(must=must) if must else None

    def _to_search_result(self, point, mode: str = "hybrid") -> SearchResult:
        """Convertit un ScoredPoint Qdrant en SearchResult."""
        payload = point.payload or {}
        return SearchResult(
            score=round(point.score, 6),
            chunk_id=str(point.id),
            content=payload.get("content", ""),
            chunk_type=payload.get("chunk_type", "unknown"),
            page_number=payload.get("page_number", 0),
            source_file=payload.get("source_file", ""),
            section_title=payload.get("section_title", ""),
            metadata=payload,
            search_mode=mode,
        )


# ---------------------------------------------------------------------------
# 3. UTILITAIRES
# ---------------------------------------------------------------------------

def format_context_for_llm(results: list[SearchResult]) -> str:
    """Formate les résultats en contexte pour injection dans un prompt LLM."""
    if not results:
        return "Aucun contexte pertinent trouvé."

    parts = []
    for i, r in enumerate(results, 1):
        header = (
            f"[SOURCE {i}] p.{r.page_number} | {r.chunk_type} | "
            f"score={r.score:.4f} | mode={r.search_mode} | "
            f"section: {r.section_title or 'N/A'}"
        )
        parts.append(f"{header}\n{r.content}")

    return "\n---\n".join(parts)


def format_multimodal_context(results: dict[str, list[SearchResult]]) -> str:
    """Formate les résultats multimodaux pour le LLM."""
    sections = []
    if results.get("text_results"):
        sections.append("=== TEXTE & COMMANDES CLI ===")
        sections.append(format_context_for_llm(results["text_results"]))
    if results.get("image_results"):
        sections.append("\n=== SCHÉMAS & TOPOLOGIES ===")
        sections.append(format_context_for_llm(results["image_results"]))
    if results.get("table_results"):
        sections.append("\n=== TABLEAUX & DONNÉES ===")
        sections.append(format_context_for_llm(results["table_results"]))
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# 4. POINT D'ENTRÉE — Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    print("=" * 65)
    print("  TEST HYBRID SEARCH — Dense + Sparse BM25 + RRF")
    print("=" * 65)

    searcher = HybridSearch()

    # Test 1 : hybride
    print("\n── Test 1 : Recherche hybride ──")
    q1 = "Comment configurer un trunk VLAN entre deux switches Huawei ?"
    r1 = searcher.search(q1, top_k=3)
    print(f"  Query : '{q1}'")
    print(f"  {len(r1)} résultat(s) :")
    for r in r1:
        print(r.display())

    # Test 2 : sparse BM25 (commande exacte)
    print("\n── Test 2 : Sparse BM25 (commande CLI exacte) ──")
    q2 = "display ip routing-table"
    r2 = searcher.search_sparse(q2, top_k=3)
    print(f"  Query : '{q2}'")
    print(f"  {len(r2)} résultat(s) :")
    for r in r2:
        print(r.display())

    # Test 3 : dense uniquement
    print("\n── Test 3 : Dense uniquement ──")
    q3 = "topologie réseau VLAN Layer 3 switching"
    r3 = searcher.search_dense(q3, top_k=3)
    print(f"  Query : '{q3}'")
    print(f"  {len(r3)} résultat(s) :")
    for r in r3:
        print(r.display())

    # Test 4 : multimodal
    print("\n── Test 4 : Multimodal ──")
    q4 = "VLAN routing topology configuration"
    mm = searcher.search_multimodal(q4, top_k=3)
    print(f"  Query : '{q4}'")
    print(f"  Texte/CLI : {len(mm['text_results'])}")
    print(f"  Images    : {len(mm['image_results'])}")
    print(f"  Tableaux  : {len(mm['table_results'])}")

    # Test 5 : comparaison dense vs sparse vs hybride
    print("\n── Test 5 : Comparaison Dense vs Sparse vs Hybride ──")
    q5 = "port link-type trunk allow-pass vlan"
    print(f"  Query : '{q5}'")

    d = searcher.search_dense(q5, top_k=3)
    s = searcher.search_sparse(q5, top_k=3)
    h = searcher.search(q5, top_k=3)

    print(f"\n  Dense  ({len(d)} résultats) :")
    for r in d[:2]:
        print(f"    {r.display()}")
    print(f"\n  Sparse ({len(s)} résultats) :")
    for r in s[:2]:
        print(f"    {r.display()}")
    print(f"\n  Hybride ({len(h)} résultats) :")
    for r in h[:2]:
        print(f"    {r.display()}")

    print(f"\n{'='*65}")
    print("  Tests terminés ✓")
    print(f"{'='*65}\n")