"""Import the structured local legal corpus through the API.

The directory immediately below ``data/legal`` determines the document type.
Source provenance and effective dates are deliberately left for manual review.
"""

import argparse
import json
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_DIR = ROOT / "data" / "legal"
DOCUMENT_TYPES = {
    "constitution": "constitution",
    "codes": "code",
    "tax": "tax",
    "special_laws": "special_law",
    "decrees": "decree",
    "decree_laws": "decree_law",
    "orders": "order",
    "jort": "jort",
    "jurisprudence": "jurisprudence",
}


def corpus_files(corpus_dir: Path) -> list[Path]:
    """Return every source PDF in a stable order."""
    return sorted(
        (path for path in corpus_dir.rglob("*.pdf") if path.is_file()),
        key=lambda path: path.relative_to(corpus_dir).as_posix().casefold(),
    )


def metadata_for(path: Path, corpus_dir: Path) -> dict:
    relative = path.relative_to(corpus_dir)
    category = relative.parts[0] if len(relative.parts) > 1 else ""
    try:
        document_type = DOCUMENT_TYPES[category]
    except KeyError as exc:
        expected = ", ".join(sorted(DOCUMENT_TYPES))
        raise ValueError(
            f"{relative}: catégorie inconnue; placer le PDF dans l'un de ces dossiers: {expected}"
        ) from exc
    return {
        "title_fr": path.stem.replace("_", " ").strip(),
        "document_type": document_type,
        "official": False,
        "status": "unknown",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help="Racine du corpus structuré (défaut: data/legal)",
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Afficher les PDF et métadonnées sans appeler l'API",
    )
    args = parser.parse_args()

    corpus_dir = args.corpus_dir
    if not corpus_dir.is_absolute():
        corpus_dir = ROOT / corpus_dir
    corpus_dir = corpus_dir.resolve()
    if not corpus_dir.is_dir():
        parser.error(f"dossier de corpus introuvable: {corpus_dir}")

    entries = [
        {
            "path": path.relative_to(corpus_dir).as_posix(),
            "metadata": metadata_for(path, corpus_dir),
        }
        for path in corpus_files(corpus_dir)
    ]
    if not entries:
        parser.error(f"aucun PDF trouvé dans {corpus_dir}")
    if args.dry_run:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return

    settings = dotenv_values(ROOT / ".env")
    api_key = settings.get("API_KEY")
    if not api_key:
        parser.error("API_KEY absent; configurer le fichier .env")

    report = []
    with httpx.Client(
        base_url=args.url,
        headers={"X-API-Key": api_key},
        timeout=180,
    ) as client:
        for entry in entries:
            path = corpus_dir / entry["path"]
            with path.open("rb") as pdf:
                response = client.post(
                    "/legal-documents/upload",
                    files={"file": (path.name, pdf, "application/pdf")},
                    data={"metadata": json.dumps(entry["metadata"], ensure_ascii=False)},
                )
            response.raise_for_status()
            document_id = response.json()["id"]
            response = client.post(
                "/legal-documents/ingest", json={"document_id": document_id}
            )
            response.raise_for_status()
            item = {
                "path": entry["path"],
                "filename": path.name,
                "document_type": entry["metadata"]["document_type"],
                "id": document_id,
                "status": response.json()["ingestion_status"],
            }
            report.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)

    (ROOT / "docs" / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
