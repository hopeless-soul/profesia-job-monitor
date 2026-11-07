# The job sources. Each one lives in its own package with its config; a new source is one package
# plus one entry in PROVIDER_CLASSES and one field on Config.

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SourceProvider
from .linkedin import LinkedInProvider
from .profesia_sk import ProfesiaProvider

if TYPE_CHECKING:
    from ..config import Config

# Every provider class; the label per source name is used by telegram
PROVIDER_CLASSES: tuple[type[SourceProvider], ...] = (ProfesiaProvider, LinkedInProvider)
SOURCE_LABELS = {cls.name: cls.label for cls in PROVIDER_CLASSES}


# Providers for this run, in order; each one only when its config was loaded
def build_providers(config: Config) -> list[SourceProvider]:
    providers: list[SourceProvider] = []
    if config.profesia:
        providers.append(ProfesiaProvider(config.profesia))
    if config.linkedin:
        providers.append(LinkedInProvider(config.linkedin))
    return providers
