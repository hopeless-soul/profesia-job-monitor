# Verdict pipeline agents. No langchain import here, so the CLI can import AGENT without it.

from contextvars import ContextVar

# name of the agent running in the current context; the logger prints it as a [tag] on each line
AGENT: ContextVar[str] = ContextVar("agent", default="")
