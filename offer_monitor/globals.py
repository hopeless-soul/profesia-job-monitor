from pathlib import Path

# Runtime files (config, jobs.db, subscribers, backstory) live in data/, shared with Docker's /data volume
DEFAULT_CONFIG = Path("data/configs/config.yaml")

# ANSI colors for console output only (not logs); 256-color orange, since the 16-color yellow looks more yellow than orange
YELLOW, ORANGE, GREY, RESET = "\033[33m", "\033[38;5;208m", "\033[90m", "\033[0m"
