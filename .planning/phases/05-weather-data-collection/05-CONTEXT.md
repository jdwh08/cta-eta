# Phase 5: Weather Data Collection - Context

**Gathered:** 2026-01-19
**Status:** Ready for research

<vision>
## How This Should Work

Weather collection runs as an independent daemon on a 15-minute polling cycle (driven by Open-Meteo's 10k daily call limit with healthy margin). This runs completely separately from the high-frequency train polling - no coordination needed.

Each collection cycle iterates through all CTA stations, but uses the weather grid cache from Phase 3 to intelligently deduplicate API calls. Most stations map to a smaller set of unique weather grid points, so we only query each unique point once per cycle. When cache entries TTL expire, we poll using the station location to refresh the cache mapping.

For each unique weather point, we query NWS and Open-Meteo in parallel for speed. NWS provides core weather variables, Open-Meteo supplements with variables NWS doesn't have (snow depth, visibility, pressure). The data from both sources gets merged into a single unified weather record per location.

OpenWeatherMap serves as a fallback - if either NWS or Open-Meteo fails for a location, we use OpenWeatherMap to fill the gap. If all three sources fail, we retry that location within the polling cycle using exponential backoff. If it still fails by cycle end, we write missing/None values and move on - don't block the entire collection.

The system should build on the daemon framework from Phase 1, handling signals gracefully and running reliably for months of continuous collection.

</vision>

<essential>
## What Must Be Nailed

- **Complete weather coverage** - Capture every weather variable needed for ETA prediction modeling. No gaps in the variable set - that's why we're using multiple complementary sources.
- **Efficient rate limit management** - Stay well within all API limits (Open-Meteo 10k/day is the constraint, hence 15min cycles). Use weather grid cache deduplication to minimize redundant calls.
- **Reliable daemon operation** - Runs continuously every 15 minutes for months without manual intervention. Builds on Phase 1 daemon patterns.

</essential>

<boundaries>
## What's Out of Scope

- Historical weather data backfill - Only collect current/forecast data going forward
- Weather data validation and quality checks - Accept API responses as-is, validation comes later
- Advanced forecasting or weather prediction - Just collect what APIs provide, don't compute derived metrics
- Weather-based alerting or thresholds - Don't react to weather conditions, just collect the data
- Coordination with train polling - Weather runs independently on its own schedule

</boundaries>

<specifics>
## Specific Ideas

- **Build on Phase 3 infrastructure**: Leverage `weather_grid_cache.py` for station-to-grid-point mapping and deduplication
- **Build on Phase 1 daemon**: Use the daemon framework for lifecycle management, signal handling, continuous operation
- **Parallel collection**: Query NWS and Open-Meteo simultaneously to minimize collection time per cycle
- **15-minute polling**: 96 collection cycles per day stays well under Open-Meteo's 10k limit even with unique grid points
- **Merged storage**: Combine NWS + Open-Meteo data into single unified weather record per location
- **Smart retry**: Within a cycle, retry failed locations with exponential backoff; write None if still failing
- **Fallback logic**: OpenWeatherMap kicks in if either NWS or Open-Meteo fails for a location

</specifics>

<notes>
## Additional Context

The 15-minute interval is specifically chosen to respect Open-Meteo's 10k daily call limit with margin. This is much lower frequency than train polling (15min vs 15sec), so the two can run independently without coordination.

Weather grid cache deduplication is critical - without it, querying every CTA station would quickly exhaust rate limits. The cache maps many stations to fewer unique forecast grid points.

Priority is completeness of weather variables over any single source. That's why we use NWS (comprehensive) + Open-Meteo (fills gaps) + OpenWeatherMap (emergency backup).

</notes>

---

*Phase: 05-weather-data-collection*
*Context gathered: 2026-01-19*
