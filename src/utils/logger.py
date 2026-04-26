"""중앙 로거. loguru가 없으면 logging fallback."""
from __future__ import annotations

import os
import sys

try:
    from loguru import logger as _logger

    _logger.remove()
    _logger.add(
        sys.stdout,
        level="DEBUG" if os.getenv("SCANNER_DEBUG", "false").lower() == "true" else "INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )
    logger = _logger
except ImportError:  # pragma: no cover
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    logger = logging.getLogger("scanner")
