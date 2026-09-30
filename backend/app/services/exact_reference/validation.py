from dataclasses import dataclass


@dataclass(frozen=True)
class SourceIdentityReport:
    ok: bool
    reason: str | None = None


class SourceIdentityValidator:
    def validate(self, *, document, version, article, chunk=None, chunks=None, article_ids=None) -> SourceIdentityReport:
        if article.tenant_id != document.tenant_id or version.tenant_id != document.tenant_id:
            return SourceIdentityReport(False, "tenant_mismatch")
        if article.document_id != document.id or version.document_id != document.id:
            return SourceIdentityReport(False, "document_mismatch")
        if article.version_id != version.id:
            return SourceIdentityReport(False, "version_mismatch")
        if chunks is not None:
            allowed_article_ids = set(article_ids or [article.id])
            for current in chunks:
                if current.document_id != document.id:
                    return SourceIdentityReport(False, "chunk_document_mismatch")
                if current.article_id not in allowed_article_ids:
                    return SourceIdentityReport(False, "chunk_article_mismatch")
                if current.article_number != article.article_number:
                    return SourceIdentityReport(False, "article_number_mismatch")
            return SourceIdentityReport(True)
        if chunk is None:
            return SourceIdentityReport(True)
        if chunk.document_id != document.id:
            return SourceIdentityReport(False, "chunk_document_mismatch")
        if chunk.article_id != article.id:
            return SourceIdentityReport(False, "chunk_article_mismatch")
        if chunk.article_number != article.article_number:
            return SourceIdentityReport(False, "article_number_mismatch")
        if chunk.original_text != article.original_text:
            return SourceIdentityReport(False, "article_text_mismatch")
        if chunk.page_start != article.page_start or chunk.page_end != article.page_end:
            return SourceIdentityReport(False, "page_mismatch")
        return SourceIdentityReport(True)
