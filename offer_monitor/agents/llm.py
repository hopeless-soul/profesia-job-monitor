# Chat model setup shared by every agent.

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ..config import ModelConfig

# config provider -> (LangChain provider id, default model)
PROVIDERS = {
    "anthropic": ("anthropic", "claude-sonnet-4-5"),
    "openai": ("openai", "gpt-4o-mini"),
    "google": ("google_genai", "gemini-2.5-flash"),
}


# Build the chat model described by the config's `model` section
def build_llm(model: ModelConfig) -> BaseChatModel:
    provider, default_name = PROVIDERS[model.provider]

    # pass the key only when set, so the provider's env var still works
    key = {"api_key": model.api_key} if model.api_key else {}
    return init_chat_model(
        model.name or default_name,
        model_provider=provider,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        **key,
    )
