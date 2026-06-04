---
name: hk-rental-tracker
description: Work with this repo's local Hong Kong rental tracking system, including creating task folders, running scans, interpreting SQLite/CSV outputs, adjusting site search URLs, and discussing rental market changes from maintained local data.
---

# HK Rental Tracker

Use this skill when the user asks to operate or discuss the local Hong Kong rental tracker in this repository. If the user opens this repository and expresses intent to create, start, initialize, configure, scan, or track a Hong Kong rental market, treat it as a request to begin the tracker setup flow. This includes short prompts like "开始" as well as natural-language requests like "帮我追踪启德两房" or "set up a tracker for LOHAS Park under 30k".

## Canonical Workflow

Read the canonical repo skill before acting:

1. `skills/hk-rental-tracker/SKILL.md`
2. `README.md`
3. `docs/scanner-workflow.md`

For a specific market, read `tasks/<slug>/tracker.json` before scanning or interpreting results.

## Startup Flow

When the user asks to create, start, initialize, configure, scan, or track a market without giving full parameters:

1. Check whether `tasks/` already contains tracked markets and summarize the available choices if any exist.
2. If no target market is clear, ask for the minimum required setup details: area or estate, layout, and any hard budget limit.
3. Create the task with conservative defaults and default sources (`midland`, `centanet`, `hkp`, `ricacorp`) once the minimum details are known.
4. Run the initial scan only after the task exists and the user has provided enough scope to avoid an accidental broad scrape.
5. Summarize the scan output, source errors, generated snapshot, and export paths. Do not run browser verification unless explicitly requested.

## Safety Rules

- Do not run browser verification during normal scans.
- Do not call `verify-web` unless the user explicitly asks for webpage evidence or a site adapter is being debugged.
- Do not mark listings as delisted after a failed source scan.
- Treat unexpected zero usable listings from a source as a scan quality issue until diagnosed.
- Default sources are `midland`, `centanet`, `hkp`, and `ricacorp`.
- Preserve historical rows in `rental.db`; mark inactive state rather than deleting delisted listings.
