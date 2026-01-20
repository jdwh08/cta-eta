# Technology Stack

**Analysis Date:** 2026-01-19

## Languages

**Primary:**
- Python 3.13+ - All application code (`pyproject.toml` line 17, `.python-version`)

**Secondary:**
- TOML - Configuration files (`config.toml`, `pyproject.toml`)
- Markdown - Documentation (`README.md`, `CLAUDE.md`, `.planning/*.md`)

## Runtime

**Environment:**
- Python 3.13+ required (modern features)
- WSL Debian Linux (per `CLAUDE.md`)

**Package Manager:**
- UV - Modern Python package manager
- Lockfile: `uv.lock` (269 KB dependency lockfile)

## Frameworks

**Core:**
- None (vanilla Python data pipeline)

**Testing:**
- pytest 9.0.2 - Unit testing framework (`pyproject.toml` line 73)
- pytest-cov 7.0.0 - Code coverage (`pyproject.toml` line 74)
- pytest-sugar 1.1.1 - Enhanced test output (`pyproject.toml` line 75)
- pytest-mock 3.15.1 - Mocking utilities (`pyproject.toml` line 76)

**Build/Dev:**
- Hatchling - Build backend with UV dynamic versioning
- Ruff 0.14.13 - Fast linter and formatter (`pyproject.toml` lines 114-162)
- basedpyright 1.37.1 - Static type checker (`pyproject.toml` line 79)
- codespell 2.4.1 - Spell checker (`pyproject.toml` line 69)

## Key Dependencies

**HTTP Client:**
- httpx 0.28.1+ - Async-capable HTTP client for API requests

**Retry/Resilience:**
- stamina 25.2.0+ - Retry decorator with exponential backoff
- tenacity 9.1.2+ - Alternative retry library

**Data Storage:**
- pyarrow 22.0.0+ - Parquet file format with Snappy compression
- fsspec 2026.1.0+ - Filesystem abstraction (cloud-agnostic)
- gcsfs 2026.1.0+ - Google Cloud Storage backend
- s3fs 2026.1.0+ - AWS S3 storage backend

**Configuration:**
- python-dotenv 0.9.9+ - Environment variable loading from `.env` files
- tomllib - Built-in TOML parser (Python 3.11+)

**Utilities:**
- rich 14.2.0+ - Terminal output formatting
- funlog 0.2.1+ - Logging utilities

**Documentation:**
- mkdocs 1.6.1+ - Documentation generation
- mkdocs-material 9.7.1+ - Material theme for documentation

## Configuration

**Environment:**
- `.env.template` - Template for API keys and secrets (CTA_API_KEY, CHIDATA_APP_TOK, etc.)
- Environment variables loaded via python-dotenv

**Application:**
- `config.toml` - Operational settings (intervals, retry logic, storage backends, logging)
- TOML + environment variable merging pattern

**Build:**
- `pyproject.toml` - All development, build, linting, and test configuration
- `.pre-commit-config.yaml` - Ruff linting and formatting hooks

## Platform Requirements

**Development:**
- Any platform with Python 3.13+ (Linux, macOS, Windows)
- WSL Debian recommended (per `CLAUDE.md`)
- UV package manager installed

**Production:**
- Cloud VPS (Oracle Cloud Infrastructure, AWS EC2/Lightsail, GCP Compute Engine)
- 24/7 uptime required for continuous data collection
- Object storage (S3/GCS) for Parquet file backups

---

*Stack analysis: 2026-01-19*
*Update after major dependency changes*
