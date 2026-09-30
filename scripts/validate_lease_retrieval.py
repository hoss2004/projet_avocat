"""Read-only real-corpus regression; writes only local validation reports."""
from pathlib import Path
from dotenv import dotenv_values
import httpx,json
root=Path(__file__).resolve().parents[1]
s=dotenv_values(root/".env")
before=json.loads((root/"docs/lease-before.json").read_text(encoding="utf-8"))
results=[]
with httpx.Client(base_url="http://localhost:8000",headers={"X-API-Key":s["API_KEY"]},timeout=240) as client:
    for name,q in [("after",before["question"]),("short","paiement de loyers impayés et résiliation du bail"),("arabic","عدم دفع معين الكراء وفسخ عقد الكراء")]:
        response=client.post("/search/legal",json={"question":q,"top_k":10,"debug":True});response.raise_for_status()
        result=response.json()
        assert result["debug"]["query_understanding"]["subdomain"]=="lease"
        assert result["sources"] and all(x["metadata"]["legal_subdomain"]=="lease" for x in result["sources"][:3])
        assert len(result["sources"])<10
        (root/f"docs/lease-{name}.json").write_text(json.dumps({"question":q,**result},ensure_ascii=False,indent=2),encoding="utf-8")
        results.append({"test":name,"articles":[{"article":x["article_number"],"score":x["score"],"subdomain":x["metadata"]["legal_subdomain"]} for x in result["sources"]]})
    for source in before["sources"]:
        response=client.get("/legal-articles/"+source["article_id"]);response.raise_for_status()
        assert response.json()["original_text"]==source["original_text"]
print(json.dumps(results,ensure_ascii=False))
