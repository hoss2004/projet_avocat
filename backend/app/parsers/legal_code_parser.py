"""Conservative heading parser. Source offsets always refer to the untouched text."""
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
import re
from app.utils.arabic import normalize_for_search
from app.parsers.legal_structure import LegalStructureParser
from app.parsers.composite import boundary

ARTICLE = re.compile(r"^(?:الفصل|article|art\.)\s*(?P<number>\d+|الاول|االول|اول|premier|1er|unique|الوحيد)(?:\s*(?P<suffix>مكرر(?:\s+\d+)?|bis|ter|quater))?(?=$|[\s.:،؛()\-–—])", re.I)
@dataclass(frozen=True)
class ParsedArticle:
    article_number: str
    original_text: str
    page_start: int
    page_end: int
    start_offset: int
    end_offset: int
    chunk_index: int
    book: str | None
    title_section: str | None
    chapter: str | None
    section: str | None
    subsection: str | None = None
    legal_domain: str | None = None
    legal_subdomain: str | None = None
    section_ar: str | None = None
    section_fr: str | None = None
    structure_version: str | None = None
    legal_text: dict = field(default_factory=dict)

@dataclass(frozen=True)
class ParseResult:
    articles: list[ParsedArticle]
    warnings: list[str]

def canonical_article_number(value: str) -> str:
    value = normalize_for_search(value)
    value = re.sub(r"^(?:الفصل|article|art\.)\s*", "", value)
    value = re.sub(r"^(?:الاول|االول|اول|premier|1er)(?=$|\s)", "1", value)
    value = re.sub(r"^(\d+)", lambda m: str(int(m[1])), value)
    return value

def parse_legal_code(pages: list[str]) -> ParseResult:
    text = "\n\f\n".join(pages)
    page_offsets, position = [], 0
    for page in pages:
        page_offsets.append(position)
        position += len(page) + 3
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    structure_parser = LegalStructureParser()
    starts, structure_offsets = [], []
    visual_headers = False
    legal_text={"id":"main","kind":"main","title":None,"confidence":"document_metadata","start_offset":0}
    last_number=None
    boundary_since_article=False
    declared_code=None
    main_code_entered=False
    for index, line in enumerate(lines):
        detected=boundary(lines,index)
        if detected:
            if detected["kind"]=="code": declared_code=detected.copy()
            legal_text={**detected,"id":f"text:{offsets[index]}","start_offset":offsets[index]}
            structure_parser=LegalStructureParser()
            structure_offsets.append(offsets[index])
            boundary_since_article=True
            continue
        normalized = normalize_for_search(line)
        candidate = normalized
        # Some PDF producers put the article number on the following line.
        if normalized in {"الفصل", "article", "art."} and index + 1 < len(lines):
            candidate += " " + normalize_for_search(lines[index + 1])
        # Certain Arabic PDFs emit numeric headings as "403 الفصل" and move
        # amendment annotations before them. Interpret headings, never rewrite text.
        reversed_heading = re.search(r"(?P<number>\d+)\s+الفصل[\s().\-]*$", normalized)
        if reversed_heading:
            prefix = normalized[:reversed_heading.start()].strip()
            if not prefix or (prefix.startswith((")", "(")) and any(word in prefix for word in ("نقح", "اضيف", "الغي"))):
                suffix = " مكرر" if re.search(r"(?:^|\s)مكرر(?:\s|$)", prefix) else ""
                candidate = "الفصل " + reversed_heading["number"] + suffix
                visual_headers = True
        match = ARTICLE.match(candidate)
        if match:
            number = match["number"] + (" " + match["suffix"] if match["suffix"] else "")
            number=canonical_article_number(number)
            if number=="1" and last_number is not None and not boundary_since_article:
                if structure_parser.context["book"] and declared_code and not main_code_entered:
                    legal_text={**declared_code,"id":f"code:{offsets[index]}","confidence":"book_after_promulgation","start_offset":offsets[index]}
                    main_code_entered=True
                else:
                    legal_text={"id":f"unidentified:{offsets[index]}","kind":"unidentified","title":None,"confidence":"numbering_restart","review_required":True,"start_offset":offsets[index]}
                    structure_parser=LegalStructureParser()
            parents=structure_parser.snapshot()
            parents["legal_text"]=legal_text.copy()
            starts.append((offsets[index],number,parents))
            last_number=number
            boundary_since_article=False
            structure_parser.pending = None
            continue
        if structure_parser.consume(line):
            structure_offsets.append(offsets[index])
    articles = []
    for index, (start, number, parents) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        following_structure = next((p for p in structure_offsets if start < p < end), None)
        if following_structure is not None:
            end = following_structure
        articles.append(ParsedArticle(number, text[start:end], bisect_right(page_offsets, start), bisect_right(page_offsets, max(start, end - 1)), start, end, index, **parents))
    warnings = []
    if visual_headers:
        warnings.append("Ordre visuel RTL détecté dans les en-têtes; vérifier la lecture des phrases dans le PDF original")
    duplicates = [number for number, count in Counter(a.article_number for a in articles).items() if count > 1]
    if duplicates:
        warnings.append("Numéros répétés (lois introductives, annexes ou sommaire possibles): " + ", ".join(duplicates))
    if not articles:
        warnings.append("Aucun en-tête d'article reconnu; vérifier la couche texte et la mise en page")
    warnings.append("Découpage automatique à vérifier dans le PDF; les notes et en-têtes peuvent rester dans les extraits")
    return ParseResult(articles, warnings)
