import json
import os
from dataclasses import dataclass
from typing import Any

from common.paths import CONFIG_DIR
from common.policy import PolicyContext
from common.run_logging import LogConfig, load_log_config


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    endpoint_url: str
    api_key: str
    auth_header: str
    auth_scheme: str
    model: str
    request_format: str
    response_format: str
    headers: dict[str, str]
    max_tokens: int
    temperature: float
    batch_size: int
    delay_seconds: float


@dataclass(frozen=True)
class ExternalProcessingConfig:
    mode: str
    lambda_max_rows_without_override: int
    on_large_input: str
    handoff_root: str
    wait_for_result: bool
    poll_interval_seconds: float
    result_timeout_seconds: float


@dataclass(frozen=True)
class DbConfig:
    dbname: str
    user: str
    password: str
    host: str
    port: int


@dataclass(frozen=True)
class BlockchainConfig:
    enabled: bool
    provider: str
    rpc_url: str
    account_address: str
    private_key: str
    chain_id: int
    gas: int
    gas_price_gwei: int
    anchor_digest: bool


@dataclass(frozen=True)
class VaultConfig:
    addr: str
    root_token: str
    unseal_key: str
    transit_key: str
    auto_start: bool
    vault_binary: str
    dev_mode: bool


@dataclass(frozen=True)
class RuntimeConfig:
    mapping_store: str
    mapping_encryption_provider: str
    enable_performance_metrics: bool
    enable_provenance: bool
    rehydration_max_passes: int
    structured_lambda_name: str
    unstructured_lambda_name: str
    external_processing: ExternalProcessingConfig
    llm: LlmConfig
    db: DbConfig
    vault: VaultConfig
    blockchain: BlockchainConfig
    log: LogConfig


def load_runtime_config(policy_context: PolicyContext) -> RuntimeConfig:
    runtime_file = _load_config_file("runtime_config.json")
    llm_file = _load_config_file("llm_config.json")
    db_file = _load_config_file("db_config.json")
    vault_file = _load_config_file("vault_config.json")
    blockchain_file = _load_config_file("blockchain_config.json")
    flags = policy_context.lifecycle.get("runtime_flags", {})
    mapping_storage = policy_context.lifecycle.get("mapping_storage", {})
    file_flags = runtime_file.get("runtime_flags", {})
    file_mapping = runtime_file.get("mapping_store", {})
    file_lambda = runtime_file.get("lambda", {})
    file_external = runtime_file.get("external_processing", {})
    llm_provider = os.getenv("LLM_PROVIDER", llm_file.get("provider", "http_json")).strip().lower()
    if not llm_provider or llm_provider.startswith("<"):
        llm_provider = "http_json"
    api_key_env = str(llm_file.get("api_key_env", "LLM_API_KEY"))
    llm_headers = llm_file.get("headers", {})
    if not isinstance(llm_headers, dict):
        llm_headers = {}
    return RuntimeConfig(
        mapping_store=os.getenv("MAPPING_STORE", file_mapping.get("mode", mapping_storage.get("mode", "db"))).lower(),
        mapping_encryption_provider=os.getenv(
            "MAPPING_ENCRYPTION_PROVIDER",
            file_mapping.get(
                "encryption_provider",
                policy_context.lifecycle.get("mapping_security", {}).get("encryption_provider", "vault"),
            ),
        ).lower(),
        enable_performance_metrics=_as_bool(
            os.getenv("ENABLE_PERFORMANCE_METRICS"),
            file_flags.get("enable_performance_metrics", flags.get("enable_performance_metrics", True)),
        ),
        enable_provenance=_as_bool(
            os.getenv("ENABLE_PROVENANCE"),
            file_flags.get("enable_provenance", flags.get("enable_provenance", True)),
        ),
        rehydration_max_passes=max(
            1,
            int(
                os.getenv(
                    "REHYDRATION_MAX_PASSES",
                    str(runtime_file.get("rehydration", {}).get("max_passes", 5)),
                )
            ),
        ),
        structured_lambda_name=os.getenv(
            "STRUCTURED_LAMBDA_NAME",
            file_lambda.get("structured_function_name", "lambda_anonymization"),
        ),
        unstructured_lambda_name=os.getenv(
            "UNSTRUCTURED_LAMBDA_NAME",
            file_lambda.get("unstructured_function_name", "lambda_anonymization_unstructured"),
        ),
        llm=LlmConfig(
            provider=llm_provider,
            endpoint_url=os.getenv("LLM_ENDPOINT_URL", os.getenv("LLM_URL", llm_file.get("endpoint_url", llm_file.get("url", "")))),
            api_key=os.getenv(api_key_env, os.getenv("LLM_API_KEY", llm_file.get("api_key", ""))),
            auth_header=os.getenv("LLM_AUTH_HEADER", llm_file.get("auth_header", "Authorization")),
            auth_scheme=os.getenv("LLM_AUTH_SCHEME", llm_file.get("auth_scheme", "Bearer")),
            model=os.getenv("LLM_MODEL", llm_file.get("model", "")),
            request_format=str(llm_file.get("request_format", "openai_chat")).lower(),
            response_format=str(llm_file.get("response_format", "openai_chat")).lower(),
            headers={str(key): str(value) for key, value in llm_headers.items()},
            max_tokens=int(
                os.getenv(
                    "LLM_MAX_TOKENS",
                    str(llm_file.get("max_tokens", 2000)),
                )
            ),
            temperature=float(
                os.getenv(
                    "LLM_TEMPERATURE",
                    str(llm_file.get("temperature", 0.0)),
                )
            ),
            batch_size=int(
                os.getenv(
                    "LLM_BATCH_SIZE",
                    str(llm_file.get("batch_size", 25)),
                )
            ),
            delay_seconds=float(
                os.getenv(
                    "LLM_DELAY_SECONDS",
                    str(llm_file.get("delay_seconds", 0.5)),
                )
            ),
        ),
        external_processing=ExternalProcessingConfig(
            mode=os.getenv("EXTERNAL_PROCESSING_MODE", file_external.get("mode", "lambda")).lower(),
            lambda_max_rows_without_override=int(
                os.getenv(
                    "LAMBDA_MAX_ROWS_WITHOUT_OVERRIDE",
                    str(file_external.get("lambda_max_rows_without_override", 100)),
                )
            ),
            on_large_input=os.getenv("EXTERNAL_PROCESSING_ON_LARGE_INPUT", file_external.get("on_large_input", "sftp_share")).lower(),
            handoff_root=os.getenv("EXTERNAL_HANDOFF_ROOT", file_external.get("handoff_root", "handoff")),
            wait_for_result=_as_bool(
                os.getenv("EXTERNAL_HANDOFF_WAIT_FOR_RESULT"),
                file_external.get("wait_for_result", True),
            ),
            poll_interval_seconds=float(
                os.getenv(
                    "EXTERNAL_HANDOFF_POLL_INTERVAL_SECONDS",
                    str(file_external.get("poll_interval_seconds", 2.0)),
                )
            ),
            result_timeout_seconds=float(
                os.getenv(
                    "EXTERNAL_HANDOFF_RESULT_TIMEOUT_SECONDS",
                    str(file_external.get("result_timeout_seconds", 3600.0)),
                )
            ),
        ),
        db=DbConfig(
            dbname=os.getenv("MAPPING_DB_NAME", db_file.get("dbname", "mappingdb")),
            user=os.getenv("MAPPING_DB_USER", db_file.get("user", "postgres")),
            password=os.getenv("MAPPING_DB_PASSWORD", db_file.get("password", "")),
            host=os.getenv("MAPPING_DB_HOST", db_file.get("host", "localhost")),
            port=int(os.getenv("MAPPING_DB_PORT", str(db_file.get("port", 5432)))),
        ),
        vault=VaultConfig(
            addr=os.getenv("VAULT_ADDR", vault_file.get("VAULT_ADDR", vault_file.get("addr", "http://127.0.0.1:8200"))),
            root_token=os.getenv("VAULT_ROOT_TOKEN", vault_file.get("ROOT_TOKEN", vault_file.get("token", ""))),
            unseal_key=os.getenv("VAULT_UNSEAL_KEY", vault_file.get("UNSEAL_KEY", "")),
            transit_key=os.getenv(
                "VAULT_TRANSIT_KEY",
                vault_file.get("DEFAULT_KEY_NAME", vault_file.get("transit_key", "pii-kek")),
            ),
            auto_start=_as_bool(os.getenv("VAULT_AUTO_START"), vault_file.get("AUTO_START", True)),
            vault_binary=os.getenv("VAULT_BINARY", vault_file.get("VAULT_BINARY", "vault")),
            dev_mode=_as_bool(os.getenv("VAULT_DEV_MODE"), vault_file.get("DEV_MODE", True)),
        ),
        blockchain=BlockchainConfig(
            enabled=_as_bool(os.getenv("BLOCKCHAIN_ENABLED"), blockchain_file.get("enabled", True)),
            provider=os.getenv("BLOCKCHAIN_PROVIDER", blockchain_file.get("provider", "ganache")),
            rpc_url=os.getenv("BLOCKCHAIN_RPC_URL", blockchain_file.get("rpc_url", "http://127.0.0.1:7545")),
            account_address=os.getenv("BLOCKCHAIN_ACCOUNT_ADDRESS", blockchain_file.get("account_address", "")),
            private_key=os.getenv("BLOCKCHAIN_PRIVATE_KEY", blockchain_file.get("private_key", "")),
            chain_id=int(os.getenv("BLOCKCHAIN_CHAIN_ID", str(blockchain_file.get("chain_id", 1337)))),
            gas=int(os.getenv("BLOCKCHAIN_GAS", str(blockchain_file.get("gas", 100000)))),
            gas_price_gwei=int(os.getenv("BLOCKCHAIN_GAS_PRICE_GWEI", str(blockchain_file.get("gas_price_gwei", 10)))),
            anchor_digest=_as_bool(os.getenv("BLOCKCHAIN_ANCHOR_DIGEST"), blockchain_file.get("anchor_digest", True)),
        ),
        log=load_log_config(),
    )


def _as_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_config_file(filename: str) -> dict[str, Any]:
    path = CONFIG_DIR / filename
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
