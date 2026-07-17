import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common.paths import CONFIG_DIR, LOGS_DIR, ROOT_DIR


@dataclass(frozen=True)
class LogConfig:
    enabled: bool
    level: str
    log_dir: str
    file_logging: bool
    console_logging: bool
    capture_debug: bool
    include_timestamps: bool


def load_log_config() -> LogConfig:
    path = CONFIG_DIR / "log_config.json"
    data = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    return LogConfig(
        enabled=_as_bool(data.get("enabled", True)),
        level=str(data.get("level", "DEBUG")).upper(),
        log_dir=str(data.get("log_dir", "logs")),
        file_logging=_as_bool(data.get("file_logging", True)),
        console_logging=_as_bool(data.get("console_logging", True)),
        capture_debug=_as_bool(data.get("capture_debug", True)),
        include_timestamps=_as_bool(data.get("include_timestamps", True)),
    )


def setup_run_logging(domain: str, run_id: str, config: LogConfig | None = None) -> Path | None:
    config = config or load_log_config()
    if not config.enabled:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", force=True)
        return None

    level_name = "DEBUG" if config.capture_debug else config.level
    level = getattr(logging, level_name, logging.DEBUG)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = _resolve_log_dir(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"privacy_gateway_{domain}_{timestamp}_{run_id}.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        if config.include_timestamps
        else "%(levelname)s [%(name)s] %(message)s"
    )
    handlers: list[logging.Handler] = []
    if config.file_logging:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    if config.console_logging:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("botocore").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    return log_path


def _resolve_log_dir(configured: str) -> Path:
    path = Path(configured)
    if path.is_absolute():
        return path
    if configured == "logs":
        return LOGS_DIR
    return ROOT_DIR / path


def _as_bool(raw, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
