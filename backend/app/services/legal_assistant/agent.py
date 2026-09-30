from __future__ import annotations

from app.services.legal_assistant.orchestrator import LegalConversationOrchestrator


class LegalConversationAgent:
    """Single conversational entrypoint that decides which internal tools a turn needs."""

    def __init__(self, provider=None, max_tool_calls: int = 8):
        self.provider = provider
        self.max_tool_calls = max_tool_calls
        self._orchestrator = LegalConversationOrchestrator()

    def decide(self, request, repository, seed, prompt_context):
        return self._orchestrator.understand(
            request,
            repository,
            self.provider,
            seed,
            prompt_context,
            self.max_tool_calls,
        )

    @staticmethod
    def response_contract(
        result: dict,
        *,
        case_updates=None,
        draft_updates=None,
        sources_used=None,
        tool_calls=None,
        follow_up_needed: bool = False,
    ) -> dict:
        """Add the stable agent contract while retaining legacy API fields."""
        result["user_facing_response"] = result.get("answer", "")
        result["case_updates"] = list(case_updates or [])
        result["draft_updates"] = list(draft_updates or [])
        result["sources_used"] = list(sources_used or [])
        result["tool_calls"] = list(tool_calls or [])
        result["follow_up_needed"] = bool(follow_up_needed)
        return result
