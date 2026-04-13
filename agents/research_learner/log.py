"""Logging setup for the research learner. Detailed file log + concise stdout."""

import logging
from pathlib import Path

LOG_PATH = Path(__file__).parent / "learner.log"

_configured = False


def get_logger(name: str = "learner") -> logging.Logger:
    """Return a logger that writes detailed output to learner.log."""
    global _configured
    logger = logging.getLogger(f"research_learner.{name}")

    if not _configured:
        root = logging.getLogger("research_learner")
        root.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )

        fh = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)

        _configured = True

    return logger
