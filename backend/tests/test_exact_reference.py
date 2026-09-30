from datetime import datetime, timezone
from itertools import product
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import assistant as assistant_routes
from app.core.database import get_session
from app.main import app
from app.models.legal import Base, LegalArticle, LegalDocument, LegalVersion, LegalDocumentAlias
from app.models.knowledge import SearchChunk
from app.repositories.legal import LegalRepository
from app.schemas.assistant import SearchRequest
from app.services.exact_reference.normalization import (
    normalize_article_number,
    normalize_document_label,
    normalize_document_reference,
    normalize_legal_reference,
)
from app.services.exact_reference.parser import ExactReferenceParser
from app.services.exact_reference.router import QueryRouter
from app.services.exact_reference.service import ExactReferenceService
from test_ingestion_api import client


def add_reference(session, tenant, title, article_number="TEST_ARTICLE_X", text=None, chunk=True, checksum=None, series_id=None, version_number=1):
    document = LegalDocument(
        tenant_id=tenant,
        title_fr=title,
        title_ar=None,
        filename=f"{title}.pdf",
        storage_key=f"{tenant}/{title}.pdf",
        checksum=checksum or uuid4().hex,
        source_url=None,
        source_name=None,
        original_text=text or f"{title} {article_number} original text.",
        pages=[text or f"{title} {article_number} original text."],
        created_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()
    version = LegalVersion(
        tenant_id=tenant,
        document_id=document.id,
        series_id=series_id or uuid4(),
        version=version_number,
        publication_date=None,
        effective_from=None,
        effective_to=None,
        modification_date=None,
        repeal_date=None,
        status="unknown",
        previous_version_id=None,
    )
    session.add(version)
    session.flush()
    body = text or f"{title} {article_number} original text."
    article = LegalArticle(
        tenant_id=tenant,
        document_id=document.id,
        version_id=version.id,
        article_number=article_number,
        book=None,
        title_section=None,
        chapter=None,
        section=None,
        subsection=None,
        legal_domain="test_domain",
        legal_subdomain="test_subdomain",
        section_ar=None,
        section_fr=None,
        structure_version="test",
        legal_text={},
        original_text=body,
        text_ar=None,
        text_fr=body,
        translation_is_official=False,
        normalized_text_for_search=body.lower(),
        language="fr",
        page_start=1,
        page_end=1,
        start_offset=0,
        end_offset=len(body),
        chunk_index=0,
        checksum=uuid4().hex,
    )
    session.add(article)
    session.flush()
    if chunk:
        session.add(
            SearchChunk(
                tenant_id=tenant,
                source_key=f"law:{article.id}",
                source_type="law",
                document_id=document.id,
                article_id=article.id,
                case_id=None,
                title=title,
                article_number=article.article_number,
                original_text=article.original_text,
                normalized_text=article.normalized_text_for_search,
                search_text=article.normalized_text_for_search,
                language=article.language,
                page_start=article.page_start,
                page_end=article.page_end,
                metadata_json={},
                effective_from=None,
                effective_to=None,
                embedding=None,
                embedding_model=None,
            )
        )
    return document, version, article


def add_alias(session, tenant, document, alias, language="fr", alias_type="alias"):
    row = LegalDocumentAlias(
        tenant_id=tenant,
        document_id=document.id,
        alias=alias,
        normalized_alias=normalize_document_label(alias),
        language=language,
        alias_type=alias_type,
    )
    session.add(row)
    return row


def add_chunk(session, tenant, document, article, index, text, page):
    chunk = SearchChunk(
        tenant_id=tenant,
        source_key=f"law:{article.id}:{index}",
        source_type="law",
        document_id=document.id,
        article_id=article.id,
        case_id=None,
        title=document.title_fr,
        article_number=article.article_number,
        original_text=text,
        normalized_text=text.lower(),
        search_text=text.lower(),
        language=article.language,
        page_start=page,
        page_end=page,
        metadata_json={},
        effective_from=None,
        effective_to=None,
        embedding=None,
        embedding_model=None,
    )
    session.add(chunk)
    return chunk


def service_result(session, tenant, question):
    request = SearchRequest(question=question, debug=True)
    repository = LegalRepository(session, tenant)
    route = QueryRouter().route(request, repository=repository)
    assert route.query_type in {"EXACT_REFERENCE_QUERY", "MULTI_EXACT_REFERENCE_QUERY"}
    return ExactReferenceService().execute(repository, route.exact_reference, request)


def add_article_to_document(session, tenant, document, version, article_number, chunk_index):
    body = f"{document.title_fr} {article_number} original text."
    article = LegalArticle(
        tenant_id=tenant,
        document_id=document.id,
        version_id=version.id,
        article_number=article_number,
        book=None,
        title_section=None,
        chapter=None,
        section=None,
        subsection=None,
        legal_domain="test_domain",
        legal_subdomain="test_subdomain",
        section_ar=None,
        section_fr=None,
        structure_version="test",
        legal_text={},
        original_text=body,
        text_ar=None,
        text_fr=body,
        translation_is_official=False,
        normalized_text_for_search=body.lower(),
        language="fr",
        page_start=chunk_index + 1,
        page_end=chunk_index + 1,
        start_offset=0,
        end_offset=len(body),
        chunk_index=chunk_index,
        checksum=uuid4().hex,
    )
    session.add(article)
    session.flush()
    add_chunk(session, tenant, document, article, chunk_index, body, chunk_index + 1)
    return article


def test_parser_normalizes_exact_reference_without_specific_articles():
    parsed = ExactReferenceParser().parse("Donner article TEST_ARTICLE_X dans TEST_CODE_A")
    assert parsed.query_type == "EXACT_REFERENCE_QUERY"
    assert parsed.raw_reference == "TEST_ARTICLE_X"
    assert parsed.normalized_reference == "test_article_x"
    assert parsed.requested_document == "TEST_CODE_A"
    assert normalize_legal_reference("article ＴＥＳＴ＿ＡＲＴＩＣＬＥ＿Ｘ").normalized_reference == "test_article_x"
    assert QueryRouter().route(SearchRequest(question="Quelle est la règle applicable au TEST_CODE_A ?")).query_type == "CONCEPTUAL_LEGAL_SEARCH"


def test_exact_reference_found_absent_document_scope_and_ambiguity():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        doc_a, _, article_a = add_reference(session, tenant, "TEST_CODE_A")
        doc_b, _, _ = add_reference(session, tenant, "TEST_CODE_B")
        session.commit()
        found = service_result(session, tenant, "article TEST_ARTICLE_X dans TEST_CODE_A")
        assert found["status"] == "FOUND"
        assert len(found["sources"]) == 1
        assert found["sources"][0].document_id == doc_a.id
        assert found["sources"][0].article_id == article_a.id
        assert found["debug"]["semantic_search_called"] is False
        assert found["debug"]["hybrid_search_called"] is False
        assert found["debug"]["reranker_called"] is False
        assert found["debug"]["llm_called"] is False
        assert found["debug"]["returned_article_ids"] == [str(article_a.id)]
        assert found["debug"]["raw_article_reference"] == "TEST_ARTICLE_X"
        assert found["debug"]["normalized_article_reference"] == "test_article_x"
        assert service_result(session, tenant, "article TEST_ARTICLE_Y dans TEST_CODE_A")["status"] == "ARTICLE_NOT_FOUND"
        scoped = service_result(session, tenant, "article TEST_ARTICLE_X dans TEST_CODE_B")
        assert scoped["status"] == "FOUND"
        assert scoped["sources"][0].document_id == doc_b.id
        ambiguous = service_result(session, tenant, "article TEST_ARTICLE_X")
        assert ambiguous["status"] == "AMBIGUOUS_REFERENCE"
        assert ambiguous["sources"] == []
        diagnostic_fields = {
            "legal_article_id", "document_id", "article_number", "article_suffix",
            "version_id", "chunk_count", "checksum", "diagnostic",
        }
        assert all(set(candidate) == diagnostic_fields for candidate in ambiguous["candidates"])
        assert {candidate["document_id"] for candidate in ambiguous["candidates"]} == {str(doc_a.id), str(doc_b.id)}


def test_exact_reference_unicode_and_suffix_are_distinct():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        add_reference(session, tenant, "TEST_CODE_A", article_number="TEST_ARTICLE_X bis")
        session.commit()
        assert service_result(session, tenant, "article TEST_ARTICLE_X dans TEST_CODE_A")["status"] == "ARTICLE_NOT_FOUND"
        found = service_result(session, tenant, "article ＴＥＳＴ＿ＡＲＴＩＣＬＥ＿Ｘ bis dans TEST_CODE_A")
        assert found["status"] == "FOUND"
        assert found["sources"][0].article_number == "TEST_ARTICLE_X bis"


def test_source_identity_mismatch_blocks_display():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        _, _, article = add_reference(session, tenant, "TEST_CODE_A")
        chunk = session.query(SearchChunk).filter_by(article_id=article.id).one()
        chunk.article_number = "TEST_ARTICLE_Y"
        session.commit()
        assert service_result(session, tenant, "article TEST_ARTICLE_X dans TEST_CODE_A")["status"] == "SOURCE_IDENTITY_MISMATCH"


def test_exact_reference_numeric_unicode_is_same_reference():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        _document, _version, article = add_reference(session, tenant, "TEST_CODE_A", article_number="123")
        session.commit()
        found = service_result(session, tenant, "article \u06F1\u06F2\u06F3 dans TEST_CODE_A")
        assert found["status"] == "FOUND"
        assert len(found["sources"]) == 1
        assert found["sources"][0].article_id == article.id
        assert found["debug"]["normalized_article_reference"] == "123"


def test_exact_reference_multi_chunk_article_is_one_source():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(
            session,
            tenant,
            "TEST_CODE_A",
            text="TEST_ARTICLE_X full stored original text.",
            chunk=False,
        )
        add_chunk(session, tenant, document, article, 0, "chunk 0", 1)
        add_chunk(session, tenant, document, article, 1, "chunk 1", 2)
        add_chunk(session, tenant, document, article, 2, "chunk 2", 3)
        session.commit()
        found = service_result(session, tenant, "article TEST_ARTICLE_X dans TEST_CODE_A")
        assert found["status"] == "FOUND"
        assert len(found["sources"]) == 1
        source = found["sources"][0]
        assert source.article_id == article.id
        assert source.original_text == article.original_text
        assert len(source.metadata["chunk_ids"]) == 3
        assert [chunk["original_text"] for chunk in source.metadata["chunks"]] == ["chunk 0", "chunk 1", "chunk 2"]
        assert found["debug"]["raw_rows_found"] == 1
        assert found["debug"]["canonical_articles_found"] == 1
        assert found["debug"]["chunks_found"] == 3
        assert found["debug"]["chunk_groups_found"] == 1
        assert found["debug"]["final_status"] == "FOUND"


def test_exact_reference_duplicate_article_rows_are_deduped():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, version, article = add_reference(session, tenant, "TEST_CODE_A", checksum="TEST_CHECKSUM")
        duplicate = LegalArticle(
            tenant_id=tenant,
            document_id=document.id,
            version_id=version.id,
            article_number=article.article_number,
            book=None,
            title_section=None,
            chapter=None,
            section=None,
            subsection=None,
            legal_domain="test_domain",
            legal_subdomain="test_subdomain",
            section_ar=None,
            section_fr=None,
            structure_version="test",
            legal_text={},
            original_text=article.original_text,
            text_ar=None,
            text_fr=article.original_text,
            translation_is_official=False,
            normalized_text_for_search=article.normalized_text_for_search,
            language="fr",
            page_start=1,
            page_end=1,
            start_offset=0,
            end_offset=len(article.original_text),
            chunk_index=1,
            checksum=article.checksum,
        )
        session.add(duplicate)
        session.flush()
        add_chunk(session, tenant, document, duplicate, 1, duplicate.original_text, 1)
        session.commit()
        found = service_result(session, tenant, "article TEST_ARTICLE_X dans TEST_CODE_A")
        assert found["status"] == "FOUND"
        assert len(found["sources"]) == 1
        assert found["debug"]["raw_rows_found"] == 2
        assert found["debug"]["canonical_articles_found"] == 1
        assert found["debug"]["duplicate_rows_removed"] == 1
        assert found["debug"]["duplicates_removed"] == 1
        assert found["debug"]["final_status"] == "FOUND"


@pytest.mark.parametrize(
    "question",
    [
        "article TEST_ARTICLE_X TEST_CODE_A",
        "الفصل TEST_ARTICLE_X TEST_CODE_A",
        "faits inutiles puis donnees stockees de article TEST_ARTICLE_X concernant TEST_CODE_A",
        "TEST_CODE_A : donne le texte de l'article TEST_ARTICLE_X avec le contexte disponible",
    ],
)
def test_exact_reference_formulations_resolve_same_canonical_document(question):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(session, tenant, "TEST_CODE_A")
        session.commit()
        result = service_result(session, tenant, question)
        assert result["status"] == "FOUND"
        assert result["sources"][0].document_id == document.id
        assert result["sources"][0].article_id == article.id
        assert result["debug"]["resolved_document_id"] == str(document.id)
        assert result["debug"]["normalized_article_reference"] == "test_article_x"
        assert result["debug"]["raw_query"] == question
        assert result["debug"]["final_status"] == "FOUND"
        assert result["debug"]["semantic_search_called"] is False


def test_exact_reference_resolves_configurable_document_alias():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(session, tenant, "TEST_CODE_CANONICAL")
        alias = add_alias(session, tenant, document, "TEST_CODE_ALIAS")
        session.commit()
        found = service_result(session, tenant, "article TEST_ARTICLE_X dans TEST_CODE_ALIAS")
        assert found["status"] == "FOUND"
        assert found["sources"][0].document_id == document.id
        assert found["sources"][0].article_id == article.id
        assert found["debug"]["normalized_document_reference"] == "test code alias"
        assert found["debug"]["matched_alias_id"] == str(alias.id)
        assert found["debug"]["parser_confidence"] == 1.0
        assert found["debug"]["resolver_status"] == "FOUND"


@pytest.mark.parametrize(
    "question",
    [
        "article 123 dans TEST_CODE_CANONICAL",
        "الفصل ١٢٣ من TEST_ALIAS_AR",
        "TEST_ALIAS_AR، اعرض الفصل ۱۲۳ الآن",
        "contexte avant TEST_CODE_CANONICAL : article n° 123, contexte après",
        "123 article dans TEST_CODE_CANONICAL",
        "article: １２３ — TEST_ALIAS_SHORT",
        "texte avant، الفصل 123 TEST_ALIAS_AR، texte après",
    ],
)
def test_exact_reference_property_formulations_keep_same_identity(question):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(
            session,
            tenant,
            "TEST_CODE_CANONICAL",
            article_number="123",
        )
        add_alias(session, tenant, document, "TEST_ALIAS_AR", language="ar", alias_type="translated_title")
        add_alias(session, tenant, document, "TEST_ALIAS_SHORT", language="fr", alias_type="short_title")
        session.commit()
        result = service_result(session, tenant, question)
        assert result["status"] == "FOUND"
        assert result["sources"][0].document_id == document.id
        assert result["sources"][0].article_id == article.id
        assert result["debug"]["normalized_article_number"] == "123"
        assert result["debug"]["resolved_document_id"] == str(document.id)
        assert result["debug"]["parser_confidence"] == 1.0
        assert result["debug"]["resolver_status"] == "FOUND"
        assert result["debug"]["final_status"] == "FOUND"


@pytest.mark.parametrize(
    ("question", "expected_status"),
    [
        ("article 20 21", "INVALID_REFERENCE_QUERY"),
    ],
)
def test_exact_reference_rejects_multiple_or_malformed_references(question, expected_status):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        result = service_result(session, tenant, question)
        assert result["status"] == expected_status
        assert result["sources"] == []
        assert result["debug"]["parser_confidence"] == 0.0
        assert result["debug"]["resolver_status"] == expected_status
        assert result["debug"]["canonical_articles_found"] == 0
        assert result["debug"]["final_status"] == expected_status


def test_overlapping_parser_matches_are_one_exact_reference():
    parsed = ExactReferenceParser().parse("Donner le texte de l'article (123).")
    assert parsed.query_type == "EXACT_REFERENCE_QUERY"
    assert parsed.raw_parser_matches > 1
    assert parsed.deduplicated_reference_count == 1
    assert len(parsed.references) == 1
    assert parsed.references[0].normalized_article_number == "123"


def test_multi_exact_two_articles_same_document_execute_two_lookups():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, version, first = add_reference(session, tenant, "TEST_CODE_MULTI", article_number="10")
        second = add_article_to_document(session, tenant, document, version, "20", 1)
        session.commit()
        result = service_result(session, tenant, "article 10 et article 20 dans TEST_CODE_MULTI")
        assert result["query_type"] == "MULTI_EXACT_REFERENCE_QUERY"
        assert result["status"] == "FOUND"
        assert result["debug"]["deduplicated_reference_count"] == 2
        assert result["debug"]["lookup_count"] == 2
        assert {source.article_id for source in result["sources"]} == {first.id, second.id}


def test_multi_exact_three_articles_same_document_execute_three_lookups():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, version, first = add_reference(session, tenant, "TEST_CODE_MULTI", article_number="10")
        second = add_article_to_document(session, tenant, document, version, "20", 1)
        third = add_article_to_document(session, tenant, document, version, "30", 2)
        session.commit()
        result = service_result(session, tenant, "الفصل ١٠، الفصل (۲۰)، الفصل [30] من TEST_CODE_MULTI")
        assert result["status"] == "FOUND"
        assert result["debug"]["lookup_count"] == 3
        assert {source.article_id for source in result["sources"]} == {first.id, second.id, third.id}


def test_multi_exact_preserves_two_article_document_pairs():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document_a, _version_a, article_a = add_reference(session, tenant, "TEST_CODE_A", article_number="10")
        document_b, _version_b, article_b = add_reference(session, tenant, "TEST_CODE_B", article_number="20")
        add_alias(session, tenant, document_a, "TEST_ALIAS_A")
        add_alias(session, tenant, document_b, "TEST_ALIAS_B")
        session.commit()
        result = service_result(session, tenant, "article 10 TEST_ALIAS_A ; article 20 TEST_ALIAS_B")
        assert result["status"] == "FOUND"
        assert result["debug"]["lookup_count"] == 2
        assert {(source.document_id, source.article_id) for source in result["sources"]} == {
            (document_a.id, article_a.id),
            (document_b.id, article_b.id),
        }


def test_multi_exact_reports_partial_result_without_early_return():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(session, tenant, "TEST_CODE_MULTI", article_number="10")
        session.commit()
        result = service_result(session, tenant, "article 10 et article 99 dans TEST_CODE_MULTI")
        assert result["status"] == "PARTIAL_EXACT_RESULTS"
        assert result["debug"]["lookup_count"] == 2
        assert len(result["sources"]) == 1
        assert result["sources"][0].article_id == article.id
        assert [item["status"] for item in result["debug"]["reference_results"]] == ["FOUND", "ARTICLE_NOT_FOUND"]


def test_equivalent_reference_generator_keeps_canonical_identity():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(session, tenant, "TEST_CODE_EQ", article_number="123")
        add_alias(session, tenant, document, "TEST_ALIAS_EQ")
        session.commit()
        markers = ("article", "الفصل")
        numbers = ("123", "١٢٣")
        decorations = ("{}", "({})", "[{}]")
        positions = ("before", "after")
        separators = (" ", ", ", "، ", " - ", "; ", "\n")
        instructions = ("recherche", "ابحث", "recherche ثم")
        for marker, number, decoration, position, separator, instruction in product(
            markers, numbers, decorations, positions, separators, instructions
        ):
            numbered = decoration.format(number)
            if position == "before":
                question = f"{instruction} TEST_ALIAS_EQ{separator}{marker} {numbered}"
            else:
                question = f"{instruction} {marker} {numbered}{separator}TEST_ALIAS_EQ"
            result = service_result(session, tenant, question)
            assert result["status"] == "FOUND", question
            assert result["debug"]["normalized_article_number"] == "123", question
            assert result["debug"]["resolved_document_id"] == str(document.id), question
            assert result["sources"][0].article_id == article.id, question


def test_document_alias_number_shorthand_is_exact_but_isolated_number_is_not():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(session, tenant, "TEST_CODE_SHORT", article_number="123")
        add_alias(session, tenant, document, "TEST_ALIAS_SHORT")
        session.commit()
        result = service_result(session, tenant, "TEST_ALIAS_SHORT ١٢٣")
        assert result["status"] == "FOUND"
        assert result["debug"]["normalized_article_number"] == "123"
        assert result["sources"][0].article_id == article.id
        isolated = QueryRouter().route(SearchRequest(question="TEST_CONTEXT ١٢٣"), repository=LegalRepository(session, tenant))
        assert isolated.query_type == "CONCEPTUAL_LEGAL_SEARCH"


def test_contract_dates_and_payment_delays_are_not_article_references():
    question = (
        "Le contrat du 15 janvier 2024 prévoit un paiement à 30 jours. "
        "BETA a répondu en février 2026. Je veux un avis juridique, pas une liste d'articles."
    )
    route = QueryRouter().route(SearchRequest(question=question))
    assert route.query_type == "CONCEPTUAL_LEGAL_SEARCH"
    assert route.exact_reference is None


def test_unresolved_explicit_document_is_document_not_found():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        result = service_result(session, tenant, "article 123 dans TEST_UNKNOWN_DOCUMENT")
        assert result["status"] == "DOCUMENT_NOT_FOUND"
        assert result["sources"] == []
        assert result["debug"]["lookup_count"] == 0
        assert result["debug"]["resolver_failure_reason"] == "document_reference_not_resolved"


def test_article_marker_without_number_is_terminal_invalid_reference():
    route = QueryRouter().route(SearchRequest(question="Afficher article () dans TEST_CODE_A"))
    assert route.query_type == "EXACT_REFERENCE_QUERY"
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        result = ExactReferenceService().execute(LegalRepository(session, tenant), route.exact_reference, SearchRequest(question="Afficher article () dans TEST_CODE_A", debug=True))
        assert result["status"] == "INVALID_REFERENCE_QUERY"
        assert result["debug"]["parser_failure_reason"] == "article_marker_without_valid_number"
        assert result["debug"]["semantic_search_called"] is False


def test_multi_exact_reports_ambiguous_reference_mapping():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document_a, _version_a, _article_a = add_reference(session, tenant, "TEST_CODE_A", article_number="10")
        document_b, _version_b, _article_b = add_reference(session, tenant, "TEST_CODE_B", article_number="10")
        add_alias(session, tenant, document_a, "TEST_ALIAS_A")
        add_alias(session, tenant, document_b, "TEST_ALIAS_B")
        session.commit()
        result = service_result(session, tenant, "TEST_ALIAS_A,article 10,TEST_ALIAS_B")
        assert result["status"] == "AMBIGUOUS_REFERENCE_MAPPING"
        assert result["sources"] == []
        assert result["debug"]["lookup_count"] == 0
        assert result["debug"]["parser_failure_reason"] == "article_document_association_is_ambiguous"


@pytest.mark.parametrize(
    "question",
    [
        "class Article 20 { pass }",
        "SELECT article 20 FROM TEST_TABLE",
        "`article 20` est un nom de variable",
        "article_number = 20",
    ],
)
def test_query_router_does_not_treat_technical_text_as_exact_reference(question):
    route = QueryRouter().route(SearchRequest(question=question))
    assert route.query_type == "CONCEPTUAL_LEGAL_SEARCH"


def test_exact_reference_global_lookup_finds_single_document():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        document, _version, article = add_reference(session, tenant, "TEST_CODE_ONLY")
        session.commit()
        result = service_result(session, tenant, "article TEST_ARTICLE_X")
        assert result["status"] == "FOUND"
        assert result["sources"][0].document_id == document.id
        assert result["sources"][0].article_id == article.id


def test_requested_normalization_functions_are_canonical():
    assert normalize_article_number("Article : １２３").normalized_reference == "123"
    assert normalize_article_number("الفصل ١٢٣").normalized_reference == "123"
    assert normalize_document_reference("  TEST—CODE / A  ") == "test code a"


def test_exact_reference_distinguishes_multiple_versions():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    series_id = uuid4()
    with Session(engine) as session:
        add_reference(session, tenant, "TEST_CODE_VERSION_1", series_id=series_id, version_number=1)
        add_reference(session, tenant, "TEST_CODE_VERSION_2", series_id=series_id, version_number=2)
        session.commit()
        result = service_result(session, tenant, "article TEST_ARTICLE_X")
        assert result["status"] == "AMBIGUOUS_VERSION"
        assert result["sources"] == []
        assert result["debug"]["canonical_articles_found"] == 2
        assert result["debug"]["versions_found"] == 2
        assert result["debug"]["final_status"] == "AMBIGUOUS_VERSION"
        assert len(result["candidates"]) == 2
        assert {candidate["diagnostic"] for candidate in result["candidates"]} == {"MULTIPLE_VERSION"}


def test_exact_reference_missing_without_document_is_not_found_after_global_lookup():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant = uuid4()
    with Session(engine) as session:
        add_reference(session, tenant, "TEST_CODE_A")
        session.commit()
        missing = service_result(session, tenant, "article TEST_ARTICLE_Y")
        assert missing["status"] == "ARTICLE_NOT_FOUND"
        assert missing["sources"] == []
        assert missing["debug"]["raw_rows_found"] == 0
        assert missing["debug"]["canonical_articles_found"] == 0
        assert missing["debug"]["final_status"] == "ARTICLE_NOT_FOUND"


def test_exact_reference_route_stops_retrieval_reranker_and_llm(client, monkeypatch):
    api, settings = client
    sessions = app.dependency_overrides[get_session]()
    session = next(sessions)
    try:
        add_reference(session, settings.tenant_id, "TEST_CODE_A")
        session.commit()
    finally:
        sessions.close()

    def forbidden(*args, **kwargs):
        raise AssertionError("classic pipeline must not be called")

    monkeypatch.setattr(assistant_routes, "retrieve", forbidden)
    monkeypatch.setattr(assistant_routes, "query_rag", forbidden)
    monkeypatch.setattr(assistant_routes, "sync_legal_index", forbidden)
    search = api.post("/search/legal", json={"question": "article TEST_ARTICLE_X dans TEST_CODE_A", "debug": True})
    assert search.status_code == 200, search.text
    assert search.json()["status"] == "FOUND"
    assert len(search.json()["sources"]) == 1
    assert search.json()["debug"]["semantic_search_called"] is False
    assert search.json()["debug"]["hybrid_search_called"] is False
    assert search.json()["debug"]["reranker_called"] is False
    assert search.json()["debug"]["llm_called"] is False
    rag = api.post("/rag/query", json={"question": "article TEST_ARTICLE_X dans TEST_CODE_A", "debug": True})
    assert rag.status_code == 200, rag.text
    assert rag.json()["mode"] == "exact_reference"
    assert rag.json()["status"] == "FOUND"
    assert rag.json()["claims"] == []
    assert len(rag.json()["retrieved_sources"]) == 1
    assert rag.json()["debug"]["semantic_search_called"] is False
    assert rag.json()["debug"]["hybrid_search_called"] is False
    assert rag.json()["debug"]["reranker_called"] is False
    assert rag.json()["debug"]["llm_called"] is False


def test_conceptual_query_still_uses_classic_search(client, monkeypatch):
    api, settings = client
    called = {"retrieve": False}

    def fake_retrieve(*args, **kwargs):
        called["retrieve"] = True
        return [], ["classic search used"]

    monkeypatch.setattr(assistant_routes, "retrieve", fake_retrieve)
    response = api.post("/search/legal", json={"question": "Quelle est la règle applicable au TEST_CODE_A ?", "debug": True})
    assert response.status_code == 200, response.text
    assert response.json()["query_type"] == "CONCEPTUAL_LEGAL_SEARCH"
    assert called["retrieve"] is True


def test_exact_reference_not_found_is_terminal_with_zero_sources(client, monkeypatch):
    api, settings = client
    sessions = app.dependency_overrides[get_session]()
    session = next(sessions)
    try:
        add_reference(session, settings.tenant_id, "TEST_CODE_A")
        session.commit()
    finally:
        sessions.close()

    def forbidden(*args, **kwargs):
        raise AssertionError("classic pipeline must not be called")

    monkeypatch.setattr(assistant_routes, "retrieve", forbidden)
    monkeypatch.setattr(assistant_routes, "query_rag", forbidden)
    search = api.post("/search/legal", json={"question": "article TEST_ARTICLE_Y dans TEST_CODE_A", "debug": True})
    assert search.status_code == 200, search.text
    assert search.json()["status"] == "ARTICLE_NOT_FOUND"
    assert search.json()["sources"] == []
    assert search.json()["debug"]["semantic_search_called"] is False
    assert search.json()["debug"]["hybrid_search_called"] is False
    assert search.json()["debug"]["reranker_called"] is False
    assert search.json()["debug"]["llm_called"] is False
    rag = api.post("/rag/query", json={"question": "article TEST_ARTICLE_Y dans TEST_CODE_A", "debug": True})
    assert rag.status_code == 200, rag.text
    assert rag.json()["status"] == "ARTICLE_NOT_FOUND"
    assert rag.json()["retrieved_sources"] == []
    assert rag.json()["debug"]["semantic_search_called"] is False
    assert rag.json()["debug"]["hybrid_search_called"] is False
    assert rag.json()["debug"]["reranker_called"] is False
    assert rag.json()["debug"]["llm_called"] is False


def test_exact_reference_stored_data_mode_has_no_extra_sources(client):
    api, settings = client
    sessions = app.dependency_overrides[get_session]()
    session = next(sessions)
    try:
        document, _version, article = add_reference(session, settings.tenant_id, "TEST_CODE_A")
        session.commit()
        document_id = str(document.id)
        article_id = str(article.id)
        original_text = article.original_text
    finally:
        sessions.close()
    result = api.post(
        "/search/legal",
        json={"question": "donnees stockees article TEST_ARTICLE_X dans TEST_CODE_A", "debug": True},
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["status"] == "FOUND"
    assert len(payload["sources"]) == 1
    source = payload["sources"][0]
    assert source["document_id"] == document_id
    assert source["article_id"] == article_id
    assert source["article_number"] == "TEST_ARTICLE_X"
    assert source["original_text"] == original_text
    assert source["metadata"]["document_title"] == "TEST_CODE_A"
    assert source["metadata"]["page_number"] == source["page_start"]
    assert "chunk_id" in source["metadata"]
    assert "source_url" in source["metadata"]
