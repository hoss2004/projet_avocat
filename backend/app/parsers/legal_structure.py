"""Conservative, source-preserving hierarchy and controlled heading taxonomy."""
import re
from app.utils.arabic import normalize_for_search

# Labels are routing aids, never official translations of the source.
TAXONOMY = {
    "general_obligations": ("Obligations générales", ["الالتزامات بوجه عام", "obligations en general"]),
    "sale": ("Vente", ["البيع", "في البيع", "de la vente", "vente"]),
    "lease": ("Bail / location", ["الكراء", "في الكراء", "كراء الاشياء", "في كراء الاشياء", "du louage", "du bail", "bail", "location"]),
    "mandate": ("Mandat", ["الوكالة", "في الوكالة", "du mandat", "mandat"]),
    "guarantee": ("Cautionnement", ["الكفالة", "في الكفالة", "du cautionnement", "cautionnement"]),
    "loan": ("Prêt", ["القرض", "في القرض", "du pret"]),
    "service_contract": ("Louage de services / ouvrage", ["في الاجارة علي خدمة الادمي او علي صنعه", "في الاجارة على خدمة الادمي او على صنعه", "في الاجارة على خدمة الاادمي او على صنعه", "في الاجارة على خدمة الادمي او على صنعه", "في اجارة الخدمة", "في الاجارة علي الصنع"]),
    "emphyteusis": ("Enzel et droits assimilés", ["في الانزال والكردار والخلو والنصبة", "في الانزال"]),
    "exchange": ("Échange", ["في المعاوضة"]),
    "deposit": ("Dépôt", ["الوديعة", "في الوديعة", "du depot"]),
    "partnership": ("Société contractuelle", ["الشركة", "في الشركة", "de la societe"]),
}
LEVELS = {"كتاب":"book", "الكتاب":"book", "الجزء":"subsection", "livre":"book",
          "المقالة":"title_section", "عنوان":"title_section", "العنوان":"title_section", "titre":"title_section",
          "باب":"chapter", "الباب":"chapter", "chapitre":"chapter", "chapter":"chapter",
          "قسم":"section", "القسم":"section", "section":"section", "فصل":"section", "الفصل":"section",
          "فرع":"subsection", "الفرع":"subsection", "sous-section":"subsection", "subsection":"subsection"}
ORDER = ["book", "title_section", "chapter", "section", "subsection"]
PREFIX = re.compile(r"^("+"|".join(LEVELS)+r")(?:\s|$)", re.I)

def classify_heading(text):
    value = normalize_for_search((text or "").splitlines()[-1] if text else "").strip(" .:ـ-–")
    if len(value)>100: return None, None
    value=value.replace("اال", "الا").replace("اإل", "الإ")
    for key, (label, aliases) in TAXONOMY.items():
        for alias in aliases+[label]:
            alias = normalize_for_search(alias)
            if value == alias:
                return key, label
    return None, None

class LegalStructureParser:
    def __init__(self):
        self.context = dict.fromkeys(ORDER)
        self.pending = None

    def consume(self, line):
        value = normalize_for_search(line)
        if not value: return False
        if "http" in value or re.search(r"\d{1,2}/\d{1,2}/\d{4}.*\d{1,2}:\d{2}",value): return False
        if len(value)>160:
            self.pending = None
            return False
        # Numeric الفصل is always an article, never a structural chapter.
        if re.match(r"^(?:الفصل|فصل|article|art\.)\s*\d", value) or re.search(r"\d+\s+الفصل\s*$",value):
            self.pending=None
            return False
        match = PREFIX.match(value)
        subdomain, _ = classify_heading(value)
        if match:
            level = LEVELS[match[1].lower()]
            for lower in ORDER[ORDER.index(level)+1:]: self.context[lower]=None
            self.context[level]=line.strip()
            self.pending=level
            return True
        if subdomain or (self.pending and value.startswith(("في ", "du ", "de la ", "des ")) and len(value)<100 and not re.search(r"[.؛:]",value)):
            # A numbered heading and its subject often occupy separate PDF lines.
            level=self.pending or "section"
            if self.pending:
                self.context[level]+="\n"+line.strip()
            else:
                self.context[level]=line.strip()
                for lower in ORDER[ORDER.index(level)+1:]: self.context[lower]=None
            self.pending=None
            return True
        self.pending=None
        return False

    def snapshot(self):
        result=self.context.copy()
        subject=next((s for s in reversed(list(result.values())) if s and classify_heading(s)[0]),None)
        subdomain,label=classify_heading(subject)
        result.update(legal_domain="contract_law" if subdomain else None,legal_subdomain=subdomain,
                      section_ar=subject if subject and re.search(r"[\u0600-\u06ff]",subject) else None,
                      section_fr=label,structure_version="hierarchy_v2")
        return result
