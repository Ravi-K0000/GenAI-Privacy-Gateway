"""Anchor demo logs in the local mock provenance ledger."""

import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from common.paths import LOGS_DIR, OUTPUT_DIR, PROVENANCE_DIR

logger = logging.getLogger(__name__)

LEDGER_FILE = PROVENANCE_DIR / "ledger.json"


def _read_ledger() -> List[Dict]:
    if not LEDGER_FILE.exists():
        return []
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_ledger(ledger: List[Dict]) -> None:
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)


def _new_logs(ledger: List[Dict]) -> List[Path]:
    seen = set()
    for entry in ledger:
        seen.update(entry.get("included_logs", []))
    return [
        path for path in sorted(LOGS_DIR.glob("*.log"))
        if path.name not in seen
    ]


def _create_zip(logs: List[Path]) -> Tuple[str, Path]:
    zip_name = f"logs_bundle_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip"
    zip_path = PROVENANCE_DIR / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for log_path in logs:
            zf.write(log_path, arcname=log_path.name)
    return zip_name, zip_path


def anchor_new_logs() -> Path | None:
    PROVENANCE_DIR.mkdir(parents=True, exist_ok=True)
    ledger = _read_ledger()
    logs = _new_logs(ledger)
    if not logs:
        logger.info("No new logs to anchor")
        return None

    zip_name, zip_path = _create_zip(logs)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    tx_hash = "mock_" + hashlib.sha256(f"{zip_name}:{digest}".encode("utf-8")).hexdigest()
    ledger.append(
        {
            "zip_name": zip_name,
            "zip_path": str(zip_path.relative_to(OUTPUT_DIR)),
            "sha256": digest,
            "tx_hash": tx_hash,
            "blockNumber": len(ledger) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rpc": "mock://local-ledger",
            "mode": "mock",
            "included_logs": [path.name for path in logs],
        }
    )
    _write_ledger(ledger)
    logger.info("Anchored %s log(s) in mock provenance ledger: %s", len(logs), LEDGER_FILE)
    return zip_path
