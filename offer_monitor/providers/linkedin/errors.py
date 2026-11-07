# LinkedIn errors: one base for the source, and one subclass per cause so run_once knows what to do

from ..base import SourceAuthError, SourceProviderError, SourceResponseError, SourceUnavailableError


# Any LinkedIn failure; the cause subclasses below say what went wrong
class LinkedInError(SourceProviderError):
    pass


# The MCP server did not start, crashed, or timed out at the server
class LinkedInUnavailableError(LinkedInError, SourceUnavailableError):
    pass


# The server needs a login or a verification step in the browser
class LinkedInAuthError(LinkedInError, SourceAuthError):
    pass


# The server replied, but the reply was not JSON
class LinkedInResponseError(LinkedInError, SourceResponseError):
    pass
