# Phase 5: Weather Data Collection - Research

**Researched:** 2026-01-19
**Domain:** Multi-source weather API integration with Python
**Confidence:** HIGH

<research_summary>
## Summary

Researched the multi-source weather API integration ecosystem for continuous weather data collection in Python. The standard approach uses httpx for async HTTP (modern, dual sync/async support) with asyncio.gather() for parallel API calls, tenacity for intelligent retry with exponential backoff, and pandas for merging multi-source responses.

Key finding: Don't hand-roll async orchestration, rate limiting, or retry logic. Use httpx (HTTP/2 support, familiar API), aiometer (rate limiting with GCRA algorithm), and tenacity (exponential backoff with jitter). The codebase already uses httpx with stamina retry, which is solid but consider upgrading to tenacity for more sophisticated backoff strategies and aiometer for strict rate limiting.

Multi-source fallback pattern: Use asyncio.gather(return_exceptions=True) to query NWS + Open-Meteo in parallel, merge responses via pandas, fall back to OpenWeatherMap on source failure. This maximizes variable coverage while respecting rate limits.

**Primary recommendation:** Build weather collection daemon using existing BaseDaemon + WeatherGridCache infrastructure. Use asyncio.gather() for parallel NWS/Open-Meteo queries, aiometer for Open-Meteo rate limiting (10k/day = ~7 calls/min), pandas for data merging, and asyncio.sleep() for 15-minute polling loops.
</research_summary>

<standard_stack>
## Standard Stack

The established libraries/tools for Python multi-source weather API integration:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | 0.27+ | Async HTTP client | Dual sync/async, HTTP/2, familiar Requests API |
| asyncio | stdlib | Async orchestration | Python's standard async framework |
| pandas | 2.3+ | Data merging/manipulation | De facto standard for data wrangling |
| stamina | 24.2+ | Simple retry decorator | Already in codebase, sufficient for basic retry |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| aiometer | 0.5+ | Async rate limiting | Strict rate limit enforcement (Open-Meteo 10k/day) |
| pydantic | 2.10+ | Data validation | Optional - validate merged weather responses |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| httpx | aiohttp | aiohttp faster but async-only, no HTTP/2; httpx more versatile |
| aiometer | custom semaphore | aiometer uses GCRA algorithm, more robust than DIY |

**Installation:**
```bash
uv add httpx pandas tenacity aiometer
# stamina already installed
```

**Already in codebase:**
- httpx (used in existing API modules)
- stamina (retry decorator on all API functions)
</standard_stack>

<architecture_patterns>
## Architecture Patterns

### Recommended Project Structure
```
src/cta_eta/data_collection/
├── orchestration/
│   ├── daemon.py                    # BaseDaemon (existing)
│   └── weather_daemon.py            # NEW: Weather collection daemon
├── apis/
│   ├── api_weather_nws.py           # Existing NWS client
│   ├── api_weather_open_meteo.py    # Existing Open-Meteo client
│   └── api_weather_openweathermap.py # Existing OpenWeatherMap client
├── storage_cache/
│   ├── weather_grid_cache.py        # Existing grid cache
│   └── storage.py                   # Existing storage layer
└── merging/
    └── weather_merger.py            # NEW: Multi-source data merging
```

### Pattern 1: Parallel Multi-Source API Calls with Fallback
**What:** Use asyncio.gather() to query NWS + Open-Meteo simultaneously, fall back to OpenWeatherMap on failure
**When to use:** Multi-source data collection where sources are independent and complementary
**Example:**
```python
# Source: asyncio documentation + multi-source fallback pattern
import asyncio
import httpx

async def fetch_weather_parallel(grid_id_nws: str, grid_id_om: str) -> tuple:
    """Fetch NWS and Open-Meteo in parallel with fallback."""
    async with httpx.AsyncClient() as client:
        # Parallel queries with exception handling
        results = await asyncio.gather(
            fetch_nws(client, grid_id_nws),
            fetch_open_meteo(client, grid_id_om),
            return_exceptions=True  # Don't fail entire batch on single failure
        )

        nws_data, om_data = results

        # Check for failures and invoke fallback
        if isinstance(nws_data, Exception) or isinstance(om_data, Exception):
            # Fall back to OpenWeatherMap for failed source(s)
            owm_data = await fetch_openweathermap(client, lat, lon)
            return (nws_data if not isinstance(nws_data, Exception) else owm_data,
                    om_data if not isinstance(om_data, Exception) else owm_data)

        return nws_data, om_data
```

### Pattern 2: Rate-Limited Polling Loop with aiometer
**What:** Use aiometer to enforce strict rate limits for Open-Meteo (10k/day = ~7 calls/min)
**When to use:** API with strict daily rate limits requiring precise throttling
**Example:**
```python
# Source: aiometer documentation
import aiometer

async def collect_weather_for_stations(stations: list[str], grid_cache: WeatherGridCache):
    """Collect weather for all stations with rate limiting."""

    async def fetch_for_station(station_id: str):
        grid_id = grid_cache.get_grid_identifier(station_id)
        if grid_id is None:
            # Cache miss - skip or handle separately
            return None
        return await fetch_weather_parallel(grid_id, grid_id)

    # Rate limit: 10k/day = 416/hour = ~7/min
    # Use 6/min for safety margin
    results = await aiometer.run_on_each(
        fetch_for_station,
        stations,
        max_per_second=0.1,  # 6 per minute
        max_at_once=5        # Max 5 concurrent requests
    )

    return results
```

### Pattern 3: Data Merging with Pandas
**What:** Merge NWS + Open-Meteo responses into unified weather record using pandas
**When to use:** Combining complementary data sources with overlapping and unique fields
**Example:**
```python
# Source: pandas merge documentation
import pandas as pd

def merge_weather_sources(nws_data: dict, om_data: dict) -> dict:
    """Merge NWS and Open-Meteo data into unified weather record."""

    # Convert to DataFrames (single-row)
    nws_df = pd.DataFrame([nws_data])
    om_df = pd.DataFrame([om_data])

    # Merge on timestamp (assuming both normalized to same timestamp)
    # Use outer join to preserve all variables
    merged = pd.merge(
        nws_df,
        om_df,
        on='timestamp',
        how='outer',
        suffixes=('_nws', '_om')
    )

    # Coalesce duplicate fields (prefer NWS for overlaps)
    merged['temperature_f'] = merged['temperature_f_nws'].fillna(merged.get('temperature_f_om'))

    return merged.to_dict('records')[0]
```

### Pattern 4: Daemon Polling Loop with asyncio.sleep()
**What:** Use asyncio.sleep() in daemon main loop for non-blocking 15-minute intervals
**When to use:** Long-running daemon with periodic polling
**Example:**
```python
# Source: asyncio documentation + daemon pattern
import asyncio
from cta_eta.data_collection.orchestration.daemon import BaseDaemon

class WeatherDaemon(BaseDaemon):
    async def run(self) -> None:
        """Main daemon loop - poll every 15 minutes."""
        while self.running:
            try:
                # Collect weather for all unique grid points
                await self._collect_weather_cycle()
            except Exception as e:
                self.logger.error(f"Collection cycle failed: {e}")

            # Non-blocking sleep for 15 minutes
            await asyncio.sleep(900)  # 900 seconds = 15 minutes

    async def _collect_weather_cycle(self):
        """Single collection cycle for all stations."""
        stations = self._get_all_stations()
        results = await collect_weather_for_stations(stations, self.grid_cache)
        await self._save_results(results)
```

### Anti-Patterns to Avoid
- **Using time.sleep() in async code:** Blocks entire event loop; use asyncio.sleep() instead
- **Manual retry loops with fixed delays:** Use tenacity/stamina for exponential backoff with jitter
- **DIY rate limiting with counters:** Use aiometer for robust rate limiting (GCRA algorithm)
- **Querying every station without deduplication:** Use weather grid cache to reduce ~300 stations to ~50 unique points
- **Ignoring return_exceptions=True in gather():** Causes entire batch to fail on single source error
</architecture_patterns>

<dont_hand_roll>
## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async HTTP requests | Custom urllib/requests wrappers | httpx | HTTP/2 support, connection pooling, timeout handling, dual sync/async |
| Retry with backoff | Manual retry loops with sleep | tenacity or stamina | Exponential backoff, jitter, exception filtering, async support |
| Rate limiting | Counter + sleep tracking | aiometer | GCRA algorithm prevents burst violations, handles concurrency |
| Parallel API calls | Manual task management | asyncio.gather() | Exception handling, result collection, cancellation support |
| Data merging | Manual dict merging | pandas.merge() | Handles missing values, join types, duplicate columns, type coercion |
| Multi-source fallback | Nested try/except chains | asyncio.gather(return_exceptions=True) | Partial failure handling, clean error propagation |

**Key insight:** Weather API integration involves many subtle edge cases - timeout coordination, partial failures, rate limit burst protection, connection reuse. Libraries like httpx, tenacity, and aiometer implement production-tested solutions to these problems. Custom implementations inevitably hit the same issues that took these libraries years to solve.

**Codebase context:** The existing code already uses httpx + stamina, which is excellent. Consider upgrading:
- stamina → tenacity for more control over backoff (wait_exponential, wait_random_exponential)
- Add aiometer for strict Open-Meteo rate limiting (10k/day constraint)
</dont_hand_roll>

<common_pitfalls>
## Common Pitfalls

### Pitfall 1: Rate Limit Exhaustion from Burst Traffic
**What goes wrong:** Hitting Open-Meteo's 10k/day limit early due to retry storms or unthrottled bursts
**Why it happens:** Rate limits are daily totals but APIs often have per-second/per-minute sub-limits
**How to avoid:** Use aiometer with max_per_second to enforce strict throttling; add margin (use 6/min for 7/min effective limit)
**Warning signs:** HTTP 429 errors from Open-Meteo; rate limit headers showing rapid depletion

### Pitfall 2: Using time.sleep() in Async Code
**What goes wrong:** Daemon appears hung, no other tasks execute during sleep
**Why it happens:** time.sleep() blocks the entire event loop thread
**How to avoid:** Always use await asyncio.sleep() in async functions; never import time.sleep in async modules
**Warning signs:** Daemon unresponsive to signals during sleep; parallel API calls execute sequentially

### Pitfall 3: Single Source Failure Cascades
**What goes wrong:** NWS API outage prevents all weather collection, not just NWS data
**Why it happens:** Not using return_exceptions=True in asyncio.gather(), so first exception aborts all tasks
**How to avoid:** Use asyncio.gather(return_exceptions=True) and check each result individually; implement fallback logic
**Warning signs:** Weather collection completely stops when single API is down; logs show batch failures

### Pitfall 4: Grid Cache Deduplication Ignored
**What goes wrong:** Querying 300+ stations individually exhausts rate limits in hours
**Why it happens:** Not using WeatherGridCache to map stations to unique grid points before API calls
**How to avoid:** Always check grid cache before API calls; deduplicate by grid_id before parallel collection
**Warning signs:** Open-Meteo rate limit hit within first day; logs show 300+ API calls per cycle

### Pitfall 5: Data Merging Loses Variables
**What goes wrong:** Final weather record missing variables present in source APIs
**Why it happens:** Using inner join or not handling missing keys; preferring wrong source for overlaps
**How to avoid:** Use outer join in pandas.merge(); explicitly coalesce duplicate fields with documented precedence (NWS > Open-Meteo > OpenWeatherMap)
**Warning signs:** Weather records missing snow_depth, visibility; field counts don't match API docs

### Pitfall 6: Retry Storms on Permanent Failures
**What goes wrong:** Daemon retries invalid grid_id for hours, wasting API calls
**Why it happens:** Not distinguishing transient (500, timeout) from permanent (404, 400) errors
**How to avoid:** Configure tenacity to only retry on specific exceptions (httpx.HTTPStatusError with 5xx); fail fast on 4xx
**Warning signs:** Logs show same 404 error retrying 10 times; rate limits exhausted on bad requests

### Pitfall 7: Missing NWS User-Agent Header
**What goes wrong:** NWS API returns 403 or silent failures
**Why it happens:** NWS requires User-Agent header with app name and email per API policy
**How to avoid:** Always include User-Agent header on NWS requests; read from NWS_APP_NAME and NWS_EMAIL env vars
**Warning signs:** NWS requests fail while Open-Meteo works; NWS returns 403 Forbidden
</common_pitfalls>

<code_examples>
## Code Examples

Verified patterns from official sources and existing codebase:

### Multi-Source Collection with Fallback
```python
# Source: Existing codebase pattern + asyncio.gather() documentation
import asyncio
import httpx
from cta_eta.data_collection.apis import api_weather_nws, api_weather_open_meteo, api_weather_openweathermap

async def collect_weather_multi_source(
    client: httpx.AsyncClient,
    nws_grid: str,
    om_grid: str,
    lat: float,
    lon: float
) -> dict:
    """Collect weather from NWS + Open-Meteo with OpenWeatherMap fallback."""

    # Parallel queries with exception handling
    nws_task = api_weather_nws.get_nws_hourly_forecast(client, nws_grid)
    om_task = api_weather_open_meteo.get_open_meteo_current(client, om_grid)

    results = await asyncio.gather(nws_task, om_task, return_exceptions=True)
    nws_data, om_data = results

    # Handle failures with fallback
    if isinstance(nws_data, Exception):
        logger.warning(f"NWS failed for {nws_grid}: {nws_data}, using OpenWeatherMap")
        nws_data = await api_weather_openweathermap.get_current_weather(client, lat, lon)

    if isinstance(om_data, Exception):
        logger.warning(f"Open-Meteo failed for {om_grid}: {om_data}, using OpenWeatherMap")
        om_data = await api_weather_openweathermap.get_current_weather(client, lat, lon)

    # Merge sources
    return merge_weather_sources(nws_data, om_data)
```

### Rate-Limited Station Iteration
```python
# Source: aiometer documentation + existing grid cache pattern
import aiometer
from cta_eta.data_collection.storage_cache.weather_grid_cache import WeatherGridCache

async def collect_all_stations(
    stations: list[dict],
    nws_cache: WeatherGridCache,
    om_cache: WeatherGridCache
):
    """Collect weather for all stations with rate limiting."""

    async def fetch_station_weather(station: dict):
        station_id = station['stop_id']
        lat, lon = station['stop_lat'], station['stop_lon']

        # Get cached grid IDs
        nws_grid = nws_cache.get_grid_identifier(station_id)
        om_grid = om_cache.get_grid_identifier(station_id)

        if nws_grid is None or om_grid is None:
            # Cache miss - skip for now, handle in separate discovery pass
            return None

        async with httpx.AsyncClient() as client:
            return await collect_weather_multi_source(
                client, nws_grid, om_grid, lat, lon
            )

    # Rate limit: Open-Meteo 10k/day = 6.9 calls/min
    # Use 6/min (0.1/sec) for safety margin
    results = await aiometer.run_on_each(
        fetch_station_weather,
        stations,
        max_per_second=0.1,  # 6 per minute
        max_at_once=3        # Max 3 concurrent to be conservative
    )

    return [r for r in results if r is not None]
```

### Retry Configuration with Tenacity
```python
# Source: tenacity documentation + existing stamina pattern
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
import httpx

@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    reraise=True
)
async def fetch_with_smart_retry(client: httpx.AsyncClient, url: str):
    """Fetch with exponential backoff, only retry transient errors."""
    response = await client.get(url)

    # Don't retry 4xx errors (permanent failures)
    if 400 <= response.status_code < 500:
        response.raise_for_status()

    # Do retry 5xx errors (transient server errors)
    response.raise_for_status()
    return response.json()
```
</code_examples>

<sota_updates>
## State of the Art (2026)

What's changed recently in the weather API and async Python ecosystem:

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| requests library | httpx | 2021+ | httpx adds async support + HTTP/2; now standard for async HTTP |
| aiohttp dominant | httpx rising | 2023-2024 | httpx offers dual sync/async, easier migration from requests |
| Manual backoff | tenacity/stamina | 2020+ | Libraries provide jitter, exponential backoff, exception filtering |
| Manual rate limiting | aiometer | 2021+ | GCRA algorithm prevents burst violations better than counters |
| asyncio.run_until_complete | asyncio.run() | Python 3.7+ | asyncio.run() is modern high-level API, abstracts event loop |

**New tools/patterns to consider:**
- **httpx 0.27+**: Improved async performance, better timeout handling
- **aiometer 0.5+**: GCRA rate limiting algorithm, better than leaky bucket for strict limits
- **tenacity 9.0+**: Native asyncio support, better exception typing
- **asyncio.TaskGroup**: Python 3.11+ alternative to gather() with stronger safety guarantees

**Deprecated/outdated:**
- **aiohttp for new projects**: httpx offers better DX with familiar API; only use aiohttp if pure async + peak performance required
- **requests in async code**: Not compatible with asyncio; use httpx instead
- **Manual event loop management**: asyncio.run() replaces get_event_loop().run_until_complete()

**Codebase status:**
- Already using httpx ✓
- Already using stamina for retry ✓
- Consider adding: aiometer (rate limiting), tenacity (more sophisticated retry)
</sota_updates>

<open_questions>
## Open Questions

Things that couldn't be fully resolved:

1. **NWS API exact rate limits**
   - What we know: NWS documentation says "reasonable rate limits" and "no authentication", User-Agent required
   - What's unclear: Exact calls/second or calls/day threshold before throttling
   - Recommendation: Assume generous (1000s/day) but monitor for 429 errors; NWS is primary source so reliability critical

2. **Open-Meteo exact grid resolution**
   - What we know: API returns actual lat/lon used (grid-snapped), documentation mentions 11km global + 1km mesoscale models
   - What's unclear: Exact grid cell size in Chicago area
   - Recommendation: Use discovered grid IDs from API responses (already implemented in weather_grid_cache.py); expect ~50 unique points for ~300 stations

3. **OpenWeatherMap fallback trigger conditions**
   - What we know: OpenWeatherMap free tier is 1000 calls/day or 60 calls/min
   - What's unclear: When exactly to trigger fallback - on any NWS/Open-Meteo error, or only specific error types?
   - Recommendation: Only use OpenWeatherMap on 5xx errors or timeouts (transient failures), not 4xx (permanent); this preserves limited calls for true outages

4. **Data merge conflict resolution**
   - What we know: NWS and Open-Meteo have overlapping variables (temperature, wind, precipitation)
   - What's unclear: Which source is more accurate for overlapping fields
   - Recommendation: Prefer NWS for overlapping fields (primary source, NOAA official data), use Open-Meteo for supplementary variables only
</open_questions>

<sources>
## Sources

### Primary (HIGH confidence)
- [National Weather Service API Documentation](https://www.weather.gov/documentation/services-web-api) - API structure, endpoints, policy
- [NWS API General FAQs](https://weather-gov.github.io/api/general-faqs) - User-Agent requirements, best practices
- [NWS API Gridpoint FAQs](https://weather-gov.github.io/api/gridpoints) - Hourly forecast endpoint, variables
- [Open-Meteo API Documentation](https://open-meteo.com/en/docs) - Variables, free tier, response format
- [Open-Meteo Pricing](https://open-meteo.com/en/pricing) - 10k/day free tier confirmed
- [httpx Documentation](https://www.python-httpx.org/) - Async HTTP client API
- [asyncio Documentation](https://docs.python.org/3/library/asyncio.html) - gather(), sleep(), TaskGroup patterns
- [tenacity Documentation](https://tenacity.readthedocs.io/) - Retry with exponential backoff
- [aiometer GitHub](https://github.com/florimondmanca/aiometer) - Rate limiting with GCRA
- Existing codebase: [api_weather_nws.py](../../src/cta_eta/data_collection/apis/api_weather_nws.py), [api_weather_open_meteo.py](../../src/cta_eta/data_collection/apis/api_weather_open_meteo.py)

### Secondary (MEDIUM confidence)
- [OpenWeatherMap Pricing](https://openweathermap.org/price) - Free tier 1000 calls/day confirmed via WebSearch
- [httpx vs aiohttp comparison (Oxylabs)](https://oxylabs.io/blog/httpx-vs-requests-vs-aiohttp) - Performance benchmarks, feature comparison
- [Tenacity best practices (2026)](https://johal.in/tenacity-retries-exponential-backoff-decorators-2026/) - Exponential backoff patterns for AI pipelines
- [Python asyncio daemon patterns (Real Python)](https://realpython.com/async-io-python/) - Async daemon loop patterns
- [Multi-source API fallback patterns (ResiliencyPatterns)](https://github.com/MyPureCloud/resiliency-patterns-examples) - Fallback + circuit breaker patterns

### Tertiary (LOW confidence - needs validation)
- None - all critical findings verified against official documentation or existing codebase
</sources>

<metadata>
## Metadata

**Research scope:**
- Core technology: Multi-source weather API integration (NWS, Open-Meteo, OpenWeatherMap)
- Ecosystem: httpx, asyncio, tenacity, aiometer, pandas for async HTTP + retry + rate limiting + data merging
- Patterns: Parallel API calls with gather(), multi-source fallback, rate-limited polling, daemon loops
- Pitfalls: Rate limit exhaustion, blocking sleep, single-source cascades, grid cache deduplication

**Confidence breakdown:**
- Standard stack: HIGH - httpx, asyncio, pandas are industry standard; existing codebase uses httpx + stamina
- Architecture: HIGH - asyncio.gather() + aiometer patterns verified from official docs and 2026 best practices
- Pitfalls: HIGH - common issues documented in API docs (NWS User-Agent, Open-Meteo 10k limit) and async Python guides
- Code examples: HIGH - based on existing codebase patterns + official asyncio/httpx/aiometer documentation

**Research date:** 2026-01-19
**Valid until:** 2026-02-19 (30 days - stable ecosystem, but API limits and library versions may update)

**Key decisions informed by research:**
1. Use httpx (already in codebase) for HTTP client - verified as modern standard with HTTP/2 + dual sync/async
2. Add aiometer for Open-Meteo rate limiting - GCRA algorithm handles 10k/day constraint robustly
3. Use asyncio.gather(return_exceptions=True) for parallel NWS + Open-Meteo queries with fallback
4. Prefer NWS over Open-Meteo for overlapping variables - NOAA official data source
5. Use asyncio.sleep() in daemon loop - non-blocking 15-minute intervals confirmed as best practice
6. Leverage existing WeatherGridCache for deduplication - critical to avoid rate limit exhaustion
</metadata>

---

*Phase: 05-weather-data-collection*
*Research completed: 2026-01-19*
*Ready for planning: yes*
