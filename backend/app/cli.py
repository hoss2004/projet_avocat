"""Read-only local PDF inspection without database or cloud services."""
import argparse
import json
from pathlib import Path
from app.parsers.pdf_parser import extract_pdf, PDFError
from app.parsers.legal_code_parser import parse_legal_code, canonical_article_number
from app.services.language.detection import detect_language

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--article")
    args = parser.parse_args()
    try:
        extracted = extract_pdf(args.pdf.read_bytes())
    except PDFError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
    parsed = parse_legal_code(extracted.pages)
    articles = parsed.articles
    if args.article:
        articles = [a for a in articles if a.article_number == canonical_article_number(args.article)]
    print(json.dumps({"filename": args.pdf.name, "pages": len(extracted.pages), "language": detect_language(extracted.text), "article_count": len(parsed.articles), "warnings": extracted.warnings + parsed.warnings, "articles": [{"number": a.article_number, "page_start": a.page_start, "page_end": a.page_end, "original_text": a.original_text} for a in articles]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
