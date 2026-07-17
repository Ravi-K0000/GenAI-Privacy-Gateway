import logging
import subprocess
import time

from common.config import VaultConfig


log = logging.getLogger(__name__)


def prepare_vault(vault_config: VaultConfig) -> None:
    if not vault_config.auto_start:
        log.info("Vault auto-start disabled; assuming Vault is already running")
    else:
        ensure_vault_running(vault_config)

    client = _client(vault_config)
    if client.sys.is_sealed():
        auto_unseal(client, vault_config)

    if not client.is_authenticated():
        raise RuntimeError("Vault authentication failed")

    ensure_transit_and_key(client, vault_config)


def ensure_vault_running(vault_config: VaultConfig) -> None:
    try:
        client = _client(vault_config, require_auth=False)
        if client.sys.is_initialized():
            log.info("Vault is already running")
            return
    except Exception as exc:
        log.warning("Vault not reachable (%s); attempting to start Vault", exc)

    if not vault_config.dev_mode:
        raise RuntimeError("Vault is not reachable and DEV_MODE is false; start Vault manually")

    try:
        subprocess.Popen(
            [vault_config.vault_binary, "server", "-dev", f"-dev-root-token-id={vault_config.root_token}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to start Vault with binary '{vault_config.vault_binary}': {exc}") from exc

    time.sleep(5)
    client = _client(vault_config, require_auth=False)
    if not client.sys.is_initialized():
        raise RuntimeError("Vault did not become reachable after auto-start")
    log.info("Vault dev server started")


def auto_unseal(client, vault_config: VaultConfig) -> None:
    if not vault_config.unseal_key:
        raise RuntimeError("Vault is sealed and UNSEAL_KEY is missing in vault_config.json")
    client.sys.submit_unseal_key(vault_config.unseal_key)
    if client.sys.is_sealed():
        raise RuntimeError("Vault auto-unseal failed")
    log.info("Vault successfully unsealed")


def ensure_transit_and_key(client, vault_config: VaultConfig) -> None:
    mounts = client.sys.list_mounted_secrets_engines()
    if "transit/" not in mounts:
        client.sys.enable_secrets_engine(backend_type="transit", path="transit")
        log.info("Vault transit engine enabled")

    try:
        client.secrets.transit.read_key(name=vault_config.transit_key)
    except Exception:
        client.secrets.transit.create_key(name=vault_config.transit_key)
        log.info("Vault transit key created: %s", vault_config.transit_key)


def _client(vault_config: VaultConfig, require_auth: bool = True):
    try:
        import hvac
    except ImportError as exc:
        raise RuntimeError("hvac is required for Vault integration") from exc

    client = hvac.Client(url=vault_config.addr, token=vault_config.root_token)
    if require_auth and not client.is_authenticated():
        raise RuntimeError("Vault authentication failed")
    return client
