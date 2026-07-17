# Privacy Gateway Reference Prototype

This reference prototype demonstrates policy-driven anonymization, external processing, rehydration, metrics, and provenance for structured and unstructured data.

## Quickstart

1. Create a Python environment and install dependencies:

   ```bash
   pip install -r requirements-demo.txt
   ```

2. Set runtime secrets and endpoints in the files under `configs/`. Environment variables can still override config values for local testing.

3. Review policy files:

   - `common/privacy_policy.json`
   - `common/policy_lifecycle.json`
   - `configs/runtime_config.json`
   - `configs/llm_config.json`
   - `configs/db_config.json`
   - `configs/vault_config.json`
   - `configs/blockchain_config.json`
   - `configs/log_config.json`

4. Run a flow:

   ```bash
   python run_demo.py structured
   python run_demo.py unstructured
   python run_demo.py both
   ```

Outputs are written under `output/<domain>/...`. Run logs are written under top-level `logs/`. Every run artifact receives a sidecar metadata file with `run_id`, `policy_id`, `policy_version`, and `policy_hash`.

Third-party responses are saved inside a gateway metadata wrapper. The gateway `run_id` is authoritative even when the external processor does not echo it or returns its own ID.

Optional environment overrides include `LLM_PROVIDER`, `LLM_ENDPOINT_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`, `LLM_BATCH_SIZE`, `LLM_DELAY_SECONDS`, `STRUCTURED_LAMBDA_NAME`, `UNSTRUCTURED_LAMBDA_NAME`, `MAPPING_STORE`, `MAPPING_ENCRYPTION_PROVIDER`, `MAPPING_DB_NAME`, `MAPPING_DB_USER`, `MAPPING_DB_PASSWORD`, `MAPPING_DB_HOST`, `MAPPING_DB_PORT`, `VAULT_ADDR`, `VAULT_ROOT_TOKEN`, `VAULT_UNSEAL_KEY`, `VAULT_TRANSIT_KEY`, `BLOCKCHAIN_ENABLED`, `BLOCKCHAIN_ANCHOR_DIGEST`, `BLOCKCHAIN_RPC_URL`, `BLOCKCHAIN_ACCOUNT_ADDRESS`, and `BLOCKCHAIN_PRIVATE_KEY`.

## LLM Provider

Dynamic LLM detection is routed through a provider-neutral HTTP interface. Configure the endpoint in `configs/llm_config.json` or through environment variables:

```json
{
  "provider": "http_json",
  "endpoint_url": "https://your-enterprise-llm-endpoint/chat/completions",
  "api_key_env": "LLM_API_KEY",
  "auth_header": "Authorization",
  "auth_scheme": "Bearer",
  "model": "your-model-name",
  "request_format": "openai_chat",
  "response_format": "openai_chat",
  "max_tokens": 2000,
  "temperature": 0.0,
  "batch_size": 25,
  "delay_seconds": 0.5
}
```

The endpoint may be an enterprise gateway, private hosted model, local model server, Azure-compatible deployment, or any other LLM endpoint that accepts the configured request format and returns the configured response format. The default request/response format is OpenAI-compatible chat JSON. A simple prompt/text format is also supported through `request_format: "prompt"` and `response_format: "text"`. Authentication is configurable through `auth_header` and `auth_scheme`; for example, Azure-style API-key endpoints can use `auth_header: "api-key"` and `auth_scheme: ""`.

Set `batch_size` and `delay_seconds` in `configs/llm_config.json` to tune large runs. The configured batch size is not a fixed code path: unstructured processing starts with that value and recursively splits only a batch whose provider response is malformed JSON. Throttling and connection failures continue to use retry backoff. Each LLM batch is logged with provider, batch number, record count, and character count so long-running runs show visible progress without printing PII.

For structured CSVs, `First Name`, `Last Name`, `Email`, `Contact Number`, and `Address` are static anonymization fields. Before LLM processing, the same deterministic stage removes emails, phone numbers, confidently formatted postal addresses, and the row's known full name from `Transaction Description`, `Loan Officer Remarks`, `Case Resolution Notes`, and `Customer Notes`. The partially anonymized free text is then sent to the configured LLM for contextual and ambiguous PII detection. The structured prompt explicitly covers health, employment, family, travel, organization and location context, reference identifiers, and common shorthand such as `cust`, `emp id`, `txn`, `pnr`, `appt`, `hosp`, `mob`, and `addr`. LLM results are applied within each batch while one run-level placeholder registry preserves deterministic reuse across batches. Policy-defined residual patterns then select only suspicious remaining rows for one smaller targeted recovery pass. LLM category aliases are normalized to stable labels such as `NAME`, `HEALTH_CONTEXT`, `EMPLOYEE_ID`, and `BOOKING_REFERENCE`.

For unstructured text, static regex and name-context patterns run first. Static matches are replaced in bounded regex passes instead of rescanning the full document once per detected value. Obvious employee, travel, transaction, medical and passport references are handled deterministically before the remaining text is sent to the configured LLM. Policy patterns also cover contextual names following honorifics or shorthand such as `cust`, `customer`, `pt`, and `emp`, healthcare organizations using `Centre` or `Center`, and company names in employee transaction contexts. The unstructured prompt detects contextual identifiers and sensitive context such as health context, employment context, family relationships, travel context, financial activity, locations, organizations, reference numbers, and other snippets tied to an individual or their activities. Existing placeholders are immutable during LLM replacement, preventing one detection from corrupting another placeholder. Recovery considers concrete residual validation findings and uses the configured provider batch size with the same automatic split behavior.

## External Processing

External processor behavior is controlled in `configs/runtime_config.json`:

```json
{
  "external_processing": {
    "mode": "lambda",
    "lambda_max_rows_without_override": 100,
    "on_large_input": "sftp_share",
    "handoff_root": "handoff",
    "wait_for_result": true,
    "poll_interval_seconds": 2,
    "result_timeout_seconds": 3600
  }
}
```

The prototype supports multiple external-processing integrations behind the same gateway boundary:

- `lambda`: invokes the existing AWS Lambda functions. This path remains unchanged.
- `sftp_share`: publishes an anonymized file and manifest to an SFTP-mounted or otherwise shared directory, waits for a correlated result manifest, and then rehydrates the returned data.
- `file_drop`: publishes a controlled handoff result without waiting.
- `skip`: deliberately skips external processing.

With the default settings, small structured runs call Lambda. Structured runs over the threshold use `sftp_share`, which is suitable for 5k/20k/50k demonstrations without coupling the gateway to the reference processor. Set `on_large_input` to `lambda` or increase the threshold to force Lambda for larger files.

The shared-folder contract is transport-neutral: `handoff_root` can be a local directory for the reference demonstration or a filesystem path mounted from an SFTP-managed share. The gateway does not import or start the external processor. Additional connectors can be added behind the same third-party client contract later. For numbered unstructured text, manifests and processor results report logical numbered records rather than physical nonblank lines.

Start the independent reference processor in a second PowerShell window before running a large structured flow:

```powershell
python run_external_processor.py
```

Then run the gateway normally:

```powershell
python run_demo.py structured
```

The exchange layout is:

```text
handoff/
  outbound/<domain>/<run_id>/
    anonymized.csv|txt
    request_manifest.json
  inbound/<domain>/<run_id>/
    processed_result.csv|txt
    response_manifest.json
```

The reference processor performs deterministic risk enrichment; it is not a pass-through. It has no mapping database or Vault access. Input and result files are written under temporary names and atomically renamed, and each manifest is published last as the readiness signal. A per-request atomic claim prevents two processor instances from handling the same request. The gateway accepts only a completed response with the expected `run_id`, domain, filename, and SHA-256 digest.

## Mapping Storage

Set `mapping_storage.mode` in `common/policy_lifecycle.json`, or override with `MAPPING_STORE`.

Allowed values:

- `local`: write mapping JSON files under `output/<domain>/mappings`
- `db`: write/read mappings from Postgres using `mappings_table` for structured runs and `mappings_unstructured_table` for unstructured runs
- `both`: write local JSON and Postgres

The whitepaper-reference default is `db` with Vault encryption. Set `mapping_store.mode` to `local` only for isolated troubleshooting.

Vault-backed encryption is the default mapping security model:

```bash
MAPPING_ENCRYPTION_PROVIDER=vault
VAULT_ADDR=http://127.0.0.1:8200
VAULT_ROOT_TOKEN=root
VAULT_UNSEAL_KEY=<unseal-key-if-needed>
VAULT_TRANSIT_KEY=pii-kek
```

When Vault is enabled, mapping values are encrypted before persistence. The prototype can auto-start a local Vault dev server, unseal it when an unseal key is provided, enable the transit engine, and create the configured transit key. Rehydration decrypts mappings inside the gateway boundary. For isolated development only, `MAPPING_ENCRYPTION_PROVIDER=none` stores plaintext mappings.

After installing dependencies, verify Vault setup with:

```bash
python -m common.vault_check
```

This command prepares Vault, ensures the transit key exists, encrypts a test value, decrypts it, and fails if the round-trip does not match.

## Rehydration Integrity

Rehydration is dependency-aware. A restored mapping value may expose another
placeholder created by an earlier anonymization stage, so the gateway performs
bounded replacement passes until no placeholders remain or no progress is
possible. Configure the safety ceiling in `configs/runtime_config.json`:

```json
{
  "rehydration": {
    "max_passes": 5
  }
}
```

`REHYDRATION_MAX_PASSES` provides an environment override. The gateway checks
reachable mapping dependencies for cycles and validates the result after the
last pass. Cycles, unresolved placeholders, and unexpected third-party
placeholders produce `rehydration_failed_<run_id>.json`; no partial rehydrated
output is published.

The primary `Rehydration` benchmark measures integrity checking, placeholder
restoration, and output construction. Mapping retrieval/Vault decryption is
reported separately, and `End-to-end rehydration` includes every phase.

## Policy And Regex Rules

Regex detection rules live in `common/privacy_policy.json` under `unstructured.regex_patterns`. There is no separate regex config file; the policy is the single source of truth.

## Blockchain Provenance

Blockchain settings are in `configs/blockchain_config.json`.

By default, the prototype creates a top-level run log, zips the run log into `logs_bundle_<timestamp>.zip`, hashes the zip with SHA-256, writes a local provenance ledger entry, and anchors the bundle digest to Ganache or another Web3-compatible local chain.

```json
{
  "enabled": true,
  "anchor_digest": true,
  "rpc_url": "http://127.0.0.1:7545",
  "account_address": "...",
  "private_key": "..."
}
```

Only the SHA-256 digest is anchored. Logs, mappings, payloads, and sensitive data are not written to chain.

Set `enabled` or `anchor_digest` to `false` in `configs/blockchain_config.json` only when you deliberately want to run without blockchain anchoring.

Verify provenance hashes after a run:

```bash
python -m provenance.verify_audit
python -m provenance.verify_audit --check-chain
```

## Logging

Logging is controlled by `configs/log_config.json`. The default is debug-level console and file logging under top-level `logs/`. The code logs run boundaries, mapping store choices, DB table usage, Lambda invocation boundaries, rehydration mapping counts, provenance bundle creation, and blockchain anchoring status. It does not log PII values, mappings, prompts, or raw LLM/Lambda responses.

## Metrics

Performance metrics are controlled by `runtime_flags.enable_performance_metrics` or `ENABLE_PERFORMANCE_METRICS`.

The anonymization metric includes static + dynamic anonymization only. It excludes config loading, mapping persistence, third-party processing, external handoff waiting, blockchain/provenance, configured LLM inter-batch delay, retry backoff sleeps, and unrelated waiting. Metrics also show static anonymization, LLM inference, dynamic replacement, and excluded provider waits separately.

Structured runs write `output/structured/validation/anonymization_validation_<run_id>.json`. This post-anonymization check reports residual obvious emails, phones, known row names and addresses, policy-defined contextual findings, and malformed placeholders without storing raw PII findings. Residual findings after the targeted recovery pass mark anonymization as `completed_with_warnings`.

Rehydration timing starts before placeholder integrity and mapping retrieval and ends after the rehydrated output is written. It therefore includes DB/local mapping retrieval, Vault-backed decryption, placeholder restoration, integrity checks, and output construction. Metrics show those phases separately. External processing and handoff waiting remain excluded.

`Placeholder restoration fidelity` measures whether returned placeholders were valid and resolved. For shape-preserving structured responses, the gateway additionally compares every original field and reports exact field fidelity, semantic numeric fidelity, changed fields, and changed rows. Structured CSVs are read as strings throughout the flow so textual representations such as `3708.0` are preserved.

For derived third-party outputs, omitted placeholders are allowed because the processor may return insights, aggregates, or summaries instead of the original record shape. Fidelity is `100%` when all placeholders that are returned are known and successfully rehydrated, and no unresolved placeholders remain.

Scope of the Reference Implementation

This repository provides the complete implementation of the Privacy Gateway itself. Components responsible for downstream third-party processing are intentionally excluded, as they are deployment-specific and may be implemented using custom applications, serverless functions, enterprise integration platforms, commercial products, or manual workflows. The gateway exposes the artifacts required for such integrations without prescribing a particular processing technology.



The repository includes gateway-side adapters for two third-party processing integration patterns: synchronous function invocation, represented by an AWS Lambda-compatible adapter, and asynchronous file handoff, represented by the file/SFTP-style handoff contract. The third-party processor implementations themselves are environment-specific and are not included; adopters can implement them using AWS Lambda, Java, .NET, Python, manual workflows, or other enterprise processing stacks.


By default, the demo uses the file/SFTP-style handoff path for external processing. This keeps the repository cloud-provider-neutral and avoids requiring an AWS runtime.

Supported external_processing.mode values include:

- sftp_share / file_handoff: writes anonymized payloads to the handoff folder and waits for an external processor response.
- file_drop: writes a handoff manifest and does not invoke a processor.
- skip: bypasses external processing for local inspection.
- lambda: invokes configured AWS Lambda functions synchronously. This mode requires boto3, AWS credentials, and deployed Lambda functions compatible with the gateway payload contract.

The repository includes the gateway-side Lambda adapter for reference, but boto3 is not installed by default. Add boto3 to your environment only if using Lambda mode.

By default, the demo uses the file/SFTP-style handoff path for external processing. This keeps the repository cloud-provider-neutral and avoids requiring an AWS runtime.

in this version, sftp_share implementation is left for end user. By default it will behave same as local handoff-folder contract. The naming allows adopters to map the pattern to SFTP shares, managed file transfer, object storage, manual drops, or other enterprise handoff mechanisms.


The runtime configuration includes a `lambda` section with default function-name placeholders. These values are not used unless `external_processing.mode` is set to `lambda`. The default mode is `sftp_share`, which uses the file handoff contract and does not require AWS, boto3, or deployed Lambda functions.

Note that currently file_handoff is an alias for sftp_share.

If `external_processing.mode` is changed to `lambda`, users must provide compatible AWS Lambda functions, configure AWS credentials, and install boto3 in their Python environment. The Lambda branch is retained as an optional synchronous integration adapter; the default repository configuration remains file-handoff based and cloud-provider-neutral. `lambda_max_rows_without_override` applies only in Lambda mode. It is ignored for the default `sftp_share` / `file_handoff` modes.


Default sftp_share mode may wait for an inbound response. Default mode uses file/SFTP-style handoff and waits for a response in the inbound handoff folder. For local inspection without an external processor, set external_processing.mode to "file_drop" or "skip".

Users must create:
sample-data/structured/sample_data.csv
sample-data/unstructured/sample_transactions.txt

Vault preparation is performed at runtime when mapping encryption is configured with `encryption_provider: "vault"`. The helper can validate/connect to Vault, optionally start a dev-mode Vault server, unseal it when configured, and ensure the transit engine/key exist.


Repository Scope


This repository contains the reference implementation of the Privacy Gateway itself. Runtime artifacts (datasets, logs, hand-off files, generated outputs and external processing implementations) are intentionally excluded from version control. These components are created during execution or are deployment-specific and should be supplied by adopters as appropriate.

