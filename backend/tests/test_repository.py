from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models.legal import Base, LegalDocument
from app.repositories.legal import LegalRepository

def test_tenant_isolation():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    with Session(engine) as session:
        doc = LegalDocument(tenant_id=owner, filename="TEST LAW.pdf", storage_key="x", checksum="a"*64)
        session.add(doc)
        session.commit()
        assert LegalRepository(session, owner).get(LegalDocument, doc.id)
        assert LegalRepository(session, other).get(LegalDocument, doc.id) is None
        assert LegalRepository(session, other).documents() == []
        assert LegalRepository(session, other).document_by_checksum(doc.checksum) is None
