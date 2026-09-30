"""Index the configured cabinet in resumable batches using local embeddings."""
from pathlib import Path
from dotenv import dotenv_values
import httpx,json,time
root=Path(__file__).resolve().parents[1]
settings=dotenv_values(root/".env")
with httpx.Client(base_url="http://127.0.0.1:8000",headers={"X-API-Key":settings["API_KEY"]},timeout=240) as client:
    for batch in range(2000):
        start=time.monotonic()
        response=client.post("/search/index",params={"batch_size":16})
        response.raise_for_status()
        state=response.json()
        state["batch_seconds"]=round(time.monotonic()-start,2)
        print(json.dumps(state),flush=True)
        if not state.get("remaining"):
            break
    (root/"docs"/"index-status.json").write_text(json.dumps(client.get("/assistant/status").json(),indent=2),encoding="utf-8")
