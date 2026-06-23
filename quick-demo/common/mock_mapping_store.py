import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from common.paths import MOCK_STORE_DIR


STORE_FILES = {
    "mappings_table": MOCK_STORE_DIR / "mappings_structured.json",
    "mappings_unstructured_table": MOCK_STORE_DIR / "mappings_unstructured.json",
}


def _store_file(table_name: str) -> Path:
    try:
        return STORE_FILES[table_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported mock mapping table: {table_name}") from exc


def _read_rows(table_name: str) -> List[dict]:
    store_file = _store_file(table_name)
    if not store_file.exists():
        return []
    with open(store_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_rows(table_name: str, rows: List[dict]) -> None:
    MOCK_STORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_store_file(table_name), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)


def save_mapping(
    mapping: Dict[str, str],
    request_id: str,
    stage: str,
    table_name: str,
    uuid: Optional[str] = None,
) -> int:
    rows = [
        row for row in _read_rows(table_name)
        if not (
            row.get("table_name") == table_name
            and row.get("request_id") == request_id
            and row.get("stage") == stage
        )
    ]
    now = datetime.now(timezone.utc).isoformat()
    effective_uuid = uuid or request_id

    for placeholder, original_value in mapping.items():
        rows.append(
            {
                "table_name": table_name,
                "uuid": effective_uuid,
                "request_id": request_id,
                "placeholder": str(placeholder).strip().lstrip("<").rstrip(">").strip(),
                "original_value": str(original_value),
                "stage": stage,
                "created_at": now,
            }
        )

    _write_rows(table_name, rows)
    return len(mapping)


def fetch_mapping(request_id: str, stage: str, table_name: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for row in _read_rows(table_name):
        if (
            row.get("table_name") == table_name
            and row.get("request_id") == request_id
            and row.get("stage") == stage
        ):
            result[str(row["placeholder"])] = str(row["original_value"])
    return result


def get_latest_request_id(stage: str, table_name: str) -> Optional[str]:
    rows = [
        row for row in _read_rows(table_name)
        if row.get("table_name") == table_name and row.get("stage") == stage
    ]
    if not rows:
        return None
    rows.sort(key=lambda row: row.get("created_at", ""), reverse=True)
    return rows[0].get("request_id")
