# Contributing

Thanks for improving HK Rental Tracker.

## Development

Use Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,browser]"
python -m pytest
```

Most tests should avoid live network access. Site adapters should be tested with small fixtures or normalized observations where possible.

## Data And Privacy

Do not commit local task data, generated reports, screenshots, caches, environment files, tokens, or SQLite databases. The repository intentionally ignores `tasks/*`, `.cache/`, `.venv/`, and database files.

The tracker should not collect or persist agent names, phone numbers, owner details, tenant details, or raw source payloads that may contain personal data.

## Source Behavior

Keep site-specific scraping and parsing logic in `hk_rental_tracker/adapters/`, `site_catalog.py`, `source_capabilities.py`, or extraction helpers. Keep storage, deduplication, exports, and reports source-agnostic unless a source-specific distinction is unavoidable.

Do not enable Ricacorp by default. Ricacorp access must remain authorization-gated.

Normal scans should not run browser verification. `verify-web` is a manual diagnostic path for adapter debugging or explicit user requests.

## Reliability Rules

Do not mark listings as delisted after a failed source scan. If a source unexpectedly returns zero usable listings, treat it as a scan quality issue until diagnosed.

`first_delisted_at` and `source_state.first_missing_at` mean first local observation of disappearance from a source. They are not transaction dates.
