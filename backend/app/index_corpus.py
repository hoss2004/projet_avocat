"""Resume local corpus indexing without the HTTP service: python -m app.index_corpus."""
import json
import time
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_engine
from app.services.retrieval.indexing import sync_legal_index, embed_pending


def main():
    settings = get_settings()
    with Session(get_engine()) as session:
        session.info["tenant_id"] = settings.tenant_id
        created = sync_legal_index(session, settings.tenant_id)
        session.commit()
        print(json.dumps({"new_chunks": created}), flush=True)
        while True:
            start = time.monotonic()
            result = embed_pending(session, settings.tenant_id, settings, limit=16)
            print(json.dumps({**result, "seconds": round(time.monotonic()-start, 2)}), flush=True)
            if not result.get("remaining"):
                break


if __name__ == "__main__":
    main()
