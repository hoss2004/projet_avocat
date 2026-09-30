import pymupdf
import pytest
from app.parsers.pdf_parser import extract_pdf, PDFError, OCRRequired

def make_pdf(*pages):
    with pymupdf.open() as doc:
        for value in pages:
            doc.new_page().insert_text((50, 70), value)
        return doc.tobytes()

def test_pages_and_source_text():
    result = extract_pdf(make_pdf("TEST LAW\nArticle 1. Synthetic text only.", "TEST ARTICLE 2. Synthetic second page."))
    assert len(result.pages) == 2
    assert "TEST LAW" in result.text
    assert "\f" in result.text

@pytest.mark.parametrize("data", [b"bad", b"%PDF-invalid"])
def test_invalid_pdf(data):
    with pytest.raises(PDFError):
        extract_pdf(data)

def test_scan_and_limit():
    with pytest.raises(OCRRequired):
        extract_pdf(make_pdf(""))
    with pytest.raises(PDFError):
        extract_pdf(make_pdf("TEST LAW", "TEST LAW"), max_pages=1)


def test_unreadable_font_mapping_requires_ocr(monkeypatch):
    class Page:
        def get_text(self, *args, **kwargs):
            return "TEST LAW" + "\x01\x02\x03" * 50
    class Document:
        needs_pass = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __len__(self): return 1
        def __iter__(self): return iter([Page()])
    monkeypatch.setattr("app.parsers.pdf_parser.pymupdf.open", lambda **kwargs: Document())
    with pytest.raises(OCRRequired, match="encodage"):
        extract_pdf(b"%PDF-TEST LAW")
