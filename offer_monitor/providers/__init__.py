# The job sources. Each one lives in its own package with its config; a new source is one package
# plus one entry in _SOURCES and its name in Config.providers.

from pathlib import Path

from ..config import ProviderKey
from ..errors import ConfigError
from .base import SourceProvider
from .linkedin import LinkedInProvider
from .linkedin import config as linkedin_config
from .profesia_sk import ProfesiaProvider
from .profesia_sk import config as profesia_config

# Name in `providers` -> (config module of that source: its CONFIG_FILE and load_config, provider class)
_SOURCES: dict[ProviderKey, tuple] = {
    "profesia": (profesia_config, ProfesiaProvider),
    "linkedin": (linkedin_config, LinkedInProvider),
}

# The label per source name is used by telegram
SOURCE_LABELS = {provider_cls.name: provider_cls.label for _, provider_cls in _SOURCES.values()}

# Providers for this run, in the order of `names`; each reads its own file from `folder`
# sources to run; each one's file must sit next to this config (read by providers.build_providers).
def build_providers(providers: list[str], folder: Path) -> list[SourceProvider]:
    providers: list[SourceProvider] = []
    for key in providers:
        module, provider_cls = _SOURCES[key]
        # Fail if the config file is missing; OR invalid provider key;
        file = folder / module.CONFIG_FILE
        if not file.exists():
            raise ConfigError(f"providers lists '{key}', but {file} is missing")
        # Instatiate providers with provided config;
        providers.append(provider_cls(module.load_config(file)))
    return providers
