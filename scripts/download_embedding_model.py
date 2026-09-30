"""Download a local model with concise progress; sends no user document."""
import json
import httpx
last=-1
with httpx.stream("POST","http://127.0.0.1:11434/api/pull",json={"model":"bge-m3","stream":True},timeout=600) as response:
    response.raise_for_status()
    for line in response.iter_lines():
        if not line: continue
        event=json.loads(line)
        if "error" in event: raise RuntimeError(event["error"])
        total=event.get("total",0)
        if total:
            progress=int(event.get("completed",0)/total*100)//10*10
            if progress!=last:
                print(f"bge-m3: {progress}%",flush=True);last=progress
        elif event.get("status")=="success":
            print("bge-m3: ready",flush=True)
