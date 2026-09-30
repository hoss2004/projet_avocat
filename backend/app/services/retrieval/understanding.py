"""Deterministic legal routing vocabulary, not an opinion on applicable law."""
from dataclasses import dataclass, field, asdict
import re
import unicodedata
from sqlalchemy import select
from app.models.knowledge import LegalTerm
from app.utils.arabic import normalize_for_search


def norm(value):
    return "".join(c for c in unicodedata.normalize("NFKD", normalize_for_search(value).replace("_", " ")) if not unicodedata.combining(c))


def matches(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(norm(phrase)) + r"(?!\w)", norm(text)))

# Stable source identities derived from titles, never from isolated words in an article.
CODES = {
 "cpcc": ("civil_procedure", "مجلة المرافعات المدنية والتجارية", ["procedure civile", "المرافعات المدنية", "civil procedure", "cpcc"]),
 "coc": ("contract_law", "مجلة الالتزامات والعقود", ["obligations et des contrats", "الالتزامات والعقود", "االلتزامات والعقود", "اللتزامات والعقود", "civil law"]),
 "commerce": ("commercial_law", "المجلة التجارية", ["code de commerce", "المجلة التجارية", "commercial law"]),
 "companies": ("company_law", "مجلة الشركات التجارية", ["societes commerciales", "الشركات التجارية", "company law"]),
 "labor": ("labor_law", "مجلة الشغل", ["code de travail", "code du travail", "مجلة الشغل", "labor law"]),
 "criminal_procedure": ("criminal_procedure", "مجلة الإجراءات الجزائية", ["procedure penale", "الاجراءات الجزائية", "criminal procedure"]),
 "criminal": ("criminal_law", "المجلة الجزائية", ["code penal", "المجلة الجزائية", "criminal law"]),
 "family": ("family_law", "مجلة الأحوال الشخصية", ["statut personnel", "الاحوال الشخصية", "family law"]),
 "property": ("real_estate", "مجلة الحقوق العينية", ["droits reels", "الحقوق العينية", "real estate"]),
 "local_tax": ("local_tax", "مجلة الجباية المحلية", ["fiscalite locale", "الجباية المحلية", "local tax"]),
 "tax": ("tax_law", "مجلة الحقوق والإجراءات الجبائية", ["fiscaux", "fiscal", "tva", "latva", "finances", "enregistrement", "الجبائية", "tax law"]),
 "administrative": ("administrative_law", "النصوص الإدارية", ["administratif", "administrative law", "المحكمة الادارية"]),
 "arbitration": ("arbitration", "مجلة التحكيم", ["arbitrage", "التحكيم", "arbitration"]),
 "international": ("international_private_law", "مجلة القانون الدولي الخاص", ["international prive", "الدولي الخاص", "international private law"]),
}
DOMAIN_TERMS = {
 "contract_law": [
     "contrat", "contractuel", "obligation", "inexecution", "manquement", "mise en demeure",
     "resiliation", "resolution", "dommages interets", "retard de paiement", "facture impayee",
     "عقد", "العقد", "الالتزام", "عدم التنفيذ", "اعذار", "الفسخ", "التعويض",
 ],
 "civil_procedure": ["saisie", "saisi", "execution", "huissier", "العقلة", "المعقول", "عدل منفذ"],
 "commercial_law": ["commercant", "fonds de commerce", "effet de commerce", "تاجر", "اصل تجاري"],
 "company_law": ["associe", "societe", "gerant", "actionnaire", "شركة", "الشركة", "شريك"],
 "labor_law": ["licenciement", "salarie", "salaire", "travail", "اجير", "طرد", "الشغل"],
 "criminal_law": [
     "vol", "escroquerie", "infraction", "peine", "blanchiment", "corruption",
     "reglement penal", "règlement pénal", "flux financiers", "origine des fonds",
     "dissimulation des fonds", "comptes personnels", "سرقة", "جريمة", "عقوبة",
     "غسل الاموال", "غسل الأموال", "الصلح الجزائي", "فساد", "مصدر الاموال",
 ],
 "criminal_procedure": ["garde a vue", "detention provisoire", "juge d instruction", "احتفاظ", "ايقاف تحفظي"],
 "family_law": ["divorce", "pension alimentaire", "mariage", "garde des enfants", "طلاق", "نفقة", "حضانة"],
 "real_estate": ["immatriculation fonciere", "titre foncier", "immeuble", "copropriete", "رسم عقاري", "عقار"],
 "tax_law": ["impot", "fiscal", "fiscale", "tva", "taxe", "redressement fiscal", "جباية", "ضريبة", "اداء"],
 "administrative_law": ["decision administrative", "exces de pouvoir", "marche public", "قرار اداري", "تجاوز السلطة"],
 "arbitration": ["arbitrage", "arbitre", "sentence arbitrale", "تحكيم", "حكم تحكيمي"],
 "international_private_law": ["conflit de lois", "exequatur", "jugement etranger", "تنازع القوانين", "حكم اجنبي"],
}
DEFAULT_TERMS = [
 ("money_laundering", "criminal_law", ["blanchiment", "blanchiment d'argent", "origine des fonds", "dissimulation de l'origine des fonds"], ["غسل الأموال", "غسل الاموال", "تبييض الأموال"]),
 ("penal_settlement", "criminal_law", ["règlement pénal", "reglement penal", "conciliation pénale", "transaction pénale"], ["الصلح الجزائي", "صلح جزائي"]),
 ("financial_flows", "criminal_law", ["mouvements financiers", "flux financiers", "virements", "comptes personnels", "contrepartie économique"], ["تحويلات مالية", "تدفقات مالية", "حسابات شخصية", "مقابل اقتصادي"]),
 ("corruption", "criminal_law", ["corruption", "avantage indu"], ["فساد", "رشوة", "منفعة غير مشروعة"]),
 ("corporate_assets_misuse", "company_law", ["abus de biens sociaux", "utilisation des biens sociaux", "intérêt social"], ["استغلال أموال الشركة", "مصلحة الشركة"]),
 ("rent", "contract_law", ["loyer", "loyers", "loyers impayés", "loyer impayé"], ["معين الكراء", "عدم دفع معين الكراء", "بدل الكراء"]),
 ("lease", "contract_law", ["bail", "location", "contrat de location"], ["الكراء", "كراء", "عقد الكراء"]),
 ("tenant", "contract_law", ["locataire"], ["المكتري", "مكتري"]),
 ("landlord", "contract_law", ["bailleur"], ["المسوغ", "المكري"]),
 ("termination", "contract_law", ["résiliation", "résilier"], ["الفسخ", "فسخ", "فسخ العقد", "فسخ الكراء"]),
 ("contract_performance", "contract_law", ["exécution du contrat", "obligations contractuelles", "prestations convenues", "force obligatoire", "bonne foi contractuelle"], ["تنفيذ العقد", "الالتزامات التعاقدية", "القوة الملزمة للعقد", "الوفاء بالعقد", "ما انعقد على الوجه الصحيح", "يقوم مقام القانون", "تمام الأمانة"]),
 ("contract_breach", "contract_law", ["inexécution contractuelle", "manquement grave", "retard d'exécution", "défaut d'exécution"], ["عدم تنفيذ العقد", "عدم الوفاء بالعقد", "لم يوف", "الإخلال الجسيم", "التأخير في التنفيذ"]),
 ("debtor_delay", "contract_law", ["retard du débiteur", "échéance de l'obligation", "exécution forcée"], ["حل الأجل", "تأخر المدين عن الوفاء", "إجبار المدين على الوفاء"]),
 ("formal_notice", "contract_law", ["mise en demeure", "mise en demeure restée sans effet"], ["الإعذار", "إنذار", "إنذار بالدفع"]),
 ("reciprocal_obligations", "contract_law", ["obligations réciproques", "exception d'inexécution", "empêchement du créancier"], ["الالتزامات المتقابلة", "الدفع بعدم التنفيذ", "منع الدائن للتنفيذ", "إذا كان الالتزام من الطرفين", "يمتنع من إتمام", "قد وفى من جهته", "فعل الدائن", "سبب ينسب إليه", "عدم تنفيذ التزامه"]),
 ("contract_damages", "contract_law", ["responsabilité contractuelle", "dommage contractuel", "lien de causalité", "réparation du préjudice"], ["المسؤولية العقدية", "الضرر", "العلاقة السببية", "جبر الضرر"]),
 ("unpaid_invoice", "contract_law", ["facture impayée", "factures impayées", "retard de paiement"], ["فاتورة غير مدفوعة", "فواتير غير مدفوعة", "التأخير في الدفع"]),
 ("seizure", "civil_procedure", ["saisie", "saisi", "bien saisi"], ["العقلة", "المعقول", "عقلة"]),
 ("third_party", "civil_procedure", ["tiers", "tierce personne"], ["الغير", "غير"]),
 ("ownership", "civil_law", ["propriete", "proprietaire", "revendication de propriete"], ["ملكية", "الملكية", "استحقاق", "الستحقاق"]),
 ("third_party_claim", "civil_procedure", ["revendication par un tiers", "propriete du bien saisi"], ["ملكية المعقول", "ادعاء الغير ملكية المعقول", "ادعى الغير ملكية المعقول"]),
 ("protective_seizure", "civil_procedure", ["saisie conservatoire"], ["العقلة التحفظية"]),
]


def seed_terms(session, tenant):
    keys=set(session.scalars(select(LegalTerm.key).where(LegalTerm.tenant_id==tenant)))
    for key,domain,fr,ar in DEFAULT_TERMS:
        if key not in keys:
            session.add(LegalTerm(tenant_id=tenant,key=key,domain=domain,terms_fr=fr,terms_ar=ar,term_fr=fr[0],term_ar=ar[0],subdomain="lease" if key in {"rent","lease","tenant","landlord"} else None))
    session.flush()


def source_identity(title, metadata=None):
    metadata=metadata or {}
    metadata={**metadata,"domain":metadata.get("legal_domain") or metadata.get("domain","unknown")} if metadata.get("legal_domain") or metadata.get("domain") else metadata
    if metadata.get("legal_text",{}).get("kind") in {"instrument","unidentified"}:
        return metadata.get("domain","unknown"),None
    code=metadata.get("code_id")
    if code in CODES:
        return metadata.get("domain",CODES[code][0]),code
    for key,(domain,_,aliases) in CODES.items():
        if any(norm(a) in norm(title) for a in aliases):
            return metadata.get("domain",domain),key
    return metadata.get("domain","unknown"),None


@dataclass
class LegalQuery:
    original_question: str
    jurisdiction: str = "TN"
    domain: str = "unknown"
    subdomain: str | None = None
    concepts_fr: list = field(default_factory=list)
    concepts_ar: list = field(default_factory=list)
    query_fr: str = ""
    query_ar: str = ""
    preferred_document_types: list = field(default_factory=lambda:["code","jurisprudence"])
    preferred_sections: list = field(default_factory=list)
    preferred_codes: list = field(default_factory=list)
    preferred_sources: list = field(default_factory=list)
    excluded_or_low_priority_domains: list = field(default_factory=list)
    legal_issues: list = field(default_factory=list)
    concept_groups: list = field(default_factory=list)
    exact_article: str | None = None
    explicit_code: str | None = None
    confidence: str = "low"
    classifier: str = "deterministic_lexicon_v2"
    def to_dict(self): return asdict(self)


class LegalQueryAnalyzer:
    def analyze(self, question, session=None, tenant_id=None):
        subdomains={k:"lease" for k in ["rent","lease","tenant","landlord"]}
        subdomains.update({
            "money_laundering":"money_laundering",
            "penal_settlement":"economic_crime_settlement",
            "financial_flows":"financial_crime",
            "corruption":"corruption",
            "corporate_assets_misuse":"corporate_governance",
        })
        terms={k:(d,fr,ar) for k,d,fr,ar in DEFAULT_TERMS}
        if session is not None:
            for row in session.scalars(select(LegalTerm).where(LegalTerm.tenant_id==tenant_id)):
                if row.subdomain: subdomains[row.key]=row.subdomain
                built_in=terms.get(row.key, (row.domain, [], []))
                terms[row.key]=(
                    row.domain,
                    list(dict.fromkeys(built_in[1]+([row.term_fr] if row.term_fr else [])+row.terms_fr)),
                    list(dict.fromkeys(built_in[2]+([row.term_ar] if row.term_ar else [])+row.terms_ar)),
                )
        active={k:v for k,v in terms.items() if any(matches(question,t) for t in v[1]+v[2])}
        scores={d:sum(matches(question,t) for t in phrases) for d,phrases in DOMAIN_TERMS.items()}
        for domain,_,_ in active.values():
            if domain in scores: scores[domain]+=1
        result=LegalQuery(original_question=question)
        if max(scores.values(),default=0):
            result.domain=max(scores,key=scores.get)
            result.confidence="medium"
        core={"seizure","third_party","ownership"}
        if core.issubset(active) or "third_party_claim" in active:
            result.domain="civil_procedure"
            result.subdomain="enforcement_seizure"
            result.legal_issues=["third_party_ownership_of_seized_property"]
            result.confidence="high"
            active.update({k:terms[k] for k in core|{"third_party_claim"}})
            result.concept_groups=[terms[k][1]+terms[k][2] for k in ("seizure","third_party","ownership")]
            result.excluded_or_low_priority_domains=["tax_law","local_tax","tax","environment"]
        else:
            result.concept_groups=[fr+ar for _,fr,ar in active.values()]
        if {"rent","lease","tenant","landlord"}.intersection(active) and result.subdomain!="enforcement_seizure":
            result.domain="contract_law"
            result.subdomain="lease"
            result.confidence="high"
            result.preferred_sections=["في الكراء"]
            result.legal_issues=["payment_of_rent"]
            if any(matches(question,t) for t in ["impayé","impayés","عدم دفع","non paiement"]): result.legal_issues.append("non_payment_of_rent")
            if "termination" in active: result.legal_issues.append("termination_of_lease")
            active.update({k:terms[k] for k in ["rent","lease","tenant","landlord"]})
            result.concept_groups=[sum((terms[k][1]+terms[k][2] for k in ["rent","lease","tenant","landlord"]),[])]
            if "termination" in active: result.concept_groups.append(terms["termination"][1]+terms["termination"][2])
            result.excluded_or_low_priority_domains=["tax_law","local_tax"]
        if result.subdomain is None:
            detected=[subdomains[k] for k in active if k in subdomains]
            if detected and len(set(detected))==1:
                result.subdomain=detected[0]
                result.domain=next(active[k][0] for k in active if subdomains.get(k)==result.subdomain)
        result.concepts_fr=list(dict.fromkeys(t for _,fr,_ in active.values() for t in fr))
        result.concepts_ar=list(dict.fromkeys(t for _,_,ar in active.values() for t in ar))
        if not result.concept_groups and result.domain!="unknown":
            result.concept_groups=[[t] for t in DOMAIN_TERMS.get(result.domain,[]) if matches(question,t)]
        normalized=normalize_for_search(question)
        ref=re.search(r"(?:article|الفصل|فصل)\s*(\d+(?:\s*(?:bis|ter|مكرر))?)",normalized)
        result.exact_article=ref[1] if ref else normalized if normalized.isdecimal() else None
        for key,(domain,_,aliases) in CODES.items():
            if any(norm(a) in norm(question) for a in aliases):
                result.explicit_code=key
                result.domain="tax_law" if domain=="local_tax" else domain
                break
        result.preferred_codes=[k for k,(d,_,_) in CODES.items() if d==result.domain]
        result.preferred_sources=[CODES[k][1] for k in result.preferred_codes]
        result.query_fr=" ".join(result.concepts_fr) or question
        result.query_ar=" ".join(result.concepts_ar) or " ".join(t for t in DOMAIN_TERMS.get(result.domain,[]) if re.search("[\u0600-\u06ff]",t))
        return result
