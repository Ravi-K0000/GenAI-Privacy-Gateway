from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
COMMON_DIR = ROOT_DIR / "common"
CONFIG_DIR = ROOT_DIR / "configs"
SAMPLE_DATA_DIR = ROOT_DIR / "sample-data"
OUTPUT_DIR = ROOT_DIR / "output"
LOGS_DIR = ROOT_DIR / "logs"
HANDOFF_DIR = ROOT_DIR / "handoff"


def ensure_output_tree() -> None:
    paths = [
        OUTPUT_DIR / "structured" / "anonymized" / "static",
        OUTPUT_DIR / "structured" / "anonymized" / "dynamic",
        OUTPUT_DIR / "structured" / "anonymized" / "final",
        OUTPUT_DIR / "structured" / "mappings",
        OUTPUT_DIR / "structured" / "external-handoff",
        OUTPUT_DIR / "structured" / "third-party-results",
        OUTPUT_DIR / "structured" / "rehydrated",
        OUTPUT_DIR / "structured" / "validation",
        OUTPUT_DIR / "structured" / "metrics",
        OUTPUT_DIR / "unstructured" / "anonymized" / "static",
        OUTPUT_DIR / "unstructured" / "anonymized" / "dynamic",
        OUTPUT_DIR / "unstructured" / "anonymized" / "final",
        OUTPUT_DIR / "unstructured" / "mappings",
        OUTPUT_DIR / "unstructured" / "external-handoff",
        OUTPUT_DIR / "unstructured" / "third-party-results",
        OUTPUT_DIR / "unstructured" / "rehydrated",
        OUTPUT_DIR / "unstructured" / "validation",
        OUTPUT_DIR / "unstructured" / "metrics",
        OUTPUT_DIR / "provenance" / "bundles",
        OUTPUT_DIR / "provenance" / "ledger",
        HANDOFF_DIR / "outbound" / "structured",
        HANDOFF_DIR / "outbound" / "unstructured",
        HANDOFF_DIR / "inbound" / "structured",
        HANDOFF_DIR / "inbound" / "unstructured",
        LOGS_DIR,
    ]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def domain_output(domain: str, *parts: str) -> Path:
    return OUTPUT_DIR.joinpath(domain, *parts)


def sample_input(domain: str) -> Path:
    if domain == "structured":
        path = SAMPLE_DATA_DIR / "structured" / "sample_data.csv"
        expected = "sample-data/structured/sample_data.csv"
    elif domain == "unstructured":
        path = SAMPLE_DATA_DIR / "unstructured" / "sample_transactions.txt"
        expected = "sample-data/unstructured/sample_transactions.txt"
    else:
        raise ValueError(f"Unsupported domain: {domain}")

    if not path.exists():
        raise FileNotFoundError(
            f"Sample input dataset not found for '{domain}'.\n"
            f"Expected file: {path}\n\n"
            "Please place your input file at:\n"
            f"  {expected}\n\n"
            "The repository does not include sample data by default. "
            "Create the sample-data folder structure or update common/paths.py "
            "to point to your own input location."
        )

    return path
