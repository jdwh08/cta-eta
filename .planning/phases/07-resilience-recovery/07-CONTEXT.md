# Phase 7: Resilience & Recovery - Context

**Gathered:** 2026-01-25
**Status:** Ready for planning

<vision>
## How This Should Work

The system should never lose data within API rate limits - that's the core project value. When API calls fail, the system doesn't give up immediately. It retries intelligently with long timeout windows (5-10 minutes) using exponential backoff, blocking subsequent polls until the retry resolves.

The key insight: CTA's Train Tracker API has no historical data endpoint. Whatever we collect IS the only data available. This biases us toward trying harder to get each data point rather than moving on quickly.

When retries fail or hit definitive errors (daily limits, rate limits), the system detects the gap, flags it in dataset metadata for downstream ML processing, and returns to normal 15-second polling. On restart after shutdown or crash, the system calculates downtime gaps from saved state and continues polling.

The system knows the difference between "keep trying" errors (network timeouts) and "stop trying" errors (missing API key, invalid parameters, daily quota exceeded). It acts accordingly without manual intervention.

</vision>

<essential>
## What Must Be Nailed

- **Intelligent retry with appropriate timeouts** - Use stamina's exponential backoff with jitter, but customize based on CTA's non-standard error codes. Daily limits (error 102) get longer backoff since it could require hours. In theory, the rate limit resets at midnight Chicago time.

- **Complete gap visibility** - Every missed collection window must be detected and flagged in dataset metadata. Downstream ML needs to know which time periods have missing data.

- **Never fall behind indefinitely** - Block polls during retry, accept the gap if retry exhausted, then return to 15-second schedule. Don't queue up missed polls or drift permanently.

All three aspects work together: retry persists to minimize gaps, gap detection ensures data quality transparency, schedule management prevents cascading failures.

</essential>

<boundaries>
## What's Out of Scope

- **No monitoring dashboards** - Gap detection and logging yes, but visualization and dashboards are Phase 8
- **No email alerts** - System logs failures and gaps, but automated alerting is Phase 9
- **No automatic backfilling** - Detect and flag gaps, but don't automatically attempt to fill historical missing data
- **No real-time alerting** - This phase focuses on resilient real-time collection, not notification systems

This phase is about making the collection daemon bulletproof during operation, not about observability or historical data repair.

</boundaries>

<specifics>
## Specific Ideas

**CTA API Error Code Handling:**
The CTA Train Tracker API uses non-standard error codes that must be handled specifically.

Example output for a 500 CTA error is as follows:
```json
{"ctatt":{"tmst":"2026-01-25T19:11:05","TimeStamp":"2026-01-25T19:11:05","errCd":"500","errNm":"Invalid parameter: 'test'."}}
```

Thus, in addition to handling standard HTTP like errors, the daemon also needs to handle CTA-specific errCd information.

**Critical: CTA returns HTTP 200 with application errors in the body.** The example above has no `route` key. If we only check HTTP status, we get 200, parse JSON, and `normalize_train_positions` yields 0 records with no error. We must treat non‑success `errCd` as errors (raise an exception) and never silently produce 0 records.

- **Permanent errors (don't retry):** 100 (missing parameter), 101 (missing API key), 106 (invalid route), 107 (requesting >8 routes), 500 (invalid parameter)
- **Daily quota error (special handling):** 102 (maximum usage exceeded - their daily API limit, not a short-term rate limit). Per developer docs, quota resets at midnight Chicago time. Plan 07-01 uses a bounded probe (1–3 retries at 5 min, 15 min, [1 hour]) before sleeping until midnight, to guard against false positives; then sleep until midnight Chicago + buffer and resume 15-second polling.
- **Also follow standard HTTP patterns** as safety net: don't retry 4xx (except 429), do retry 5xx and network errors.

**Retry Strategy:**
- Use stamina package defaults (exponential backoff with jitter)
- Extend maximum retry window to 5-10 minutes for network/timeout errors
- Error 102 (daily limit): bounded probe, then sleep until midnight Chicago if still 102 (see Plan 07-01).

**Gap Detection:**
- Train position data updates every 15-20 seconds per API documentation
- Missing a poll within that window technically constitutes a gap
- Flag gaps in dataset metadata (not just logs) for downstream ML to handle appropriately

**State Preservation:**
- Save last poll timestamp periodically and on shutdown (SIGTERM/SIGINT)
- On restart, calculate gap from last saved timestamp to current time
- Report downtime gaps in metadata

</specifics>

<notes>
## Additional Context

**Critical constraint:** CTA Train Tracker API has no historical data endpoint. Whatever we collect during each polling cycle is the only source of truth for that time period. There's no "backfill" option later.

This fundamentally shapes the retry philosophy: better to block and retry than to give up, because giving up means permanent data loss.

**Rate limit context:** Currently polling at 15-second intervals, well under the API's generous rate limits. The 102 error (maximum usage exceeded) is a daily quota, not a per-minute rate limit, so hitting it is unexpected and suggests either API changes or system misconfiguration.

**CTA 200 + errCd:** CTA can return HTTP 200 with `errCd` and `errNm` in `ctatt` and no `route`. These must be treated as errors (raise from the API layer, then classify and handle in the daemon). Do not silently produce 0 records.

</notes>

---

*Phase: 07-resilience-recovery*
*Context gathered: 2026-01-25*
