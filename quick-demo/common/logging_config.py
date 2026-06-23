import logging
import os
from pathlib import Path

from common.paths import LOGS_DIR


def configure_logging(log_file: Path = None) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler()]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def get_logger(name: str):
    return logging.getLogger(name)
