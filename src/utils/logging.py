"""Tiny logger factory.

Uses stdlib logging with a sensible default format. Respects $LOG_LEVEL.
"""

from __future__ import annotations

import logging
import os

_FMT = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"


def get_logger(name: str) -> logging.Logger:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(_FMT))
        logger.addHandler(h)
        logger.setLevel(level)
        logger.propagate = False
    return logger
