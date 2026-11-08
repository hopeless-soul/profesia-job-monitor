import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage

from offer_monitor.agents import AGENT, offer_reviewer
from offer_monitor.agents.offer_reviewer import as_tool, system_prompt
from offer_monitor.agents.orchestrator import build_orchestrator, evaluate
from offer_monitor.models import Verdict

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
    assert result["structured_response"].model_dump() == VERDICT


# evaluate turns the orchestrator's answer into a Verdict
def test_evaluate_returns_verdict():
    llm = ScriptedModel(messages=iter([call("Verdict", VERDICT, "1")]))

    assert evaluate(build_orchestrator(llm, "backstory"), URL) == Verdict("GO", "fits", URL)


# an invalid answer (unknown tag, blank content) goes back to the model, which then answers correctly
def test_evaluate_reasks_on_invalid_verdict():
    llm = ScriptedModel(messages=iter([
        call("Verdict", {"tag": "MAYBE", "content": "fits", "url": URL}, "1"),
        call("Verdict", {"tag": "GO", "content": "   ", "url": URL}, "2"),
        call("Verdict", VERDICT, "3"),
    ]))

    assert evaluate(build_orchestrator(llm, "backstory"), URL) == Verdict("GO", "fits", URL)


# the stored verdict keeps the URL we asked about, whatever URL the model echoes
def test_evaluate_keeps_input_url():
    llm = ScriptedModel(messages=iter([call("Verdict", {**VERDICT, "url": "https://elsewhere"}, "1")]))

    assert evaluate(build_orchestrator(llm, "backstory"), URL).url == URL


# the subagent tool sends the URL as the only message and returns the verdict as JSON
def test_reviewer_tool_passes_url_and_returns_json():
    class StubReviewer:
        def invoke(self, state):
            self.state = state
            self.agent = AGENT.get()
            return {"structured_response": offer_reviewer.Verdict(**VERDICT)}

    reviewer = StubReviewer()

    out = as_tool(reviewer).invoke({"url": URL})

    assert reviewer.state == {"messages": [{"role": "user", "content": URL}]}
    assert json.loads(out) == VERDICT
    # the log tag is set during the run and cleared after it
    assert reviewer.agent == "offer_reviewer"
    assert AGENT.get() == ""


# every model call is tagged with the agent that made it, across LangGraph's worker threads
def test_model_calls_are_tagged_with_agent_name():
    seen = []

    class RecordingModel(ScriptedModel):
        def _generate(self, *args, **kwargs):
            seen.append(AGENT.get())
            return super()._generate(*args, **kwargs)

    llm = RecordingModel(messages=iter([
        call("offer_reviewer", {"url": URL}, "1"),  # orchestrator delegates
        call("Verdict", VERDICT, "2"),  # reviewer answers
        call("Verdict", VERDICT, "3"),  # orchestrator relays
    ]))

    evaluate(build_orchestrator(llm, "backstory"), URL)

    assert seen == ["orchestrator", "offer_reviewer", "orchestrator"]
    assert AGENT.get() == ""


# the reviewer prompt carries its duties, the research method and the backstory
def test_reviewer_prompt_includes_research_and_backstory():
    prompt = system_prompt("BACKSTORY-MARKER")

    assert prompt.startswith("# Duties — Offer Reviewer")
    assert "# Offer research" in prompt
    assert "Write the verdict `content` in English" in prompt
    assert prompt.endswith("# Candidate backstory\n\nBACKSTORY-MARKER")
