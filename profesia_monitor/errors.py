# Errors shared by the main config and the provider configs (kept apart so providers don't import config.py)


# A config file or config section is invalid; the message names the field
class ConfigError(ValueError):
    pass
