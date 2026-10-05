"""
ingestion/vision_analyzer.py
============================
Analyse des images techniques via Groq (llama-4-scout-17b vision).

DEUX MODES :
    mock_mode=True  → réponses simulées, 0 appel API  (développement)
    mock_mode=False → appels réels à Groq             (production)

INSTALLATION :
    pip install groq
    Variable d'env : GROQ_API_KEY=gsk_...
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pdf_extractor import ChunkType, ExtractedChunk


# ---------------------------------------------------------------------------
# 1. TYPES DE DONNÉES
# ---------------------------------------------------------------------------

class ImageCategory(str, Enum):
    NETWORK_TOPOLOGY  = "network_topology"
    ARCHITECTURE_DIAG = "architecture_diag"
    PERFORMANCE_GRAPH = "performance_graph"
    FLOW_DIAGRAM      = "flow_diagram"
    TABLE_VISUAL      = "table_visual"
    SCREENSHOT        = "screenshot"
    TECHNICAL_SCHEMA  = "technical_schema"
    UNKNOWN           = "unknown"


@dataclass
class VisionAnalysis:
    """Résultat complet de l'analyse d'une image."""
    description:     str
    category:        ImageCategory = ImageCategory.UNKNOWN
    key_elements:    list[str]     = field(default_factory=list)
    technical_terms: list[str]     = field(default_factory=list)
    confidence:      float         = 1.0
    tokens_used:     int           = 0
    raw_response:    str           = ""
    is_mock:         bool          = False
    error:           Optional[str] = None

    @property
    def is_successful(self) -> bool:
        return self.error is None and bool(self.description.strip())

    def to_searchable_text(self) -> str:
        """
        Texte final indexé dans Qdrant.
        Combine description + éléments clés + termes techniques.
        C'est le PONT entre l'analyse vision et le RAG.
        """
        parts = [self.description]
        if self.key_elements:
            parts.append("Éléments clés : " + ", ".join(self.key_elements))
        if self.technical_terms:
            parts.append("Termes techniques : " + ", ".join(self.technical_terms))
        return "\n\n".join(parts)

    def summary(self) -> str:
        mode = "[MOCK]" if self.is_mock else "[GROQ]"
        return (
            f"{mode} {self.category.value} "
            f"| confiance={self.confidence:.1f} "
            f"| {len(self.key_elements)} éléments "
            f"| {self.tokens_used} tokens"
        )


# ---------------------------------------------------------------------------
# 2. CONFIGURATION
# ---------------------------------------------------------------------------

@dataclass
class VisionConfig:
    """
    Paramètres de l'analyseur vision.

    model          : llama-4-scout-17b — meilleur modèle vision sur Groq
    mock_mode=True : aucun appel API (défaut — développement sans clé)
    domain_context : contexte métier injecté dans le prompt
    """
    model:          str   = "qwen/qwen3.6-27b"
    max_tokens:     int   = 1024
    temperature:    float = 0.1       # réponses factuelles, pas créatives
    retry_attempts: int   = 3
    retry_delay:    float = 2.0
    domain_context: str   = "télécommunications et infrastructure réseau Huawei"
    mock_mode:      bool  = True      # ← False quand tu passes en prod


# ---------------------------------------------------------------------------
# 3. PROMPTS
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es un expert technique senior spécialisé en infrastructure réseau,
télécommunications et systèmes Huawei.

Ta mission : analyser des images techniques extraites de PDFs industriels et produire
des descriptions textuelles précises et structurées.

RÈGLES :
- Sois précis et technique. Utilise la terminologie correcte du domaine.
- Si tu identifies des équipements Huawei (S12700, NE40E, S5735, S12700E...), nomme-les.
- Décris ce que tu VOIS réellement. Ne suppose pas ce qui n'est pas visible.
- Réponds TOUJOURS en français.

FORMAT DE RÉPONSE (respecte exactement cette structure) :

DESCRIPTION:
[Description technique complète, 3-8 phrases]

CATÉGORIE:
[network_topology | architecture_diag | performance_graph | flow_diagram | table_visual | screenshot | technical_schema | unknown]

ÉLÉMENTS CLÉS:
[liste séparée par des virgules]

TERMES TECHNIQUES:
[liste de termes/produits identifiés, séparés par des virgules]

CONFIANCE:
[nombre entre 0.0 et 1.0]"""


def build_user_prompt(domain_context: str, page_number: int) -> str:
    return (
        f"Analyse cette image technique extraite d'un document PDF "
        f"du domaine : {domain_context}.\n"
        f"Cette image provient de la page {page_number} du document.\n"
        f"Produis une analyse structurée selon le format demandé."
    )


# ---------------------------------------------------------------------------
# 4. MOCK ENGINE
# ---------------------------------------------------------------------------

MOCK_RESPONSES = {
    "network_topology": {
        "description": (
            "Ce diagramme représente une topologie réseau Huawei composée "
            "d'équipements de couche 2 et couche 3 interconnectés. "
            "On distingue un routeur principal (R1) connecté à des switches "
            "Huawei S5735 via des liaisons Gigabit Ethernet. "
            "Le schéma illustre la segmentation en VLANs avec des sous-interfaces "
            "configurées pour le routage inter-VLAN. "
            "Les adresses IP du réseau 10.0.x.0/24 sont visibles sur les liens."
        ),
        "category": ImageCategory.NETWORK_TOPOLOGY,
        "key_elements": [
            "Routeur R1", "Switch S1", "Switch S3", "Switch S4",
            "Liaisons Gigabit Ethernet", "Trunk VLAN", "Sous-interfaces",
        ],
        "technical_terms": [
            "VLAN", "Trunk", "GigabitEthernet", "Sub-interface",
            "Layer 3 switching", "Huawei S5735", "10.0.0.0/24",
        ],
        "confidence": 0.92,
    },
    "layer3_switching": {
        "description": (
            "Ce schéma illustre une architecture de commutation de couche 3 "
            "avec un switch Huawei central jouant le rôle de routeur. "
            "On observe plusieurs VLANs (VLAN 3, VLAN 4, VLAN 5) connectés "
            "au switch principal via des interfaces Vlanif. "
            "Un lien Eth-Trunk (agrégation LACP) est visible entre S1 et S2 "
            "pour la redondance et l'augmentation de bande passante."
        ),
        "category": ImageCategory.NETWORK_TOPOLOGY,
        "key_elements": [
            "Switch L3 S1", "Switch S2", "Routeur R1", "Routeur R3",
            "Eth-Trunk", "Interface Vlanif", "VLAN 3", "VLAN 4", "VLAN 5",
        ],
        "technical_terms": [
            "Layer 3 switching", "Vlanif", "Eth-Trunk", "LACP",
            "OSPF", "ip route-static", "Huawei VRP", "10.0.3.0/24",
        ],
        "confidence": 0.89,
    },
}


def _generate_mock_response(chunk: ExtractedChunk) -> VisionAnalysis:
    """
    Réponse mock basée sur le numéro de page.
    Cohérent avec les images du TP :
      page 1  → VLAN routing topology  (Lab 1-2)
      page 7+ → Layer 3 switching      (Lab 1-3)
    """
    if chunk.page_number <= 6:
        template     = MOCK_RESPONSES["network_topology"]
        context_note = "Topologie VLAN routing (Lab 1-2) — routeur R1 + switch S1"
    else:
        template     = MOCK_RESPONSES["layer3_switching"]
        context_note = "Topologie Layer 3 switching (Lab 1-3) — switch S1 multi-VLAN"

    description = (
        f"[Page {chunk.page_number}] {template['description']} "
        f"Contexte : {context_note}."
    )
    return VisionAnalysis(
        description=description,
        category=template["category"],
        key_elements=template["key_elements"].copy(),
        technical_terms=template["technical_terms"].copy(),
        confidence=template["confidence"],
        tokens_used=0,
        raw_response="[MOCK — aucun appel API effectué]",
        is_mock=True,
    )


# ---------------------------------------------------------------------------
# 5. PARSEUR DE RÉPONSE
# ---------------------------------------------------------------------------

def _parse_model_response(raw_text: str, tokens_used: int) -> VisionAnalysis:
    """
    Parse la réponse structurée du modèle en VisionAnalysis.
    Robuste : si une section manque → valeur par défaut, jamais d'exception.
    """
    def extract_section(text: str, marker: str) -> str:
        if marker not in text:
            return ""
        start     = text.index(marker) + len(marker)
        remaining = text[start:]
        all_markers = [
            "DESCRIPTION:", "CATÉGORIE:", "ÉLÉMENTS CLÉS:",
            "TERMES TECHNIQUES:", "CONFIANCE:",
        ]
        end = len(remaining)
        for nm in all_markers:
            if nm != marker and nm in remaining:
                pos = remaining.index(nm)
                if pos < end:
                    end = pos
        return remaining[:end].strip()

    description  = extract_section(raw_text, "DESCRIPTION:")
    category_str = extract_section(raw_text, "CATÉGORIE:").lower().strip()
    elements_str = extract_section(raw_text, "ÉLÉMENTS CLÉS:")
    terms_str    = extract_section(raw_text, "TERMES TECHNIQUES:")
    conf_str     = extract_section(raw_text, "CONFIANCE:")

    # Catégorie → enum
    category = ImageCategory.UNKNOWN
    for cat in ImageCategory:
        if cat.value in category_str:
            category = cat
            break

    # Listes
    key_elements    = [e.strip() for e in elements_str.split(",") if e.strip()]
    technical_terms = [t.strip() for t in terms_str.split(",") if t.strip()]

    # Confiance
    confidence = 0.8
    try:
        confidence = max(0.0, min(1.0, float(conf_str.replace(",", "."))))
    except (ValueError, TypeError):
        pass

    if not description:
        description = raw_text.strip()

    return VisionAnalysis(
        description=description,
        category=category,
        key_elements=key_elements,
        technical_terms=technical_terms,
        confidence=confidence,
        tokens_used=tokens_used,
        raw_response=raw_text,
        is_mock=False,
    )


# ---------------------------------------------------------------------------
# 6. ANALYSEUR PRINCIPAL
# ---------------------------------------------------------------------------

class VisionAnalyzer:
    """
    Analyse les images techniques — mode mock OU mode API Groq.

    SANS CLÉ (développement) :
        analyzer = VisionAnalyzer()
        analyzer = VisionAnalyzer(mock_mode=True)

    AVEC CLÉ (production) :
        $env:GROQ_API_KEY = "gsk_..."
        analyzer = VisionAnalyzer(mock_mode=False)
        analyzer = VisionAnalyzer(api_key="gsk_...", mock_mode=False)

    PHILOSOPHIE :
        Ne lève JAMAIS d'exception vers l'appelant.
        En cas d'erreur → VisionAnalysis(error=...) et le pipeline continue.
    """

    def __init__(
        self,
        api_key:   Optional[str]       = None,
        config:    Optional[VisionConfig] = None,
        mock_mode: Optional[bool]      = None,
    ):
        self.config  = config or VisionConfig()
        self._client = None

        if mock_mode is not None:
            self.config.mock_mode = mock_mode

        if self.config.mock_mode:
            print("[VisionAnalyzer] Mode MOCK — aucun appel API effectué")
            print("                 → mock_mode=False + GROQ_API_KEY pour la prod")
        else:
            self._init_groq_client(api_key)

    def _init_groq_client(self, api_key: Optional[str]) -> None:
        """Initialise le client Groq."""
        resolved_key = api_key or os.getenv("GROQ_API_KEY")

        if not resolved_key:
            raise ValueError(
                "\n[VisionAnalyzer] Clé API Groq manquante !\n"
                "  Solution 1 : $env:GROQ_API_KEY = 'gsk_...'\n"
                "  Solution 2 : VisionAnalyzer(api_key='gsk_...')\n"
                "  Solution 3 : VisionAnalyzer(mock_mode=True) pour dev sans clé\n"
                "  Obtenir une clé : https://console.groq.com/keys"
            )

        try:
            from groq import Groq
            self._client = Groq(api_key=resolved_key)
            print(f"[VisionAnalyzer] Mode API Groq — modèle : {self.config.model}")
        except ImportError:
            raise ImportError(
                "Package 'groq' manquant.\n"
                "  → pip install groq\n"
                "  → Ou : VisionAnalyzer(mock_mode=True)"
            )

    # ------------------------------------------------------------------
    # ANALYSE D'UN CHUNK
    # ------------------------------------------------------------------

    def analyze_chunk(self, chunk: ExtractedChunk) -> VisionAnalysis:
        """
        Analyse un chunk IMAGE. Route vers mock ou API Groq.
        Ne lève jamais d'exception.
        """
        if chunk.chunk_type != ChunkType.IMAGE:
            return VisionAnalysis(
                description="",
                error=f"Type '{chunk.chunk_type.value}' ignoré — attend IMAGE"
            )
        if not chunk.image_bytes:
            return VisionAnalysis(
                description="",
                error="Chunk IMAGE sans bytes"
            )

        print(
            f"  [Vision] Chunk #{chunk.chunk_index} "
            f"| page {chunk.page_number} "
            f"| {len(chunk.image_bytes) // 1024}KB "
            f"| {'[MOCK]' if self.config.mock_mode else '[GROQ]'}"
        )

        return _generate_mock_response(chunk) if self.config.mock_mode \
               else self._analyze_with_api(chunk)

    def analyze_all_chunks(
        self,
        chunks: list[ExtractedChunk],
        delay_between_calls: float = 1.0,
    ) -> dict[int, VisionAnalysis]:
        """
        Analyse tous les chunks IMAGE d'une liste.
        Retourne {chunk_index → VisionAnalysis}.

        delay_between_calls : important pour Groq free tier (rate limit)
        """
        image_chunks = [c for c in chunks if c.chunk_type == ChunkType.IMAGE]

        if not image_chunks:
            print("[VisionAnalyzer] Aucun chunk IMAGE trouvé.")
            return {}

        mode_label = "MOCK" if self.config.mock_mode else f"GROQ {self.config.model}"
        print(f"\n[VisionAnalyzer] {len(image_chunks)} image(s) [{mode_label}]")
        print("-" * 60)

        results:      dict[int, VisionAnalysis] = {}
        total_tokens: int = 0

        for i, chunk in enumerate(image_chunks, start=1):
            print(f"  [{i}/{len(image_chunks)}] ", end="", flush=True)
            analysis = self.analyze_chunk(chunk)
            results[chunk.chunk_index] = analysis

            if analysis.is_successful:
                total_tokens += analysis.tokens_used
                print(f"✓ {analysis.summary()}")
            else:
                print(f"✗ ERREUR : {analysis.error}")

            if not self.config.mock_mode and i < len(image_chunks):
                time.sleep(delay_between_calls)

        print("-" * 60)
        suffix = "0 token (mock)" if self.config.mock_mode else f"{total_tokens} tokens"
        print(f"[VisionAnalyzer] Terminé — {suffix}")
        return results

    # ------------------------------------------------------------------
    # MODE API — Groq
    # ------------------------------------------------------------------

    def _analyze_with_api(self, chunk: ExtractedChunk) -> VisionAnalysis:
        """Appelle l'API Groq avec retry automatique."""
        last_error = None

        for attempt in range(1, self.config.retry_attempts + 1):
            try:
                return self._call_groq_api(chunk)
            except Exception as e:
                last_error = str(e)
                # Détection rate limit Groq → attente plus longue
                if "rate_limit" in str(e).lower() or "429" in str(e):
                    wait = self.config.retry_delay * attempt * 4
                    print(f"\n    Rate limit Groq — attente {wait}s... ({attempt}/{self.config.retry_attempts})")
                elif attempt < self.config.retry_attempts:
                    wait = self.config.retry_delay * attempt
                    print(f"\n    Erreur Groq — retry dans {wait}s... ({attempt}/{self.config.retry_attempts})")
                else:
                    break
                time.sleep(wait)

        return VisionAnalysis(
            description="",
            error=f"Échec après {self.config.retry_attempts} tentatives : {last_error}"
        )

    def _call_groq_api(self, chunk: ExtractedChunk) -> VisionAnalysis:
        """
        Appel direct à l'API Groq avec l'image encodée en base64.

        POURQUOI BASE64 INLINE ?
            Groq suit le format OpenAI Vision :
            l'image est passée comme data URL base64 dans image_url.
            Format : "data:<media_type>;base64,<données>"

        STRUCTURE DU MESSAGE :
            Un seul message "user" avec content = liste de blocs :
            - bloc texte  : le prompt système + question
            - bloc image  : la data URL base64
        """
        # Encodage base64
        image_b64, media_type = self._encode_image(chunk)
        data_url = f"data:{media_type};base64,{image_b64}"

        # Prompt : système + utilisateur concaténés
        # (Groq/llama-4-scout accepte system séparé, on l'utilise)
        full_user_prompt = build_user_prompt(
            self.config.domain_context,
            chunk.page_number
        )

        response = self._client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            messages=[
                {
                    # Message système : définit le rôle de l'expert
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    # Message utilisateur : image + question
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": full_user_prompt,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                            },
                        },
                    ],
                },
            ],
        )

        raw_response = response.choices[0].message.content
        tokens_used  = response.usage.total_tokens if response.usage else 0

        return _parse_model_response(raw_response, tokens_used)

    def _encode_image(self, chunk: ExtractedChunk) -> tuple[str, str]:
        """Encode l'image en base64 avec son media_type MIME."""
        image_b64  = base64.standard_b64encode(chunk.image_bytes).decode("utf-8")
        mime_map   = {
            "png":  "image/png",
            "jpeg": "image/jpeg",
            "jpg":  "image/jpeg",
            "gif":  "image/gif",
            "webp": "image/webp",
            "jp2":  "image/jpeg",
        }
        media_type = mime_map.get((chunk.image_format or "png").lower(), "image/png")
        return image_b64, media_type


# ---------------------------------------------------------------------------
# 7. ENRICHISSEMENT DES CHUNKS
# ---------------------------------------------------------------------------

def enrich_chunks_with_vision(
    chunks: list[ExtractedChunk],
    analyzer: VisionAnalyzer,
) -> list[ExtractedChunk]:
    """
    Enrichit les chunks IMAGE avec leur analyse vision.

    Après cette fonction :
    - chunk.content       = texte searchable complet (pour Qdrant)
    - chunk.metadata      = description, catégorie, éléments, termes, confiance

    UNIFORMISATION : le chunker.py traitera les IMAGE comme du TEXT.
    Plus rien ne distingue un chunk image d'un chunk texte pour le RAG.
    """
    analyses       = analyzer.analyze_all_chunks(chunks)
    enriched_count = 0
    error_count    = 0

    for chunk in chunks:
        if chunk.chunk_type != ChunkType.IMAGE:
            continue

        analysis = analyses.get(chunk.chunk_index)
        if not analysis:
            continue

        if analysis.is_successful:
            chunk.metadata.update({
                "vision_description":  analysis.description,
                "vision_searchable":   analysis.to_searchable_text(),
                "vision_category":     analysis.category.value,
                "vision_key_elements": analysis.key_elements,
                "vision_terms":        analysis.technical_terms,
                "vision_confidence":   analysis.confidence,
                "vision_is_mock":      analysis.is_mock,
                "vision_tokens_used":  analysis.tokens_used,
            })
            chunk.content  = analysis.to_searchable_text()
            enriched_count += 1
        else:
            chunk.metadata["vision_error"] = analysis.error
            chunk.content  = f"[Image p.{chunk.page_number} — non analysée : {analysis.error}]"
            error_count   += 1

    print(f"\n[enrich_chunks] {enriched_count} enrichie(s), {error_count} erreur(s)")
    return chunks


# ---------------------------------------------------------------------------
# 8. POINT D'ENTRÉE
#    python vision_analyzer.py "F:/TP2&3RLE.pdf"          → mock
#    python vision_analyzer.py "F:/TP2&3RLE.pdf" --api    → Groq réel
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pdf_extractor import PDFExtractor, ExtractionConfig

    if len(sys.argv) < 2:
        print("Usage : python vision_analyzer.py <chemin.pdf> [--api]")
        print()
        print("Modes :")
        print("  (rien)  → MOCK, 0 appel API, test instantané")
        print("  --api   → API Groq réelle (nécessite GROQ_API_KEY)")
        print()
        print("Exemples :")
        print('  python vision_analyzer.py "F:/TP2&3RLE.pdf"')
        print('  python vision_analyzer.py "F:/TP2&3RLE.pdf" --api')
        sys.exit(1)

    pdf_path = sys.argv[1]
    use_api  = "--api" in sys.argv

    # ── Étape 1 : Extraction ───────────────────────────────────────────
    print("=" * 60)
    print("  ÉTAPE 1 — Extraction PDF")
    print("=" * 60)
    extractor = PDFExtractor(ExtractionConfig(extract_images=True))
    chunks    = extractor.extract(pdf_path)
    img_count = sum(1 for c in chunks if c.chunk_type == ChunkType.IMAGE)
    print(f"\n→ {img_count} image(s) à analyser\n")

    if img_count == 0:
        print("Aucune image. Test terminé.")
        sys.exit(0)

    # ── Étape 2 : Vision ──────────────────────────────────────────────
    print("=" * 60)
    print(f"  ÉTAPE 2 — Analyse Vision [{'API Groq' if use_api else 'MOCK'}]")
    print("=" * 60)
    analyzer = VisionAnalyzer(mock_mode=not use_api)
    enriched = enrich_chunks_with_vision(chunks, analyzer)

    # ── Résultats ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  RÉSULTATS")
    print("=" * 60)

    for chunk in enriched:
        if chunk.chunk_type != ChunkType.IMAGE:
            continue
        print(f"\n{'─'*55}")
        print(f"  Chunk #{chunk.chunk_index} | Page {chunk.page_number}")
        print(f"  Catégorie : {chunk.metadata.get('vision_category', 'N/A')}")
        print(f"  Confiance : {chunk.metadata.get('vision_confidence', 'N/A')}")
        print(f"  Mock      : {chunk.metadata.get('vision_is_mock', 'N/A')}")
        print(f"\n  Description :")
        for line in chunk.content.split(". "):
            if line.strip():
                print(f"    • {line.strip()}.")
        print(f"\n  Éléments : {', '.join(chunk.metadata.get('vision_key_elements', []))}")
        print(f"  Termes   : {', '.join(chunk.metadata.get('vision_terms', []))}")

    print(f"\n{'─'*55}")
    print(f"  Pipeline terminé ✓  —  {len(enriched)} chunks prêts pour chunker.py")
    print(f"{'─'*55}\n")