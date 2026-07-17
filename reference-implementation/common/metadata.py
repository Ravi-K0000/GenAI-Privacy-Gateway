import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.policy import PolicyContext


def write_metadata(target_path: Path, policy_context: PolicyContext, run_id: str, extra: dict[str, Any] | None = None) -> Path:
    metadata = {
        **policy_context.metadata(run_id),
        "artifact": target_path.name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        metadata.update(extra)
    metadata_path = target_path.with_suffix(target_path.suffix + ".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata_path
