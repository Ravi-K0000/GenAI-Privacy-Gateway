from pathlib import Path


DEMO_ROOT = Path(__file__).resolve().parents[1]
COMMON_DIR = DEMO_ROOT / "common"
PRIVACY_POLICY_FILE = COMMON_DIR / "privacy_policy.json"
SAMPLE_DATA_DIR = DEMO_ROOT / "sample-data"
MOCK_STORE_DIR = DEMO_ROOT / "mock-store"
OUTPUT_DIR = DEMO_ROOT / "output"
LOGS_DIR = OUTPUT_DIR / "logs"

STRUCTURED_SAMPLE = SAMPLE_DATA_DIR / "sample_data.csv"
UNSTRUCTURED_SAMPLE_DIR = SAMPLE_DATA_DIR / "unstructured"

STRUCTURED_PROCESSED_DIR = OUTPUT_DIR / "processed" / "structured"
UNSTRUCTURED_PROCESSED_DIR = OUTPUT_DIR / "processed" / "unstructured"
STRUCTURED_ANON_DIR = STRUCTURED_PROCESSED_DIR / "anon_final"
STRUCTURED_MAPPING_DIR = STRUCTURED_PROCESSED_DIR / "mappings"
UNSTRUCTURED_ANON_DIR = UNSTRUCTURED_PROCESSED_DIR / "anon_final"
UNSTRUCTURED_MAPPING_DIR = UNSTRUCTURED_PROCESSED_DIR / "mappings"

EXTERNAL_RESULTS_DIR = OUTPUT_DIR / "external_processing_results"
REHYDRATED_DIR = EXTERNAL_RESULTS_DIR / "rehydrated"
PROVENANCE_DIR = OUTPUT_DIR / "provenance_chain"


def ensure_output_structure() -> None:
    for path in (
        MOCK_STORE_DIR,
        LOGS_DIR,
        STRUCTURED_ANON_DIR,
        STRUCTURED_MAPPING_DIR,
        UNSTRUCTURED_ANON_DIR,
        UNSTRUCTURED_MAPPING_DIR,
        EXTERNAL_RESULTS_DIR,
        REHYDRATED_DIR,
        PROVENANCE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
