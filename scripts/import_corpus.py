"""Import manually supplied PDFs; never infer official provenance or effective dates."""
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

root=Path(__file__).resolve().parents[1]
settings=dotenv_values(root/".env")
report=[]
with httpx.Client(base_url="http://127.0.0.1:8000",headers={"X-API-Key":settings["API_KEY"]},timeout=180) as client:
    for path in sorted(root.glob("*.pdf")):
        with path.open("rb") as pdf:
            response=client.post("/legal-documents/upload",files={"file":(path.name,pdf,"application/pdf")},data={"metadata":json.dumps({"title_fr":path.stem.replace("_"," "),"official":False,"status":"unknown"})})
        response.raise_for_status()
        document_id=response.json()["id"]
        response=client.post("/legal-documents/ingest",json={"document_id":document_id})
        response.raise_for_status()
        item={"filename":path.name,"id":document_id,"status":response.json()["ingestion_status"]}
        report.append(item)
        print(json.dumps(item,ensure_ascii=True),flush=True)
(root/"docs"/"import-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
