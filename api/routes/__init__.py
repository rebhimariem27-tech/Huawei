"""
api/routes/__init__.py
========================
Point d'agrégation de tous les routers de l'API.

POURQUOI CE FICHIER ?
    Sans lui, main.py devrait connaître et importer chaque router
    individuellement (routes.query, routes.ingest, ...) et faire
    plusieurs app.include_router(). À chaque nouveau router ajouté,
    il faudrait retoucher main.py.

    Avec ce fichier, main.py importe un seul objet — api_router —
    qui contient déjà tous les sous-routers. main.py n'a plus qu'une
    seule ligne à écrire, peu importe combien de fichiers routes/*.py
    on ajoute ensuite (ingest.py, files.py, health.py...).

USAGE DANS main.py :
    from routes import api_router
    app.include_router(api_router)
"""

from fastapi import APIRouter

from . import query as query_routes
from . import ingest as ingest_routes   # ← décommenté / ajouté

# ADAPTE / COMPLÈTE ICI au fur et à mesure des nouveaux routers :
# from routes import ingest as ingest_routes
# from routes import files as files_routes

api_router = APIRouter()

api_router.include_router(query_routes.router)
api_router.include_router(ingest_routes.router)
# api_router.include_router(files_routes.router)