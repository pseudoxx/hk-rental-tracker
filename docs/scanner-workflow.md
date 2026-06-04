# Scanner Workflow

This is the operating workflow for the HK rental tracker and the baseline for turning this project into a reusable Codex skill. It describes the intended operator flow, safety rules, validation signals, and packaging boundaries.

## 1. Scope

The scanner workflow tracks Hong Kong rental listings for one configured market at a time. A market is usually an area, estate, or closely related group of aliases under `tasks/<slug>/`.

The workflow has three normal operating modes:

- Task setup: collect scope and create or update `tracker.json`.
- Scan run: fetch agency sources, normalize listings, update `rental.db`, and regenerate routine exports.
- Reporting: summarize local state and generate end-of-day reports.

Browser or frontend verification is outside the normal workflow. It is a manual diagnostic path only.

## 2. Language And User Intake

Default to a Traditional-Chinese-friendly style for Hong Kong rental workflows, while matching the user's Simplified or Traditional Chinese style. Normalize Hong Kong place names across Traditional and Simplified Chinese when deriving search terms.

Before creating scan state, collect the minimum viable task scope:

- Target market: area, estate, or aliases.
- Layout: studio/open-plan and/or one-, two-, three-, or four-bedroom units. Normalize natural input such as `两房`, `兩房`, `二房`, and `2 bedroom` to canonical scanner terms such as `2房` before saving the task or pushing API filters.

Ask for optional filters in short rounds, and always give the user an easy way to skip. Do not ask for every possible filter in one message.

Optional rounds can include:

- Budget filters: minimum and maximum rent.
- Unit details: usable area, gross area, building age, and price per usable sqft.
- Text filters: required keywords, allowed estates, excluded estates, and excluded keywords.
- Sources: default to `midland`, `centanet`, `hkp`, and `ricacorp`.
- Output and operations: local reports by default, optional Telegram/email/webhook push, and optional one-off or daily automation.

If the user gives only the target market and layout, create a useful task with conservative defaults and leave optional filters empty. Optional filters can be added later as the user's search changes.

After the user has provided enough scope to create the task, and before running the initial scan, ask whether they want an automation for routine daily scans, daily report generation, and report delivery. Keep this as one short decision point, not a blocker for the initial scan. If they say yes, collect only the operational choices needed to set it up:

- Daily scan/report time and timezone.
- Whether to generate the report after every scan or only after the final scan of the day.
- Delivery channels: local-only, Telegram, email, webhook, or a comma-separated combination.
- Whether credentials are already available in environment variables or the private env file.

Do not ask the user to paste secrets into `tracker.json`, task README files, reports, or committed docs. Use environment variables or the private local env file for tokens, SMTP credentials, and webhook URLs.

## 3. Task Directory And Config

Each tracked market owns one task directory:

```text
tasks/<slug>/
├── README.md
├── tracker.json
├── rental.db
├── snapshots/
└── exports/
```

`tracker.json` is the source of truth for scan scope:

- `area` and `area_aliases`
- `filters`
- `sites`
- `source_search_urls`
- `source_policies`
- `scan_options`
- `notes`

Task setup should preserve user-provided aliases and also derive language variants. If a website changes its URL pattern or the default search entry becomes inaccurate, update `source_search_urls` before changing adapter code.

The normal creation command is:

```bash
python3 -m hk_rental_tracker init-task --slug <slug> --area "<区域或屋苑>" --layouts "<开放式,一房,两房>"
```

Add optional filter flags only when the user supplied them. Add a verified source page with:

```bash
python3 -m hk_rental_tracker add-url --task tasks/<slug> --site <site> --url "https://..."
```

If a task should omit a default source, initialize with an explicit `--sites` list:

```bash
python3 -m hk_rental_tracker init-task --slug <slug> --area "<区域或屋苑>" --layouts "<开放式,一房>" --sites "midland,centanet,hkp"
```

## 4. Filter Model

All filters are required local filters. API pushdown is an optimization only.

The scanner may push filters into a source request only when that source parameter is known to affect API results with matching semantics. After parsing, every observation must still pass the local `SearchFilters.matches(...)` check before database writes.

Supported local filters:

- Area or estate text via `area` and `area_aliases`.
- Rent range.
- Layout or bedroom count.
- Usable area range.
- Gross area range.
- Building age range.
- Price per usable sqft range.
- Allowed estates.
- Required keywords.
- Excluded estates.
- Excluded keywords.

Unknown, unstable, or silently ignored source parameters must be documented as local-only until proven stable.

## 5. Source Filter Trust Records

Site scraping details belong in `hk_rental_tracker/adapters/`. Source metadata belongs in `site_catalog.py`, and filter trust rules belong in `hk_rental_tracker/source_capabilities.py`.

Each source should record which filters are safe to send to that website and which filters must remain local-only. This record is for scanner and skill decisions; it is not something the user normally needs to understand.

For each source, track:

- Confirmed API pushdown parameters that reduce results without changing the intended meaning.
- Parameters that appear to exist but are unstable, ignored, or have unclear semantics.
- Parameters that must stay local-only.
- Parsed observation fields.
- Authentication, token, or session handling.
- Retry and fallback behavior.
- Expected zero-result behavior.

The scanner core should depend only on normalized `ListingObservation` objects. Database, deduplication, exports, and reports should stay source-agnostic.

This exists to avoid two common mistakes:

- Dirty data: the API accepts a filter but still returns listings outside the user's criteria.
- Missing data: the API filter is too narrow, silently ignored, or semantically different, causing qualified listings to be missed.

Current trust baseline:

- `midland` and `hkp`: push down rent, bedroom/layout, usable area, gross area, and price per usable sqft; keep building age and text filters local.
- `ricacorp`: parse server-rendered public rental list pages and keep all filters local until stable webpage parameters are confirmed.
- `centanet`: push down rent, bedroom/layout, usable area, building age, and price per usable sqft; keep gross area and text filters local.
- Unknown or new sources: do not push down filters until their behavior is confirmed.

## 6. Scan Execution

Initial scan:

```bash
python3 -m hk_rental_tracker scan --task tasks/<slug> --mode initial
```

Daily scan:

```bash
python3 -m hk_rental_tracker scan --task tasks/<slug> --mode daily
```

For each enabled source, the scan runner should:

1. Load `tracker.json`.
2. Run network preflight when enabled.
3. Build source-specific requests from the task config.
4. Push down only confirmed source parameters.
5. Fetch available pages with bounded retry and request delay.
6. Parse raw API or HTML responses into `ListingObservation`.
7. Apply all local required filters.
8. Upsert matching observations into `rental.db`.
9. Mark source-level missing records only when safety gates allow it.
10. Write a JSON run snapshot.
11. Regenerate routine exports and `exports/summary.md`.
12. Finish the scan run with `ok`, `partial`, `blocked`, or `failed` status.

Routine validation should focus on usefulness, not formal ceremony. Its purpose is to prevent two errors:

- Invalid API filtering lets unqualified listings into the database.
- Over-trusting API filtering misses qualified listings before local filtering can see them.

The main safeguards are:

- Treat API pushdown as optional narrowing only; local filtering is the final gate before database writes.
- Push down only filters already recorded as trustworthy for that source.
- Keep uncertain filters local-only, even if that means fetching more data.
- Inspect source errors, raw observation counts, filtered observation counts, snapshots, and `exports/summary.md` for sudden zeroes or sharp unexplained drops.
- Use manual browser or webpage diagnosis only when there is a concrete suspicion of missing qualified listings or broken source behavior.

Each scan snapshot should include validation signals per source:

- `raw_observations`: parsed observations before local filters.
- `filtered_observations`: observations that passed local filters and were eligible for database writes.
- `rejected_observations`: parsed observations rejected by local filters.
- `missing_marking_enabled`: whether the source was allowed to mark missing records in that run.
- `missing_sources_marked`: number of source records marked missing.

## 7. Missing And Failure Safety

The workflow must protect against false delisting.

Rules:

- A failed source must not block successful sources.
- A failed source must not cause listings to be marked missing.
- Initial scans must not mark missing listings.
- Daily or manual scans should mark missing source records only after that same source returned usable filtered observations, unless the task explicitly disables the safety gate.
- An unexpected zero-result source is suspicious until diagnosed.
- A blocked network preflight should produce a snapshot/export if possible, then stop without making missing-state claims.

`first_delisted_at` and `source_state.first_missing_at` mean first local observation of disappearance from a source. They do not mean transaction date, actual lease date, or actual website removal time.

## 8. Deduplication And State

Deduplication is intentionally conservative.

Same-source continuity should prefer source listing ID or normalized source URL. Cross-source merging should happen only when rent differs by at most HKD 500 and all mutually present identity fields among estate, block/building, floor, flat, layout, and usable area strictly match after normalization.

State semantics:

- `first_seen_at`: first local observation.
- `last_seen_at`: latest local observation.
- `first_delisted_at`: first local observation that a listing disappeared from one source.
- `active`: true when at least one source still sees the listing.
- `source_state`: source-specific lifecycle and links.
- `ever_rent_decreased`: true after any locally observed rent drop.
- `first_rent_decrease_at` and `last_rent_decrease_at`: first and latest local rent-drop observations.

Do not delete delisted listings from `rental.db`. Preserve listings, source states, and observation history.

## 9. Exports And Reports

Each scan should regenerate routine exports under `tasks/<slug>/exports/`:

- `active_listings.csv`
- `all_listings.csv`
- `latest_changes.csv`
- `summary.md`

End-of-day reporting is a separate command:

```bash
python3 -m hk_rental_tracker daily-report --task tasks/<slug>
```

Daily reports should include:

- New listings.
- Rent drops in a `租盘降价` section.
- Budget stats using rent bands derived dynamically from the whole local listing database's rent distribution, not fixed hard-coded budget thresholds.
- Source disappearance lag.
- Fresh-value watchlist.
- Stale-value watchlist.

Rent-drop rows should include useful basics: estate, unit clues, rent before and after, drop amount, drop percentage, source link, and local listing age. Listings that ever had a rent drop must be marked in database-backed queries and exports.

Reports should stay explainable. Where possible, show source, first seen date, listing age, rent, usable area, price per sqft, and source links.

The normal data model should avoid personal data. Do not collect or persist agent names, phone numbers, owner details, tenant details, or raw source payloads that may contain those fields.

## 10. Diagnostics

Normal scans should not run `verify-web`, Browser, or Chrome.

Use browser or frontend diagnosis only when:

- The user explicitly asks for webpage evidence.
- A site adapter is being debugged.
- A new source parameter is being researched.
- API totals conflict with parsed results.
- A source repeatedly fails or returns suspicious zero usable results.

Diagnostic output should be stored separately and clearly labeled. Diagnostic findings can inform `source_search_urls` or adapter changes, but should not be mixed into routine scan conclusions.

## 11. Automation

A daily automation should run the normal workflow without browser verification:

1. Run `scan --mode daily`.
2. Inspect scan status, source errors, latest snapshot, and `exports/summary.md`.
3. Treat failed or suspicious zero-result sources as scan quality issues, not market delisting evidence.
4. Generate `daily-report` only after the final scan of the day.
5. Optionally send Telegram, email, or webhook only when environment configuration exists.

Automation summaries should explicitly mention partial failures and should avoid interpreting missing records from failed sources as real removals.

Supported report delivery channels:

- `telegram`: sends a message preview and Markdown document using Telegram bot credentials.
- `email`: sends the Markdown report as body and attachment using SMTP settings.
- `webhook`: sends the report to `HK_RENTAL_TRACKER_WEBHOOK_URL`; defaults to JSON with `title`, `text`, and `filename`, or sends plain text when `HK_RENTAL_TRACKER_WEBHOOK_FORMAT=text`.

## 12. Skill Packaging Shape

The future skill should expose a small set of operator intents:

- Create or update a tracked market.
- Run an initial scan.
- Run a daily scan.
- Summarize current market state.
- Generate an end-of-day report.
- Investigate a source problem.
- Adjust a verified source search URL.

The skill should encode safeguards as operating rules, not optional advice:

- Read `README.md` first.
- Read the task config before operating a specific market.
- Prefer `exports/summary.md` for fast market context.
- Query `rental.db` only when detail is needed.
- Avoid browser verification in routine scans.
- Never trust failed or zero-result scans for delisting.
- Use local filtering to prevent dirty data and source trust records to reduce missed qualified listings.
- Preserve historical records.

## 13. Future App Packaging

A desktop or persistent app should separate:

- Configuration UI.
- Source filter trust registry.
- Scan runner.
- Local database.
- Report renderer.
- Notification sender.
- Diagnostic tools.

Potential refactors before packaging:

- Expand and calibrate source filter trust records as website behavior changes.
- Add filter hit and exclusion explanations.
- Add source health history.
- Store parsed gross area and building age in first-class database columns where missing.
- Add a UI-friendly task schema with validation.
- Add controlled scheduling outside the interactive session.
