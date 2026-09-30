"""Small deployed-stack smoke test; never prints credentials or source contents."""
import json
import os
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


question = " ".join(sys.argv[1:]) or (
    "article 20 du code de travail, analyse les risques juridiques et donne un avis juridique"
)
payload = json.dumps({
    "question": question,
    "mode": "LEGAL_OPINION",
    "language": "fr",
    "debug": True,
}).encode()
request = urllib.request.Request(
    "http://127.0.0.1:8000/rag/query",
    data=payload,
    headers={"X-API-Key": os.environ["API_KEY"], "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=240) as response:
    result = json.load(response)

summary = {
    "primary_intent": result.get("primary_intent"),
    "primary_goal": result.get("primary_goal"),
    "secondary_goals": result.get("secondary_goals"),
    "mode": result.get("mode"),
    "answer_kind": result.get("answer_kind"),
    "response_mode": result.get("response_mode"),
    "intent": result.get("intent"),
    "conversation_id_created": bool(result.get("conversation_id")),
    "draft_created": result.get("draft_created"),
    "draft_version": result.get("draft_version"),
    "explicit_references": result.get("legal_user_request", {}).get("explicit_references"),
    "issues": result.get("reasoning", {}).get("issues"),
    "sources": [
        {"title": source.get("title"), "article_number": source.get("article_number")}
        for source in result.get("retrieved_sources", [])
    ],
    "claim_validation": result.get("claim_validation"),
    "generation_error": result.get("debug", {}).get("generation_error"),
    "orchestration": {
        key: result.get("debug", {}).get(key)
        for key in (
            "llm_orchestrator_called", "tool_calls", "sources_selected_count",
            "sources_rejected_count", "reasoning_llm_called", "draft_created",
            "draft_id", "claim_validation_called", "final_response_type",
        )
    },
    "warnings": result.get("warnings"),
}
print(json.dumps(summary, ensure_ascii=False))
