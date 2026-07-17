import argparse
import hashlib
import json
from pathlib import Path

from common.config import load_runtime_config
from common.paths import OUTPUT_DIR, ROOT_DIR
from common.policy import load_policy_context
from provenance.log_to_blockchain import LEDGER_FILE


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify provenance log bundle hashes and optional blockchain anchors.")
    parser.add_argument("--check-chain", action="store_true", help="Also validate blockchain transaction input data.")
    args = parser.parse_args()

    policy_context = load_policy_context()
    runtime_config = load_runtime_config(policy_context)
    results = verify_audit_log(runtime_config, check_chain=args.check_chain)
    print(json.dumps(results, indent=2))
    if not all(item["hash_valid"] and item.get("chain_valid", True) for item in results):
        raise SystemExit(1)


def verify_audit_log(runtime_config, check_chain: bool = False) -> list[dict]:
    if not LEDGER_FILE.exists():
        raise FileNotFoundError(f"Provenance ledger not found: {LEDGER_FILE}")
    ledger = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    results = []
    for entry in ledger:
        bundle_path = _resolve_bundle_path(entry)
        actual_hash = _sha256_file(bundle_path) if bundle_path.exists() else None
        expected_hash = entry.get("sha256")
        result = {
            "run_id": entry.get("run_id"),
            "domain": entry.get("domain"),
            "zip_name": entry.get("zip_name"),
            "bundle_exists": bundle_path.exists(),
            "expected_sha256": expected_hash,
            "actual_sha256": actual_hash,
            "hash_valid": bool(actual_hash and actual_hash == expected_hash),
        }
        if check_chain and entry.get("blockchain_tx_hash"):
            result["chain_valid"] = _verify_chain_digest(entry, runtime_config)
        results.append(result)
    return results


def _resolve_bundle_path(entry: dict) -> Path:
    raw = entry.get("zip_path") or entry.get("bundle")
    if not raw:
        return OUTPUT_DIR / "provenance" / "bundles" / str(entry.get("zip_name", ""))
    path = Path(raw)
    return path if path.is_absolute() else ROOT_DIR / path


def _verify_chain_digest(entry: dict, runtime_config) -> bool:
    try:
        from web3 import Web3
    except ImportError as exc:
        raise RuntimeError("web3 is required for --check-chain") from exc

    tx_hash = entry["blockchain_tx_hash"]
    web3 = Web3(Web3.HTTPProvider(runtime_config.blockchain.rpc_url))
    if not web3.is_connected():
        raise RuntimeError(f"Could not connect to blockchain RPC: {runtime_config.blockchain.rpc_url}")
    tx = web3.eth.get_transaction(tx_hash)
    input_data = tx.get("input", "")
    anchored_digest = input_data.hex() if isinstance(input_data, bytes) else str(input_data).removeprefix("0x")
    return anchored_digest.lower() == str(entry.get("sha256", "")).lower()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


if __name__ == "__main__":
    main()
