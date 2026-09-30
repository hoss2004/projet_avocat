from dataclasses import dataclass
from typing import Protocol
import unicodedata
import pymupdf

class PDFError(ValueError):
    pass

class OCRRequired(PDFError):
    pass

class OCRProvider(Protocol):
    def extract(self, pdf: bytes) -> list[str]: ...

@dataclass(frozen=True)
class ExtractedPDF:
    pages: list[str]
    warnings: list[str]

    @property
    def text(self) -> str:
        return "\n\f\n".join(self.pages)

def extract_pdf(data: bytes, max_pages: int = 1500) -> ExtractedPDF:
    """Extract the PDF text layer unchanged; retain page order and flag incomplete extraction."""
    if not data.startswith(b"%PDF-"):
        raise PDFError("Le fichier ne possède pas une signature PDF valide")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise PDFError("Les PDF chiffrés ne sont pas acceptés")
            if not 0 < len(document) <= max_pages:
                raise PDFError("Nombre de pages hors limite")
            pages, warnings = [], []
            total_chars = 0
            for index, page in enumerate(document):
                text = page.get_text("text", sort=False)
                invalid_controls = sum(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in text)
                if invalid_controls > max(5, len(text) * 0.02) or "\x00" in text:
                    raise OCRRequired(f"Page {index + 1}: encodage de police illisible; OCR ou autre PDF nécessaire")
                total_chars += len(text)
                if total_chars > 12_000_000:
                    raise PDFError("Couche texte trop volumineuse")
                pages.append(text)
                if len(text.strip()) < 20:
                    warnings.append(f"Page {index + 1}: texte faible ou absent, contrôle/OCR nécessaire")
                if any(c == "\ufffd" or unicodedata.category(c) == "Co" for c in text):
                    warnings.append(f"Page {index + 1}: caractères non interprétables, vérifier le PDF")
            if not any(sum(c.isalpha() for c in p) >= 10 for p in pages):
                raise OCRRequired("Aucune couche texte exploitable. OCR nécessaire, non implémenté dans cette phase")
            return ExtractedPDF(pages, warnings)
    except PDFError:
        raise
    except (RuntimeError, ValueError) as exc:
        raise PDFError("PDF endommagé ou illisible") from exc
