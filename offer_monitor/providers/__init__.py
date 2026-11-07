# The job sources. Each one lives in its own package with its config; a new source is one package
# plus one entry in SOURCES and its name in Config.providers.

from pathlib import Path

from ..config import SourceProviderKey
from ..settings import require_file
from .base import SourceProvider
from .linkedin import LinkedInProvider
from .linkedin import config as linkedin_config
from .profesia_sk import ProfesiaProvider
from .profesia_sk import config as profesia_config

# Name in `providers` -> (config module of that source: its CONFIG_FILE and load_config, provider class)
# Each key must equal its provider's `name`: telegram looks up job.source here for the label
SOURCES: dict[SourceProviderKey, tuple] = {
    "profesia": (profesia_config, ProfesiaProvider),
    "linkedin": (linkedin_config, LinkedInProvider),
}


def build_providers(provider_keys: list[str], folder: Path) -> list[SourceProvider]:
    """
    Providers for this run, in the order of `names`; each reads its own file from `folder`
    source providers to run; each one's file must sit next to this config (read by providers.build_providers).
    """
    providers: list[SourceProvider] = []
    for key in provider_keys:
        module, provider_cls = SOURCES[key]
        # Fail if the config file is missing; OR invalid provider key;
        file = folder / module.CONFIG_FILE
        require_file(file, f"providers lists '{key}'")
        # Instatiate providers with provided config;
        providers.append(provider_cls(module.load_config(file)))
    return providers
