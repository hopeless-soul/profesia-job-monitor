# Profesia.sk errors: one base for the source, and one subclass per cause so run_once knows what to do

from ..base import SourceBlockedError, SourceLayoutError, SourceProviderError, SourceRateLimitedError, SourceUnavailableError


# Any Profesia failure; the cause subclasses below say what went wrong
class ProfesiaError(SourceProviderError):
    pass


# Connection failed or timed out, or Profesia returned 5xx
class ProfesiaUnavailableError(ProfesiaError, SourceUnavailableError):
    pass


# Profesia returned 429; retry_after carries its Retry-After
class ProfesiaRateLimitedError(ProfesiaError, SourceRateLimitedError):
    pass


# Profesia returned 401/403, so it refuses us
class ProfesiaBlockedError(ProfesiaError, SourceBlockedError):
    pass


# Listing rows are on the page but none parse: the site's markup changed
class ProfesiaLayoutError(ProfesiaError, SourceLayoutError):
    pass
