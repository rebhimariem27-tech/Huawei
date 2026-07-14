"""
vectorstore/qdrant_connection.py
=============================
Couche d'abstraction entre les agents et Qdrant.

POURQUOI CE FICHIER ?
    Sans cette couche, les agents font des appels Qdrant directs :
        client.search(collection_name="huawei_industrial_docs",
                      query_vector=vector, limit=5, ...)

    C'est fragile : si on change de collection ou de config,
    on modifie 10 endroits différents dans le code.

    Avec cette couche, les agents font simplement :
        qdrant.search("Comment configurer OSPF ?")
        qdrant.get_by_type("image_desc")
        qdrant.get_stats()

    Un seul endroit à modifier si la config change.
    C'est le principe de la FAÇADE (design pattern).

RESPONSABILITÉS :
    - Connexion et reconnexion automatique à Qdrant
    - Toutes les opérations CRUD sur les points
    - Statistiques et monitoring de la collection
    - Health check (Qdrant est-il disponible ?)

UTILISÉ PAR :
    - agents/documentalist_agent.py  → search()
    - agents/architect_agent.py      → get_stats()
    - agents/validator_agent.py      → get_by_id()
    - api/routes/query.py            → search()
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from qdrant_client import QdrantClient

from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchValue,
    Range,
    PointStruct,
    ScoredPoint,
)

sys.path.append(str(Path(__file__).parent))
from collection_setup import COLLECTION_NAME, EMBEDDING_MODEL
from Embedder import Embedder
from hybrid_search import HybridSearch, SearchResult, SearchConfig, format_multimodal_context


# ---------------------------------------------------------------------------
# 1. CONFIGURATION CONNEXION
# ---------------------------------------------------------------------------

@dataclass
class QdrantConnectionConfig:
    """
    Paramètres de connexion à Qdrant.

    Centralisé ici — un seul endroit pour changer host/port
    quand on passe de local à cloud.
    """
    host:            str = "localhost"
    port:            int = 6333
    collection_name: str = COLLECTION_NAME
    timeout:         int = 10    # secondes avant timeout


# ---------------------------------------------------------------------------
# 2. CLIENT WRAPPER
# ---------------------------------------------------------------------------

class QdrantWrapper:
    """
    Façade unifiée pour toutes les opérations Qdrant.

    C'est LE point d'entrée unique pour les agents.
    Combine CollectionManager + Embedder + HybridSearch
    en une interface simple et cohérente.

    USAGE :
        qdrant = QdrantWrapper()

        # Recherche sémantique
        results = qdrant.search("configurer trunk LACP")

        # Recherche multimodale
        results = qdrant.search_multimodal("topologie VLAN Layer 3")

        # Stats
        stats = qdrant.get_stats()

        # Health check
        if qdrant.is_healthy():
            print("Qdrant OK")
    """

    def __init__(self, config: Optional[QdrantConnectionConfig] = None):
        self.config  = config or QdrantConnectionConfig()
        self._client = None
        self._search = None
        self._embed  = None
        self._connect()

    # ------------------------------------------------------------------
    # CONNEXION
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """
        Initialise la connexion à Qdrant.

        On initialise QdrantClient, Embedder et HybridSearch
        avec les mêmes paramètres de connexion → cohérence garantie.
        """
        self._client = QdrantClient(
            host=self.config.host,
            port=self.config.port,
        )
        self._embed = Embedder(
            host=self.config.host,
            port=self.config.port,
        )
        self._search = HybridSearch(
            host=self.config.host,
            port=self.config.port,
            config=SearchConfig(top_k=5, score_threshold=0.3),
        )
        print(f"[QdrantWrapper] Connecté → "
              f"{self.config.host}:{self.config.port} "
              f"| collection: {self.config.collection_name}")

    # ------------------------------------------------------------------
    # HEALTH CHECK
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """
        Vérifie que Qdrant est disponible et la collection existe.

        Appelé par les agents avant chaque requête pour éviter
        des erreurs silencieuses si Qdrant est down.
        """
        try:
            collections = self._client.get_collections().collections
            names       = [c.name for c in collections]
            return self.config.collection_name in names
        except Exception:
            return False

    def get_health_status(self) -> dict:
        """Retourne le statut détaillé de Qdrant."""
        try:
            info = self._client.get_collection(self.config.collection_name)
            return {
                "status":       "healthy",
                "points_count": info.points_count,
                "collection":   self.config.collection_name,
                "host":         f"{self.config.host}:{self.config.port}",
            }
        except Exception as e:
            return {
                "status":  "unhealthy",
                "error":   str(e),
                "host":    f"{self.config.host}:{self.config.port}",
            }

    # ------------------------------------------------------------------
    # RECHERCHE — Interface principale pour les agents
    # ------------------------------------------------------------------

    def search(
        self,
        query:       str,
        top_k:       int               = 5,
        chunk_types: Optional[list[str]] = None,
        source_file: Optional[str]     = None,
        page_range:  Optional[tuple]   = None,
    ) -> list[SearchResult]:
        """
        Recherche sémantique — méthode principale des agents.

        EXEMPLE AGENT DOCUMENTALISTE :
            results = qdrant.search(
                "Comment configurer Eth-Trunk en mode LACP ?",
                chunk_types=["code_cli", "text"],
                top_k=5,
            )

        Args:
            query       : question en langage naturel
            top_k       : nombre de résultats
            chunk_types : filtrer par type (None = tous les types)
            source_file : filtrer par PDF source
            page_range  : filtrer par plage de pages

        Returns:
            Liste de SearchResult triée par score décroissant
        """
        if not self.is_healthy():
            print("[QdrantWrapper] ⚠ Qdrant non disponible")
            return []

        return self._search.search(
            query=query,
            top_k=top_k,
            chunk_types=chunk_types,
            source_file=source_file,
            page_range=page_range,
            collection=self.config.collection_name,
        )

    def search_multimodal(
        self,
        query: str,
        top_k: int = 5,
    ) -> dict[str, list[SearchResult]]:
        """
        Recherche multimodale — texte + images + tableaux en parallèle.

        Utilisé par l'agent Documentaliste pour construire
        un contexte complet (texte ET schémas visuels).

        Returns:
            {
                "text_results"  : chunks texte/CLI,
                "image_results" : descriptions de schémas,
                "table_results" : tableaux de données,
            }
        """
        if not self.is_healthy():
            return {"text_results": [], "image_results": [], "table_results": []}

        return self._search.search_multimodal(
            query=query,
            top_k=top_k,
            collection=self.config.collection_name,
        )

    def search_and_format(self, query: str, top_k: int = 5) -> str:
        """
        Recherche multimodale + formatage direct pour le LLM.

        Raccourci utilisé par les agents pour obtenir le contexte
        prêt à injecter dans un prompt LLM en une seule ligne :

            context = qdrant.search_and_format("Comment configurer OSPF ?")
            prompt  = f"Contexte:\n{context}\n\nQuestion: ..."
        """
        mm_results = self.search_multimodal(query=query, top_k=top_k)
        return format_multimodal_context(mm_results)

    # ------------------------------------------------------------------
    # OPÉRATIONS CRUD SUR LES POINTS
    # ------------------------------------------------------------------

    def get_by_id(self, chunk_id: str) -> Optional[dict]:
        """
        Récupère un point par son UUID.

        Utilisé par l'agent Validateur pour vérifier
        qu'un chunk cité existe bien dans la base.

        Returns:
            Payload du chunk, ou None si introuvable
        """
        try:
            points = self._client.retrieve(
                collection_name=self.config.collection_name,
                ids=[chunk_id],
                with_payload=True,
                with_vectors=False,
            )
            return points[0].payload if points else None
        except Exception as e:
            print(f"[QdrantWrapper] get_by_id error : {e}")
            return None

    def get_by_type(
        self,
        chunk_type: str,
        limit:      int = 10,
    ) -> list[dict]:
        """
        Récupère tous les chunks d'un type donné.

        EXEMPLES :
            qdrant.get_by_type("image_desc")  → tous les schémas
            qdrant.get_by_type("code_cli")    → toutes les commandes
            qdrant.get_by_type("table")       → tous les tableaux

        Utilisé par l'agent Architecte pour comprendre
        ce qui est disponible dans la base.
        """
        try:
            results, _ = self._client.scroll(
                collection_name=self.config.collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(
                        key="chunk_type",
                        match=MatchValue(value=chunk_type),
                    )]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [r.payload for r in results]
        except Exception as e:
            print(f"[QdrantWrapper] get_by_type error : {e}")
            return []

    def get_by_source(
        self,
        source_file: str,
        limit:       int = 100,
    ) -> list[dict]:
        """
        Récupère tous les chunks d'un PDF source.

        Utilisé pour vérifier ce qui a été indexé depuis un fichier
        ou pour afficher le contenu d'un document spécifique.
        """
        try:
            results, _ = self._client.scroll(
                collection_name=self.config.collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(
                        key="source_file",
                        match=MatchValue(value=source_file),
                    )]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [r.payload for r in results]
        except Exception as e:
            print(f"[QdrantWrapper] get_by_source error : {e}")
            return []

    def get_by_page(
        self,
        page_number: int,
        limit:       int = 20,
    ) -> list[dict]:
        """Récupère tous les chunks d'une page spécifique."""
        try:
            results, _ = self._client.scroll(
                collection_name=self.config.collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(
                        key="page_number",
                        match=MatchValue(value=page_number),
                    )]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [r.payload for r in results]
        except Exception as e:
            print(f"[QdrantWrapper] get_by_page error : {e}")
            return []

    # ------------------------------------------------------------------
    # STATISTIQUES — Pour l'agent Architecte
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """
        Retourne les statistiques complètes de la collection.

        Utilisé par l'agent Architecte pour comprendre
        ce qui est disponible avant de planifier la recherche.

        Returns:
            {
                "total_points" : 68,
                "by_type"      : {"code_cli": 42, "text": 16, ...},
                "by_source"    : {"TP2&3RLE.pdf": 68},
                "pages_range"  : {"min": 1, "max": 16},
                "status"       : "green",
            }
        """
        try:
            info         = self._client.get_collection(self.config.collection_name)
            total_points = info.points_count

            # Stats par type de chunk
            by_type = {}
            for chunk_type in ["text", "code_cli", "image_desc", "table", "heading"]:
                chunks = self.get_by_type(chunk_type, limit=1000)
                if chunks:
                    by_type[chunk_type] = len(chunks)

            # Sources indexées
            all_chunks, _ = self._client.scroll(
                collection_name=self.config.collection_name,
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )

            sources = {}
            pages   = []
            for point in all_chunks:
                if point.payload:
                    src = point.payload.get("source_file", "unknown")
                    sources[src] = sources.get(src, 0) + 1
                    page = point.payload.get("page_number", 0)
                    if page:
                        pages.append(page)

            return {
                "total_points": total_points,
                "by_type":      by_type,
                "by_source":    sources,
                "pages_range":  {
                    "min": min(pages) if pages else 0,
                    "max": max(pages) if pages else 0,
                },
                "status":       str(info.status),
                "collection":   self.config.collection_name,
                "model":        EMBEDDING_MODEL,
            }

        except Exception as e:
            return {"error": str(e), "total_points": 0}

    def list_sources(self) -> list[str]:
        """
        Liste tous les fichiers PDF indexés.

        Utilisé par l'interface pour afficher les documents disponibles.
        """
        stats   = self.get_stats()
        sources = stats.get("by_source", {})
        return list(sources.keys())

    def count_by_type(self) -> dict[str, int]:
        """Compte les chunks par type — version allégée de get_stats()."""
        stats = self.get_stats()
        return stats.get("by_type", {})


# ---------------------------------------------------------------------------
# 3. SINGLETON — Instance partagée entre tous les agents
# ---------------------------------------------------------------------------

# Instance globale — importée par tous les agents
# Évite de créer une nouvelle connexion à chaque appel
_qdrant_instance: Optional[QdrantWrapper] = None


def get_qdrant_client(
    host:       str = "localhost",
    port:       int = 6333,
    collection: str = COLLECTION_NAME,
) -> QdrantWrapper:
    """
    Retourne l'instance singleton de QdrantWrapper.

    PATTERN SINGLETON :
    Tous les agents utilisent la même connexion Qdrant.
    On évite d'ouvrir 10 connexions simultanées pour rien.

    USAGE dans les agents :
        from vectorstore.qdrant_connection import get_qdrant_client
        qdrant = get_qdrant_client()
        results = qdrant.search("ma question")
    """
    global _qdrant_instance
    if _qdrant_instance is None:
        config           = QdrantConnectionConfig(
            host=host,
            port=port,
            collection_name=collection,
        )
        _qdrant_instance = QdrantWrapper(config=config)
    return _qdrant_instance


# ---------------------------------------------------------------------------
# 4. POINT D'ENTRÉE — Tests
#    python qdrant_connection.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    print("=" * 60)
    print("  TEST QDRANT CLIENT WRAPPER")
    print("=" * 60)

    qdrant = get_qdrant_client()

    # ── Health check ───────────────────────────────────────────────────
    print("\n── Health Check ──")
    health = qdrant.get_health_status()
    for k, v in health.items():
        print(f"  {k:15s}: {v}")

    if health["status"] != "healthy":
        print("\n❌ Qdrant non disponible. Lance Docker Qdrant d'abord.")
        sys.exit(1)

    # ── Stats ──────────────────────────────────────────────────────────
    print("\n── Statistiques collection ──")
    stats = qdrant.get_stats()
    print(f"  Total points : {stats['total_points']}")
    print(f"  Par type     :")
    for t, c in sorted(stats.get("by_type", {}).items()):
        print(f"    {t:15s}: {c}")
    print(f"  Sources      : {list(stats.get('by_source', {}).keys())}")
    print(f"  Pages        : {stats.get('pages_range')}")

    # ── Recherche simple ───────────────────────────────────────────────
    print("\n── Recherche sémantique ──")
    query   = "configurer VLAN trunk interface Huawei"
    results = qdrant.search(query, top_k=3)
    print(f"  Query : '{query}'")
    print(f"  {len(results)} résultat(s) :")
    for r in results:
        print(r.display())

    # ── Recherche multimodale ──────────────────────────────────────────
    print("\n── Recherche multimodale ──")
    mm = qdrant.search_multimodal("VLAN Layer 3 switching topology", top_k=3)
    print(f"  Texte/CLI : {len(mm['text_results'])} résultats")
    print(f"  Images    : {len(mm['image_results'])} résultats")
    print(f"  Tableaux  : {len(mm['table_results'])} résultats")

    # ── Contexte formaté LLM ───────────────────────────────────────────
    print("\n── Contexte formaté pour LLM ──")
    context = qdrant.search_and_format("OSPF configuration Layer 3")
    print(context[:500] + "...[tronqué]" if len(context) > 500 else context)

    # ── get_by_type ────────────────────────────────────────────────────
    print("\n── Chunks image_desc disponibles ──")
    images = qdrant.get_by_type("image_desc", limit=5)
    for img in images:
        print(f"  p.{img.get('page_number')} | "
              f"{img.get('section_title', 'N/A')} | "
              f"{img.get('content', '')[:80]}...")

    print("\n" + "=" * 60)
    print("  Tests terminés ✓")
    print("  → Prochaine étape : agents/graph.py")
    print("=" * 60)