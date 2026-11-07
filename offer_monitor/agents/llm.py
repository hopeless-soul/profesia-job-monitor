# Chat model setup shared by every agent.

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ..config import ModelConfig
from ..errors import ConfigError

# config provider -> (LangChain provider id, default model, env var holding the API key)
PROVIDERS = {
    "anthropic": ("anthropic", "claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "gpt-4o-mini", "OPENAI_API_KEY"),
    "google": ("google_genai", "gemini-2.5-flash", "GOOGLE_API_KEY"),
}


# Build the chat model described by the config's `model` section
def build_llm(model: ModelConfig) -> BaseChatModel:
    provider, default_name, env_var = PROVIDERS[model.provider]

    # the config key wins; otherwise the provider's env var; with neither, fail before building
    api_key = model.api_key or os.environ.get(env_var)
    if not api_key:
        raise ConfigError(f"model.api_key: not set in config.yaml and {env_var} is unset")

    return init_chat_model(
        model.name or default_name,
        model_provider=provider,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        api_key=api_key,
    )
