import os


MODE_ENV_VAR = "PRIVACY_GATEWAY_MODE"
MOCK_MODE = "mock"


def get_mode() -> str:
    return os.getenv(MODE_ENV_VAR, MOCK_MODE).strip().lower() or MOCK_MODE


def is_mock_mode() -> bool:
    return True
