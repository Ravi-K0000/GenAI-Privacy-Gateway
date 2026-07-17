import hashlib
import json
from pathlib import Path

from common.paths import OUTPUT_DIR


LEDGER_FILE = OUTPUT_DIR / "provenance" / "ledger" / "ledger.json"


def verify_bundle(bundle_path: Path) -> bool:
    ledger = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    digest = _sha256_file(bundle_path)
    return any(entry.get("bundle", "").endswith(bundle_path.name) and entry.get("sha256") == digest for entry in ledger)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
