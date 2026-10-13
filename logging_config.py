"""
Centralized logging configuration for CondoManager.
"""

import logging
import sys
import os

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

def setup_logging():
    """Configures the root logger with a standardized format."""
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def get_logger(name: str) -> logging.Logger:
    """Returns a logger instance for the given module name."""
    return logging.getLogger(name)
