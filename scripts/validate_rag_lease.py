"""Validate the real RAG endpoint on the lease/non-payment scenario."""
from pathlib import Path
import json
import re

import httpx
from dotenv import dotenv_values


root = Path(__file__).resolve().parents[1]
settings = dotenv_values(root / ".env")
question = "paiement de loyers impayés et résiliation du bail"

payload = {
    "question": question,
    "top_k": 8,
    "debug": True,
    "mode": "LEGAL_RESEARCH",
    "language": "fr",
    "scope": "LEGAL_ONLY",
}

with httpx.Client(
    base_url="http://localhost:8000",
    headers={"X-API-Key": settings["API_KEY"]},
    timeout=360,
) as client:
    response = client.post("/rag/query", json=payload)
    response.raise_for_status()
    result = response.json()

sources = result["retrieved_sources"]
assert sources, "RAG should keep reliable lease sources"
assert result["debug"]["query_understanding"]["subdomain"] == "lease"
assert all(source["metadata"].get("legal_subdomain") == "lease" for source in sources[:3])

if result["mode"] == "generated":
    claims = result["claims"]
    assert claims, "Generated mode must include validated claims"
    for claim in claims:
        for citation in claim["citations"]:
            quote = citation["quote"].strip()
            alpha_count = len(re.sub(r"[\W\d_]", "", quote, flags=re.UNICODE))
            assert alpha_count >= 20, f"Weak quote was accepted: {quote!r}"
            assert not re.fullmatch(r"(?i)(article|الفصل)?\s*\d+[\w\-]*", quote)
else:
    assert "conclusion juridique automatique" in result["answer"]

(root / "docs/lease-rag-validation.json").write_text(
    json.dumps({"question": question, **result}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(
    json.dumps(
        {
            "mode": result["mode"],
            "sources": [
                {
                    "article": source["article_number"],
                    "subdomain": source["metadata"].get("legal_subdomain"),
                    "score": source["score"],
                }
                for source in sources
            ],
            "warnings": result["warnings"],
        },
        ensure_ascii=False,
    )
)
