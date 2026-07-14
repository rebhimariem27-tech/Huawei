"""
ingestion/chunker.py
====================
Découpage et optimisation des chunks pour l'indexation Qdrant.

CONCEPT CLÉ — Pourquoi chunker ?
    Les chunks bruts issus de pdf_extractor sont de taille très variable :
    - Blocs courts : "Step 1", "Figure 1.5" → trop petits pour être utiles
    - Blocs longs  : configurations CLI de 1500 chars → trop grands pour
                     l'embedding (contexte perdu, précision réduite)

    Le chunker résout ça en 3 opérations :
    1. FUSION    : blocs courts → fusionnés avec voisins (contexte préservé)
    2. DÉCOUPAGE : blocs longs → sous-chunks avec overlap (continuité assurée)
    3. ENRICHISSEMENT : chaque chunk final porte son contexte complet
                        (section, page, type, source) pour le RAG

OVERLAP — Pourquoi ?
    Si on coupe un bloc à 800 chars sans overlap, une information à cheval
    sur deux chunks sera perdue lors de la recherche.
    Avec 150 chars d'overlap, chaque chunk contient le début du suivant
    → aucune information ne tombe entre deux chunks.

    Chunk 1 : [----texte----][overlap]
    Chunk 2 :           [overlap][----texte----][overlap]
    Chunk 3 :                             [overlap][----texte----]

SORTIE :
    List[FinalChunk] → prêt pour vectorstore/embedder.py + Qdrant
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pdf_extractor import ChunkType, ExtractedChunk


# ---------------------------------------------------------------------------
# 1. TYPES DE DONNÉES — Chunk final optimisé pour Qdrant
# ---------------------------------------------------------------------------

class FinalChunkType(str, Enum):
    """
    Types de chunks finaux après traitement.

    TEXT       : paragraphe de texte standard
    TABLE      : tableau de données (routage, VLAN, etc.)
    IMAGE_DESC : description textuelle d'une image (issue du vision analyzer)
    CODE_CLI   : commandes CLI Huawei VRP (display, interface, ospf...)
    HEADING    : titre de section (Lab 1-2, Step 3, etc.)
    """
    TEXT       = "text"
    TABLE      = "table"
    IMAGE_DESC = "image_desc"
    CODE_CLI   = "code_cli"
    HEADING    = "heading"


@dataclass
class FinalChunk:
    """
    Chunk final optimisé, prêt pour l'indexation Qdrant.

    C'est l'objet que vectorstore/embedder.py recevra pour créer
    les vecteurs et les stocker dans Qdrant.

    Champs essentiels pour le RAG :
        chunk_id      : UUID unique — clé primaire dans Qdrant
        content       : texte final à embedder (propre, normalisé)
        chunk_type    : type sémantique (TEXT, TABLE, CODE_CLI...)
        page_number   : page source dans le PDF
        source_file   : PDF d'origine (traçabilité)
        section_title : titre de section parent (contexte)
        chunk_index   : position dans le document
        metadata      : tout le reste (confiance vision, overlap, etc.)
    """
    chunk_id:      str
    content:       str
    chunk_type:    FinalChunkType
    page_number:   int
    source_file:   str
    chunk_index:   int
    section_title: str            = ""
    overlap_prev:  str            = ""   # début du chunk précédent (overlap)
    overlap_next:  str            = ""   # fin du chunk suivant (overlap)
    metadata:      dict           = field(default_factory=dict)

    @property
    def content_with_context(self) -> str:
        """
        Texte enrichi avec contexte — c'est CE texte qui sera embedder.

        On préfixe avec la section et le type pour améliorer la précision
        de la recherche vectorielle. Un embedding de
        "Section: Lab 1-3\nType: CLI\n[S1]vlan batch 3 to 7"
        sera beaucoup plus précis que juste
        "[S1]vlan batch 3 to 7"
        """
        parts = []
        if self.section_title:
            parts.append(f"Section: {self.section_title}")
        if self.chunk_type in (FinalChunkType.CODE_CLI, FinalChunkType.TABLE):
            parts.append(f"Type: {self.chunk_type.value}")
        if self.overlap_prev:
            parts.append(f"[contexte précédent]: {self.overlap_prev}")
        parts.append(self.content)
        return "\n".join(parts)

    @property
    def char_count(self) -> int:
        return len(self.content)

    def preview(self, max_chars: int = 80) -> str:
        text = self.content.replace("\n", " ").strip()
        return text[:max_chars] + "..." if len(text) > max_chars else text

    def to_qdrant_payload(self) -> dict:
        """
        Sérialise le chunk en payload Qdrant.

        Le payload est stocké avec le vecteur dans Qdrant.
        Il est retourné lors de la recherche pour permettre
        d'afficher la source et le contexte à l'utilisateur.
        """
        return {
            "chunk_id":      self.chunk_id,
            "content":       self.content,
            "chunk_type":    self.chunk_type.value,
            "page_number":   self.page_number,
            "source_file":   self.source_file,
            "chunk_index":   self.chunk_index,
            "section_title": self.section_title,
            "char_count":    self.char_count,
            **self.metadata,
        }


# ---------------------------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------------------------

@dataclass
class ChunkerConfig:
    """
    Paramètres du chunker — tous les seuils ajustables ici.

    VALEURS PAR DÉFAUT optimisées pour les TPs Huawei réseau :
        min_chunk_chars  : 80  → en dessous, on fusionne (ex: "Step 1")
        max_chunk_chars  : 800 → au dessus, on découpe
        overlap_chars    : 150 → overlap entre chunks découpés
        cli_min_lines    : 3   → minimum de lignes pour détecter du CLI
    """
    min_chunk_chars:  int = 80    # seuil fusion (blocs trop courts)
    max_chunk_chars:  int = 800   # seuil découpage (blocs trop longs)
    overlap_chars:    int = 150   # overlap lors du découpage
    cli_min_lines:    int = 3     # lignes min pour détecter CLI Huawei
    merge_same_page:  bool = True # fusionner seulement sur la même page


# ---------------------------------------------------------------------------
# 3. DÉTECTEURS SÉMANTIQUES
#    Ces fonctions analysent le contenu pour typer chaque chunk.
# ---------------------------------------------------------------------------

# Pattern CLI Huawei VRP — commandes caractéristiques
_CLI_PATTERNS = re.compile(
    r"(\[[\w\-]+\]|<[\w\-]+>|"           # prompts : [S1], <R1>
    r"display |interface |vlan |ospf |"   # commandes courantes
    r"ip route|undo |system-view|"        # autres commandes
    r"GigabitEthernet|Eth-Trunk|Vlanif)", # interfaces Huawei
    re.IGNORECASE
)

# Pattern titre de section
_HEADING_PATTERNS = re.compile(
    r"^(Lab\s+\d|Step\s+\d|Figure\s+\d|Section\s+\d|"
    r"Chapter\s+\d|\d+\.\d+\s+[A-Z])",
    re.IGNORECASE | re.MULTILINE
)


def detect_chunk_type(chunk: ExtractedChunk) -> FinalChunkType:
    """
    Détecte le type sémantique d'un chunk.

    LOGIQUE DE PRIORITÉ :
    1. IMAGE → toujours IMAGE_DESC (déjà enrichi par vision_analyzer)
    2. TABLE → toujours TABLE (détecté par pdf_extractor)
    3. Titre de section → HEADING
    4. Commandes CLI Huawei → CODE_CLI
    5. Tout le reste → TEXT
    """
    if chunk.chunk_type == ChunkType.IMAGE:
        return FinalChunkType.IMAGE_DESC

    if chunk.chunk_type == ChunkType.TABLE:
        return FinalChunkType.TABLE

    text = chunk.content.strip()

    # Titre de section : court + pattern reconnaissable
    if len(text) < 120 and _HEADING_PATTERNS.search(text):
        return FinalChunkType.HEADING

    # CLI Huawei : présence de patterns de commandes
    cli_matches = _CLI_PATTERNS.findall(text)
    lines = text.split("\n")
    if len(cli_matches) >= 2 and len(lines) >= 3:
        return FinalChunkType.CODE_CLI

    return FinalChunkType.TEXT


def extract_section_title(chunks: list[ExtractedChunk], index: int) -> str:
    """
    Remonte dans la liste pour trouver le titre de section le plus proche.

    POURQUOI ?
    Chaque chunk doit savoir dans quelle section il se trouve.
    Ex: "Step 6 Preparing the environment" est le contexte de
    tous les chunks CLI qui suivent jusqu'au prochain heading.

    On remonte jusqu'à 20 chunks en arrière pour trouver le dernier heading.
    """
    for i in range(index - 1, max(-1, index - 20), -1):
        chunk = chunks[i]
        text  = chunk.content.strip()
        if _HEADING_PATTERNS.search(text) and len(text) < 150:
            # Nettoyage du titre : première ligne uniquement
            return text.split("\n")[0].strip()
    return ""


# ---------------------------------------------------------------------------
# 4. CHUNKER PRINCIPAL
# ---------------------------------------------------------------------------

class Chunker:
    """
    Transforme les ExtractedChunks en FinalChunks optimisés pour Qdrant.

    PIPELINE INTERNE :
        chunk()
          ├── _detect_types()      → typage sémantique de chaque chunk
          ├── _merge_short()       → fusion des blocs trop courts
          ├── _split_long()        → découpage des blocs trop longs
          ├── _add_overlap()       → ajout de l'overlap entre chunks
          └── _finalize()          → numérotation + métadonnées finales

    USAGE :
        chunker = Chunker()
        final_chunks = chunker.chunk(extracted_chunks)
        for fc in final_chunks:
            print(fc.preview())
            print(fc.to_qdrant_payload())
    """

    def __init__(self, config: Optional[ChunkerConfig] = None):
        self.config = config or ChunkerConfig()

    # ------------------------------------------------------------------
    # POINT D'ENTRÉE PUBLIC
    # ------------------------------------------------------------------

    def chunk(self, extracted_chunks: list[ExtractedChunk]) -> list[FinalChunk]:
        """
        Transforme les chunks extraits en chunks finaux prêts pour Qdrant.

        Args:
            extracted_chunks : sortie de pdf_extractor + vision_analyzer

        Returns:
            Liste de FinalChunk optimisés et numérotés
        """
        if not extracted_chunks:
            return []

        source_file = extracted_chunks[0].source_file
        print(f"\n[Chunker] Traitement de {len(extracted_chunks)} chunks bruts...")

        # Étape 1 : filtrage des chunks vides
        chunks = [c for c in extracted_chunks if c.content.strip()]
        print(f"  → Après filtrage vides    : {len(chunks)} chunks")

        # Étape 2 : fusion des blocs trop courts
        chunks = self._merge_short_chunks(chunks)
        print(f"  → Après fusion courts     : {len(chunks)} chunks")

        # Étape 3 : découpage des blocs trop longs
        chunks = self._split_long_chunks(chunks)
        print(f"  → Après découpage longs   : {len(chunks)} chunks")

        # Étape 4 : construction des FinalChunks avec contexte
        final_chunks = self._build_final_chunks(chunks, source_file)
        print(f"  → FinalChunks construits  : {len(final_chunks)} chunks")

        # Étape 5 : ajout de l'overlap entre chunks adjacents
        final_chunks = self._add_overlap(final_chunks)

        print(f"\n[Chunker] Terminé : {len(final_chunks)} chunks prêts pour Qdrant")
        self._print_summary(final_chunks)

        return final_chunks

    # ------------------------------------------------------------------
    # ÉTAPE 2 : FUSION DES CHUNKS COURTS
    # ------------------------------------------------------------------

    def _merge_short_chunks(
        self,
        chunks: list[ExtractedChunk],
    ) -> list[ExtractedChunk]:
        """
        Fusionne les chunks trop courts avec leurs voisins.

        RÈGLES DE FUSION :
        - Un chunk court (< min_chunk_chars) est fusionné avec le suivant
        - Exception : les chunks IMAGE ne sont JAMAIS fusionnés
          (leur contenu est une description vision, déjà optimisée)
        - Exception : on ne fusionne pas à travers les changements de page
          si merge_same_page=True

        EXEMPLE :
        Avant : ["Step 1", "Preparing the environment...long text..."]
        Après : ["Step 1\nPreparing the environment...long text..."]
        """
        if not chunks:
            return chunks

        merged:  list[ExtractedChunk] = []
        pending: Optional[ExtractedChunk] = None  # chunk en attente de fusion

        for chunk in chunks:
            # Les images ne participent jamais à une fusion
            if chunk.chunk_type == ChunkType.IMAGE:
                if pending:
                    merged.append(pending)
                    pending = None
                merged.append(chunk)
                continue

            if pending is None:
                pending = chunk
                continue

            # Vérification du changement de page
            same_page = (pending.page_number == chunk.page_number)
            if self.config.merge_same_page and not same_page:
                merged.append(pending)
                pending = chunk
                continue

            # Fusion si le chunk pending est court
            if len(pending.content.strip()) < self.config.min_chunk_chars:
                # On crée un nouveau chunk fusionné
                pending = self._merge_two_chunks(pending, chunk)
            else:
                merged.append(pending)
                pending = chunk

        if pending:
            merged.append(pending)

        return merged

    def _merge_two_chunks(
        self,
        a: ExtractedChunk,
        b: ExtractedChunk,
    ) -> ExtractedChunk:
        """Fusionne deux chunks en un seul en combinant leur contenu."""
        from copy import deepcopy
        merged = deepcopy(a)
        merged.content  = a.content.strip() + "\n" + b.content.strip()
        merged.metadata = {**a.metadata, **b.metadata, "merged": True}
        # La bbox englobe les deux chunks
        merged.bbox.x1  = max(a.bbox.x1, b.bbox.x1)
        merged.bbox.y1  = max(a.bbox.y1, b.bbox.y1)
        return merged

    # ------------------------------------------------------------------
    # ÉTAPE 3 : DÉCOUPAGE DES CHUNKS LONGS
    # ------------------------------------------------------------------

    def _split_long_chunks(
        self,
        chunks: list[ExtractedChunk],
    ) -> list[ExtractedChunk]:
        """
        Découpe les chunks trop longs en sous-chunks.

        Les chunks IMAGE et TABLE ne sont jamais découpés :
        - IMAGE : la description vision est déjà courte et cohérente
        - TABLE : un tableau découpé perd son sens

        Pour les TEXT et CODE_CLI longs, on découpe intelligemment
        en préférant les coupures aux sauts de ligne.
        """
        result: list[ExtractedChunk] = []

        for chunk in chunks:
            # Ne pas découper les images et tableaux
            if chunk.chunk_type in (ChunkType.IMAGE, ChunkType.TABLE):
                result.append(chunk)
                continue

            if len(chunk.content) <= self.config.max_chunk_chars:
                result.append(chunk)
                continue

            # Découpage nécessaire
            sub_chunks = self._split_chunk(chunk)
            result.extend(sub_chunks)

        return result

    def _split_chunk(self, chunk: ExtractedChunk) -> list[ExtractedChunk]:
        """
        Découpe un chunk long en sous-chunks avec overlap.

        ALGORITHME :
        1. Identifier les points de coupure naturels (sauts de ligne)
        2. Découper en respectant max_chunk_chars
        3. Ajouter overlap_chars de chevauchement entre sous-chunks

        On préfère couper aux sauts de ligne plutôt qu'au milieu
        d'une phrase ou d'une commande CLI.
        """
        from copy import deepcopy

        text     = chunk.content
        max_size = self.config.max_chunk_chars
        overlap  = self.config.overlap_chars

        sub_chunks = []
        start      = 0
        part_index = 0

        while start < len(text):
            end = start + max_size

            if end >= len(text):
                # Dernier morceau
                sub_text = text[start:]
            else:
                # Chercher une coupure naturelle (saut de ligne) proche de end
                # On cherche dans les 100 derniers chars de la fenêtre
                natural_cut = text.rfind("\n", start + max_size - 100, end)
                if natural_cut > start:
                    end      = natural_cut
                    sub_text = text[start:end]
                else:
                    sub_text = text[start:end]

            if sub_text.strip():
                sub = deepcopy(chunk)
                sub.content  = sub_text.strip()
                sub.metadata = {
                    **chunk.metadata,
                    "split_part":  part_index,
                    "split_total": -1,    # mis à jour après
                    "split_start": start,
                    "split_end":   start + len(sub_text),
                }
                sub_chunks.append(sub)
                part_index += 1

            # Avance avec overlap (recul de overlap_chars)
            start = end - overlap
            if start <= 0:
                break

        # Mise à jour du total
        for sc in sub_chunks:
            sc.metadata["split_total"] = len(sub_chunks)

        return sub_chunks

    # ------------------------------------------------------------------
    # ÉTAPE 4 : CONSTRUCTION DES FINALCHUNKS
    # ------------------------------------------------------------------

    def _build_final_chunks(
        self,
        chunks: list[ExtractedChunk],
        source_file: str,
    ) -> list[FinalChunk]:
        """
        Construit les FinalChunks avec tout le contexte nécessaire.

        Pour chaque chunk :
        - Détection du type sémantique (CLI, TABLE, IMAGE_DESC...)
        - Extraction du titre de section parent
        - Génération d'un UUID unique
        - Enrichissement des métadonnées
        """
        final_chunks: list[FinalChunk] = []

        for i, chunk in enumerate(chunks):
            chunk_type    = detect_chunk_type(chunk)
            section_title = extract_section_title(chunks, i)

            # Récupération des métadonnées vision si IMAGE
            vision_meta = {}
            if chunk.chunk_type == ChunkType.IMAGE:
                vision_meta = {
                    "vision_category":     chunk.metadata.get("vision_category", ""),
                    "vision_confidence":   chunk.metadata.get("vision_confidence", 0.0),
                    "vision_key_elements": chunk.metadata.get("vision_key_elements", []),
                    "vision_terms":        chunk.metadata.get("vision_terms", []),
                    "vision_is_mock":      chunk.metadata.get("vision_is_mock", True),
                    "image_path":          chunk.metadata.get("image_path", ""),
                }

            final_chunk = FinalChunk(
                chunk_id=str(uuid.uuid4()),
                content=chunk.content.strip(),
                chunk_type=chunk_type,
                page_number=chunk.page_number,
                source_file=source_file,
                chunk_index=i,
                section_title=section_title,
                metadata={
                    "original_type":   chunk.chunk_type.value,
                    "bbox_y0":         round(chunk.bbox.y0, 1),
                    "char_count":      len(chunk.content.strip()),
                    "position_ratio":  chunk.metadata.get("position_ratio", 0.0),
                    **vision_meta,
                    **{k: v for k, v in chunk.metadata.items()
                       if k not in ("position_ratio",)},
                },
            )
            final_chunks.append(final_chunk)

        # Numérotation finale (chunk_index global dans le document)
        for i, fc in enumerate(final_chunks):
            fc.chunk_index = i
            fc.metadata["total_chunks"] = len(final_chunks)

        return final_chunks

    # ------------------------------------------------------------------
    # ÉTAPE 5 : OVERLAP
    # ------------------------------------------------------------------

    def _add_overlap(self, chunks: list[FinalChunk]) -> list[FinalChunk]:
        """
        Ajoute l'overlap entre chunks TEXT adjacents.

        overlap_prev = fin du chunk précédent (contexte amont)
        overlap_next = début du chunk suivant (contexte aval)

        Uniquement pour TEXT et CODE_CLI — pas pour IMAGE_DESC et TABLE.
        """
        overlap_types = {FinalChunkType.TEXT, FinalChunkType.CODE_CLI}
        ol = self.config.overlap_chars

        for i, chunk in enumerate(chunks):
            if chunk.chunk_type not in overlap_types:
                continue

            # overlap_prev : fin du chunk précédent
            if i > 0 and chunks[i-1].chunk_type in overlap_types:
                prev_text         = chunks[i-1].content
                chunk.overlap_prev = prev_text[-ol:].strip() if len(prev_text) > ol else prev_text

            # overlap_next : début du chunk suivant
            if i < len(chunks) - 1 and chunks[i+1].chunk_type in overlap_types:
                next_text         = chunks[i+1].content
                chunk.overlap_next = next_text[:ol].strip() if len(next_text) > ol else next_text

        return chunks

    # ------------------------------------------------------------------
    # RÉSUMÉ
    # ------------------------------------------------------------------

    def _print_summary(self, chunks: list[FinalChunk]) -> None:
        from collections import Counter
        type_counts = Counter(fc.chunk_type.value for fc in chunks)
        avg_chars   = sum(fc.char_count for fc in chunks) / len(chunks) if chunks else 0
        pages       = sorted({fc.page_number for fc in chunks})

        print(f"\n{'='*55}")
        print(f"  RÉSUMÉ CHUNKER")
        print(f"{'='*55}")
        for chunk_type, count in sorted(type_counts.items()):
            print(f"  {chunk_type:15s} : {count} chunks")
        print(f"  {'─'*30}")
        print(f"  Total          : {len(chunks)} chunks")
        print(f"  Taille moyenne : {avg_chars:.0f} chars")
        print(f"  Pages          : {pages[0]}–{pages[-1]}")
        print(f"{'='*55}\n")


# ---------------------------------------------------------------------------
# 5. FONCTIONS UTILITAIRES
# ---------------------------------------------------------------------------

def get_chunks_by_type(
    chunks: list[FinalChunk],
    chunk_type: FinalChunkType,
) -> list[FinalChunk]:
    """Filtre les chunks par type — utile pour débugger et analyser."""
    return [c for c in chunks if c.chunk_type == chunk_type]


def get_chunks_by_page(
    chunks: list[FinalChunk],
    page: int,
) -> list[FinalChunk]:
    """Retourne tous les chunks d'une page donnée."""
    return [c for c in chunks if c.page_number == page]


def chunks_to_json(chunks: list[FinalChunk]) -> list[dict]:
    """
    Sérialise les chunks en liste de dicts JSON.
    Utile pour sauvegarder les chunks avant indexation Qdrant
    ou pour débugger le pipeline.
    """
    return [fc.to_qdrant_payload() for fc in chunks]


# ---------------------------------------------------------------------------
# 6. POINT D'ENTRÉE — Test complet du pipeline Extraction → Vision → Chunk
#    python chunker.py "F:/TP2&3RLE.pdf" [--api]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json
    from pdf_extractor import PDFExtractor, ExtractionConfig
    from vision_analyzer import VisionAnalyzer, enrich_chunks_with_vision

    if len(sys.argv) < 2:
        print("Usage : python chunker.py <chemin.pdf> [--api] [--max-images N]")
        print()
        print("Options :")
        print("  (rien)           → vision mock (pas d'appel API)")
        print("  --api            → vision via Groq (nécessite GROQ_API_KEY)")
        print("  --max-images N   → analyser max N images (défaut: toutes)")
        print()
        print("Exemples :")
        print('  python chunker.py "F:/TP2&3RLE.pdf"')
        print('  python chunker.py "F:/doc.pdf" --api --max-images 5')
        sys.exit(1)

    pdf_path   = sys.argv[1]
    use_api    = "--api" in sys.argv

    # Parsing --max-images N
    max_images = None
    if "--max-images" in sys.argv:
        idx = sys.argv.index("--max-images")
        try:
            max_images = int(sys.argv[idx + 1])
            print(f"[Config] Limite images : {max_images}")
        except (IndexError, ValueError):
            print("Erreur : --max-images nécessite un nombre entier")
            sys.exit(1)

    # ── Étape 1 : Extraction PDF ───────────────────────────────────────
    print("=" * 55)
    print("  ÉTAPE 1 — Extraction PDF")
    print("=" * 55)
    extractor = PDFExtractor(ExtractionConfig(
        extract_images=True,
        extract_text=True,
        image_dpi=200,
    ))
    raw_chunks = extractor.extract(pdf_path)

    # ── Étape 2 : Analyse Vision ───────────────────────────────────────
    print("\n" + "=" * 55)
    print(f"  ÉTAPE 2 — Analyse Vision [{'API Groq' if use_api else 'MOCK'}]")
    print("=" * 55)
    analyzer        = VisionAnalyzer(mock_mode=not use_api)
    enriched_chunks = enrich_chunks_with_vision(
        raw_chunks,
        analyzer,
    )

    # ── Étape 3 : Chunking ────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  ÉTAPE 3 — Chunking & Optimisation")
    print("=" * 55)
    chunker      = Chunker()
    final_chunks = chunker.chunk(enriched_chunks)

    # ── Aperçu des résultats ──────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  APERÇU DES CHUNKS FINAUX")
    print("=" * 55)

    # Afficher 3 exemples par type
    from collections import defaultdict
    by_type: dict = defaultdict(list)
    for fc in final_chunks:
        by_type[fc.chunk_type.value].append(fc)

    for chunk_type, type_chunks in sorted(by_type.items()):
        print(f"\n  ── {chunk_type.upper()} ({len(type_chunks)} chunks) ──")
        for fc in type_chunks[:2]:   # 2 exemples par type
            print(f"  [{fc.chunk_index}] p.{fc.page_number}"
                  f" | {fc.char_count} chars"
                  f" | section: '{fc.section_title[:40]}'"
                  )
            print(f"       {fc.preview(100)}")

    # ── Sauvegarde JSON (optionnel, pour vérification) ─────────────────
    output_json = "chunks_output.json"
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(chunks_to_json(final_chunks), f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ Chunks sauvegardés → {output_json}")
    print(f"\n  Pipeline Extraction → Vision → Chunking terminé ✓")
    print(f"  {len(final_chunks)} chunks prêts pour vectorstore/qdrant\n")