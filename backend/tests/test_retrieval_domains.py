from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.models.legal import Base
from app.models.knowledge import SearchChunk, LegalTerm
from app.core.config import Settings
from app.schemas.assistant import SearchRequest
from app.services.retrieval.search import retrieve
from app.services.retrieval.understanding import LegalQueryAnalyzer, DOMAIN_TERMS
from app.utils.arabic import normalize_for_search

FR="Un tiers affirme être propriétaire d'un bien saisi et souhaite contester la saisie."
AR="ادعى الغير ملكية المعقول"


def chunk(tenant,title,text):
    return SearchChunk(id=uuid4(),tenant_id=tenant,source_key=str(uuid4()),source_type="law",document_id=uuid4(),title=title,original_text=text,search_text=normalize_for_search(title+" "+text),language="ar" if "ال" in text else "fr",page_start=1,page_end=1,metadata_json={})


@pytest.mark.parametrize("question,body",[(FR,"TEST ARTICLE: Un tiers revendique la propriété du bien saisi."),(AR,"TEST ARTICLE: ادعى الغير ملكية المعقول"),(FR,"TEST ARTICLE: ادعى الغير ملكية المعقول")])
def test_legal_issue_beats_tax_keyword_spam(question,body):
    engine=create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant=uuid4()
    with Session(engine) as session:
        a=chunk(tenant,"TEST LOCAL TAX LAW",("saisie العقلة "*200)+"régularisation fiscale")
        b=chunk(tenant,"TEST CIVIL PROCEDURE LAW",body)
        session.add_all([a,b]);session.commit()
        original=b.original_text
        trace={}
        result,_=retrieve(session,tenant,SearchRequest(question=question,top_k=10),Settings(_env_file=None,api_key="test-only-key-with-at-least-32-characters",tenant_id=tenant,embedding_provider="disabled",llm_provider="disabled"),trace)
        assert result[0].id==b.id
        assert len(result)==1  # no irrelevant padding
        scores={c["source_id"]:c for c in trace["candidate_documents"]}
        assert scores[str(b.id)]["final_score"]-scores[str(a.id)]["final_score"]>4
        assert not scores[str(a.id)]["selected"]
        assert b.original_text==original


@pytest.mark.parametrize("domain,terms",DOMAIN_TERMS.items())
def test_all_required_domains(domain,terms):
    assert LegalQueryAnalyzer().analyze(terms[0]).domain==domain


def test_custom_legal_term_is_tenant_scoped():
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    owner,other=uuid4(),uuid4()
    with Session(engine) as session:
        session.add(LegalTerm(tenant_id=owner,key="custom",domain="arbitration",terms_fr=["TESTSPECIAL"],terms_ar=["تحكيم خاص"]))
        session.commit()
        assert "تحكيم خاص" in LegalQueryAnalyzer().analyze("TESTSPECIAL",session,owner).concepts_ar
        assert "تحكيم خاص" not in LegalQueryAnalyzer().analyze("TESTSPECIAL",session,other).concepts_ar


def test_seeded_lexicon_rows_do_not_hide_new_builtin_variants():
    engine=create_engine("sqlite://");Base.metadata.create_all(engine);tenant=uuid4()
    with Session(engine) as session:
        session.add(LegalTerm(
            tenant_id=tenant,key="contract_performance",domain="contract_law",
            terms_fr=["ancienne variante"],terms_ar=["صياغة قديمة"],
        ))
        session.commit()
        analysis=LegalQueryAnalyzer().analyze("force obligatoire du contrat",session,tenant)
        assert "ما انعقد على الوجه الصحيح" in analysis.concepts_ar
        assert "صياغة قديمة" in analysis.concepts_ar


def test_contract_breach_routes_to_coc_contract_domain():
    analysis = LegalQueryAnalyzer().analyze(
        "Contrat de prestations, mise en demeure, inexécution et résiliation pour manquement grave"
    )
    assert analysis.domain == "contract_law"
    assert "coc" in analysis.preferred_codes
    assert "mise en demeure" in analysis.concepts_fr


def test_classifier_does_not_exclude_other_domains_and_exact_wins():
    engine=create_engine("sqlite://");Base.metadata.create_all(engine);tenant=uuid4()
    with Session(engine) as session:
        b=chunk(tenant,"TEST CIVIL PROCEDURE LAW","TEST ARTICLE Un tiers revendique la propriété du bien saisi.")
        a=chunk(tenant,"TEST LOCAL TAX LAW","TEST ARTICLE saisie administrative.");a.article_number="10"
        session.add_all([a,b]);session.commit()
        trace={}
        result,_=retrieve(session,tenant,SearchRequest(question=FR+" article 10"),Settings(_env_file=None,api_key="test-only-key-with-at-least-32-characters",tenant_id=tenant,embedding_provider="disabled",llm_provider="disabled"),trace)
        assert result[0].id==a.id
        assert any(c["source_id"]==str(b.id) for c in trace["candidate_documents"])
