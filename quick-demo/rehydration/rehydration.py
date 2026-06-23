"""Rehydrate mock external outputs from locally stored mappings."""

import json
import logging
from pathlib import Path
from typing import Dict

from common.mock_mapping_store import fetch_mapping, get_latest_request_id
from common.paths import EXTERNAL_RESULTS_DIR, REHYDRATED_DIR

logger = logging.getLogger(__name__)


TABLE_BY_FLOW = {
    "structured": "mappings_table",
    "unstructured": "mappings_unstructured_table",
}

GLOB_BY_FLOW = {
    "structured": "external_output_str_*.json",
    "unstructured": "external_output_unstr_*.json",
}


def apply_mappings(obj, mappings: Dict[str, str]):
    if isinstance(obj, dict):
        return {key: apply_mappings(value, mappings) for key, value in obj.items()}
    if isinstance(obj, list):
        return [apply_mappings(value, mappings) for value in obj]
    if isinstance(obj, str):
        result = obj
        for placeholder, original in sorted(mappings.items(), key=lambda item: len(item[0]), reverse=True):
            clean_placeholder = placeholder.strip("<>")
            clean_original = original.strip("<>")
            result = result.replace(f"<{clean_placeholder}>", clean_original)
            result = result.replace(clean_placeholder, clean_original)
        return result
    return obj


def _latest_external_output(flow: str) -> Path:
    files = sorted(EXTERNAL_RESULTS_DIR.glob(GLOB_BY_FLOW[flow]), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No {flow} external output found in {EXTERNAL_RESULTS_DIR}")
    return files[0]


def rehydrate_latest(flow: str, request_id: str = None, stage: str = "final") -> Path:
    table_name = TABLE_BY_FLOW[flow]
    request_id = request_id or get_latest_request_id(stage=stage, table_name=table_name)
    if not request_id:
        raise RuntimeError(f"No request_id found in mock store for {flow} flow")

    mappings = fetch_mapping(request_id=request_id, stage=stage, table_name=table_name)
    if not mappings:
        raise RuntimeError(f"No mappings found for request_id={request_id}, flow={flow}")

    input_file = _latest_external_output(flow)
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    rehydrated = apply_mappings(data, mappings)
    suffix = input_file.stem.replace("external_output_", "")
    out_path = REHYDRATED_DIR / f"rehydrated_{suffix}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rehydrated, f, indent=2, ensure_ascii=False)

    logger.info("Wrote %s rehydrated output: %s", flow, out_path)
    return out_path
