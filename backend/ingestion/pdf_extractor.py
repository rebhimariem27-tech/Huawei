"""
ingestion/pdf_extractor.py
==========================
Extraction multimodale de PDFs industriels (texte + images).

CONCEPT CLÉ :
    Un PDF n'est pas un document linéaire — c'est une collection d'objets
    positionnés sur des pages : blocs de texte, images, vecteurs, tableaux.
    Notre job : identifier chaque objet, l'extraire proprement, et le
    préparer pour la prochaine étape (vision_analyzer.py + chunker.py).

SORTIE :
    List[ExtractedChunk] — chaque chunk porte soit du texte, soit une image,
    avec ses métadonnées de provenance (page, position, type).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
import fitz  # pymupdf — pip install pymupdf


# ---------------------------------------------------------------------------
# 1. TYPES DE DONNÉES
#    On définit d'abord la "forme" de nos données avant toute logique.
#    C'est une bonne pratique : le type guide l'implémentation.
# ---------------------------------------------------------------------------

class ChunkType(str, Enum):
    """
    Deux types possibles pour un chunk extrait d'un PDF.

    TEXT  : un bloc de paragraphe ou de texte brut
    IMAGE : une image embarquée (schéma réseau, diagramme, graphique)
    TABLE : texte détecté comme tableau (heuristique basique)
    """
    TEXT  = "text"
    IMAGE = "image"
    TABLE = "table"


@dataclass
class BoundingBox:
    """
    Position d'un élément sur la page PDF (en points, 1pt = 1/72 inch).

    Utile pour :
    - Reconstruire le contexte spatial ("ce texte est sous cette image")
    - Débugger visuellement ce qui a été extrait
    """
    x0: float  # bord gauche
    y0: float  # bord haut
    x1: float  # bord droit
    y1: float  # bord bas

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass
class ExtractedChunk:
    """
    Unité atomique extraite d'un PDF.

    Chaque chunk représente UN élément cohérent : un paragraphe, une image,
    un tableau. C'est ce qu'on va envoyer au vision_analyzer.py et au chunker.

    Champs clés :
        chunk_type   : TEXT, IMAGE ou TABLE
        content      : le texte brut (si TEXT/TABLE), vide sinon
        image_bytes  : les bytes de l'image (si IMAGE), None sinon
        image_format : "png", "jpeg", etc. (si IMAGE)
        page_number  : numéro de page (commence à 1, plus lisible)
        bbox         : position sur la page
        source_file  : chemin du PDF d'origine (traçabilité)
        chunk_index  : position dans la liste totale des chunks
    """
    chunk_type:   ChunkType
    page_number:  int
    bbox:         BoundingBox
    source_file:  str
    chunk_index:  int = 0

    # Contenu textuel (rempli si TEXT ou TABLE)
    content:      str = ""

    # Contenu image (rempli si IMAGE)
    image_bytes:  Optional[bytes] = None
    image_format: Optional[str]   = None

    # Métadonnées enrichies (remplies au fur et à mesure du pipeline)
    metadata: dict = field(default_factory=dict)

    def is_text(self) -> bool:
        return self.chunk_type in (ChunkType.TEXT, ChunkType.TABLE)

    def is_image(self) -> bool:
        return self.chunk_type == ChunkType.IMAGE

    def has_content(self) -> bool:
        """Vérifie que le chunk contient vraiment quelque chose d'utile."""
        if self.is_text():
            return bool(self.content.strip())
        if self.is_image():
            return self.image_bytes is not None and len(self.image_bytes) > 0
        return False

    def preview(self, max_chars: int = 80) -> str:
        """Aperçu lisible pour logs et debugging."""
        if self.is_image():
            size_kb = len(self.image_bytes) / 1024 if self.image_bytes else 0
            return f"[IMAGE {self.image_format} {size_kb:.1f}KB page={self.page_number}]"
        text = self.content.replace("\n", " ").strip()
        return text[:max_chars] + "..." if len(text) > max_chars else text


# ---------------------------------------------------------------------------
# 2. CONFIGURATION DE L'EXTRACTEUR
#    Regrouper tous les paramètres dans un objet dédié.
#    Avantage : on peut ajuster le comportement SANS toucher à la logique.
# ---------------------------------------------------------------------------

@dataclass
class ExtractionConfig:
    """
    Paramètres qui contrôlent le comportement de l'extraction.

    POURQUOI ces valeurs par défaut ?
        min_text_length  : ignorer les fragments courts type numéros de page
        min_image_area   : ignorer les logos et icônes minuscules
        image_dpi        : 200 DPI = bon compromis qualité/taille pour les modèles vision
        extract_images   : désactivable si on veut un pipeline texte-seul rapide
    """
    min_text_length:  int   = 20      # caractères minimum pour garder un bloc texte
    min_image_area:   float = 100000.0  # pixels² minimum pour garder une image
    image_dpi:        int   = 200     # résolution de rendu des images matricielles
    extract_images:   bool  = True    # désactiver pour pipeline texte-only
    extract_text:     bool  = True    # désactiver pour pipeline image-only
    table_heuristic:  bool  = True    # tenter de détecter les tableaux


# ---------------------------------------------------------------------------
# 3. EXTRACTEUR PRINCIPAL
#    La classe qui fait le vrai travail.
# ---------------------------------------------------------------------------

class PDFExtractor:
    """
    Extrait le contenu multimodal d'un PDF industriel.

    ARCHITECTURE INTERNE :
        extract()              — point d'entrée public
          ├── _extract_page()  — traite une page
          │     ├── _extract_text_blocks()  — blocs de texte
          │     └── _extract_images()       — images embarquées
          └── _post_process()  — nettoyage + numérotation finale

    USAGE :
        extractor = PDFExtractor()
        chunks = extractor.extract("rapport_huawei.pdf")
        for chunk in chunks:
            print(chunk.preview())
    """

    def __init__(self, config: Optional[ExtractionConfig] = None):
        self.config = config or ExtractionConfig()

    # ------------------------------------------------------------------
    # POINT D'ENTRÉE PUBLIC
    # ------------------------------------------------------------------

    def extract(self, pdf_path: str | Path) -> list[ExtractedChunk]:
        """
        Extrait tous les chunks (texte + images) d'un PDF.

        Args:
            pdf_path : chemin vers le fichier PDF

        Returns:
            Liste ordonnée de chunks (ordre : page 1 → N, haut → bas)

        Raises:
            FileNotFoundError : si le fichier n'existe pas
            ValueError        : si le fichier n'est pas un PDF valide
        """
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF introuvable : {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Ce fichier n'est pas un PDF : {pdf_path}")

        all_chunks: list[ExtractedChunk] = []
        source_file = str(pdf_path)

        # fitz.open() ouvre le PDF en mémoire
        # On utilise un context manager pour garantir la fermeture propre
        with fitz.open(str(pdf_path)) as doc:
            print(f"[PDFExtractor] Ouverture : {pdf_path.name}")
            pdf_ver = getattr(doc, "pdf_version", lambda: "?")()
            print(f"  → {len(doc)} page(s) | PDF v{pdf_ver}")

            for page_num, page in enumerate(doc, start=1):
                print(f"  → Traitement page {page_num}/{len(doc)}...", end=" ")

                page_chunks = self._extract_page(page, page_num, source_file)
                all_chunks.extend(page_chunks)

                print(f"{len(page_chunks)} chunks extraits")

        # Post-traitement : numérotation globale + filtrage final
        chunks = self._post_process(all_chunks)

        print(f"[PDFExtractor] Terminé : {len(chunks)} chunks valides")
        self._print_summary(chunks)

        return chunks

    # ------------------------------------------------------------------
    # TRAITEMENT D'UNE PAGE
    # ------------------------------------------------------------------

    def _extract_page(
        self,
        page: fitz.Page,
        page_number: int,
        source_file: str,
    ) -> list[ExtractedChunk]:
        """Extrait tous les éléments d'une page unique."""
        chunks: list[ExtractedChunk] = []

        if self.config.extract_text:
            chunks.extend(self._extract_text_blocks(page, page_number, source_file))

        if self.config.extract_images:
            chunks.extend(self._extract_images(page, page_number, source_file))

        # Tri spatial : de haut en bas (y0 croissant)
        # Important pour préserver l'ordre de lecture naturel
        chunks.sort(key=lambda c: (c.bbox.y0, c.bbox.x0))

        return chunks

    # ------------------------------------------------------------------
    # EXTRACTION DE TEXTE
    # ------------------------------------------------------------------

    def _extract_text_blocks(
        self,
        page: fitz.Page,
        page_number: int,
        source_file: str,
    ) -> list[ExtractedChunk]:
        """
        Extrait les blocs de texte d'une page.

        pymupdf découpe le texte en "blocs" — des zones rectangulaires
        contenant du texte cohérent. C'est exactement ce dont on a besoin.

        Structure d'un bloc pymupdf :
            (x0, y0, x1, y1, text, block_no, block_type)
            block_type = 0 : texte
            block_type = 1 : image (on les ignore ici, on les gère séparément)
        """
        chunks: list[ExtractedChunk] = []

        # get_text("blocks") retourne la liste des blocs avec position
        blocks = page.get_text("blocks")

        for block in blocks:
            x0, y0, x1, y1, text, block_no, block_type = block

            # On ne garde que les blocs texte (type 0)
            if block_type != 0:
                continue

            # Nettoyage du texte
            text = text.strip()

            # Filtrage des fragments trop courts (numéros de page, etc.)
            if len(text) < self.config.min_text_length:
                continue

            bbox = BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)

            # Heuristique tableau : détection basique par présence de pipes
            # ou de multiples tabulations (structure tabulaire)
            chunk_type = ChunkType.TEXT
            if self.config.table_heuristic and self._looks_like_table(text):
                chunk_type = ChunkType.TABLE

            chunk = ExtractedChunk(
                chunk_type=chunk_type,
                page_number=page_number,
                bbox=bbox,
                source_file=source_file,
                content=text,
                metadata={
                    "block_index": block_no,
                    "char_count": len(text),
                    "line_count": text.count("\n") + 1,
                },
            )
            chunks.append(chunk)

        return chunks

    def _looks_like_table(self, text: str) -> bool:
        """
        Heuristique simple pour détecter un tableau.

        UN VRAI DÉTECTEUR DE TABLEAUX est plus complexe (analyse des
        bounding boxes, espacement régulier, etc.). Cette heuristique
        couvre 80% des cas pour un MVP.

        Critères :
        - Plusieurs lignes avec tabulations ou pipes
        - Pattern répétitif de séparateurs
        """
        lines = text.strip().split("\n")
        if len(lines) < 3:
            return False

        # Compter les lignes contenant des séparateurs typiques de tableau
        separator_lines = sum(
            1 for line in lines
            if "\t" in line or "  |  " in line or line.count("  ") >= 3
        )

        # Si plus de 40% des lignes ont des séparateurs → tableau probable
        return (separator_lines / len(lines)) >= 0.4

    # ------------------------------------------------------------------
    # EXTRACTION D'IMAGES
    # ------------------------------------------------------------------

    def _extract_images(
        self,
        page: fitz.Page,
        page_number: int,
        source_file: str,
    ) -> list[ExtractedChunk]:
        """
        Extrait les images d'une page avec rendu haute résolution.
 
        STRATÉGIE AMÉLIORÉE :
        Au lieu d'extraire l'image embarquée brute (souvent basse résolution
        ou vectorielle illisible), on rend TOUJOURS la zone de l'image
        directement depuis la page PDF à 200 DPI.
 
        POURQUOI ?
        Les schémas réseau Huawei sont souvent dessinés en vectoriel dans
        le PDF (paths, formes géométriques). Extraire l'image brute donne
        une image floue ou partielle. Rendre la page à haute résolution
        donne une image nette avec tous les labels visibles.
        C'est ce qui permet au modèle vision de lire correctement les
        adresses IP, noms d'interfaces et étiquettes VLAN.
        """
        chunks: list[ExtractedChunk] = []
        image_list = page.get_images(full=True)
 
        for img_index, img_info in enumerate(image_list):
            xref       = img_info[0]
            img_width  = img_info[2]
            img_height = img_info[3]
 
            # Filtre taille minimale
            if (img_width * img_height) < self.config.min_image_area:
                continue
 
            # Récupération de la position de l'image sur la page
            rects = page.get_image_rects(xref)
            if rects:
                rect = rects[0]
                bbox = BoundingBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)
            else:
                page_rect = page.rect
                bbox = BoundingBox(
                    x0=page_rect.x0, y0=page_rect.y0,
                    x1=page_rect.x1, y1=page_rect.y1
                )
 
            # RENDU HAUTE RÉSOLUTION de la zone (200 DPI)
            # On rend la région exacte de l'image depuis la page PDF
            # → textes, labels, adresses IP tous lisibles par le LLM vision
            image_bytes = self._render_region_hd(page, bbox)
            image_ext   = "png"  # rendu toujours en PNG propre
 
            chunk = ExtractedChunk(
                chunk_type=ChunkType.IMAGE,
                page_number=page_number,
                bbox=bbox,
                source_file=source_file,
                image_bytes=image_bytes,
                image_format=image_ext,
                metadata={
                    "img_index":  img_index,
                    "xref":       xref,
                    "img_width":  img_width,
                    "img_height": img_height,
                    "source":     "hd_render",  # rendu HD depuis la page
                    "render_dpi": self.config.image_dpi,
                },
            )
            chunks.append(chunk)
 
        return chunks
    def _render_region_hd(self, page: fitz.Page, bbox: BoundingBox) -> bytes:
        """
        Rend une région de la page en PNG haute résolution.
 
        DPI recommandés :
            150 DPI → correct pour texte simple
            200 DPI → bon pour schémas avec labels (notre défaut)
            300 DPI → excellent mais fichiers lourds
 
        On utilise image_dpi depuis la config (défaut 200).
        """
        rect        = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
        zoom_factor = self.config.image_dpi / 72.0
        mat         = fitz.Matrix(zoom_factor, zoom_factor)
        pixmap      = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
        return pixmap.tobytes("png")
 
    def render_page_region_as_image(
        self,
        page: fitz.Page,
        bbox: BoundingBox,
    ) -> bytes:
        """
        Rend une région d'une page en image PNG.

        UTILE POUR :
        - Graphiques vectoriels (dessinés en PDF natif, pas en images)
        - Captures de zones mixtes texte+graphique (ex: légende d'un schéma)

        Args:
            page : la page pymupdf
            bbox : la zone à capturer

        Returns:
            bytes PNG de la région capturée
        """
        rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)

        # mat = matrice de transformation = facteur de zoom
        # DPI 72 = factor 1.0 | DPI 150 = factor 2.08 | DPI 300 = factor 4.17
        zoom_factor = self.config.image_dpi / 72.0
        mat = fitz.Matrix(zoom_factor, zoom_factor)

        # Rendu de la zone en pixmap (image raster)
        pixmap = page.get_pixmap(matrix=mat, clip=rect)

        return pixmap.tobytes("png")

    # ------------------------------------------------------------------
    # POST-TRAITEMENT
    # ------------------------------------------------------------------

    def _post_process(self, chunks: list[ExtractedChunk]) -> list[ExtractedChunk]:
        """
        Nettoyage final et numérotation des chunks.

        Opérations :
        1. Filtrage des chunks vides
        2. Numérotation globale (chunk_index)
        3. Ajout de métadonnées de contexte (page précédente/suivante)
        """
        # Filtrage : on ne garde que les chunks avec du contenu
        valid_chunks = [c for c in chunks if c.has_content()]

        # Numérotation globale
        for i, chunk in enumerate(valid_chunks):
            chunk.chunk_index = i
            # Ajout du contexte de position relative dans le document
            chunk.metadata["total_chunks"]    = len(valid_chunks)
            chunk.metadata["position_ratio"]  = round(i / max(len(valid_chunks) - 1, 1), 3)

        return valid_chunks

    def _print_summary(self, chunks: list[ExtractedChunk]) -> None:
        """Affiche un résumé lisible de l'extraction."""
        text_count  = sum(1 for c in chunks if c.chunk_type == ChunkType.TEXT)
        table_count = sum(1 for c in chunks if c.chunk_type == ChunkType.TABLE)
        image_count = sum(1 for c in chunks if c.chunk_type == ChunkType.IMAGE)
        pages       = sorted({c.page_number for c in chunks})

        print(f"\n{'='*50}")
        print(f"  RÉSUMÉ EXTRACTION")
        print(f"{'='*50}")
        print(f"  Texte    : {text_count} blocs")
        print(f"  Tableaux : {table_count} blocs")
        print(f"  Images   : {image_count} images")
        print(f"  Pages    : {pages[0]}–{pages[-1]} ({len(pages)} pages)")
        print(f"  Total    : {len(chunks)} chunks")
        print(f"{'='*50}\n")


# ---------------------------------------------------------------------------
# 4. FONCTIONS UTILITAIRES
#    Des helpers autonomes, utiles pour tester et débugger.
# ---------------------------------------------------------------------------

def save_extracted_images(
    chunks: list[ExtractedChunk],
    output_dir: str | Path,
    pdf_name: Optional[str] = None,
) -> list[Path]:
    """
    Sauvegarde les images extraites dans un sous-dossier nommé d'après le PDF.
 
    POURQUOI UN SOUS-DOSSIER PAR PDF ?
        Si on ingère plusieurs PDFs, leurs images se mélangeraient dans un
        seul dossier plat. Avec des sous-dossiers, chaque image reste
        traçable jusqu'à sa source, et le vision_analyzer sait exactement
        à quel document elle appartient.
 
    Structure produite :
        output_dir/
        └── TP2_3RLE/              ← nom du PDF sans extension (caractères nettoyés)
            ├── page001_img0000.png
            ├── page003_img0001.jpeg
            └── ...
 
    Args:
        chunks     : liste de chunks issus de pdf_extractor.py
        output_dir : dossier racine (ex: "extracted_images")
        pdf_name   : nom du PDF source. Si None, déduit depuis chunk.source_file.
 
    Returns:
        Liste des chemins absolus des fichiers sauvegardés.
    """
    output_dir = Path(output_dir)
    img_chunks = [c for c in chunks if c.is_image() and c.image_bytes]

    saved_paths = []
    img_chunks = [c for c in chunks if c.is_image() and c.image_bytes]
    if not img_chunks:
        print("[save_images] Aucune image à sauvegarder.")
        return []
    if pdf_name:
        folder_name = pdf_name
    elif img_chunks[0].source_file:
        # Ex : "F:/docs/TP2&3RLE.pdf"  →  "TP2&3RLE"
        folder_name = Path(img_chunks[0].source_file).stem
    else:
        folder_name = "unknown_pdf"
 
    # Nettoyage du nom : retirer les caractères interdits sur Windows
    # Windows interdit : \ / : * ? " < > |  et & pose des problèmes dans le shell
    for char in r'\\/:*?"<>|&':
        folder_name = folder_name.replace(char, "_")
 
    # Création du sous-dossier : output_dir / folder_name
    pdf_output_dir = output_dir / folder_name
    pdf_output_dir.mkdir(parents=True, exist_ok=True)
 
    # --- Sauvegarde des images ---
    saved_paths = []
 
 
    for chunk in img_chunks:
        filename = (
            f"page{chunk.page_number:03d}_"
            f"img{chunk.chunk_index:04d}"
            f".{chunk.image_format or 'png'}"
        )
        path = pdf_output_dir / filename
        path.write_bytes(chunk.image_bytes)
        saved_paths.append(path)
        chunk.metadata["image_path"]      = str(path)
        chunk.metadata["image_folder"]    = str(pdf_output_dir)
        chunk.metadata["pdf_folder_name"] = folder_name

    print(f"[save_images] {len(saved_paths)} image(s) sauvegardée(s)")
    print(f"  → Dossier : {pdf_output_dir}")
    for p in saved_paths:
        print(f"     • {p.name}")
 
    return saved_paths


def get_extraction_stats(chunks: list[ExtractedChunk]) -> dict:
    """
    Retourne des statistiques sur l'extraction — utile pour les logs
    et pour ajuster la configuration.
    """
    if not chunks:
        return {"total": 0}

    text_chunks  = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE]
    image_chunks = [c for c in chunks if c.chunk_type == ChunkType.IMAGE]

    avg_text_len = (
        sum(len(c.content) for c in text_chunks) / len(text_chunks)
        if text_chunks else 0
    )

    return {
        "total":          len(chunks),
        "text_count":     len(text_chunks),
        "table_count":    len(table_chunks),
        "image_count":    len(image_chunks),
        "avg_text_len":   round(avg_text_len, 1),
        "pages_covered":  len({c.page_number for c in chunks}),
        "image_formats":  list({c.image_format for c in image_chunks if c.image_format}),
    }


# ---------------------------------------------------------------------------
# 5. POINT D'ENTRÉE POUR TEST RAPIDE
#    python ingestion/pdf_extractor.py mon_rapport.pdf
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor.py <chemin_vers.pdf>")
        print("\nExemple:")
        print("  python pdf_extractor.py docs/topologie_huawei.pdf")
        sys.exit(1)

    pdf_path = sys.argv[1]

    # Configuration : on peut ajuster selon le type de document
    config = ExtractionConfig(
        min_text_length=20,    # ignorer les fragments courts
        min_image_area=100000,   # ignorer les petites icônes
        image_dpi=150,         # qualité correcte pour les modèles vision
        extract_images=True,
        extract_text=True,
        table_heuristic=True,
    )

    extractor = PDFExtractor(config=config)
    chunks = extractor.extract(pdf_path)

    # Affichage des premiers chunks pour vérification
    print("\n--- APERÇU DES PREMIERS CHUNKS ---")
    for chunk in chunks[::]:
        print(f"  [{chunk.chunk_index}] {chunk.chunk_type.value.upper():6s} "
              f"p.{chunk.page_number} | {chunk.preview()}")

    # Statistiques
    stats = get_extraction_stats(chunks)
    print(f"\nStats : {stats}")

    # Sauvegarde des images extraites
    if any(c.is_image() for c in chunks):
        pdf_stem = Path(pdf_path).stem  # "TP2&3RLE.pdf" → "TP2&3RLE"
        save_extracted_images(chunks, output_dir="D:\\HUAWEI\\backend\\extracted_images",pdf_name=pdf_stem,          # → sous-dossier TP2_3RLE/
        )
 