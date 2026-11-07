# Imports nothing, so it can't be part of a circular import;
# Raised when a `config` file or section is invalid;
class ConfigError(ValueError):
    pass
