from common.config import load_runtime_config
from common.policy import load_policy_context
from common.vault_client import configure_vault_environment, decrypt_value, encrypt_value


def main() -> None:
    policy_context = load_policy_context()
    runtime_config = load_runtime_config(policy_context)
    configure_vault_environment(runtime_config.vault)

    plaintext = "vault-roundtrip-check"
    encrypted = encrypt_value(plaintext).to_dict()
    restored = decrypt_value(encrypted)

    if restored != plaintext:
        raise RuntimeError("Vault encryption/decryption round-trip failed")

    print("Vault check passed")
    print(f"Vault address: {runtime_config.vault.addr}")
    print(f"Transit key: {runtime_config.vault.transit_key}")


if __name__ == "__main__":
    main()
