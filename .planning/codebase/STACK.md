# Technology Stack

**Analysis Date:** 2026-01-17

## Languages

**Primary:**
- Python 3.13 - All application code (`.python-version`, `pyproject.toml`)

**Secondary:**
- Not detected

## Runtime

**Environment:**
- Python 3.13 (`.python-version`)
- WSL Debian environment (`CLAUDE.md`)

**Package Manager:**
- UV - Modern Python package manager
- Lockfile: `uv.lock` present (version = 1, revision = 3, requires-python = ">=3.13")

## Frameworks

**Core:**
- None (direct HTTP client usage, no web framework)

**Testing:**
- pytest - Configured in CI but no test files yet
- Framework ready for future test implementation

**Build/Dev:**
- ruff v0.1.11 - Fast Python linter and formatter (`.pre-commit-config.yaml`)
- basedpyright - Type checking (`devtools/lint.py`)
- codespell - Spell checking with `--write-changes` flag (`devtools/lint.py`)
- pre-commit v4.5.0 - Git hook management (`.pre-commit-config.yaml`)

## Key Dependencies

**Critical:**
- httpx>=0.28.1 - Modern async HTTP client (`pyproject.toml`, used in `src/cta_eta/api_*.py`)
- stamina>=25.2.0 - Retry decorator for API resilience (`pyproject.toml`, `@stamina.retry` in API modules)
- dotenv>=0.9.9 - Environment variable management (`pyproject.toml`, loads `.env` file)
- tenacity>=9.1.2 - Additional retry/resilience library (declared but not currently used)

**Infrastructure:**
- python-dotenv - Loads environment variables from `.env` (`uv.lock`)
- anyio>=4.12.1 - Async I/O compatibility layer (`uv.lock`)
- certifi>=2026.1.4 - SSL/TLS certificates (`uv.lock`)
- h11, httpcore, idna - HTTP protocol libraries (dependencies of httpx)

## Configuration

**Environment:**
- `.env` file - API keys and secrets (not in git)
  - `CTA_API_KEY` - CTA Train Tracker API
  - `CHIDATA_APP_TOK` - Chicago Open Data Portal token
  - `CHIDATA_APP_SECRET` - Chicago Open Data Portal secret
- `load_dotenv()` used in all API modules

**Build:**
- `pyproject.toml` - Project metadata, dependencies, Python version requirement
- `.pre-commit-config.yaml` - Pre-commit hooks configuration
- `.python-version` - Python version enforcement (3.13)
- `devtools/lint.py` - Linting orchestrator

## Platform Requirements

**Development:**
- WSL Debian (any platform with Python 3.13+)
- UV package manager
- Git with pre-commit hooks

**Production:**
- Planned: VPS/cloud compute (Oracle Cloud, AWS EC2/Lightsail, or GCP Compute Engine)
- Planned: Object storage (S3 or equivalent) for data backup
- Continuous polling daemon for data collection

---

*Stack analysis: 2026-01-17*
*Update after major dependency changes*
