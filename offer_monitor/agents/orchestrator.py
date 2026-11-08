# Orchestrator: main agent of the verdict pipeline. Hands each offer to its subagents.

from __future__ import annotations

from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from ..models import Verdict
from .offer_reviewer import VERDICT_FORMAT, as_tool, build_offer_reviewer

PROMPT = Path(__file__).parent / "prompts" / "orchestrator.md"


# Build the orchestrator with every subagent exposed as a tool
def build_orchestrator(llm: BaseChatModel, backstory: str) -> CompiledStateGraph:
    subagents = [as_tool(build_offer_reviewer(llm, backstory))]
    return create_agent(
        llm,
        tools=subagents,
        system_prompt=PROMPT.read_text(encoding="utf-8"),
        response_format=VERDICT_FORMAT,
        name="orchestrator",
    )


# Run the pipeline for one offer URL
def evaluate(orchestrator: CompiledStateGraph, url: str) -> Verdict:
    result = orchestrator.invoke({"messages": [{"role": "user", "content": url}]})
    out = result["structured_response"]  # already validated by VERDICT_FORMAT
    # keep the URL we asked about, not the one the model echoed back
    return Verdict(out.tag, out.content, url)
