from pathlib import Path
from dotenv import dotenv_values
import json,time,httpx
root=Path(__file__).resolve().parents[1];s=dotenv_values(root/".env")
info=json.loads((root/".tmp/case-test-id.json").read_text());ident=info["case_id"]
with httpx.Client(base_url="http://localhost:8000",headers={"X-API-Key":s["API_KEY"]},timeout=240) as c:
    case=c.get(f"/cases/{ident}").json();assert case["reference"]==info["reference"] and case["reference"].startswith("TEST-E2E-")
    def wait(previous=None):
        until=time.monotonic()+600
        while time.monotonic()<until:
            r=c.get(f"/cases/{ident}/analysis");r.raise_for_status();job=r.json()
            if job["status"]=="failed": raise AssertionError(job["error"])
            if job["id"]!=previous and job["status"] in {"completed","partial"} and not job["stale"]: return job
            time.sleep(2)
        raise TimeoutError("Analysis did not finish")
    c.post(f"/cases/{ident}/analyze").raise_for_status();baseline=wait()
    added=c.post(f"/cases/{ident}/documents",files={"file":("TEST complement.txt","TEST CASE SYNTHETIC. Une nouvelle pièce mentionne un paiement allégué; aucun justificatif bancaire n'est fourni.".encode(),"text/plain")});added.raise_for_status()
    final=wait(baseline["id"])
    assert final["report"]["changes"]["reused_documents"]==2,final["report"]["changes"]
    assert len(final["report"]["changes"]["new_documents"])==1
    assert final["report"]["coverage"]["documents_total"]==3
    assert any(s["items"] for s in final["report"]["sections"] if s["key"]=="missing_documents")
    (root/"docs/case-incremental-check.json").write_text(json.dumps({"case_id":ident,"status":final["status"],"changes":final["report"]["changes"],"coverage":final["report"]["coverage"],"new_document_auto_queued":True},indent=2),encoding="utf-8")
    print("Incremental analysis verified: 2 extractions reused, 1 new document processed, missing proof identified.")
