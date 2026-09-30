"""Upload and ingest one manually described legal source through the API."""
import argparse
import json
import os
from pathlib import Path
import httpx
from dotenv import load_dotenv

def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--title-fr")
    parser.add_argument("--title-ar")
    parser.add_argument("--source-url")
    parser.add_argument("--source-name")
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--previous-version-id")
    parser.add_argument("--article")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if not args.title_fr and not args.title_ar:
        parser.error("--title-fr ou --title-ar est obligatoire")
    if not os.getenv("API_KEY"):
        parser.error("API_KEY absent; créer le fichier .env")
    metadata = {"title_fr":args.title_fr, "title_ar":args.title_ar, "source_url":args.source_url, "source_name":args.source_name, "official":args.official, "previous_version_id":args.previous_version_id}
    with httpx.Client(base_url=args.url, headers={"X-API-Key":os.environ["API_KEY"]}, timeout=180) as client:
        with args.pdf.open("rb") as handle:
            response = client.post("/legal-documents/upload", files={"file":(args.pdf.name,handle,"application/pdf")}, data={"metadata":json.dumps(metadata,ensure_ascii=False)})
        response.raise_for_status()
        document_id = response.json()["id"]
        response = client.post("/legal-documents/ingest",json={"document_id":document_id})
        response.raise_for_status()
        print(json.dumps(response.json(),ensure_ascii=False,indent=2))
        params = {"article_number":args.article} if args.article else {}
        response = client.get(f"/legal-documents/{document_id}/articles",params=params)
        response.raise_for_status()
        print(json.dumps(response.json(),ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
