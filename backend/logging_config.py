"""
backend/logging_config.py
===========================
Configuration centralisée du logging pour tout le projet.

POURQUOI CE FICHIER ?
    Avoir des print() partout empêche de :
    - Filtrer par niveau de gravité (DEBUG vs ERROR)
    - Rediriger vers un fichier en production sans toucher au code
    - Distinguer quel module a produit quel message
    - Désactiver le bruit de debug sans supprimer les logs utiles

    Avec ce module, chaque fichier fait juste :
        from logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Pipeline prêt")
        logger.warning("Qdrant indisponible, fallback fichier")
        logger.error("SSH échoué : %s", exception)

USAGE DANS UN AGENT (ex: architect_agent.py) :
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))  # backend/
    from logging_config import get_logger

    logger = get_logger(__name__)

    class ArchitectAgent:
        def __init__(self, ...):
            logger.info("Topologie : %s", self._device_names or "non définie")
            # au lieu de : print(f"[ArchitectAgent] Topologie : {...}")

NIVEAUX (du plus verbeux au plus grave) :
    DEBUG    → détails internes (contenu des chunks, requêtes SQL/Qdrant)
    INFO     → étapes normales du pipeline (agent démarré, requête traitée)
    WARNING  → dégradation gérée (fallback SSH→fichier, Qdrant indisponible)
    ERROR    → échec d'une opération (SSH échoué, LLM indisponible)
    CRITICAL → le pipeline ne peut pas continuer

CONFIGURATION VIA VARIABLE D'ENVIRONNEMENT :
    LOG_LEVEL=DEBUG python graph.py "..."   → affiche tout, y compris le détail
    LOG_LEVEL=WARNING uvicorn main:app      → ne montre que les problèmes
    (par défaut : INFO)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "huawei_rag.log"

# Format : [timestamp] [niveau] [module] message
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


# ---------------------------------------------------------------------------
# 2. SETUP — appelé une seule fois, au premier get_logger()
# ---------------------------------------------------------------------------

def _configure_root_logger() -> None:
    """
    Configure le logger racine une seule fois pour tout le process.

    Deux sorties simultanées :
    - Console (stdout) : ce que tu vois pendant le développement
    - Fichier (logs/huawei_rag.log) : historique persistant, utile pour
      déboguer après coup ce qui s'est passé en prod / lors d'un test
    """
    global _configured
    if _configured:
        return

    LOG_DIR.mkdir(exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Librairies tierces trop verbeuses en DEBUG — on les garde en WARNING
    for noisy in ("httpx", "httpcore", "urllib3", "qdrant_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Retourne un logger nommé, prêt à l'emploi.

    Args:
        name : généralement __name__ du module appelant, pour que
               chaque ligne de log indique clairement sa provenance.

    Usage :
        logger = get_logger(__name__)
        logger.info("Message normal")
        logger.warning("Dégradation gérée : %s", detail)
        logger.error("Échec : %s", exception)
    """
    _configure_root_logger()
    return logging.getLogger(name)