"""
vectorstore/collection_setup.py
================================
Création et configuration de la collection Qdrant pour le RAG multimodal.

VERSION 2 — Recherche hybride dense + sparse (BM25)

ARCHITECTURE VECTORIELLE :
    Chaque point Qdrant contient maintenant DEUX vecteurs :

    1. VECTEUR DENSE  (BAAI/bge-small-en-v1.5 — 384 dimensions)
       → Similarité sémantique : "trunk" ≈ "liaison agrégée"
       → Capture le sens, pas les mots exacts

    2. VECTEUR SPARSE (Qdrant/bm25)
       → Correspondance exacte de mots-clés
       → Crucial pour les commandes CLI : "display ip routing-table"
         sera trouvé EXACTEMENT, même si sémantiquement loin
       → Indices creux (sparse) : seulement les tokens présents ≠ 0

    FUSION — Reciprocal Rank Fusion (RRF) :
        score_final = 1/(k + rang_dense) + 1/(k + rang_sparse)
        → Combine les deux classements sans dépendre des échelles de score
        → k=60 par défaut dans Qdrant

POURQUOI BM25 EST CRUCIAL POUR CE PROJET ?
    Les textes Huawei contiennent beaucoup de termes techniques exacts :
    - Commandes VRP : "display current-configuration", "stelnet server enable"
    - Interfaces : "GigabitEthernet0/0/1", "Vlanif3"
    - Paramètres : "port link-type trunk", "vlan batch 3 to 7"
    
    Un embedding sémantique peut les manquer car ces termes sont rares
    dans les données d'entraînement. BM25 les retrouve toujours.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    PayloadSchemaType,
    OptimizersConfigDiff,
    HnswConfigDiff,
)


# ---------------------------------------------------------------------------
# 1. CONSTANTES — Source unique de vérité pour tous les modules
# ---------------------------------------------------------------------------

COLLECTION_NAME   = "huawei_industrial_docs"

# Vecteur dense
VECTOR_SIZE       = 384
EMBEDDING_MODEL   = "BAAI/bge-small-en-v1.5"
DENSE_VECTOR_NAME = "dense"    # nom du vecteur dense dans Qdrant

# Vecteur sparse BM25
SPARSE_MODEL_NAME  = "Qdrant/bm25"
SPARSE_VECTOR_NAME = "sparse"   # nom du vecteur sparse dans Qdrant


# ---------------------------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------------------------

@dataclass
class CollectionConfig:
    """
    Paramètres de configuration de la collection Qdrant.

    SPARSE INDEX :
        on_disk=False   → index en mémoire (plus rapide pour petits datasets)
        full_scan_threshold=10000 → brute force sous ce seuil
    """
    collection_name:    str   = COLLECTION_NAME
    vector_size:        int   = VECTOR_SIZE
    distance:           str   = "Cosine"
    hnsw_m:             int   = 16
    hnsw_ef_construct:  int   = 100
    indexing_threshold: int   = 20000
    on_disk:            bool  = False
    enable_sparse:      bool  = True   # activer le vecteur sparse BM25


# ---------------------------------------------------------------------------
# 3. GESTIONNAIRE DE COLLECTION
# ---------------------------------------------------------------------------

class CollectionManager:
    """
    Crée et gère la collection Qdrant avec support dense + sparse.

    USAGE :
        manager = CollectionManager()
        manager.setup()           # crée si n'existe pas
        manager.setup(force_recreate=True)  # recrée (supprime les données)
        info = manager.get_info()
    """

    def __init__(
        self,
        host:   str = "localhost",
        port:   int = 6333,
        config: Optional[CollectionConfig] = None,
    ):
        self.config = config or CollectionConfig()
        self.client = QdrantClient(host=host, port=port)
        print(f"[CollectionManager] Connecté à Qdrant → {host}:{port}")

    # ------------------------------------------------------------------
    # SETUP
    # ------------------------------------------------------------------

    def setup(self, force_recreate: bool = False) -> bool:
        """
        Crée la collection si elle n'existe pas.

        Returns:
            True si créée, False si déjà existante (skipped)
        """
        exists = self._collection_exists()

        if exists and not force_recreate:
            info = self.get_info()
            print(f"[CollectionManager] '{self.config.collection_name}' existe déjà.")
            print(f"  → {info['points_count']} points | Status: {info['status']}")
            print(f"  → Sparse BM25: {info.get('has_sparse', False)}")
            return False

        if exists and force_recreate:
            print(f"[CollectionManager] Suppression '{self.config.collection_name}'...")
            self.client.delete_collection(self.config.collection_name)
            print("  → Supprimée ✓")

        print(f"[CollectionManager] Création '{self.config.collection_name}'...")
        self._create_collection()
        print("  → Collection créée ✓")

        print("[CollectionManager] Création des index payload...")
        self._create_payload_indexes()
        print("  → Index créés ✓")

        self.verify_collection()
        return True

    def reset(self) -> None:
        """Supprime et recrée la collection — EFFACE TOUTES LES DONNÉES."""
        print(f"\n[CollectionManager] ⚠ RESET '{self.config.collection_name}'")
        self.setup(force_recreate=True)

    # ------------------------------------------------------------------
    # CRÉATION
    # ------------------------------------------------------------------

    def _create_collection(self) -> None:
        """
        Crée la collection avec vecteurs dense ET sparse.

        STRUCTURE :
            vectors_config → dict de vecteurs nommés
                "dense"  : VectorParams(size=384, distance=Cosine)
                (pas de sparse ici — sparse va dans sparse_vectors_config)

            sparse_vectors_config → dict de vecteurs sparse
                "sparse" : SparseVectorParams(index=SparseIndexParams(...))
        """
        distance_map = {
            "Cosine": Distance.COSINE,
            "Euclid": Distance.EUCLID,
            "Dot":    Distance.DOT,
        }

        # Configuration du vecteur dense
        vectors_config = {
            DENSE_VECTOR_NAME: VectorParams(
                size=self.config.vector_size,
                distance=distance_map.get(self.config.distance, Distance.COSINE),
            )
        }

        # Configuration du vecteur sparse (BM25)
        sparse_vectors_config = {}
        if self.config.enable_sparse:
            sparse_vectors_config[SPARSE_VECTOR_NAME] = SparseVectorParams(
                index=SparseIndexParams(
                    on_disk=self.config.on_disk,
                    # full_scan_threshold : brute force sous ce seuil
                    # (plus rapide pour petits datasets comme le nôtre)
                    full_scan_threshold=10000,
                )
            )

        self.client.create_collection(
            collection_name=self.config.collection_name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_vectors_config if sparse_vectors_config else None,
            hnsw_config=HnswConfigDiff(
                m=self.config.hnsw_m,
                ef_construct=self.config.hnsw_ef_construct,
                on_disk=self.config.on_disk,
            ),
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=self.config.indexing_threshold,
            ),
        )

    def _create_payload_indexes(self) -> None:
        """Crée les index sur les champs de payload les plus filtrés."""
        indexes = [
            ("chunk_type",    PayloadSchemaType.KEYWORD),
            ("source_file",   PayloadSchemaType.KEYWORD),
            ("section_title", PayloadSchemaType.KEYWORD),
            ("page_number",   PayloadSchemaType.INTEGER),
            ("chunk_index",   PayloadSchemaType.INTEGER),
        ]
        for field_name, schema_type in indexes:
            self.client.create_payload_index(
                collection_name=self.config.collection_name,
                field_name=field_name,
                field_schema=schema_type,
            )
            print(f"    ✓ Index '{field_name}' ({schema_type.value})")

    # ------------------------------------------------------------------
    # UTILITAIRES
    # ------------------------------------------------------------------

    def _collection_exists(self) -> bool:
        collections = self.client.get_collections().collections
        return any(c.name == self.config.collection_name for c in collections)

    def get_info(self) -> dict:
        """Retourne les informations sur la collection."""
        if not self._collection_exists():
            return {"error": f"'{self.config.collection_name}' introuvable"}

        info = self.client.get_collection(self.config.collection_name)

        # Détection du vecteur sparse
        sparse_config = info.config.params.sparse_vectors
        has_sparse    = sparse_config is not None and len(sparse_config) > 0

        return {
            "name":         self.config.collection_name,
            "points_count": info.points_count,
            "status":       info.status,
            "vector_size":  info.config.params.vectors.get(DENSE_VECTOR_NAME, {}).size
                            if isinstance(info.config.params.vectors, dict)
                            else getattr(info.config.params.vectors, "size", VECTOR_SIZE),
            "distance":     str(info.config.params.vectors.get(DENSE_VECTOR_NAME, {}).distance)
                            if isinstance(info.config.params.vectors, dict)
                            else str(getattr(info.config.params.vectors, "distance", "Cosine")),
            "has_sparse":   has_sparse,
            "dense_name":   DENSE_VECTOR_NAME,
            "sparse_name":  SPARSE_VECTOR_NAME if has_sparse else None,
        }

    def verify_collection(self) -> None:
        """Affiche un résumé de la collection."""
        info = self.get_info()
        if "error" in info:
            print(f"  ✗ {info['error']}")
            return

        print(f"\n{'='*55}")
        print(f"  COLLECTION QDRANT")
        print(f"{'='*55}")
        print(f"  Nom       : {info['name']}")
        print(f"  Points    : {info['points_count']}")
        print(f"  Status    : {info['status']}")
        print(f"  Dense     : {info['vector_size']}D {info['distance']}")
        print(f"  Sparse    : {'✓ BM25 (Qdrant/bm25)' if info['has_sparse'] else '✗ désactivé'}")
        print(f"  Modèle    : {EMBEDDING_MODEL}")
        print(f"{'='*55}\n")

    def list_all_collections(self) -> list[str]:
        names = [c.name for c in self.client.get_collections().collections]
        print(f"[Qdrant] Collections : {names}")
        return names

    def delete_collection(self) -> bool:
        if not self._collection_exists():
            print(f"[CollectionManager] '{self.config.collection_name}' n'existe pas.")
            return False
        self.client.delete_collection(self.config.collection_name)
        print(f"[CollectionManager] '{self.config.collection_name}' supprimée ✓")
        return True


# ---------------------------------------------------------------------------
# 4. POINT D'ENTRÉE
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    force_reset = "--reset" in sys.argv

    print("=" * 55)
    print("  SETUP COLLECTION QDRANT — Hybride Dense + Sparse")
    print("=" * 55)
    print(f"  Collection  : {COLLECTION_NAME}")
    print(f"  Dense       : {VECTOR_SIZE}D Cosine ({EMBEDDING_MODEL})")
    print(f"  Sparse      : BM25 ({SPARSE_MODEL_NAME})")
    print(f"  Reset       : {force_reset}")
    print("=" * 55)

    manager = CollectionManager()

    if force_reset:
        manager.reset()
    else:
        manager.setup()

    manager.list_all_collections()