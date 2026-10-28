import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage

from profesia_monitor.agents.offer_reviewer import as_tool, system_prompt
from profesia_monitor.agents.orchestrator import build_orchestrator, evaluate
from profesia_monitor.models import Verdict

URL = "https://www.profesia.sk/praca/x/O1"
VERDICT = {"tag": "GO", "content": "fits", "url": URL}


# chat model that replays scripted replies in order and ignores tool binding
class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


# scripted reply that calls one tool
def call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])


# the orchestrator calls the offer_reviewer subagent and answers with its verdict
def test_orchestrator_delegates_to_offer_reviewer():
    llm = ScriptedModel(messages=iter([
        call("offer_reviewer", {"url": URL}, "1"),  # orchestrator delegates
        call("Verdict", VERDICT, "2"),  # reviewer answers
        call("Verdict", VERDICT, "3"),  # orchestrator relays
    ]))
    orchestrator = build_orchestrator(llm, "backstory")

    result = orchestrator.invoke({"messages": [{"role": "user", "content": URL}]})

    reviewer_output = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "offer_reviewer"]
    assert [json.loads(m.content) for m in reviewer_output] == [VERDICT]
    assert Verdict.from_dict(result["structured_response"]) == Verdict.from_dict(VERDICT)


# evaluate turns the orchestrator's answer into a Verdict
def test_evaluate_returns_verdict():
    llm = ScriptedModel(messages=iter([call("Verdict", VERDICT, "1")]))

    assert evaluate(build_orchestrator(llm, "backstory"), URL) == Verdict("GO", "fits", URL)


# the subagent tool sends the URL as the only message and returns the verdict as JSON
def test_reviewer_tool_passes_url_and_returns_json():
    class StubReviewer:
        def invoke(self, state):
            self.state = state
            return {"structured_response": VERDICT}

    reviewer = StubReviewer()

    out = as_tool(reviewer).invoke({"url": URL})

    assert reviewer.state == {"messages": [{"role": "user", "content": URL}]}
    assert json.loads(out) == VERDICT


# the reviewer prompt carries its duties, the research method and the backstory
def test_reviewer_prompt_includes_research_and_backstory():
    prompt = system_prompt("BACKSTORY-MARKER")

    assert prompt.startswith("# Duties — Offer Reviewer")
    assert "# Offer research" in prompt
    assert prompt.endswith("# Candidate backstory\n\nBACKSTORY-MARKER")
