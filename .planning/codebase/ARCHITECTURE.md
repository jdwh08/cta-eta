# Architecture

**Analysis Date:** 2026-01-17

## Pattern Overview

**Overall:** Modular API client layer with data serialization (ML pipeline Phase 1: Data Collection)

**Key Characteristics:**
- Independent API client modules (no cross-dependencies)
- Retry-based resilience for external API calls
- Environment-based configuration via `.env`
- Early-stage ML pipeline: data collection → processing (planned) → modeling (planned)
- Script-based execution (no package structure yet)

## Layers

**API Client Layer:**
- Purpose: Raw HTTP clients for external APIs
- Contains: Function-based API wrappers with retry decorators
- Location: `src/cta_eta/api_*.py` modules
- Depends on: httpx, stamina, dotenv
- Used by: Manual execution or future data collection daemon
- Pattern: `@stamina.retry` decorator for resilience (10 attempts on HTTP errors)

**Data Parsing Layer:**
- Purpose: Transform raw JSON responses to Python dictionaries/lists
- Contains: JSON parsing and dictionary construction
- Location: Within API client modules (`api_stations_weather.py`, `api_train_position.py`)
- Depends on: API Client Layer
- Used by: Data serialization layer

**Data Serialization Layer:**
- Purpose: Write parsed data to files
- Contains: CSV and JSON file writers
- Location: Module-level code in `api_stations_weather.py` (CSV), `api_track_shape.py` (JSON cache)
- Depends on: Data Parsing Layer
- Used by: Future data processing pipeline

**Development Tools Layer:**
- Purpose: Code quality and linting orchestration
- Contains: `devtools/lint.py` - runs ruff, basedpyright, codespell
- Location: `devtools/` directory
- Depends on: subprocess, rich, funlog
- Used by: CI pipeline and local development

**Future Layers (Planned):**
- Data Processing: Merge, transform, augment raw data with geospatial features
- Feature Engineering: Track distance calculations, velocity, ETA labels
- Model Training: LightGBM baseline → Graph Neural Networks

## Data Flow

```
External APIs (CTA, Chicago Data, Weather Services)
    ↓
[API Client Functions] → Raw JSON Response
    ↓
[Data Parsers] → Structured Dictionaries/Lists
    ↓
[CSV/JSON File Writers] → stations_weather.csv, cta_track_shape.json
    ↓
[Future: Data Processing] → Merged enriched datasets
    ↓
[Future: Model Training] → ETA predictions
```

**Request Lifecycle (API Call):**
1. User/daemon executes API module: `python src/cta_eta/api_stations_weather.py`
2. Module loads environment variables: `load_dotenv()`
3. HTTP client initialized: `httpx.Client()`
4. API function called with retry decorator: `@stamina.retry(on=httpx.HTTPStatusError, attempts=10)`
5. HTTP request sent with parameters (API key, query params)
6. Response status checked: `.raise_for_status()`
7. JSON parsed: `.json()`
8. Data transformed to Python dicts/lists
9. Written to file (CSV or JSON)

**State Management:**
- Stateless: No persistent state between executions
- File-based output: All data written to disk immediately
- Future: Continuous polling with rolling buffers and periodic parquet writes

## Key Abstractions

**Retry Pattern:**
- Purpose: Resilient API calls with automatic retries
- Implementation: `@stamina.retry(on=httpx.HTTPStatusError, attempts=10)`
- Location: All API functions in `src/cta_eta/api_*.py`
- Example: `src/cta_eta/api_train_position.py:20`, `src/cta_eta/api_stations_weather.py:20,68`

**Environment Variable Injection:**
- Purpose: Secure API key management
- Implementation: `os.getenv("CTA_API_KEY")` with `load_dotenv()`
- Location: All API modules
- Example: `src/cta_eta/api_train_position.py:25`

**Shared HTTP Client:**
- Purpose: Connection pooling and reuse
- Implementation: `client = httpx.Client()` at module level
- Location: All API modules
- Example: `src/cta_eta/api_train_position.py:15`

**Type-Hinted Function Signatures:**
- Purpose: Static type checking and IDE support
- Implementation: Modern Python 3.13+ syntax (`list[dict[str, str | float]]`)
- Location: All function definitions
- Example: `def get_stations() -> list[dict[str, str | float]]:`

**Constants with Final:**
- Purpose: Immutable configuration values
- Implementation: `CTA_LINES: Final[list[str]]`
- Location: `src/cta_eta/api_train_position.py:17`

## Entry Points

**Development/Testing:**
- Location: `devtools/lint.py`
- Triggers: Manual execution (`uv run python devtools/lint.py`) or CI/CD
- Responsibilities: Run linting pipeline (ruff, basedpyright, codespell)

**Data Collection (Manual):**
- Location: `src/cta_eta/api_stations_weather.py` (module-level execution code)
- Triggers: Manual execution (`python src/cta_eta/api_stations_weather.py`)
- Responsibilities: Fetch station + weather data, write `stations_weather.csv`

**CI/CD:**
- Location: `.github/workflows/ci.yml`
- Triggers: Push/PR to main/master branches
- Responsibilities: Install dependencies, run linting, run tests

**Future Entry Point:**
- Continuous polling daemon running on VPS/cloud compute
- Scheduled cron jobs for weather updates
- Parquet file outputs to cloud storage

## Error Handling

**Strategy:** Retry with exponential backoff, raise exceptions to caller

**Patterns:**
- API failures: `@stamina.retry(on=httpx.HTTPStatusError, attempts=10)` retries 10 times
- HTTP errors: `.raise_for_status()` raises exception if not 2xx
- Subprocess errors: Try/except with specific exception catching (`subprocess.CalledProcessError`, `KeyboardInterrupt`)
- Example: `devtools/lint.py` catches and reports subprocess errors with rich formatting

## Cross-Cutting Concerns

**Logging:**
- Console output for development (`devtools/lint.py` uses rich.print)
- Future: Structured logging for production data collection

**Validation:**
- Type hints for static analysis (`basedpyright`)
- No runtime validation currently

**Configuration:**
- Environment variables (`.env` file)
- Module-level constants (`CTA_LINES`, `train_position_url`)

---

*Architecture analysis: 2026-01-17*
*Update when major patterns change*
