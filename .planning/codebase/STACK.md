# Technology Stack

**Analysis Date:** 2026-02-28

## Languages

**Primary:**
- Python 3.13+ - All source code and data collection pipeline

## Runtime

**Environment:**
- Python 3.13 (see `.python-version`)
- WSL2 Debian environment

**Package Manager:**
- UV (modern Python package manager)
- Lockfile: `uv.lock` present

## Frameworks

**Core:**
- HTTPx 0.28.1+ - Async HTTP client for all API integrations
- Pandas 2.3.3+ - Data manipulation and analysis
- PyArrow 22.0.0+ - Apache Arrow library for IPC journal and Parquet serialization

**Retry & Rate Limiting:**
- Stamina 25.2.0+ - Exponential backoff with jitter for transient failures
- Tenacity 9.1.2+ - Retry decorator library (complementary)
- Aiometer 1.0.0+ - Rate limiting and concurrency control

**Storage:**
- fsspec 2026.1.0+ - Filesystem abstraction layer (local/cloud)
- s3fs 2026.1.0+ - S3-compatible storage via fsspec
- gcsfs 2026.1.0+ - Google Cloud Storage via fsspec

**Utilities:**
- python-dotenv 0.9.9+ - Environment variable loading from `.env`

**Testing:**
- pytest 9.0.2+ - Test runner
- pytest-cov 7.0.0+ - Coverage reporting
- pytest-sugar 1.1.1+ - Enhanced test output formatting
- pytest-mock 3.15.1+ - Mocking support
- pytest-asyncio 1.3.0+ - Async test support

**Build/Dev:**
- Ruff 0.14.13+ - Fast Python linter and formatter
- BasedPyright 1.37.1+ - Static type checker
- Hatchling - Build backend
- uv-dynamic-versioning - Git-based version management
- Codespell 2.4.1+ - Spell checker

**Documentation:**
- mkdocs 1.6.1+ - Documentation generator
- mkdocs-material 9.7.1+ - Material theme for MkDocs
- Marimo 0.19.4+ - Interactive notebooks (optional EDA dependency)
- Rich 14.2.0+ - Rich terminal output and formatting
- funlog 0.2.1+ - Functional logging

## Key Dependencies

**Critical:**
- HTTPx - All external API communication uses async HTTPx client for connection pooling
- PyArrow - Journal (IPC) and Parquet serialization; core to data collection pipeline
- Stamina - Retry logic with exponential backoff for transient API failures
- Aiometer - Rate limiting enforcement to stay within API provider quotas

**Infrastructure:**
- fsspec/s3fs/gcsfs - Cloud storage abstraction; supports local filesystem, AWS S3, Google Cloud Storage, and Azure Blob Storage
- python-dotenv - Secrets management via `.env` (git-ignored)

## Configuration

**Environment:**
- Hybrid configuration: `config.toml` for operational settings (version-controlled) + `.env` for secrets (git-ignored)
- See `.env.template` for required variables
- Configuration loading: `src/cta_eta/data_collection/config.py`

**Build:**
- `pyproject.toml`: Project metadata, dependencies, tool configuration
- `uv.lock`: Pinned dependency versions
- `.pre-commit-config.yaml`: Git hooks for ruff linting and formatting
- Ruff configuration: Line length 88, src=["src"], select all rules with specific ignores for formatter compatibility

## Platform Requirements

**Development:**
- Python 3.13+
- UV package manager
- Linux/WSL2 environment
- Git (for version management and pre-commit hooks)

**Production:**
- Python 3.13+
- Cloud storage credentials (AWS/GCS/Azure) if using cloud backend
- API keys (CTA, NWS, OpenWeatherMap, Chicago Data Portal, Mailjet)
- 50+ GB storage capacity for data collection journals and compacted Parquet

---

*Stack analysis: 2026-02-28*
