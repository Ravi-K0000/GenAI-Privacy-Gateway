# Overview

This reference implementation accompanies the paper **"A GenAI-Assisted Privacy Gateway for Secure Third-Party Data Processing"** and demonstrates a configurable implementation of the proposed gateway architecture. It shows how sensitive enterprise data can be transformed before leaving the enterprise trust boundary, processed by external systems, and selectively restored upon return, while maintaining referential consistency, auditability, and policy-driven control.

Unlike the Quick Demo, the reference implementation includes configurable gateway components such as policy-driven anonymization, GenAI-assisted contextual detection, deterministic placeholder generation, configurable mapping protection, secure rehydration, provenance generation, and performance evaluation. The implementation is designed to demonstrate the complete gateway processing lifecycle, while staying independent of any specific downstream processor, AI provider, or deployment platform.

To maintain a clear architectural boundary, this repository intentionally focuses on the privacy gateway itself. Infrastructure provisioning, production deployment artefacts, and downstream processing implementations are intentionally excluded, allowing the gateway to remain technology-neutral and adaptable.

# Scope of the Reference Implementation

The reference implementation includes:

- Policy-driven detection and anonymization for structured and unstructured data
- GenAI-assisted contextual identification of sensitive information
- Deterministic placeholder generation and replacement
- Configurable mapping protection using a database and secrets vault
- Secure rehydration with dependency-aware placeholder restoration
- Provenance generation and audit logging
- Runtime metrics and performance evaluation
- Sample structured and unstructured datasets for demonstration and validation
- Reference configuration files

The following components are intentionally outside the scope of this repository:

- Infrastructure provisioning (CloudFormation, Terraform, Kubernetes, etc.)
- Production deployment configurations
- Downstream processing implementations (AWS Lambda, Java, .NET, Python, or other consumer applications)
- Enterprise-specific integrations and custom connectors
- Authentication, authorization, and operational security controls required for production deployments

# Architecture Overview

At a high level, the gateway performs the following sequence of operations:

1. Load runtime configuration and anonymization policies.
2. Detect sensitive information using deterministic policy rules together with GenAI-assisted contextual identification.
3. Replace sensitive values with deterministic placeholders while securely persisting the associated mappings.
4. Transfer the anonymized dataset to an external processor for downstream business processing.
5. Validate the returned data and selectively restore original values through controlled rehydration.
6. Produce the final output together with audit logs, provenance records, and execution metrics.

The implementation supports both structured datasets (CSV) and unstructured text documents through a common processing model while allowing each processing pipeline to apply data-type specific detection and transformation strategies.

The gateway itself remains independent of downstream processors. Any external application capable of preserving placeholder integrity can participate in the processing workflow, regardless of implementation language, runtime platform, or deployment model.

For a detailed discussion of the gateway architecture, design rationale, and evaluation, refer to the accompanying paper.

# Repository Structure

The reference implementation is organized into modular components that separate configuration, policy management, anonymization, rehydration, provenance, and runtime execution responsibilities.

```text
reference-implementation/
│
├── common/                 Shared policy and utility components
├── configs/                Runtime, provider, database and vault configuration
├── structured/             Structured data anonymization pipeline
├── unstructured/           Unstructured text anonymization pipeline
├── rehydration/            Placeholder restoration engine
├── provenance/             Provenance generation and audit components
├── sample-data/            Sample structured and unstructured datasets
├── handoff/                External processing exchange directory
├── logs/                   Runtime logs and execution reports
├── output/                 Generated processing results
│
├── run_demo.py             Gateway execution entry point
├── requirements-demo.txt   Python dependencies
└── README.md
```

## Component Description

| Component | Purpose |
|-----------|---------|
| **common** | Shared utilities, policy loading, configuration handling, and reusable processing components. |
| **configs** | Runtime configuration, provider settings, anonymization policies, database configuration, and vault integration. |
| **structured** | Processing pipeline for structured datasets such as CSV files. |
| **unstructured** | Processing pipeline for free-text and document-based content. |
| **rehydration** | Secure restoration of original values using protected mappings and dependency-aware placeholder replacement. |
| **provenance** | Generation of audit artefacts, integrity verification, and provenance records. |
| **sample-data** | Representative structured and unstructured sample datasets for demonstration, validation, and familiarization with the expected gateway input formats. |
| **handoff** | Exchange location representing data transferred to and returned from downstream processing. |
| **logs** | Runtime execution logs generated during gateway processing. |
| **output** | Final anonymized, processed, and rehydrated datasets together with generated artefacts. |	


# Processing Pipeline

The reference implementation follows a deterministic processing pipeline that transforms sensitive enterprise data before external processing and selectively restores original values upon completion. The overall workflow remains consistent for both structured and unstructured datasets, while allowing each processing path to apply data-type specific detection strategies.

## 1. Configuration and Policy Loading

The execution begins by loading the runtime configuration and anonymization policy. These configurations define the processing mode, GenAI provider settings, mapping protection mechanisms, provenance options, and sensitive data categories to be identified during processing.

## 2. Sensitive Data Identification

Input data is analysed by combining deterministic policy rules with GenAI-assisted contextual identification.

Deterministic rules identify explicitly defined sensitive values such as names, email addresses, phone numbers, account numbers, and other policy-defined entities. GenAI-assisted analysis identifies contextual or implicit references.

## 3. Placeholder Generation

Each detected sensitive value is replaced with a deterministic placeholder while preserving referential consistency throughout the dataset.

Original values are never exposed to external processors. Instead, protected mappings between original values and generated placeholders are securely persisted using the configured database and secrets management components.

## 4. External Processing

The anonymized dataset is transferred to an external processing component for downstream business operations.

The gateway does not impose any technology requirements on the downstream processor. Any application capable of preserving placeholder integrity may participate in the processing workflow, including enterprise applications, AI services, cloud functions, or custom business services.

## 5. Controlled Rehydration

Following external processing, the returned dataset is validated before rehydration begins.

Protected mappings are retrieved and original values are selectively restored using dependency-aware placeholder replacement to ensure accurate reconstruction of the original data.

## 6. Provenance and Audit Generation

Throughout execution, the gateway records processing metadata, execution metrics, provenance information, and audit artefacts required to support traceability and processing verification.

Depending on the runtime configuration, provenance records may also be integrity protected through cryptographic hashing and optional external anchoring mechanisms.

## 7. Output Generation

The gateway produces the final restored dataset together with execution logs, provenance records, runtime metrics, and processing artefacts generated during execution.


# Prerequisites

Before executing the gateway, ensure the following prerequisites are available:

- Python 3.11 or later
- Access to a GenAI service operating within the enterprise trust boundary
- Configured database for mapping persistence
- Configured secrets vault for encryption key management
- Ganache (optional, when blockchain provenance anchoring is enabled)
- Git (optional, for cloning the repository)

Python package dependencies are listed in `requirements-demo.txt` and can be installed using:

```bash
pip install -r requirements-demo.txt
```


# Configuration

The gateway is designed to be configuration-driven. Runtime behaviour is controlled through a collection of configuration files that define processing policies, provider settings, database connections, vault integration, and provenance options. 

Before running the gateway, review the configuration files under the `configs/` directory and update provider credentials, database connections, vault settings, and runtime options as appropriate for your environment.

## Runtime Configuration

The runtime configuration defines the overall execution behaviour of the gateway, including processing mode, logging options, output locations, and runtime feature selection.

## Provider Configuration

Provider configuration specifies the GenAI service used for contextual sensitive data identification together with any provider-specific connection settings required by the selected deployment.

## Anonymization Policy

The anonymization policy defines the categories of sensitive information to be detected, the replacement strategy to be applied, and processing rules for both structured and unstructured data.

## Database Configuration

Database configuration controls the persistence of protected placeholder mappings required to support secure rehydration following external processing.

## Vault Configuration

Vault configuration defines the secure storage mechanism used to protect encryption keys and sensitive gateway secrets.

## Provenance Configuration

Provenance configuration controls the generation of audit records, integrity verification, and optional blockchain anchoring for processing provenance.


# Running the Gateway

After completing the required configuration, the gateway can be executed from the reference implementation root directory.

```bash
python run_demo.py structured
```

For unstructured data:

```bash
python run_demo.py unstructured
```

During execution, the gateway performs the following high-level operations:

1. Loads the runtime configuration and anonymization policy.
2. Processes structured or unstructured input data.
3. Identifies sensitive information using deterministic rules together with GenAI-assisted contextual detection.
4. Replaces sensitive values with deterministic placeholders and securely persists the associated mappings.
5. Transfers the anonymized data for external processing.
6. Validates the returned dataset and performs controlled rehydration.
7. Generates the final output together with execution logs, provenance records, and runtime metrics.

Upon successful execution, processing artefacts are written to the configured output directories for further inspection and validation.

## Sample Datasets

The repository includes representative structured and unstructured sample datasets under the `sample-data/` directory to simplify initial setup and evaluation.

The datasets are organized into separate `structured/` and `unstructured/` directories corresponding to the two supported processing pipelines.

These datasets are intended to allow users to execute both processing pipelines with minimal configuration. They are provided for demonstration purposes only and do not represent the benchmark datasets used for the performance evaluation presented in the accompanying paper.


# External Processing Contract

Once anonymization is complete, the transformed dataset may be processed by any external application capable of preserving placeholder integrity.

Examples include:

- Third-party service providers
- Cloud-based processing platforms
- Custom business applications
- Batch processing pipelines
- External AI services

To ensure successful rehydration, downstream processors are expected to satisfy the following requirements:

- Preserve generated placeholders without modification.
- Do not create, remove, or alter placeholder identifiers.
- Return the processed dataset using the same placeholder values received from the gateway.
- Treat placeholder values as opaque identifiers rather than business data.

Failure to preserve placeholder integrity may prevent successful rehydration or result in incomplete restoration of sensitive values.

The gateway intentionally places no restrictions on the implementation language, deployment model, or runtime platform of downstream processors, provided the placeholder preservation requirements are satisfied.


# Runtime Output

Upon successful execution, the gateway produces a collection of processing artefacts that support validation, auditability, and secure rehydration.

Depending on the selected processing mode and runtime configuration, the generated outputs may include:

- Anonymized datasets prepared for external processing
- Processed datasets returned by downstream processors
- Rehydrated datasets with original sensitive values restored
- Runtime execution logs
- Provenance records and integrity artefacts
- Performance metrics and execution statistics

The exact set of generated artefacts depends on the configured runtime options and enabled gateway features.

Output files are written to the configured output directories and may be retained for validation, benchmarking, audit, or demonstration purposes.


# Security Considerations

This reference implementation demonstrates the architectural concepts presented in the accompanying paper and is intended for evaluation, experimentation, and technology validation.

Organizations adopting the gateway should review and strengthen the implementation to align with their operational security requirements.

In particular, production deployments should consider:

- Secure management of database credentials and encryption keys.
- Enterprise authentication and authorization mechanisms.
- Network security and encrypted communication channels.
- Secrets management using enterprise-grade vault solutions.
- Infrastructure monitoring, logging, and operational alerting.
- Backup, disaster recovery, and high-availability strategies.
- Compliance with organizational governance and regulatory requirements.


# Design Assumptions and Limitations

This reference implementation has been developed to demonstrate the architectural concepts presented in the accompanying paper. The implementation assumes a trusted enterprise environment in which gateway configuration, mapping persistence, secrets management, and GenAI-assisted detection operate within the enterprise trust boundary.

The implementation further assumes that downstream processors preserve placeholder integrity throughout processing. Modification or removal of generated placeholders may prevent successful rehydration.

The reference implementation intentionally excludes several aspects that would typically be addressed as part of an enterprise production deployment, including:

- Infrastructure provisioning and deployment automation.
- Enterprise-specific integrations and custom connectors.
- Operational monitoring, scaling, and high-availability configurations.
- Authentication, authorization, and organization-specific security controls.

These design choices are intentional and allow the implementation to remain focused on demonstrating the gateway architecture while remaining adaptable to different enterprise platforms, deployment models, and downstream processing technologies.

