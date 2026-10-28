# Offer reviewer subagent: reads one job offer and judges it against the candidate backstory.

from __future__ import annotations

import json
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph

from .tools import fetch_offer

PROMPTS = Path(__file__).parent / "prompts"

# Structured answer of the reviewer and the orchestrator (same fields as models.Verdict)
VERDICT_SCHEMA = {
    "title": "Verdict",
    "description": "Verdict on one job offer.",
    "type": "object",
    "properties": {
        "tag": {
            "type": "string",
            "enum": ["GO", "STRETCH", "SKIP", "UNREACHABLE"],
            "description": "GO, STRETCH or SKIP per the rating rules; UNREACHABLE when the offer page could not be read.",
        },
        "content": {
            "type": "string",
            "description": "Plain-text review report (no markdown), at most 3000 characters.",
        },
        "url": {"type": "string", "description": "URL of the reviewed offer."},
    },
    "required": ["tag", "content", "url"],
}


# System prompt: duties and steps, research method, then the backstory
def system_prompt(backstory: str) -> str:
    duties = (PROMPTS / "offer_reviewer.md").read_text(encoding="utf-8")
    research = (PROMPTS / "offer_research.md").read_text(encoding="utf-8")
    return f"{duties}\n\n{research}\n\n# Candidate backstory\n\n{backstory}"


# Build the reviewer agent
def build_offer_reviewer(llm: BaseChatModel, backstory: str) -> CompiledStateGraph:
    return create_agent(
        llm,
        tools=[fetch_offer],
        system_prompt=system_prompt(backstory),
        response_format=VERDICT_SCHEMA,
        name="offer_reviewer",
    )


# Expose the reviewer to the orchestrator as a tool
def as_tool(reviewer: CompiledStateGraph) -> BaseTool:
    @tool(
        "offer_reviewer",
        description="Review one job offer URL against the candidate's backstory. "
        "Returns the verdict as JSON with tag, content and url.",
    )
    def review_offer(url: str) -> str:
        # run the subagent with the URL as its only message
        result = reviewer.invoke({"messages": [{"role": "user", "content": url}]})
        return json.dumps(result["structured_response"], ensure_ascii=False)

    return review_offer
