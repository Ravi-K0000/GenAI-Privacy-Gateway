import base64
import os
from dataclasses import dataclass

from common.config import VaultConfig
from common.vault_prep_steps import prepare_vault


_VAULT_CONFIG: VaultConfig | None = None
_VAULT_PREPARED = False


@dataclass(frozen=True)
class EncryptedValue:
    ciphertext_b64: str
    nonce_b64: str
    wrapped_key: str
    key_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ciphertext_b64": self.ciphertext_b64,
            "nonce_b64": self.nonce_b64,
            "wrapped_key": self.wrapped_key,
            "key_id": self.key_id,
        }


def vault_enabled() -> bool:
    provider = os.getenv("MAPPING_ENCRYPTION_PROVIDER", "vault").strip().lower()
    return provider == "vault"


def encrypt_value(value: str, key_name: str | None = None) -> EncryptedValue:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    client = _client()
    transit_key = key_name or _transit_key()
    response = client.secrets.transit.generate_data_key(name=transit_key, key_type="plaintext")
    data = response["data"]
    dek = base64.b64decode(data["plaintext"])
    wrapped_key = data["ciphertext"]
    nonce = os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, str(value).encode("utf-8"), None)
    return EncryptedValue(
        ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
        nonce_b64=base64.b64encode(nonce).decode("ascii"),
        wrapped_key=wrapped_key,
        key_id=transit_key,
    )


def decrypt_value(encrypted: dict[str, str]) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    client = _client()
    key_id = encrypted.get("key_id") or _transit_key()
    response = client.secrets.transit.decrypt_data(name=key_id, ciphertext=encrypted["wrapped_key"])
    dek = base64.b64decode(response["data"]["plaintext"])
    nonce = base64.b64decode(encrypted["nonce_b64"])
    ciphertext = base64.b64decode(encrypted["ciphertext_b64"])
    plaintext = AESGCM(dek).decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def _client():
    try:
        import hvac
    except ImportError as exc:
        raise RuntimeError("hvac is required when MAPPING_ENCRYPTION_PROVIDER=vault") from exc

    _prepare_if_needed()
    address = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
    token = os.getenv("VAULT_ROOT_TOKEN", os.getenv("VAULT_TOKEN", ""))
    if not token:
        raise RuntimeError("VAULT_ROOT_TOKEN is required when MAPPING_ENCRYPTION_PROVIDER=vault")
    client = hvac.Client(url=address, token=token)
    if not client.is_authenticated():
        raise RuntimeError("Vault authentication failed")
    return client


def configure_vault_environment(vault_config: VaultConfig) -> None:
    global _VAULT_CONFIG
    _VAULT_CONFIG = vault_config
    os.environ.setdefault("VAULT_ADDR", vault_config.addr)
    os.environ.setdefault("VAULT_ROOT_TOKEN", vault_config.root_token)
    os.environ.setdefault("VAULT_TOKEN", vault_config.root_token)
    os.environ.setdefault("VAULT_UNSEAL_KEY", vault_config.unseal_key)
    os.environ.setdefault("VAULT_TRANSIT_KEY", vault_config.transit_key)
    os.environ.setdefault("VAULT_BINARY", vault_config.vault_binary)


def _transit_key() -> str:
    return os.getenv("VAULT_TRANSIT_KEY", "pii-kek")


def _prepare_if_needed() -> None:
    global _VAULT_PREPARED
    if _VAULT_PREPARED:
        return
    if _VAULT_CONFIG is None:
        raise RuntimeError("Vault config was not initialized before Vault use")
    prepare_vault(_VAULT_CONFIG)
    _VAULT_PREPARED = True
