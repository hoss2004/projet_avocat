"""Search vocabulary only: these associations are not legal authorities."""
import re
from app.utils.arabic import normalize_for_search

TERMS = [
    ("saisie conservatoire", "العقلة التحفظية"),
    ("saisie exécutoire", "العقلة التنفيذية"),
    ("saisie", "عقلة", "العقلة", "حجز"),
    ("propriété", "الملكية", "ملكية", "استحقاق"),
    ("tiers", "الغير", "غير"),
    ("bien saisi", "المعقول", "الأموال المعقولة"),
    ("contester", "contestation", "اعتراض", "الاعتراض"),
    ("incident d'exécution", "الإشكال التنفيذي"),
    ("contrat", "العقد", "عقد"),
    ("travail", "الشغل", "licenciement", "الطرد"),
    ("prescription", "التقادم", "مرور الزمن"),
    ("appel", "الاستئناف"), ("preuve", "الإثبات", "حجة"),
    ("nullité", "البطلان"), ("compétence", "الاختصاص"),
    ("exceptions", "الاستثناءات"), ("délais", "الآجال"),
]
STOP = set("le la les de des du un une en et est ce que qui au aux par pour sur dans il elle nous vous quels quelles sont comment peut article الفصل من في على الى و ما هل هذا هذه".split())

def expand_query(question: str) -> list[str]:
    normalized = normalize_for_search(question)
    variants = [normalized]
    for group in TERMS:
        if any(re.search(r"(?<!\w)" + re.escape(normalize_for_search(term)) + r"(?!\w)", normalized) for term in group):
            variants.extend(normalize_for_search(term) for term in group)
    return list(dict.fromkeys(variants))

def query_tokens(question: str) -> list[str]:
    return list(dict.fromkeys(word for variant in expand_query(question) for word in re.findall(r"\w+", variant) if len(word) > 1 and word not in STOP))[:50]
