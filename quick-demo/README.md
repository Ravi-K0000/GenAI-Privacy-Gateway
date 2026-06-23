# Privacy Gateway Quick Demo

This is a self-contained demonstration of the Privacy Gateway architecture. It uses local sample data, policy-driven pseudonymization, mock external processing, and mapping-based rehydration without requiring databases, cloud services, or LLM connectivity.

```bash
python privacygateway.py demo
```

The quick demo has no DB, Vault, Lambda, Ganache, or cloud dependencies. It uses local sample files, local mock mappings, mock external processing, rehydration, and a local provenance ledger.

## Layout

- `common/` - shared mode, configuration, logging, paths, mock mapping store, and pseudonymization policy.
- `structured/` - structured CSV anonymization and mock external processing.
- `unstructured/` - unstructured text anonymization and mock external processing.
- `rehydration/` - mock mapping-based rehydration.
- `provenance/` - local provenance bundle and ledger handling.
- `mock-store/` - generated mock mapping rows, replacing the DB mapping tables.
- `sample-data/` - the same CSV and TXT samples used by the original demo.
- `output/` - generated anonymized files, mapping debug files, external processor results, rehydrated results, logs, and provenance bundles.
- `requirements-demo.txt` - Python packages needed for the quick demo.
- `run_demo.py` - single demo entrypoint.

## Run

```bash
python -m pip install -r requirements-demo.txt
python run_demo.py
```

Default flow is `structured`. Other options:

```bash
python run_demo.py unstructured
python run_demo.py both
```

## Output Structure

Generated files are written under:

```text
output/
  processed/
    structured/
      anon_final/
      mappings/
    unstructured/
      anon_final/
      mappings/
  external_processing_results/
    rehydrated/
  provenance_chain/
  logs/
```

Mock mappings used for rehydration are written to:

```text
mock-store/
  mappings_structured.json
  mappings_unstructured.json
```

Pseudonymization fields and regex rules are configured in:

```text
common/privacy_policy.json
```
