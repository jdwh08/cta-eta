# Technology Stack

**Analysis Date:** 2026-01-22

## Languages

**Primary:**
- Python 3.13+ - All application code (`pyproject.toml`, `.python-version`)

**Secondary:**
- None (pure Python project)

## Runtime

**Environment:**
- Python 3.13+ - `pyproject.toml` requires-python = ">=3.13,<4.0"
- WSL Debian Linux environment

**Package Manager:**
- uv - Modern Python package manager (`pyproject.toml`, `uv.lock` 341MB lockfile)
- Lockfile: `uv.lock` present (comprehensive dependency resolution)

## Frameworks

**Core:**
- asyncio - Built-in async/await framework for concurrent I/O operations
- httpx>=0.28.1 - Async HTTP client library for all API calls

**Testing:**
- pytest>=9.0.2 - Primary test framework
- pytest-asyncio>=1.3.0 - Async test support
- pytest-cov>=7.0.0 - Coverage reporting
- pytest-sugar>=1.1.1 - Enhanced test output
- pytest-mock>=3.15.1 - Mocking utilities

**Build/Dev:**
- hatchling - Build backend for Python package distribution
- ruff>=0.14.13 - Fast Python linter and formatter (replaces Black + flake8)
- basedpyright>=1.37.1 - Static type checker (Pyright fork)

## Key Dependencies

**Critical:**
- pandas>=2.3.3 - Data manipulation and DataFrame operations (`weather_merger.py`)
- pyarrow>=22.0.0 - Apache Parquet format for efficient data storage (`storage.py`)
- stamina>=25.2.0 - Retry decorator with exponential backoff (all API clients)
- httpx>=0.28.1 - Async HTTP client with connection pooling
- aiometer>=1.0.0 - Rate limiting for async operations (`weather_daemon.py`)

**Infrastructure:**
- fsspec>=2026.1.0 - Unified filesystem interface for local/cloud storage
- s3fs>=2026.1.0 - AWS S3 backend for fsspec
- gcsfs>=2026.1.0 - Google Cloud Storage backend for fsspec
- dotenv>=0.9.9 - Environment variable loading from `.env` files

## Configuration

**Environment:**
- Hybrid TOML + environment variables approach
- `config.toml` for operational settings (version-controlled)
- `.env` for secrets (git-ignored, template: `.env.template`)
- Merged via `src/cta_eta/data_collection/config.py`

**Build:**
- `pyproject.toml` - Project metadata, dependencies, tool configuration
- `.pre-commit-config.yaml` - Pre-commit hooks with ruff

## Platform Requirements

**Development:**
- Linux/WSL Debian (tested environment)
- Any platform supporting Python 3.13+
- No external runtime dependencies (database, Docker, etc.)

**Production:**
- Long-running daemon processes (24/7 operation)
- Cloud storage backend (local filesystem, AWS S3, or Google Cloud Storage)
- Configured via `config.toml` backend selection

---

*Stack analysis: 2026-01-22*
*Update after major dependency changes*
