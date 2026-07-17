import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from common.config import RuntimeConfig
from common.paths import LOGS_DIR, OUTPUT_DIR
from common.policy import PolicyContext


PROVENANCE_DIR = OUTPUT_DIR / "provenance"
LEDGER_FILE = PROVENANCE_DIR / "ledger" / "ledger.json"
log = logging.getLogger(__name__)


def record_run_provenance(
    run_id: str,
    domain: str,
    artifacts: list[Path],
    policy_context: PolicyContext,
    runtime_config: RuntimeConfig,
    current_log_path: Path | None = None,
) -> Path:
    logs = _find_logs_to_bundle(current_log_path)
    if not logs:
        raise RuntimeError("No run logs were found to bundle for provenance")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_path = PROVENANCE_DIR / "bundles" / f"logs_bundle_{timestamp}.zip"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    log.debug("Creating provenance log bundle path=%s log_count=%s", bundle_path, len(logs))
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for log_path in logs:
            archive.write(log_path, arcname=f"logs/{log_path.name}")

    digest = _sha256_file(bundle_path)
    ledger = _load_ledger()
    blockchain_receipt = None
    if runtime_config.blockchain.enabled and runtime_config.blockchain.anchor_digest:
        log.debug("Anchoring provenance log bundle digest to blockchain provider=%s rpc=%s", runtime_config.blockchain.provider, runtime_config.blockchain.rpc_url)
        blockchain_receipt = _anchor_digest_to_blockchain(digest, runtime_config)

    entry = {
        **policy_context.metadata(run_id),
        "domain": domain,
        "bundle_type": "run_logs",
        "zip_name": bundle_path.name,
        "zip_path": str(bundle_path.relative_to(OUTPUT_DIR.parent)),
        "sha256": digest,
        "included_logs": [str(path.relative_to(OUTPUT_DIR.parent)) for path in logs],
        "blockchain_enabled": runtime_config.blockchain.enabled,
        "blockchain_anchor_digest": runtime_config.blockchain.anchor_digest,
        "blockchain_provider": runtime_config.blockchain.provider,
        "blockchain_tx_hash": blockchain_receipt["tx_hash"] if blockchain_receipt else None,
        "blockchain_block_number": blockchain_receipt["block_number"] if blockchain_receipt else None,
        "blockchain_rpc_url": runtime_config.blockchain.rpc_url if runtime_config.blockchain.enabled else None,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Prototype provenance ledger. Logs are bundled and hashed locally; only this digest is anchored.",
    }
    ledger.append(entry)
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_FILE.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    log.debug("Provenance ledger updated path=%s sha256=%s", LEDGER_FILE, digest)
    return bundle_path


def _anchor_digest_to_blockchain(digest: str, runtime_config: RuntimeConfig) -> dict[str, str | int]:
    try:
        from web3 import Web3
    except ImportError as exc:
        raise RuntimeError("web3 is required when blockchain.anchor_digest is true") from exc

    cfg = runtime_config.blockchain
    if not cfg.account_address or not cfg.private_key:
        raise RuntimeError("Blockchain account_address and private_key are required when anchoring is enabled")

    web3 = Web3(Web3.HTTPProvider(cfg.rpc_url))
    if not web3.is_connected():
        raise RuntimeError(f"Could not connect to blockchain RPC: {cfg.rpc_url}")

    nonce = web3.eth.get_transaction_count(cfg.account_address)
    tx = {
        "nonce": nonce,
        "to": cfg.account_address,
        "value": 0,
        "gas": cfg.gas,
        "gasPrice": web3.to_wei(cfg.gas_price_gwei, "gwei"),
        "data": bytes.fromhex(digest),
    }
    if cfg.chain_id:
        tx["chainId"] = cfg.chain_id
    signed = web3.eth.account.sign_transaction(tx, cfg.private_key)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    return {"tx_hash": tx_hash.hex(), "block_number": receipt.blockNumber}


def _find_logs_to_bundle(current_log_path: Path | None) -> list[Path]:
    if current_log_path and current_log_path.exists():
        return [current_log_path]
    if not LOGS_DIR.exists():
        return []
    return sorted(LOGS_DIR.glob("*.log"), key=lambda path: path.stat().st_mtime)


def _load_ledger() -> list[dict]:
    if not LEDGER_FILE.exists():
        return []
    return json.loads(LEDGER_FILE.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
