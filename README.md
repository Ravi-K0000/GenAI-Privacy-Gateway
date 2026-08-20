# GenAI-Privacy-Gateway
A reference implementation demonstrating a privacy-preserving architecture for enterprise data sharing with external parties through policy-driven anonymization and controlled rehydration.
The project demonstrates how enterprise data can be processed outside an organization's trust boundary without exposing original sensitive information. Before data leaves the enterprise, sensitive values are identified and transformed using deterministic policy rules, together with GenAI-assisted contextual detection and replacement. Deterministic placeholders are used to preserve referential consistency.

The repository accompanies the research paper **"A GenAI-Assisted Privacy Gateway for Secure Third-Party Data Processing"** and contains two implementations designed for different audiences.

---

## Key Capabilities
- Policy-driven anonymization for structured and unstructured enterprise data
- GenAI-assisted contextual detection along with deterministic pattern matching
- Deterministic placeholder generation with reversible rehydration
- Provider-neutral LLM integration
- Configurable mapping storage and protection
- External processing abstraction through well-defined gateway contracts
- Performance measurement and processing metrics
- Provenance generation with optional blockchain-based audit anchoring

---

# Repository Structure

```
GenAI-Privacy-Gateway/
│
├── quick-demo/
│     Self-contained demonstration with local mock components
│
└── reference-implementation/
      Working implementation accompanying the paper
```

---

## Quick Demo
The **Quick Demo** provides a lightweight demonstration of the Privacy Gateway concept.

It is intended for readers who want to understand the end-to-end flow in a few minutes without configuring databases, LLM endpoints, Vault, or blockchain infrastructure.

### Highlights
- Self-contained execution
- Local mock processing
- Local mapping store
- Policy-driven anonymization
- Mock external processing
- Mapping-based rehydration
- Local provenance generation
- Minimal setup requirements

**Recommended for**
- First-time visitors
- Technical demonstrations
- Architecture walkthroughs
- Concept validation
- Presentations

---

## Reference Implementation
The Reference Implementation provides a working implementation of the Privacy Gateway architecture described in the accompanying paper.

Unlike the Quick Demo, it demonstrates the architectural components required for a configurable enterprise privacy gateway, including policy lifecycle management, provider-neutral LLM integration, configurable mapping storage, secure rehydration, provenance generation, and performance evaluation.

The implementation intentionally focuses on the gateway itself. Infrastructure provisioning and deployment-specific processor implementations are excluded so that the architecture remains independent of providers, programming languages, and enterprise platforms.

### Highlights
- Gateway processing pipeline
- Structured and unstructured processing
- Policy lifecycle management
- Provider-neutral LLM interface
- Configurable mapping storage
- Secure mapping protection
- Dependency-aware multi-pass rehydration
- Provenance and audit generation
- Optional blockchain digest anchoring
- Performance evaluation and runtime metrics

**Recommended for**
- Researchers
- Enterprise architects
- Security and privacy engineers
- Platform engineers
- Readers of the accompanying paper

---

# Companion Paper
This repository accompanies the research paper:

**A GenAI-Assisted Privacy Gateway for Secure Third-Party Data Processing**
The paper introduces a privacy gateway architecture that enables organizations to leverage third-party services while maintaining control over sensitive information. The proposed approach combines deterministic policy enforcement with GenAI-assisted contextual detection, reversible placeholder generation, secure mapping management, controlled rehydration, and provenance tracking.  

**Publication**
- SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7293198
- Zenodo archive: https://doi.org/10.5281/zenodo.21454148

---

# Getting Started
If you are exploring the project for the first time:

➡ **Start with the Quick Demo**
It requires minimal setup and demonstrates the complete privacy gateway lifecycle using local mock components.

If you are interested in the implementation described in the research paper:

➡ **Explore the Reference Implementation**
It provides the configurable gateway architecture, implementation details, runtime configuration, and evaluation framework.

---

# Citation

**Paper**
Ravi Kumar, *A GenAI-Assisted Privacy Gateway for Secure Third-Party Data Processing*, SSRN, 2026.  
https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7293198

**Reference implementation**
Ravi, K. (2026). *GenAI Privacy Gateway* (Version 1.0.1) [Computer software]. Zenodo.  
https://doi.org/10.5281/zenodo.21454148

---

# License

This project is licensed under the Apache License 2.0. See the LICENSE file for details.
