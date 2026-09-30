"""Deployed two-turn smoke test. It never prints credentials or generated legal content."""
import json
import os
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def ask(payload):
    request = urllib.request.Request(
        "http://127.0.0.1:8000/rag/query",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"X-API-Key":os.environ["API_KEY"], "Content-Type":"application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


first = ask({
    "question":"Rédige un avis juridique prudent sur le contenu de l'article 20 du code du travail.",
    "mode":"LEGAL_OPINION", "language":"fr", "debug":True,
})
second = ask({
    "question":"bonjour", "mode":"LEGAL_OPINION", "language":"fr", "debug":True,
    "conversation_id":first["conversation_id"],
})

assert first.get("draft_created") is True
assert second.get("intent") == "GENERAL_CHAT"
assert second.get("response_mode") == "CHAT"
assert second.get("draft_modified") is False
assert second.get("debug", {}).get("retrieval_required") is False
assert second.get("debug", {}).get("legal_issue_analyzer_called") is False
assert second.get("debug", {}).get("draft_loaded_for_generation") is False
assert not second.get("retrieved_sources")
assert not second.get("legal_issues")

print(json.dumps({
    "turn_1":{
        "intent":first.get("intent"), "response_mode":first.get("response_mode"),
        "draft_created":first.get("draft_created"), "draft_version":first.get("draft_version"),
    },
    "turn_2":{
        "intent":second.get("intent"), "response_mode":second.get("response_mode"),
        "draft_modified":second.get("draft_modified"),
        "retrieval_required":second.get("debug", {}).get("retrieval_required"),
        "legal_issue_analyzer_called":second.get("debug", {}).get("legal_issue_analyzer_called"),
        "draft_loaded_for_generation":second.get("debug", {}).get("draft_loaded_for_generation"),
        "answer_length":len(second.get("answer", "")),
    },
}, ensure_ascii=False))
