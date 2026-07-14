from qdrant_client import QdrantClient

client = QdrantClient(host="localhost", port=6333)
collection_name = "huawei_industrial_docs"

# On récupère les 3 premiers points pour voir leurs métadonnées
points, _ = client.scroll(collection_name=collection_name, limit=3, with_payload=True)

print("--- CONTENU ACTUEL DE QDRANT ---")
if not points:
    print("La collection est vide !")
else:
    for p in points:
        print(f"ID: {p.id} | Fichier en base : '{p.payload.get('source_file')}'")