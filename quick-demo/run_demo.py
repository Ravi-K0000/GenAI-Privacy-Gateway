import argparse
import importlib.util
import os
import sys
from datetime import datetime

from common.logging_config import configure_logging
from common.paths import LOGS_DIR, ensure_output_structure
from provenance.provenance import anchor_new_logs


REQUIRED_IMPORTS = {
    "pandas": "pandas",
}


def missing_dependencies() -> list[str]:
    return [
        package
        for package, import_name in REQUIRED_IMPORTS.items()
        if importlib.util.find_spec(import_name) is None
    ]


def run_structured() -> str:
    from rehydration.rehydration import rehydrate_latest
    from structured.structured_flow import run_anonymization as run_structured_anonymization
    from structured.structured_flow import run_external_processor as run_structured_external

    request_id = run_structured_anonymization()
    run_structured_external()
    rehydrate_latest("structured", request_id=request_id)
    return request_id


def run_unstructured() -> str:
    from rehydration.rehydration import rehydrate_latest
    from unstructured.unstructured_flow import run_anonymization as run_unstructured_anonymization
    from unstructured.unstructured_flow import run_external_processor as run_unstructured_external

    request_id = run_unstructured_anonymization()
    run_unstructured_external()
    rehydrate_latest("unstructured", request_id=request_id)
    return request_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Privacy Gateway mock quick demo.")
    parser.add_argument(
        "flow",
        nargs="?",
        choices=["structured", "unstructured", "both"],
        default="structured",
        help="which mock demo flow to run",
    )
    parser.add_argument(
        "--skip-dependency-check",
        action="store_true",
        help="run without checking whether pandas is installed",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["PRIVACY_GATEWAY_MODE"] = "mock"
    ensure_output_structure()

    log_file = LOGS_DIR / f"quick_demo_{args.flow}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    configure_logging(log_file)

    if not args.skip_dependency_check:
        missing = missing_dependencies()
        if missing:
            print(f"Missing demo dependencies: {', '.join(missing)}")
            print(f"Install them with: {sys.executable} -m pip install -r requirements-demo.txt")
            return 1

    if args.flow in ("structured", "both"):
        run_structured()
    if args.flow in ("unstructured", "both"):
        run_unstructured()

    anchor_new_logs()
    print(f"Demo complete. Outputs written under: {LOGS_DIR.parents[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
