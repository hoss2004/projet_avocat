from datetime import date
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models.knowledge import CaseDocument, CaseEvent, SearchChunk
from app.parsers.pdf_parser import extract_pdf, OCRRequired, ExtractedPDF, PDFError
from app.utils.arabic import normalize_for_search, DIGITS
from app.services.language.detection import detect_language

def paragraph_chunks(pages, maximum=2400):
    """Split at paragraph/line boundaries and retain exact page slices."""
    for page_number, page in enumerate(pages,1):
        for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", page):
            start, end = match.span()
            while start < end:
                stop = min(start+maximum,end)
                if stop < end:
                    boundary = max(page.rfind("\n",start,stop), page.rfind(". ",start,stop))
                    if boundary > start + maximum//2:
                        stop = boundary+1
                yield page_number, start, stop, page[start:stop]
                start = stop

def import_case_pdf(session, tenant_id, case_id, data, filename, settings):
    checksum = sha256(data).hexdigest()
    duplicate = session.scalar(select(CaseDocument).where(CaseDocument.tenant_id==tenant_id,CaseDocument.case_id==case_id,CaseDocument.checksum==checksum))
    if duplicate:
        return duplicate
    suffix=Path(filename).suffix.lower()
    if data.startswith(b"%PDF-"):
        suffix=".pdf"
        try: extracted=extract_pdf(data,settings.max_pdf_pages)
        except OCRRequired as exc:
            import pymupdf
            with pymupdf.open(stream=data,filetype="pdf") as pdf:
                extracted=ExtractedPDF([""]*len(pdf),[str(exc)])
    elif suffix in {".txt",".eml"}:
        try:
            if suffix==".eml":
                from email import policy
                from email.parser import BytesParser
                message=BytesParser(policy=policy.default).parsebytes(data)
                body=message.get_body(preferencelist=("plain",))
                if body is None: raise PDFError("Email sans corps texte : fournir un PDF ou un export texte")
                content="\n".join(str(message.get(k,"")) for k in ["From","To","Date","Subject"])+"\n"+body.get_content()
            else: content=data.decode("utf-8-sig")
            if len(content)>12_000_000: raise PDFError("Texte trop volumineux")
            extracted=ExtractedPDF([content],[])
        except (UnicodeError,LookupError,ValueError) as exc:
            raise PDFError("Texte ou email illisible; utiliser UTF-8 ou un PDF") from exc
    else: raise PDFError("Formats acceptés : PDF, TXT UTF-8, EML avec corps texte")
    document_id = uuid4()
    key = f"{tenant_id}/cases/{case_id}/{document_id}{suffix}"
    destination = settings.storage_path / key
    destination.parent.mkdir(parents=True,exist_ok=True)
    document = CaseDocument(id=document_id,tenant_id=tenant_id,case_id=case_id,filename=Path(filename.replace("\\","/")).name[:255],storage_key=key,checksum=checksum,language=detect_language(extracted.text),pages=extracted.pages,warnings=extracted.warnings)
    try:
        destination.write_bytes(data)
        session.add(document)
        session.flush()
        for index,(page,start,end,original) in enumerate(paragraph_chunks(extracted.pages)):
            session.add(SearchChunk(tenant_id=tenant_id,source_key=f"case:{document_id}:{index}",source_type="case_document",document_id=document_id,case_id=case_id,article_id=None,title=document.filename,article_number=None,original_text=original,normalized_text=normalize_for_search(original),search_text=normalize_for_search(original),language=detect_language(original),page_start=page,page_end=page,metadata_json={"document_type":"piece","review_required":True,"start_offset":start,"end_offset":end}))
        for page_number,page in enumerate(extracted.pages,1):
            # Numeric dates only: mentions are not established facts or computed deadlines.
            for match in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",page.translate(DIGITS)):
                try:
                    value=date(int(match[3]),int(match[2]),int(match[1]))
                except ValueError:
                    continue
                session.add(CaseEvent(tenant_id=tenant_id,case_id=case_id,event_date=value,description=page[max(0,match.start()-100):match.end()+160],source_document_id=document_id,source_page=page_number,verified=False))
        session.commit()
    except Exception:
        session.rollback()
        destination.unlink(missing_ok=True)
        duplicate = session.scalar(select(CaseDocument).where(CaseDocument.tenant_id==tenant_id,CaseDocument.case_id==case_id,CaseDocument.checksum==checksum))
        if duplicate:
            return duplicate
        raise
    return document
