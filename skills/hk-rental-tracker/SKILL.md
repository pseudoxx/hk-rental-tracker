---
name: hk-rental-tracker
description: Work with this repo's local Hong Kong rental tracking system, including creating task folders, running scans, interpreting SQLite/CSV outputs, adjusting site search URLs, and discussing rental market changes from maintained local data.
metadata: {"openclaw":{"requires":{"anyBins":["python3","python"]},"homepage":"https://github.com/pseudoxx/hk-rental-tracker","emoji":"🏠","version":"0.1.0"}}
---

# HK Rental Tracker

Use this skill when the user asks to operate or discuss the local Hong Kong rental tracker in this workspace. If the user opens this repository and expresses intent to create, start, initialize, configure, scan, or track a Hong Kong rental market, treat it as a request to begin the tracker setup flow. This includes short prompts like "开始" as well as natural-language requests like "帮我追踪启德两房" or "set up a tracker for LOHAS Park under 30k".

## Read Order

1. Read `README.md`.
2. Read `docs/scanner-workflow.md` for the operating workflow and validation boundaries.
3. For a specific market, read `tasks/<slug>/tracker.json`.
4. Use `tasks/<slug>/exports/summary.md` for a fast market snapshot.
5. Query `tasks/<slug>/rental.db` when the user asks for detail beyond the summary.

## Startup Flow

When the user asks to create, start, initialize, configure, scan, or track a market without giving full parameters:

1. Check whether `tasks/` already contains tracked markets and summarize the available choices if any exist.
2. If no target market is clear, ask for the minimum required setup details: area or estate, layout, and any hard budget limit.
3. Create the task with conservative defaults and default sources (`midland`, `centanet`, `hkp`, `ricacorp`) once the minimum details are known.
4. Run the initial scan only after the task exists and the user has provided enough scope to avoid an accidental broad scrape.
5. Summarize the scan output, source errors, generated snapshot, and export paths. Do not run browser verification unless explicitly requested.

## Commands

Create a task:

```bash
python3 -m hk_rental_tracker init-task --slug <slug> --area "<区域或屋苑>" --max-rent <最高租金> --min-area <最低实用面积> --max-area <最高实用面积> --min-gross-area <最低建筑面积> --max-gross-area <最高建筑面积> --min-building-age <最低楼龄> --max-building-age <最高楼龄> --max-psf <最高实用尺租> --layouts "<户型1,户型2>" --keywords "<关键词1,关键词2>"
```

Default sources include `midland`, `centanet`, `hkp`, and `ricacorp`. If a task should omit a source, pass an explicit `--sites` list:

```bash
python3 -m hk_rental_tracker init-task --slug <slug> --area "<区域或屋苑>" --layouts "<户型1,户型2>" --sites "midland,centanet,hkp"
```

Run a scan:

```bash
python3 -m hk_rental_tracker scan --task tasks/<slug> --mode daily
```

Regenerate summary:

```bash
python3 -m hk_rental_tracker summarize --task tasks/<slug>
```

Query active listings:

```bash
python3 -m hk_rental_tracker query --task tasks/<slug> --active-only --limit 50
```

Add a verified source page:

```bash
python3 -m hk_rental_tracker add-url --task tasks/<slug> --site centanet --url "https://..."
```

## Data Semantics

- `first_seen_at`: first local observation.
- `first_delisted_at`: first local observation that one agency source no longer showed the listing. It is not a transaction date.
- `active`: at least one source still sees the listing.
- `source_state`: source-specific listing lifecycle.

## Operating Rules

- Default to a Traditional-Chinese-friendly style for Hong Kong rental workflows. Match the user's Simplified/Traditional style; if the user initialized or is operating a task in Simplified Chinese, Simplified Chinese is acceptable. Otherwise prefer Traditional Chinese in replies and generated summaries.
- Default sources are `midland`, `centanet`, `hkp`, and `ricacorp`.
- Do not mark listings as delisted after a failed source scan.
- If a source returns zero usable listings unexpectedly, fix or add `source_search_urls` before trusting missing detection.
- Do not run browser-based verification during normal scans. Do not call `verify-web`, Browser, or Chrome unless the user explicitly asks for frontend evidence or a site adapter is being debugged.
- Validate routine scans with scan output, source error status, generated snapshots, and `exports/summary.md`.
- Daily reports must include a `租盘降价` section for listings that dropped rent that day. Include the basic listing fields, previous/current rent, drop amount, drop percentage, source link, and local listing age.
- Database-backed queries and exports should mark listings that have ever dropped rent with `ever_rent_decreased`.
- Keep site-specific scraping changes in `hk_rental_tracker/adapters/`, `site_catalog.py`, or extraction helpers.
- Keep analysis and database changes source-agnostic unless the user asks for source-specific behavior.
