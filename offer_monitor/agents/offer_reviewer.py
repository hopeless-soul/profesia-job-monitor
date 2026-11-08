# Offer reviewer subagent: reads one job offer and judges it against the candidate backstory.

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field, StringConstraints

from . import AGENT
from .tools import fetch_offer

PROMPTS = Path(__file__).parent / "prompts"

# Offers and backstory may be in SK/CZ/DE, but the report is always read in English
LANGUAGE_RULE = (
    "# Output language\n\n"
    "Write the verdict `content` in English, whatever the language of the offer "
    "or the backstory. Keep company names, job titles and quoted requirements as written."
)

# Structured answer of the reviewer and the orchestrator (same fields as models.Verdict).
# Pydantic, not a dict schema: LangChain only validates typed schemas
class Verdict(BaseModel):
    """Verdict on one job offer."""

    tag: Literal["GO", "STRETCH", "SKIP", "UNREACHABLE"] = Field(
        description="GO, STRETCH or SKIP per the rating rules; UNREACHABLE when the offer page could not be read."
    )
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        description="Plain-text review report (no markdown), at most 3000 characters."
    )
    url: str = Field(description="URL of the reviewed offer.")


# Tool-call output; an invalid answer is sent back to the model with the error so it can fix it
VERDICT_FORMAT = ToolStrategy(Verdict)


# System prompt: duties and steps, research method, output language, then the backstory
def system_prompt(backstory: str) -> str:
    duties = (PROMPTS / "offer_reviewer.md").read_text(encoding="utf-8")
    research = (PROMPTS / "offer_research.md").read_text(encoding="utf-8")
    return f"{duties}\n\n{research}\n\n{LANGUAGE_RULE}\n\n# Candidate backstory\n\n{backstory}"


# Build the reviewer agent
def build_offer_reviewer(llm: BaseChatModel, backstory: str) -> CompiledStateGraph:
    return create_agent(
        llm,
        tools=[fetch_offer],
        system_prompt=system_prompt(backstory),
        response_format=VERDICT_FORMAT,
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
        # run the subagent with the URL as its only message; its log lines are tagged with its name
        token = AGENT.set("offer_reviewer")
        try:
            result = reviewer.invoke({"messages": [{"role": "user", "content": url}]})
        finally:
            AGENT.reset(token)
        return result["structured_response"].model_dump_json()

    return review_offer
