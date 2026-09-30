from dataclasses import asdict, replace
from sqlalchemy import select

from app.models.knowledge import SearchChunk
from app.schemas.assistant import Source
from app.services.exact_reference.normalization import normalize_article_number, normalize_document_reference
from app.services.exact_reference.resolver import DocumentResolution, LegalDocumentResolver
from app.services.exact_reference.validation import SourceIdentityValidator


class ExactReferenceService:
    def __init__(self, resolver: LegalDocumentResolver | None = None, validator: SourceIdentityValidator | None = None):
        self.resolver = resolver or LegalDocumentResolver()
        self.validator = validator or SourceIdentityValidator()

    def execute(self, repository, query, request):
        if query.validation_status != "VALID" or not query.references:
            resolution = DocumentResolution(
                None,
                query.requested_document,
                [],
                query.validation_status,
                normalized_document_reference=normalize_document_reference(query.raw_document_reference),
            )
            debug = self._debug(query, resolution, lookup_strategy="validation_only")
            debug.update({
                "final_status": query.validation_status,
                "status_reason": "parser_rejected_reference_syntax",
                "parser_failure_reason": query.parser_failure_reason,
            })
            return self._result(query.validation_status, [], debug, query_type=query.query_type)
        if len(query.references) > 1:
            return self._execute_many(repository, query, request)
        return self._execute_single(repository, query, request)

    def _execute_single(self, repository, query, request):
        resolution = self.resolver.resolve(repository, query.requested_document, getattr(request, "document_id", None), query.original_question)
        if query.references:
            resolved_reference = replace(
                query.references[0],
                normalized_document_reference=resolution.normalized_document_reference or query.references[0].normalized_document_reference,
                resolved_document_id=resolution.document_id,
            )
            query = replace(query, references=(resolved_reference,))
        debug = self._debug(query, resolution, lookup_strategy="legal_articles_exact")
        if resolution.status == "AMBIGUOUS_REFERENCE":
            debug.update({"rows_found":0,"raw_rows_found":0,"canonical_articles_found":len(resolution.candidates),"final_status":"AMBIGUOUS_REFERENCE","status_reason":"ambiguous_document_reference"})
            return self._result("AMBIGUOUS_REFERENCE", [], debug, candidates=[self._document_candidate(d) for d in resolution.candidates])
        if resolution.status == "DOCUMENT_NOT_FOUND":
            debug.update({"final_status":"DOCUMENT_NOT_FOUND","status_reason":"requested_document_not_found","resolver_failure_reason":"document_reference_not_resolved"})
            return self._result("DOCUMENT_NOT_FOUND", [], debug)
        debug["lookup_count"] = 1
        rows = repository.exact_article_reference(
            query.normalized_reference,
            article_suffix=query.article_suffix,
            document_id=resolution.document_id,
            at_date=getattr(request, "at_date", None),
            legal_corpus=getattr(request, "legal_corpus", "all"),
            document_type=getattr(request, "document_type", None),
        )
        groups = self._canonical_groups(rows)
        chunks_found = sum(self._chunk_count(repository, group) for group in groups.values())
        duplicates_removed = max(0,len(rows)-sum(len(group["canonical_rows"]) for group in groups.values()))
        debug.update({
            "rows_found":len(rows),
            "raw_rows_found":len(rows),
            "canonical_articles_found":len(groups),
            "unique_legal_article_ids":sorted({str(row[0].id) for group in groups.values() for row in group["canonical_rows"]}),
            "duplicates_removed":duplicates_removed,
            "duplicate_rows_removed":duplicates_removed,
            "chunks_found":chunks_found,
            "chunk_groups_found":0,
            "versions_found":len({str(row[2].id) for row in rows}),
        })
        if not rows:
            debug.update({"final_status":"ARTICLE_NOT_FOUND","status_reason":"no_exact_article_row"})
            return self._result("ARTICLE_NOT_FOUND", [], debug)
        if len(groups)>1 and self._same_article_across_versions(groups):
            debug.update({"final_status":"AMBIGUOUS_VERSION","status_reason":"multiple_versions_after_canonical_dedup"})
            return self._result("AMBIGUOUS_VERSION", [], debug, candidates=[self._group_candidate(repository, group, "MULTIPLE_VERSION") for group in groups.values()])
        if len(groups) > 1:
            debug.update({"final_status":"AMBIGUOUS_REFERENCE","status_reason":"multiple_canonical_articles_after_dedup"})
            return self._result("AMBIGUOUS_REFERENCE", [], debug, candidates=[self._group_candidate(repository, group, "TRUE_AMBIGUITY") for group in groups.values()])
        group=next(iter(groups.values()))
        article, document, version = group["canonical_rows"][0]
        chunks = repository.session.scalars(
            select(SearchChunk).where(
                SearchChunk.tenant_id == repository.tenant_id,
                SearchChunk.source_type == "law",
                SearchChunk.article_id.in_([row[0].id for row in group["canonical_rows"]]),
            ).order_by(SearchChunk.page_start,SearchChunk.id)
        ).all()
        debug["chunk_groups_found"]=1 if chunks else 0
        debug["chunks_found"]=len(chunks)
        article_ids=[row[0].id for row in group["canonical_rows"]]
        identity = self.validator.validate(document=document, version=version, article=article, chunks=chunks, article_ids=article_ids)
        if not identity.ok:
            debug["identity_error"] = identity.reason
            debug.update({"final_status":"SOURCE_IDENTITY_MISMATCH","status_reason":identity.reason})
            return self._result("SOURCE_IDENTITY_MISMATCH", [], debug)
        source = self._source(repository, document, version, article, chunks, group)
        debug["returned_article_ids"] = [str(row[0].id) for row in group["canonical_rows"]]
        debug.update({"final_status":"FOUND","status_reason":"single_canonical_article_after_dedup"})
        return self._result("FOUND", [source], debug)

    def _execute_many(self, repository, query, request):
        results = []
        sources = []
        for reference in query.references:
            single_query = self._single_reference_query(query, reference)
            result = self._execute_single(repository, single_query, request)
            results.append((reference, result))
            sources.extend(result["sources"])

        statuses = [result["status"] for _reference, result in results]
        if statuses and all(status == "FOUND" for status in statuses):
            final_status = "FOUND"
        elif statuses and len(set(statuses)) == 1:
            final_status = statuses[0]
        else:
            final_status = "PARTIAL_EXACT_RESULTS"

        resolution = DocumentResolution(None, None, [], "MULTI_REFERENCE")
        debug = self._debug(query, resolution, lookup_strategy="legal_articles_exact_multi")
        debug.update({
            "lookup_count": sum(result["debug"].get("lookup_count", 0) for _reference, result in results),
            "canonical_articles_found": sum(result["debug"].get("canonical_articles_found", 0) for _reference, result in results),
            "final_status": final_status,
            "reference_results": [
                {
                    **self._reference_debug(reference),
                    "status": result["status"],
                    "resolved_document_id": result["debug"].get("resolved_document_id"),
                    "matched_alias_id": result["debug"].get("matched_alias_id"),
                    "source_count": len(result["sources"]),
                }
                for reference, result in results
            ],
        })
        candidates = [candidate for _reference, result in results for candidate in result.get("candidates", [])]
        return self._result(
            final_status,
            sources,
            debug,
            candidates=candidates or None,
            query_type="MULTI_EXACT_REFERENCE_QUERY",
        )

    def _single_reference_query(self, query, reference):
        return replace(
            query,
            query_type="EXACT_REFERENCE_QUERY",
            references=(reference,),
            deduplicated_reference_count=1,
            raw_document_reference=reference.raw_document_reference,
            raw_reference=reference.raw_article_number,
            article_number=reference.normalized_article_number,
            normalized_reference=reference.normalized_article_number,
            normalized_article_number=reference.normalized_article_number,
            article_suffix=reference.article_suffix,
            requested_document=reference.raw_document_reference,
            detected_references=(reference.normalized_article_number,),
        )

    def _debug(self, query, resolution, lookup_strategy):
        return {
            "raw_query": query.original_question,
            "query_type": query.query_type,
            "raw_document_reference": query.raw_document_reference,
            "normalized_document_reference": resolution.normalized_document_reference,
            "raw_reference": query.raw_reference,
            "normalized_reference": query.normalized_reference,
            "raw_article_reference": query.raw_reference,
            "normalized_article_reference": query.normalized_reference,
            "normalized_article_number": query.normalized_article_number,
            "requested_document": query.requested_document or resolution.requested_document,
            "resolved_document_id": str(resolution.document_id) if resolution.document_id else None,
            "matched_alias_id": str(resolution.matched_alias_id) if resolution.matched_alias_id else None,
            "parser_confidence": query.parser_confidence,
            "resolver_status": resolution.status,
            "lookup_strategy": lookup_strategy,
            "raw_parser_matches": query.raw_parser_matches,
            "deduplicated_reference_count": query.deduplicated_reference_count,
            "references": [self._reference_debug(reference, resolution) for reference in query.references],
            "lookup_count": 0,
            "parser_failure_reason": query.parser_failure_reason,
            "resolver_failure_reason": None,
            "rows_found": 0,
            "raw_rows_found": 0,
            "canonical_articles_found": 0,
            "unique_legal_article_ids": [],
            "duplicates_removed": 0,
            "duplicate_rows_removed": 0,
            "chunks_found": 0,
            "chunk_groups_found": 0,
            "versions_found": 0,
            "final_status": None,
            "status_reason": None,
            "returned_article_ids": [],
            "semantic_search_called": False,
            "hybrid_search_called": False,
            "reranker_called": False,
            "llm_called": False,
            "route": asdict(query),
        }

    def _result(self, status, sources, debug, candidates=None, query_type="EXACT_REFERENCE_QUERY"):
        result = {"query_type": query_type, "status": status, "sources": sources, "debug": debug}
        if candidates is not None:
            result["candidates"] = candidates
        return result

    def _reference_debug(self, reference, resolution=None):
        return {
            "raw_match": reference.raw_text,
            "span": [reference.start_offset, reference.end_offset],
            "raw_article_number": reference.raw_article_number,
            "normalized_article_number": reference.normalized_article_number,
            "article_suffix": reference.article_suffix,
            "raw_document_reference": reference.raw_document_reference,
            "normalized_document_reference": reference.normalized_document_reference,
            "resolved_document_id": str(resolution.document_id) if resolution and resolution.document_id else (str(reference.resolved_document_id) if reference.resolved_document_id else None),
        }

    def _source(self, repository, document, version, article, chunks, group):
        chunk_ids=[str(chunk.id) for chunk in chunks]
        metadata = {
            "document_title": document.title_ar or document.title_fr or document.filename,
            "document_type": document.document_type,
            "version": version.version,
            "version_id": str(version.id),
            "version_status": version.status,
            "version_selection_rule": "single_version_after_exact_resolution",
            "legal_article_id": str(article.id),
            "canonical_article_ids": [str(row[0].id) for row in group["canonical_rows"]],
            "chunk_id": chunk_ids[0] if chunk_ids else None,
            "chunk_ids": chunk_ids,
            "chunks": [{"chunk_id":str(chunk.id),"page_start":chunk.page_start,"page_end":chunk.page_end,"original_text":chunk.original_text} for chunk in chunks],
            "source_url": document.source_url,
            "page_number": article.page_start,
            "pages": sorted({page for row in group["canonical_rows"] for page in range(row[0].page_start,row[0].page_end+1)}),
            "book": article.book,
            "title_section": article.title_section,
            "chapter": article.chapter,
            "section": article.section,
            "subsection": article.subsection,
            "legal_domain": article.legal_domain,
            "legal_subdomain": article.legal_subdomain,
            "legal_text": article.legal_text,
        }
        return Source(
            id=chunks[0].id if chunks else article.id,
            source_type="law",
            document_id=document.id,
            article_id=article.id,
            case_id=None,
            title=metadata["document_title"],
            article_number=article.article_number,
            original_text=article.original_text,
            language=article.language,
            page_start=article.page_start,
            page_end=article.page_end,
            metadata=metadata,
            score=1.0,
            file_url=f"/legal-documents/{document.id}/file",
        )

    def _row_candidate(self, article, document, version):
        return {
            "document_id": str(document.id),
            "article_id": str(article.id),
            "article_number": article.article_number,
            "version": version.version,
            "page": article.page_start,
            "source": document.title_ar or document.title_fr or document.filename,
        }

    def _canonical_groups(self, rows):
        groups={}
        for row in rows:
            article, document, version = row
            normalized=normalize_article_number(article.article_number)
            key=(str(document.id), normalized.normalized_reference, normalized.suffix or "", str(version.id))
            group=groups.setdefault(key,{"key":key,"rows":[],"canonical_rows":[],"checksums":set()})
            group["rows"].append(row)
            dedup_key=article.checksum
            if dedup_key not in group["checksums"]:
                group["checksums"].add(dedup_key)
                group["canonical_rows"].append(row)
        return groups

    def _document_candidates_from_rows(self, rows):
        documents = {}
        for _article, document, _version in rows:
            documents[document.id] = document
        return [self._document_candidate(document) for _id, document in sorted(documents.items(), key=lambda item: str(item[0]))]

    def _document_candidates_from_groups(self, groups):
        documents = {}
        for group in groups.values():
            _article, document, _version = group["canonical_rows"][0]
            documents[document.id] = document
        return [self._document_candidate(document) for _id, document in sorted(documents.items(), key=lambda item: str(item[0]))]

    def _group_candidate(self, repository, group, diagnostic=None):
        article, document, version = group["canonical_rows"][0]
        return {
            "legal_article_id":str(article.id),
            "document_id":str(document.id),
            "article_number":article.article_number,
            "article_suffix":normalize_article_number(article.article_number).suffix,
            "version_id":str(version.id),
            "chunk_count":self._chunk_count(repository, group),
            "checksum":article.checksum,
            "diagnostic":diagnostic or self._group_diagnostic(group),
        }

    def _chunk_count(self, repository, group):
        article_ids = [row[0].id for row in group["canonical_rows"]]
        if not article_ids:
            return 0
        return len(
            repository.session.scalars(
                select(SearchChunk.id).where(
                    SearchChunk.tenant_id == repository.tenant_id,
                    SearchChunk.source_type == "law",
                    SearchChunk.article_id.in_(article_ids),
                )
            ).all()
        )

    def _same_article_across_versions(self, groups):
        signatures=set()
        version_ids=set()
        for group in groups.values():
            article, _document, version = group["canonical_rows"][0]
            normalized=normalize_article_number(article.article_number)
            signatures.add((str(version.series_id), normalized.normalized_reference, normalized.suffix or ""))
            version_ids.add(str(version.id))
        return len(signatures)==1 and len(version_ids)>1

    def _group_diagnostic(self, group):
        if len(group["rows"]) > len(group["canonical_rows"]):
            return "DUPLICATE_ROW"
        if len(group["canonical_rows"]) > 1:
            return "MULTI_CHUNK_ARTICLE"
        return "TRUE_AMBIGUITY"

    def _document_candidate(self, document):
        return {
            "document_id": str(document.id),
            "source": document.title_ar or document.title_fr or document.filename,
        }
