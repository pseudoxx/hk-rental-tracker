# HK Rental Tracker Agent Entry

This repository exposes a portable skill for operating the local Hong Kong rental tracker.

When an agent opens this repository:

1. Read `README.md`.
2. Read `skills/hk-rental-tracker/SKILL.md`.
3. Read `docs/scanner-workflow.md` for scan workflow and validation boundaries.

Use the skill when the user asks to create, start, initialize, configure, scan, or track a Hong Kong rental market. If required setup details are missing, ask for the target area or estate, layout, and hard budget limit before creating a task.

Tracked markets live under `tasks/<slug>/`; task configuration is `tasks/<slug>/tracker.json`; local data is `tasks/<slug>/rental.db`; generated reports live under `tasks/<slug>/exports/`.

Important operating rules:

- Default sources are `midland`, `centanet`, `hkp`, and `ricacorp`.
- Do not run browser verification during normal scans.
- Do not mark listings as delisted after a failed source scan or unexpected zero-result source scan.
- Keep site-specific scraping changes in `hk_rental_tracker/adapters/`, `hk_rental_tracker/site_catalog.py`, or extraction helpers.
- Keep analysis and database behavior source-agnostic unless the user asks for source-specific behavior.
- Do not commit local task data, generated reports, screenshots, caches, databases, credentials, or local/private instruction files.
