import json
import logging
import uuid
from pathlib import Path
from typing import Any

from common.config import RuntimeConfig
from common.metadata import write_metadata
from common.paths import domain_output
from common.policy import PolicyContext
from common.vault_client import configure_vault_environment, decrypt_value, encrypt_value


log = logging.getLogger(__name__)


def write_mapping(
    domain: str,
    run_id: str,
    stage: str,
    mapping: dict[str, str],
    runtime_config: RuntimeConfig,
    policy_context: PolicyContext,
) -> Path | None:
    mode = runtime_config.mapping_store
    if mode not in {"local", "db", "both"}:
        raise ValueError("Mapping store must be local, db, or both")

    local_path = None
    if mode in {"local", "both"}:
        stored_mapping = _prepare_mapping_for_storage(mapping, runtime_config)
        mapping_dir = domain_output(domain, "mappings")
        mapping_dir.mkdir(parents=True, exist_ok=True)
        local_path = mapping_dir / f"mapping_{stage}_{run_id}.json"
        payload = {
            **policy_context.metadata(run_id),
            "domain": domain,
            "stage": stage,
            "mapping_security": {
                "encryption_provider": runtime_config.mapping_encryption_provider,
                "encrypted": runtime_config.mapping_encryption_provider == "vault",
            },
            "mapping": stored_mapping,
        }
        local_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        write_metadata(local_path, policy_context, run_id, {"stage": stage})
        log.debug("Wrote %s local mapping entries for domain=%s stage=%s path=%s", len(mapping), domain, stage, local_path)

    if mode in {"db", "both"}:
        _write_mapping_db(domain, run_id, stage, mapping, runtime_config, policy_context)

    return local_path


def read_mapping(domain: str, run_id: str, runtime_config: RuntimeConfig) -> dict[str, str]:
    mode = runtime_config.mapping_store
    log.debug("Reading final mappings domain=%s run_id=%s mapping_store=%s", domain, run_id, mode)
    if mode in {"local", "both"}:
        path = domain_output(domain, "mappings", f"mapping_final_{run_id}.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _restore_mapping_from_storage(payload.get("mapping", payload), runtime_config)
    if mode == "db":
        return _read_mapping_db(domain, run_id, runtime_config)
    raise ValueError("Mapping store must be local, db, or both")


def _prepare_mapping_for_storage(mapping: dict[str, str], runtime_config: RuntimeConfig) -> dict[str, Any]:
    if runtime_config.mapping_encryption_provider == "none":
        return mapping
    if runtime_config.mapping_encryption_provider != "vault":
        raise ValueError("Mapping encryption provider must be vault or none")
    configure_vault_environment(runtime_config.vault)
    return {placeholder: encrypt_value(original_value).to_dict() for placeholder, original_value in mapping.items()}


def _restore_mapping_from_storage(mapping: dict[str, Any], runtime_config: RuntimeConfig) -> dict[str, str]:
    if runtime_config.mapping_encryption_provider == "none":
        return {placeholder: str(value) for placeholder, value in mapping.items()}
    if runtime_config.mapping_encryption_provider != "vault":
        raise ValueError("Mapping encryption provider must be vault or none")
    configure_vault_environment(runtime_config.vault)
    return {placeholder: decrypt_value(encrypted_value) for placeholder, encrypted_value in mapping.items()}


def _write_mapping_db(
    domain: str,
    run_id: str,
    stage: str,
    mapping: dict[str, str],
    runtime_config: RuntimeConfig,
    policy_context: PolicyContext,
) -> None:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2 is required when mapping_storage.mode is db or both") from exc

    cfg = runtime_config.db
    table = _mapping_table(domain)
    if stage != "final":
        log.debug("Skipping DB write for non-final mapping stage=%s domain=%s run_id=%s", stage, domain, run_id)
        return
    log.debug("Writing %s final mapping rows to table=%s domain=%s run_id=%s", len(mapping), table, domain, run_id)
    with psycopg2.connect(
        dbname=cfg.dbname,
        user=cfg.user,
        password=cfg.password,
        host=cfg.host,
        port=cfg.port,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    uuid TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    placeholder TEXT NOT NULL,
                    ciphertext TEXT,
                    wrapped_key TEXT,
                    key_id TEXT,
                    stage TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for placeholder, original_value in mapping.items():
                encrypted = None
                if runtime_config.mapping_encryption_provider == "vault":
                    configure_vault_environment(runtime_config.vault)
                    encrypted = encrypt_value(original_value).to_dict()
                cur.execute(
                    f"""
                    INSERT INTO {table}
                    (uuid, request_id, placeholder, ciphertext, wrapped_key, key_id, stage)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        placeholder,
                        json.dumps(
                            {
                                "ciphertext_b64": encrypted["ciphertext_b64"],
                                "nonce_b64": encrypted["nonce_b64"],
                            }
                        )
                        if encrypted
                        else original_value,
                        encrypted["wrapped_key"] if encrypted else None,
                        encrypted["key_id"] if encrypted else None,
                        stage,
                    ),
                )
        conn.commit()
    log.debug("DB mapping write complete table=%s domain=%s run_id=%s", table, domain, run_id)


def _read_mapping_db(domain: str, run_id: str, runtime_config: RuntimeConfig) -> dict[str, str]:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2 is required when mapping_storage.mode is db") from exc

    cfg = runtime_config.db
    table = _mapping_table(domain)
    result: dict[str, str] = {}
    log.debug("Reading mappings from DB table=%s domain=%s run_id=%s", table, domain, run_id)
    with psycopg2.connect(
        dbname=cfg.dbname,
        user=cfg.user,
        password=cfg.password,
        host=cfg.host,
        port=cfg.port,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT placeholder, ciphertext, wrapped_key, key_id
                FROM {table}
                WHERE request_id = %s AND stage = 'final'
                """,
                (run_id,),
            )
            for placeholder, ciphertext, wrapped_key, key_id in cur.fetchall():
                normalized_placeholder = _normalize_placeholder(placeholder)
                if runtime_config.mapping_encryption_provider == "vault":
                    configure_vault_environment(runtime_config.vault)
                    encrypted_payload = _decode_ciphertext(ciphertext)
                    encrypted_payload["wrapped_key"] = wrapped_key
                    encrypted_payload["key_id"] = key_id
                    result[normalized_placeholder] = decrypt_value(encrypted_payload)
                else:
                    result[normalized_placeholder] = ciphertext
    if not result:
        raise RuntimeError(f"No final mappings found in DB for domain={domain}, run_id={run_id}")
    log.debug("Read %s decrypted mapping rows from table=%s domain=%s run_id=%s", len(result), table, domain, run_id)
    return result


def _mapping_table(domain: str) -> str:
    if domain == "structured":
        return "mappings_table"
    if domain == "unstructured":
        return "mappings_unstructured_table"
    raise ValueError(f"Unsupported mapping domain: {domain}")


def _decode_ciphertext(raw: str) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Encrypted DB mapping ciphertext must be JSON with ciphertext_b64 and nonce_b64") from exc
    if "ciphertext_b64" not in payload or "nonce_b64" not in payload:
        raise RuntimeError("Encrypted DB mapping ciphertext missing ciphertext_b64 or nonce_b64")
    return payload


def _normalize_placeholder(placeholder: str) -> str:
    text = str(placeholder)
    if text.startswith("<") and text.endswith(">"):
        return text
    return f"<{text}>"
