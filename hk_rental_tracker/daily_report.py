from __future__ import annotations

import csv
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from sqlite3 import Connection, Row
from statistics import median
from typing import Any

from .config import load_task_config
from .normalization import compact_money, now_iso
from .site_catalog import display_name
from .storage import RentalStore


OK_RUN_STATUSES = ("ok",)
PRIVATE_ENV_PATHS = (Path.home() / ".codex" / "hk-rental-tracker.env",)


@dataclass
class RunRef:
    id: int
    started_at: str
    ended_at: str | None
    status: str

    @property
    def cutoff(self) -> str:
        return self.started_at

    @property
    def local_date(self) -> date:
        return datetime.fromisoformat(self.started_at).date()


@dataclass
class ReportResult:
    markdown_path: Path
    csv_paths: dict[str, Path]
    sent_channels: list[str]


def generate_daily_report(
    task_dir: str | Path,
    report_date: str | date | None = None,
    send_channels: list[str] | None = None,
    print_report: bool = False,
) -> ReportResult:
    task_path = Path(task_dir)
    config = load_task_config(task_path)
    store = RentalStore(task_path / "rental.db")
    try:
        conn = store.conn
        target_run = _resolve_target_run(conn, report_date)
        target_date = target_run.local_date
        previous_run = _latest_run_on_date(conn, target_date - timedelta(days=1))
        week_run = _latest_run_on_date(conn, target_date - timedelta(days=7))

        current_rows = _active_listing_rows(conn, target_run.cutoff)
        previous_rows = _active_listing_rows(conn, previous_run.cutoff) if previous_run else []
        week_rows = _active_listing_rows(conn, week_run.cutoff) if week_run else []

        current_by_id = {int(row["id"]): dict(row) for row in current_rows}
        previous_by_id = {int(row["id"]): dict(row) for row in previous_rows}
        week_by_id = {int(row["id"]): dict(row) for row in week_rows}

        day_start = _start_of_day(target_run.started_at, target_date)
        new_rows = _new_listing_rows(conn, day_start, target_run.cutoff)
        new_rows = _with_age(new_rows, target_run.cutoff)
        source_missing_rows = _source_missing_rows(conn, day_start, target_run.cutoff)
        removed_rows = [previous_by_id[id_] for id_ in sorted(set(previous_by_id) - set(current_by_id))]
        removed_rows = _with_age(removed_rows, target_run.cutoff)
        rent_changes = _rent_changes(current_by_id, previous_by_id, target_run.cutoff)
        rent_decreases = [row for row in rent_changes if row.get("rent_delta_hkd") is not None and row["rent_delta_hkd"] < 0]
        value_rows = _value_rows(current_rows, target_run.cutoff)
        fresh_value_rows = _fresh_value_rows(new_rows, current_rows, target_run.cutoff)
        budget_stats = _budget_stats(current_rows, previous_rows, new_rows, removed_rows)
        withdrawal_rows = _withdrawal_signal_rows(conn, target_run.cutoff)
        withdrawal_lag_stats = _withdrawal_lag_stats(withdrawal_rows)
        stale_value_rows = _stale_value_rows(current_rows, target_run.cutoff)

        layout_stats = _layout_stats(current_rows, previous_rows, week_rows)
        estate_stats = _estate_stats(current_rows, previous_rows)
        daily_velocity = _daily_velocity(conn, target_date)

        exports_dir = task_path / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        date_label = target_date.isoformat()
        csv_paths = {
            "new_listings": exports_dir / f"daily_new_listings_{date_label}.csv",
            "removed_listings": exports_dir / f"daily_removed_listings_{date_label}.csv",
            "source_disappeared": exports_dir / f"daily_source_disappeared_{date_label}.csv",
            "rent_changes": exports_dir / f"daily_rent_changes_{date_label}.csv",
            "rent_decreases": exports_dir / f"daily_rent_decreases_{date_label}.csv",
            "watchlist": exports_dir / f"daily_watchlist_{date_label}.csv",
            "fresh_value_watchlist": exports_dir / f"daily_fresh_value_watchlist_{date_label}.csv",
            "budget_stats": exports_dir / f"daily_budget_stats_{date_label}.csv",
            "withdrawal_lag_stats": exports_dir / f"daily_withdrawal_lag_stats_{date_label}.csv",
            "stale_value_watchlist": exports_dir / f"daily_stale_value_watchlist_{date_label}.csv",
        }
        _write_listing_csv(csv_paths["new_listings"], new_rows)
        _write_listing_csv(csv_paths["removed_listings"], removed_rows)
        _write_source_missing_csv(csv_paths["source_disappeared"], source_missing_rows)
        _write_rent_changes_csv(csv_paths["rent_changes"], rent_changes)
        _write_rent_changes_csv(csv_paths["rent_decreases"], rent_decreases)
        _write_listing_csv(csv_paths["watchlist"], value_rows)
        _write_listing_csv(csv_paths["fresh_value_watchlist"], fresh_value_rows)
        _write_budget_stats_csv(csv_paths["budget_stats"], budget_stats)
        _write_withdrawal_lag_stats_csv(csv_paths["withdrawal_lag_stats"], withdrawal_lag_stats)
        _write_listing_csv(csv_paths["stale_value_watchlist"], stale_value_rows)

        markdown = _render_report(
            config_area=config.area,
            target_run=target_run,
            previous_run=previous_run,
            week_run=week_run,
            current_rows=current_rows,
            previous_rows=previous_rows,
            week_rows=week_rows,
            new_rows=new_rows,
            removed_rows=removed_rows,
            source_missing_rows=source_missing_rows,
            rent_changes=rent_changes,
            rent_decreases=rent_decreases,
            value_rows=value_rows,
            fresh_value_rows=fresh_value_rows,
            budget_stats=budget_stats,
            withdrawal_rows=withdrawal_rows,
            withdrawal_lag_stats=withdrawal_lag_stats,
            stale_value_rows=stale_value_rows,
            layout_stats=layout_stats,
            estate_stats=estate_stats,
            daily_velocity=daily_velocity,
            csv_paths=csv_paths,
        )
        markdown_path = exports_dir / f"daily_report_{date_label}.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        (exports_dir / "daily_report_latest.md").write_text(markdown, encoding="utf-8")

        if print_report:
            print(markdown)

        sent_channels = send_report(markdown_path, send_channels or [])
        return ReportResult(markdown_path=markdown_path, csv_paths=csv_paths, sent_channels=sent_channels)
    finally:
        store.close()


def send_report(markdown_path: Path, channels: list[str]) -> list[str]:
    sent: list[str] = []
    for channel in channels:
        normalized = channel.strip().lower()
        if not normalized or normalized == "local":
            continue
        if normalized == "telegram":
            _send_telegram(markdown_path)
            sent.append("telegram")
        elif normalized == "email":
            _send_email(markdown_path)
            sent.append("email")
        elif normalized == "webhook":
            _send_webhook(markdown_path)
            sent.append("webhook")
        else:
            raise ValueError(f"未知发送通道：{channel}")
    return sent


def _load_private_env() -> None:
    for path in PRIVATE_ENV_PATHS:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ[key] = value


def _resolve_target_run(conn: Connection, report_date: str | date | None) -> RunRef:
    if report_date:
        parsed = date.fromisoformat(report_date) if isinstance(report_date, str) else report_date
        run = _latest_run_on_date(conn, parsed)
    else:
        run = _latest_run(conn)
    if not run:
        raise RuntimeError("没有可用于生成日报的成功扫描。请先运行 scan。")
    return run


def _latest_run(conn: Connection) -> RunRef | None:
    row = conn.execute(
        """
        SELECT id, started_at, ended_at, status
        FROM runs
        WHERE status IN ({})
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """.format(",".join("?" for _ in OK_RUN_STATUSES)),
        OK_RUN_STATUSES,
    ).fetchone()
    return _run_from_row(row)


def _latest_run_on_date(conn: Connection, target_date: date) -> RunRef | None:
    row = conn.execute(
        """
        SELECT id, started_at, ended_at, status
        FROM runs
        WHERE status IN ({}) AND substr(started_at, 1, 10) = ?
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """.format(",".join("?" for _ in OK_RUN_STATUSES)),
        (*OK_RUN_STATUSES, target_date.isoformat()),
    ).fetchone()
    return _run_from_row(row)


def _run_from_row(row: Row | None) -> RunRef | None:
    if not row:
        return None
    return RunRef(id=int(row["id"]), started_at=row["started_at"], ended_at=row["ended_at"], status=row["status"])


def _start_of_day(reference_iso: str, target_date: date) -> str:
    tz = datetime.fromisoformat(reference_iso).tzinfo or timezone(timedelta(hours=8))
    return datetime.combine(target_date, time.min, tzinfo=tz).isoformat()


def _active_listing_rows(conn: Connection, cutoff: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH active_source AS (
            SELECT
                listing_id,
                COUNT(DISTINCT source_site) AS active_source_count,
                MIN(source_site) AS sample_source_site,
                MIN(source_url) AS sample_source_url
            FROM source_state
            WHERE first_seen_at <= ?
              AND (
                  first_missing_at IS NULL
                  OR first_missing_at > ?
                  OR (active = 1 AND last_seen_at <= ?)
              )
            GROUP BY listing_id
        ),
        latest_obs AS (
            SELECT
                o.*,
                ROW_NUMBER() OVER (
                    PARTITION BY o.listing_id
                    ORDER BY r.started_at DESC, o.id DESC
                ) AS rn
            FROM listing_observations o
            JOIN runs r ON r.id = o.run_id
            WHERE r.started_at <= ?
        )
        SELECT
            l.id,
            l.canonical_id,
            l.first_seen_at,
            l.first_delisted_at,
            l.ever_rent_decreased,
            l.first_rent_decrease_at,
            l.last_rent_decrease_at,
            l.max_seen_rent_hkd,
            l.min_seen_rent_hkd,
            COALESCE(latest_obs.title, l.title) AS title,
            COALESCE(latest_obs.estate_name, l.estate_name) AS estate_name,
            COALESCE(latest_obs.block, l.block) AS block,
            COALESCE(latest_obs.floor, l.floor) AS floor,
            COALESCE(latest_obs.flat, l.flat) AS flat,
            COALESCE(latest_obs.layout, l.layout) AS layout,
            COALESCE(latest_obs.district, l.district) AS district,
            COALESCE(latest_obs.rent_hkd, l.last_rent_hkd) AS rent_hkd,
            COALESCE(latest_obs.usable_area_sqft, l.usable_area_sqft) AS usable_area_sqft,
            COALESCE(latest_obs.price_per_sqft, l.last_price_per_sqft) AS price_per_sqft,
            active_source.active_source_count,
            active_source.sample_source_site,
            active_source.sample_source_url
        FROM listings l
        JOIN active_source ON active_source.listing_id = l.id
        LEFT JOIN latest_obs ON latest_obs.listing_id = l.id AND latest_obs.rn = 1
        ORDER BY rent_hkd ASC, price_per_sqft ASC, first_seen_at DESC
        """,
        (cutoff, cutoff, cutoff, cutoff),
    ).fetchall()
    return [dict(row) for row in rows]


def _new_listing_rows(conn: Connection, start: str, cutoff: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            l.id,
            l.canonical_id,
            l.first_seen_at,
            l.first_delisted_at,
            l.ever_rent_decreased,
            l.first_rent_decrease_at,
            l.last_rent_decrease_at,
            l.max_seen_rent_hkd,
            l.min_seen_rent_hkd,
            l.title,
            l.estate_name,
            l.block,
            l.floor,
            l.flat,
            l.layout,
            l.district,
            l.last_rent_hkd AS rent_hkd,
            l.usable_area_sqft,
            l.last_price_per_sqft AS price_per_sqft,
            l.sources_count AS active_source_count,
            MIN(s.source_site) AS sample_source_site,
            MIN(s.source_url) AS sample_source_url
        FROM listings l
        LEFT JOIN source_state s ON s.listing_id = l.id AND s.active = 1
        WHERE l.first_seen_at >= ? AND l.first_seen_at <= ?
        GROUP BY l.id
        ORDER BY
            CASE WHEN l.last_price_per_sqft IS NULL THEN 1 ELSE 0 END,
            l.last_price_per_sqft ASC,
            l.last_rent_hkd ASC,
            l.first_seen_at DESC
        """,
        (start, cutoff),
    ).fetchall()
    return [dict(row) for row in rows]


def _source_missing_rows(conn: Connection, start: str, cutoff: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            s.first_missing_at,
            s.source_site,
            s.source_listing_id,
            s.source_url,
            l.id,
            l.canonical_id,
            l.first_seen_at,
            l.ever_rent_decreased,
            l.first_rent_decrease_at,
            l.last_rent_decrease_at,
            l.max_seen_rent_hkd,
            l.min_seen_rent_hkd,
            l.estate_name,
            l.block,
            l.floor,
            l.flat,
            l.layout,
            l.last_rent_hkd AS rent_hkd,
            l.usable_area_sqft,
            l.last_price_per_sqft AS price_per_sqft,
            l.active
        FROM source_state s
        JOIN listings l ON l.id = s.listing_id
        WHERE s.first_missing_at >= ? AND s.first_missing_at <= ?
        ORDER BY s.first_missing_at DESC, l.last_rent_hkd ASC
        """,
        (start, cutoff),
    ).fetchall()
    return [dict(row) for row in rows]


def _rent_changes(current_by_id: dict[int, dict[str, Any]], previous_by_id: dict[int, dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for listing_id, current in current_by_id.items():
        previous = previous_by_id.get(listing_id)
        if not previous:
            continue
        current_rent = current.get("rent_hkd")
        previous_rent = previous.get("rent_hkd")
        if current_rent is None or previous_rent is None or current_rent == previous_rent:
            continue
        row = dict(current)
        row["previous_rent_hkd"] = previous_rent
        row["rent_delta_hkd"] = int(current_rent) - int(previous_rent)
        row["rent_delta_pct"] = row["rent_delta_hkd"] / int(previous_rent) * 100 if previous_rent else None
        row["local_age_days"] = _age_days(row.get("first_seen_at"), cutoff)
        changes.append(row)
    return sorted(changes, key=lambda row: abs(row["rent_delta_hkd"]), reverse=True)


def _layout_stats(
    current_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
    week_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous = _stats_by_layout(previous_rows)
    week = _stats_by_layout(week_rows)
    rows = []
    for layout, stats in _stats_by_layout(current_rows).items():
        prev = previous.get(layout, {})
        wk = week.get(layout, {})
        rows.append(
            {
                **stats,
                "layout": layout,
                "count_delta_day": _delta(stats.get("count"), prev.get("count")),
                "avg_rent_delta_day": _delta(stats.get("avg_rent"), prev.get("avg_rent")),
                "avg_psf_delta_day": _delta(stats.get("avg_psf"), prev.get("avg_psf")),
                "count_delta_week": _delta(stats.get("count"), wk.get("count")),
                "avg_rent_delta_week": _delta(stats.get("avg_rent"), wk.get("avg_rent")),
                "avg_psf_delta_week": _delta(stats.get("avg_psf"), wk.get("avg_psf")),
            }
        )
    return sorted(rows, key=lambda row: (-row["count"], row["layout"]))


def _stats_by_layout(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("layout") or "未标明", []).append(row)
    return {layout: _basic_stats(items) for layout, items in grouped.items()}


def _basic_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rents = [int(row["rent_hkd"]) for row in rows if row.get("rent_hkd") is not None]
    psfs = [float(row["price_per_sqft"]) for row in rows if row.get("price_per_sqft") is not None]
    areas = [int(row["usable_area_sqft"]) for row in rows if row.get("usable_area_sqft") is not None]
    return {
        "count": len(rows),
        "avg_rent": sum(rents) / len(rents) if rents else None,
        "median_rent": median(rents) if rents else None,
        "min_rent": min(rents) if rents else None,
        "avg_psf": sum(psfs) / len(psfs) if psfs else None,
        "median_psf": median(psfs) if psfs else None,
        "avg_area": sum(areas) / len(areas) if areas else None,
    }


def _estate_stats(current_rows: list[dict[str, Any]], previous_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous = _count_by_estate(previous_rows)
    rows = []
    for estate, items in _group_by(current_rows, "estate_name").items():
        stats = _basic_stats(items)
        rows.append(
            {
                "estate_name": estate,
                **stats,
                "count_delta_day": stats["count"] - previous.get(estate, 0),
            }
        )
    return sorted(rows, key=lambda row: (-abs(row["count_delta_day"]), -row["count"], row["estate_name"]))[:15]


def _daily_velocity(conn: Connection, target_date: date) -> list[dict[str, Any]]:
    rows = []
    for offset in range(6, -1, -1):
        day = target_date - timedelta(days=offset)
        start = _day_prefix(day)
        new_count = conn.execute("SELECT COUNT(*) FROM listings WHERE substr(first_seen_at, 1, 10) = ?", (start,)).fetchone()[0]
        missing_count = conn.execute(
            "SELECT COUNT(*) FROM source_state WHERE substr(first_missing_at, 1, 10) = ?",
            (start,),
        ).fetchone()[0]
        latest_run = _latest_run_on_date(conn, day)
        active_count = len(_active_listing_rows(conn, latest_run.cutoff)) if latest_run else None
        rows.append({"date": start, "new": new_count, "source_missing": missing_count, "active": active_count})
    return rows


def _value_rows(rows: list[dict[str, Any]], cutoff: str, limit: int = 25) -> list[dict[str, Any]]:
    stats = _stats_by_layout(rows)
    candidates = []
    for row in rows:
        layout = row.get("layout") or "未标明"
        layout_avg_psf = stats.get(layout, {}).get("avg_psf")
        if row.get("price_per_sqft") is None or layout_avg_psf is None:
            continue
        discount = (float(row["price_per_sqft"]) - layout_avg_psf) / layout_avg_psf * 100
        if discount <= -5 or row.get("active_source_count", 0) >= 2:
            item = dict(row)
            item["psf_vs_layout_avg_pct"] = discount
            item["local_age_days"] = _age_days(item.get("first_seen_at"), cutoff)
            item["action_note"] = _action_note(item)
            candidates.append(item)
    return sorted(candidates, key=lambda row: (row.get("price_per_sqft") or 9999, row.get("rent_hkd") or 999999))[:limit]


def _fresh_value_rows(new_rows: list[dict[str, Any]], current_rows: list[dict[str, Any]], cutoff: str, limit: int = 25) -> list[dict[str, Any]]:
    stats = _stats_by_layout(current_rows)
    candidates = []
    for row in new_rows:
        item = dict(row)
        layout = item.get("layout") or "未标明"
        layout_avg_psf = stats.get(layout, {}).get("avg_psf")
        if item.get("price_per_sqft") is not None and layout_avg_psf:
            item["psf_vs_layout_avg_pct"] = (float(item["price_per_sqft"]) - layout_avg_psf) / layout_avg_psf * 100
        else:
            item["psf_vs_layout_avg_pct"] = None
        item["local_age_days"] = _age_days(item.get("first_seen_at"), cutoff)
        item["action_note"] = _action_note(item, fresh=True)
        candidates.append(item)
    return sorted(candidates, key=lambda row: (row.get("price_per_sqft") or 9999, row.get("rent_hkd") or 999999))[:limit]


def _budget_stats(
    current_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    removed_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bands = [
        ("<=15k", None, 15000),
        ("15k-16k", 15001, 16000),
        ("16k-18k", 16001, 18000),
        ("18k-20k", 18001, 20000),
        (">20k", 20001, None),
    ]
    rows = []
    layouts = sorted({row.get("layout") or "未标明" for row in current_rows})
    for label, lower, upper in bands:
        current = _rows_in_rent_band(current_rows, lower, upper)
        if not current and label == ">20k":
            continue
        previous = _rows_in_rent_band(previous_rows, lower, upper)
        today_new = _rows_in_rent_band(new_rows, lower, upper)
        today_removed = _rows_in_rent_band(removed_rows, lower, upper)
        stats = _basic_stats(current)
        row: dict[str, Any] = {
            "rent_band": label,
            "active_count": len(current),
            "active_delta_day": len(current) - len(previous) if previous_rows else None,
            "new_count": len(today_new),
            "removed_count": len(today_removed),
            "avg_rent": stats["avg_rent"],
            "avg_psf": stats["avg_psf"],
        }
        for layout in layouts:
            row[f"layout_{layout}"] = sum(1 for item in current if (item.get("layout") or "未标明") == layout)
        rows.append(row)
    return rows


def _rows_in_rent_band(rows: list[dict[str, Any]], lower: int | None, upper: int | None) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        rent = row.get("rent_hkd")
        if rent is None:
            continue
        if lower is not None and rent < lower:
            continue
        if upper is not None and rent > upper:
            continue
        result.append(row)
    return result


def _with_age(rows: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = dict(row)
        item["local_age_days"] = _age_days(item.get("first_seen_at"), cutoff)
        result.append(item)
    return result


def _withdrawal_signal_rows(conn: Connection, cutoff: str, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
    cutoff_dt = datetime.fromisoformat(cutoff)
    start = (cutoff_dt - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT
            s.first_missing_at,
            s.source_site,
            s.source_listing_id,
            s.source_url,
            l.canonical_id,
            l.first_seen_at,
            l.estate_name,
            l.block,
            l.floor,
            l.flat,
            l.layout,
            l.last_rent_hkd AS rent_hkd,
            l.usable_area_sqft,
            l.last_price_per_sqft AS price_per_sqft,
            l.active
        FROM source_state s
        JOIN listings l ON l.id = s.listing_id
        WHERE s.first_missing_at IS NOT NULL
          AND s.first_missing_at >= ?
          AND s.first_missing_at <= ?
        ORDER BY s.first_missing_at DESC, l.last_price_per_sqft ASC
        LIMIT ?
        """,
        (start, cutoff, limit),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        age = _age_days(item.get("first_seen_at"), item.get("first_missing_at"))
        item["local_age_days"] = age
        item["withdrawal_lag_bucket"] = _lag_bucket(age)
        result.append(item)
    return result


def _withdrawal_lag_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket_order = ["0-7天", "8-14天", "15-30天", "30天以上", "未知"]
    grouped = {bucket: [] for bucket in bucket_order}
    for row in rows:
        grouped.setdefault(row.get("withdrawal_lag_bucket") or "未知", []).append(row)
    total = len(rows)
    stats = []
    for bucket in bucket_order:
        items = grouped.get(bucket, [])
        if not items and bucket == "未知":
            continue
        rents = [int(row["rent_hkd"]) for row in items if row.get("rent_hkd") is not None]
        psfs = [float(row["price_per_sqft"]) for row in items if row.get("price_per_sqft") is not None]
        stats.append(
            {
                "lag_bucket": bucket,
                "source_missing_count": len(items),
                "share_pct": len(items) / total * 100 if total else None,
                "avg_rent": sum(rents) / len(rents) if rents else None,
                "avg_psf": sum(psfs) / len(psfs) if psfs else None,
            }
        )
    return stats


def _stale_value_rows(rows: list[dict[str, Any]], cutoff: str, min_age_days: int = 14, limit: int = 25) -> list[dict[str, Any]]:
    stats = _stats_by_layout(rows)
    candidates = []
    for row in rows:
        item = dict(row)
        item["local_age_days"] = _age_days(item.get("first_seen_at"), cutoff)
        if item["local_age_days"] is None or item["local_age_days"] < min_age_days:
            continue
        layout = item.get("layout") or "未标明"
        layout_avg_psf = stats.get(layout, {}).get("avg_psf")
        if item.get("price_per_sqft") is not None and layout_avg_psf:
            item["psf_vs_layout_avg_pct"] = (float(item["price_per_sqft"]) - layout_avg_psf) / layout_avg_psf * 100
        else:
            item["psf_vs_layout_avg_pct"] = None
        discount = item.get("psf_vs_layout_avg_pct")
        if (discount is not None and discount <= -8) or (item.get("rent_hkd") is not None and item["rent_hkd"] <= 16000):
            base_note = _action_note(item)
            item["action_note"] = "先确认仍可约看" if base_note == "-" else base_note + "、先确认仍可约看"
            candidates.append(item)
    return sorted(candidates, key=lambda row: (-(row.get("local_age_days") or 0), row.get("price_per_sqft") or 9999))[:limit]


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get(key) or "未标明", []).append(row)
    return grouped


def _count_by_estate(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {estate: len(items) for estate, items in _group_by(rows, "estate_name").items()}


def _delta(current: int | float | None, previous: int | float | None) -> int | float | None:
    if current is None or previous is None:
        return None
    return current - previous


def _day_prefix(value: date) -> str:
    return value.isoformat()


def _render_report(
    *,
    config_area: str,
    target_run: RunRef,
    previous_run: RunRef | None,
    week_run: RunRef | None,
    current_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
    week_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    removed_rows: list[dict[str, Any]],
    source_missing_rows: list[dict[str, Any]],
    rent_changes: list[dict[str, Any]],
    rent_decreases: list[dict[str, Any]],
    value_rows: list[dict[str, Any]],
    fresh_value_rows: list[dict[str, Any]],
    budget_stats: list[dict[str, Any]],
    withdrawal_rows: list[dict[str, Any]],
    withdrawal_lag_stats: list[dict[str, Any]],
    stale_value_rows: list[dict[str, Any]],
    layout_stats: list[dict[str, Any]],
    estate_stats: list[dict[str, Any]],
    daily_velocity: list[dict[str, Any]],
    csv_paths: dict[str, Path],
) -> str:
    current_stats = _basic_stats(current_rows)
    previous_stats = _basic_stats(previous_rows)
    week_stats = _basic_stats(week_rows)
    day_active_delta = _delta(current_stats["count"], previous_stats["count"] if previous_rows else None)
    week_active_delta = _delta(current_stats["count"], week_stats["count"] if week_rows else None)
    day_avg_rent_delta = _delta(current_stats["avg_rent"], previous_stats["avg_rent"] if previous_rows else None)
    week_avg_rent_delta = _delta(current_stats["avg_rent"], week_stats["avg_rent"] if week_rows else None)
    day_avg_psf_delta = _delta(current_stats["avg_psf"], previous_stats["avg_psf"] if previous_rows else None)
    week_avg_psf_delta = _delta(current_stats["avg_psf"], week_stats["avg_psf"] if week_rows else None)

    lines = [
        f"# {config_area}租盘日终报告",
        "",
        f"报告生成：{now_iso()}",
        f"本报告基准：run #{target_run.id}，{target_run.started_at}",
        f"昨日基准：{_run_label(previous_run)}",
        f"上周基准：{_run_label(week_run)}",
        "",
        "## 结论",
        "",
    ]
    lines.extend(f"- {item}" for item in _market_notes(
        current_stats=current_stats,
        day_active_delta=day_active_delta,
        week_active_delta=week_active_delta,
        day_avg_rent_delta=day_avg_rent_delta,
        day_avg_psf_delta=day_avg_psf_delta,
        new_count=len(new_rows),
        removed_count=len(removed_rows),
        source_missing_count=len(source_missing_rows),
        rent_decrease_count=len(rent_decreases),
        daily_velocity=daily_velocity,
    ))
    lines.extend(
        [
            "",
            "## 总览",
            "",
            "| 指标 | 当前 | 较昨日 | 较上周 |",
            "| --- | ---: | ---: | ---: |",
            f"| 活跃租盘 | {current_stats['count']} | {_fmt_delta(day_active_delta, precision=0)} | {_fmt_delta(week_active_delta, precision=0)} |",
            f"| 平均租金 | {_fmt_money_float(current_stats['avg_rent'])} | {_fmt_money_delta(day_avg_rent_delta)} | {_fmt_money_delta(week_avg_rent_delta)} |",
            f"| 平均尺租 | {_fmt_psf(current_stats['avg_psf'])} | {_fmt_psf_delta(day_avg_psf_delta)} | {_fmt_psf_delta(week_avg_psf_delta)} |",
            f"| 今日新增租盘 | {len(new_rows)} | - | - |",
            f"| 今日完整消失租盘 | {len(removed_rows)} | - | - |",
            f"| 今日来源消失记录 | {len(source_missing_rows)} | - | - |",
            f"| 今日租盘降价 | {len(rent_decreases)} | - | - |",
            f"| 今日租金调整 | {len(rent_changes)} | - | - |",
            "",
            "## 户型价格",
            "",
            "| 户型 | 活跃盘 | 平均租金 | 较昨日 | 较上周 | 平均尺租 | 较昨日 | 较上周 | 中位租金 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if layout_stats:
        for row in layout_stats:
            lines.append(
                " | ".join(
                    [
                        f"| {row['layout']}",
                        str(row["count"]),
                        _fmt_money_float(row["avg_rent"]),
                        _fmt_money_delta(row["avg_rent_delta_day"]),
                        _fmt_money_delta(row["avg_rent_delta_week"]),
                        _fmt_psf(row["avg_psf"]),
                        _fmt_psf_delta(row["avg_psf_delta_day"]),
                        _fmt_psf_delta(row["avg_psf_delta_week"]),
                        f"{compact_money(int(row['median_rent']))} |" if row["median_rent"] else "- |",
                    ]
                )
            )
    else:
        lines.append("| 暂无 | - | - | - | - | - | - | - | - |")

    _append_renter_signals_section(lines, fresh_value_rows, budget_stats, withdrawal_rows, withdrawal_lag_stats, stale_value_rows)

    lines.extend(
        [
            "",
            "## 放盘速度",
            "",
            "| 日期 | 日终活跃盘 | 新增租盘 | 来源消失 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in daily_velocity:
        lines.append(f"| {row['date']} | {row['active'] if row['active'] is not None else '-'} | {row['new']} | {row['source_missing']} |")

    lines.extend(
        [
            "",
            "## 屋苑变化",
            "",
            "| 屋苑 | 活跃盘 | 较昨日 | 平均租金 | 平均尺租 | 最低租金 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if estate_stats:
        for row in estate_stats:
            lines.append(
                f"| {_md(row['estate_name'])} | {row['count']} | {_fmt_delta(row['count_delta_day'], precision=0)} | "
                f"{_fmt_money_float(row['avg_rent'])} | {_fmt_psf(row['avg_psf'])} | {compact_money(row['min_rent'])} |"
            )
    else:
        lines.append("| 暂无 | - | - | - | - | - |")

    _append_listing_section(lines, "今日新增租盘（按尺租从低到高）", new_rows, limit=25, include_age=True)
    _append_listing_section(lines, "今日完整消失租盘", removed_rows, limit=25)
    _append_source_missing_section(lines, source_missing_rows, limit=25)
    _append_rent_decrease_section(lines, rent_decreases, limit=25)
    _append_rent_change_section(lines, rent_changes, limit=25)
    _append_listing_section(lines, "优先关注候选", value_rows, limit=25, include_reason=True, include_age=True)

    lines.extend(
        [
            "",
            "## 明细文件",
            "",
        ]
    )
    for label, path in csv_paths.items():
        lines.append(f"- {label}: `{path}`")
    lines.extend(
        [
            "",
            "## 口径说明",
            "",
            "- 新增租盘按本地首次见到该去重后租盘计算。",
            "- 今日新增租盘按尺租从低到高排序；尺租缺失的记录排在后面。",
            "- 完整消失租盘指昨日基准仍有任一来源活跃、今日基准所有来源都不再活跃。",
            "- 来源消失记录是更细粒度的代理网站记录消失；它不等同于成交。",
            "- 今日租盘降价按今日基准租金低于昨日基准租金计算，并显示本地首次见到以来的盘龄。",
            "- 撤盘滞后统计按本地首次见到到来源首次消失之间的时间分桶；它衡量网页撤盘节奏，不代表真实成交日。",
            "- `first_delisted_at` / `first_missing_at` 只代表本地首次观察到消失。",
            "- 昨日和上周比较使用对应日期最后一次成功扫描；如果当天没有成功扫描，会显示为无基准。",
            "",
        ]
    )
    return "\n".join(lines)


def _market_notes(
    *,
    current_stats: dict[str, Any],
    day_active_delta: int | float | None,
    week_active_delta: int | float | None,
    day_avg_rent_delta: int | float | None,
    day_avg_psf_delta: int | float | None,
    new_count: int,
    removed_count: int,
    source_missing_count: int,
    rent_decrease_count: int,
    daily_velocity: list[dict[str, Any]],
) -> list[str]:
    notes = []
    notes.append(
        f"当前活跃 {current_stats['count']} 个，平均租金 {_fmt_money_float(current_stats['avg_rent'])}，平均尺租 {_fmt_psf(current_stats['avg_psf'])}。"
    )
    if day_active_delta is not None:
        notes.append(f"较昨日净变化 {_fmt_delta(day_active_delta, precision=0)} 个；今日新增 {new_count} 个，完整消失 {removed_count} 个，来源消失 {source_missing_count} 条。")
    else:
        notes.append(f"今日新增 {new_count} 个，完整消失 {removed_count} 个，来源消失 {source_missing_count} 条；暂无昨日成功扫描基准。")
    if week_active_delta is not None:
        notes.append(f"较上周净变化 {_fmt_delta(week_active_delta, precision=0)} 个。")
    if day_avg_rent_delta is not None or day_avg_psf_delta is not None:
        notes.append(f"价格动能：平均租金较昨日 {_fmt_money_delta(day_avg_rent_delta)}，平均尺租较昨日 {_fmt_psf_delta(day_avg_psf_delta)}。")
    if rent_decrease_count:
        notes.append(f"今日有 {rent_decrease_count} 个租盘降价，优先看降幅和本地盘龄；盘龄较长后大幅降价的盘需要先核实是否仍可约看。")
    velocity_note = _velocity_note(daily_velocity)
    if velocity_note:
        notes.append(velocity_note)
    heat_parts = []
    if day_active_delta is not None and day_active_delta < 0:
        heat_parts.append("供应收缩")
    if day_avg_rent_delta is not None and day_avg_rent_delta > 0:
        heat_parts.append("租金抬升")
    if removed_count > new_count:
        heat_parts.append("消化快于新增")
    if heat_parts:
        notes.append("市场温度偏热信号：" + "、".join(heat_parts) + "。")
    elif day_active_delta is not None:
        notes.append("市场温度暂未显示明显升温，重点看新增盘质量和低尺租盘是否快速消失。")
    return notes


def _velocity_note(rows: list[dict[str, Any]]) -> str:
    if len(rows) < 2:
        return ""
    today = rows[-1]
    previous = rows[:-1]
    new_values = [row["new"] for row in previous]
    avg_new = sum(new_values) / len(new_values) if new_values else None
    if avg_new is None:
        return ""
    if avg_new == 0 and today["new"] > 0:
        return f"放盘速度：今日新增 {today['new']} 个，高于过去几日几乎无新增的节奏。"
    if avg_new:
        delta_pct = (today["new"] - avg_new) / avg_new * 100
        return f"放盘速度：今日新增 {today['new']} 个，过去 6 日日均 {avg_new:.1f} 个，变化 {delta_pct:+.0f}%。"
    return ""


def _append_renter_signals_section(
    lines: list[str],
    fresh_value_rows: list[dict[str, Any]],
    budget_stats: list[dict[str, Any]],
    withdrawal_rows: list[dict[str, Any]],
    withdrawal_lag_stats: list[dict[str, Any]],
    stale_value_rows: list[dict[str, Any]],
) -> None:
    lines.extend(["", "## 租客行动信号", ""])
    if fresh_value_rows:
        top = fresh_value_rows[:5]
        lines.append("今日新增里尺租最低的盘，适合优先联系和约看：")
        lines.append("")
        lines.append("| 租金 | 尺租 | 屋苑 | 座/层/室 | 户型 | 面积 | 关注点 | 来源 |")
        lines.append("| ---: | ---: | --- | --- | --- | ---: | --- | --- |")
        for row in top:
            lines.append(
                f"| {compact_money(row.get('rent_hkd'))} | {_fmt_psf(row.get('price_per_sqft'))} | "
                f"{_md(row.get('estate_name') or '-')} | {_md(_unit(row))} | {_md(row.get('layout') or '-')} | "
                f"{row.get('usable_area_sqft') or '-'} | {_md(row.get('action_note') or '-')} | {_source_link(row)} |"
            )
    else:
        lines.append("今日没有新增可排序盘。")

    lines.extend(
        [
            "",
            "预算段供应：",
            "",
            "| 租金段 | 活跃盘 | 较昨日 | 今日新增 | 今日完整消失 | 平均尺租 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in budget_stats:
        lines.append(
            f"| {row['rent_band']} | {row['active_count']} | {_fmt_delta(row.get('active_delta_day'), precision=0)} | "
            f"{row['new_count']} | {row['removed_count']} | {_fmt_psf(row.get('avg_psf'))} |"
        )

    lines.extend(["", "撤盘滞后/消化信号：", ""])
    lines.append("成交盘可能不会马上从中介网站撤下；这里看最近 30 天来源消失记录按本地盘龄分布。")
    lines.append("")
    if withdrawal_lag_stats:
        lines.append("| 撤盘盘龄 | 来源消失记录 | 占比 | 平均租金 | 平均尺租 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for row in withdrawal_lag_stats:
            lines.append(
                f"| {row['lag_bucket']} | {row['source_missing_count']} | {_fmt_pct(row.get('share_pct'))} | "
                f"{_fmt_money_float(row.get('avg_rent'))} | {_fmt_psf(row.get('avg_psf'))} |"
            )
    else:
        lines.append("暂无最近 30 天来源消失记录。")

    if withdrawal_rows:
        lines.extend(["", "最近来源消失样本：", ""])
        lines.append("| 消失时间 | 撤盘盘龄 | 来源 | 租金 | 尺租 | 屋苑 | 座/层/室 | 户型 |")
        lines.append("| --- | ---: | --- | ---: | ---: | --- | --- | --- |")
    for row in withdrawal_rows[:10]:
        lines.append(
            f"| {row.get('first_missing_at') or '-'} | {_fmt_age(row.get('local_age_days'))} | "
            f"{display_name(row.get('source_site') or '')} | {compact_money(row.get('rent_hkd'))} | "
            f"{_fmt_psf(row.get('price_per_sqft'))} | {_md(row.get('estate_name') or '-')} | "
            f"{_md(_unit(row))} | {_md(row.get('layout') or '-')} |"
        )

    lines.extend(["", "低价旧盘复核清单：", ""])
    if not stale_value_rows:
        lines.append("暂无挂盘 14 天以上且仍显著低价的活跃盘。")
        return
    lines.append("这类盘可能是真便宜，也可能是已租未撤或引流；联系时先问是否仍可约看、是否同一座/层/室。")
    lines.append("")
    lines.append("| 租金 | 尺租 | 本地盘龄 | 屋苑 | 座/层/室 | 户型 | 关注点 | 来源 |")
    lines.append("| ---: | ---: | ---: | --- | --- | --- | --- | --- |")
    for row in stale_value_rows[:10]:
        lines.append(
            f"| {compact_money(row.get('rent_hkd'))} | {_fmt_psf(row.get('price_per_sqft'))} | {_fmt_age(row.get('local_age_days'))} | "
            f"{_md(row.get('estate_name') or '-')} | {_md(_unit(row))} | {_md(row.get('layout') or '-')} | "
            f"{_md(row.get('action_note') or '-')} | {_source_link(row)} |"
        )


def _append_listing_section(
    lines: list[str],
    title: str,
    rows: list[dict[str, Any]],
    *,
    limit: int,
    include_reason: bool = False,
    include_age: bool = False,
) -> None:
    lines.extend(["", f"## {title}", ""])
    if not rows:
        lines.append("暂无。")
        return
    header = "| 租金 | 尺租 | 屋苑 | 座/层/室 | 户型 | 面积 | 来源 |"
    divider = "| ---: | ---: | --- | --- | --- | ---: | --- |"
    if include_reason:
        header = "| 租金 | 尺租 | 屋苑 | 座/层/室 | 户型 | 面积 | 关注点 | 来源 |"
        divider = "| ---: | ---: | --- | --- | --- | ---: | --- | --- |"
    if include_age and include_reason:
        header = "| 租金 | 尺租 | 屋苑 | 座/层/室 | 户型 | 面积 | 本地盘龄 | 关注点 | 来源 |"
        divider = "| ---: | ---: | --- | --- | --- | ---: | ---: | --- | --- |"
    elif include_age:
        header = "| 租金 | 尺租 | 屋苑 | 座/层/室 | 户型 | 面积 | 本地盘龄 | 来源 |"
        divider = "| ---: | ---: | --- | --- | --- | ---: | ---: | --- |"
    lines.extend([header, divider])
    for row in rows[:limit]:
        reason = ""
        if include_reason:
            discount = row.get("psf_vs_layout_avg_pct")
            reason_parts = []
            if discount is not None and discount <= -5:
                reason_parts.append(f"尺租低于同户型均值 {abs(discount):.0f}%")
            if row.get("active_source_count", 0) >= 2:
                reason_parts.append("跨来源确认")
            reason = row.get("action_note") or "、".join(reason_parts) or "-"
        cells = [
            compact_money(row.get("rent_hkd")),
            _fmt_psf(row.get("price_per_sqft")),
            _md(row.get("estate_name") or "-"),
            _md(_unit(row)),
            _md(row.get("layout") or "-"),
            str(row.get("usable_area_sqft") or "-"),
        ]
        if include_age:
            cells.append(_fmt_age(row.get("local_age_days")))
        if include_reason:
            cells.append(_md(reason))
        cells.append(_source_link(row))
        lines.append("| " + " | ".join(cells) + " |")


def _append_source_missing_section(lines: list[str], rows: list[dict[str, Any]], *, limit: int) -> None:
    lines.extend(["", "## 今日来源消失", ""])
    if not rows:
        lines.append("暂无。")
        return
    lines.extend(
        [
            "| 时间 | 来源 | 租金 | 屋苑 | 座/层/室 | 户型 | 仍有其他来源活跃 |",
            "| --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in rows[:limit]:
        lines.append(
            f"| {row.get('first_missing_at') or '-'} | {display_name(row.get('source_site') or '')} | "
            f"{compact_money(row.get('rent_hkd'))} | {_md(row.get('estate_name') or '-')} | {_md(_unit(row))} | "
            f"{_md(row.get('layout') or '-')} | {'是' if row.get('active') else '否'} |"
        )


def _append_rent_decrease_section(lines: list[str], rows: list[dict[str, Any]], *, limit: int) -> None:
    lines.extend(["", "## 今日租盘降价", ""])
    if not rows:
        lines.append("暂无。")
        return
    lines.append("重点看降幅、本地盘龄和是否仍可约看；挂盘很久后明显降价的盘要额外复核。")
    lines.extend(
        [
            "",
            "| 降幅 | 降幅比例 | 当前租金 | 昨日租金 | 实用面积(呎) | 本地盘龄 | 屋苑 | 座/层/室 | 户型 | 曾降价 | 来源 |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows[:limit]:
        lines.append(
            f"| {_fmt_money_delta(row.get('rent_delta_hkd'))} | {_fmt_pct(row.get('rent_delta_pct'))} | "
            f"{compact_money(row.get('rent_hkd'))} | {compact_money(row.get('previous_rent_hkd'))} | "
            f"{row.get('usable_area_sqft') or '-'} | {_fmt_age(row.get('local_age_days'))} | {_md(row.get('estate_name') or '-')} | "
            f"{_md(_unit(row))} | {_md(row.get('layout') or '-')} | "
            f"{'是' if row.get('ever_rent_decreased') else '否'} | {_source_link(row)} |"
        )


def _append_rent_change_section(lines: list[str], rows: list[dict[str, Any]], *, limit: int) -> None:
    lines.extend(["", "## 今日租金调整", ""])
    if not rows:
        lines.append("暂无。")
        return
    lines.extend(
        [
            "| 变化 | 当前租金 | 昨日租金 | 本地盘龄 | 屋苑 | 座/层/室 | 户型 | 曾降价 | 来源 |",
            "| ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows[:limit]:
        lines.append(
            f"| {_fmt_money_delta(row.get('rent_delta_hkd'))} | {compact_money(row.get('rent_hkd'))} | "
            f"{compact_money(row.get('previous_rent_hkd'))} | {_fmt_age(row.get('local_age_days'))} | "
            f"{_md(row.get('estate_name') or '-')} | {_md(_unit(row))} | {_md(row.get('layout') or '-')} | "
            f"{'是' if row.get('ever_rent_decreased') else '否'} | {_source_link(row)} |"
        )


def _write_listing_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "canonical_id",
        "first_seen_at",
        "local_age_days",
        "estate_name",
        "block",
        "floor",
        "flat",
        "layout",
        "rent_hkd",
        "usable_area_sqft",
        "price_per_sqft",
        "ever_rent_decreased",
        "first_rent_decrease_at",
        "last_rent_decrease_at",
        "max_seen_rent_hkd",
        "min_seen_rent_hkd",
        "active_source_count",
        "psf_vs_layout_avg_pct",
        "action_note",
        "sample_source_site",
        "sample_source_url",
        "title",
    ]
    _write_dict_csv(path, rows, columns)


def _write_source_missing_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "first_missing_at",
        "source_site",
        "source_listing_id",
        "source_url",
        "canonical_id",
        "estate_name",
        "block",
        "floor",
        "flat",
        "layout",
        "rent_hkd",
        "usable_area_sqft",
        "price_per_sqft",
        "ever_rent_decreased",
        "first_rent_decrease_at",
        "last_rent_decrease_at",
        "max_seen_rent_hkd",
        "min_seen_rent_hkd",
        "active",
    ]
    _write_dict_csv(path, rows, columns)


def _write_rent_changes_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "canonical_id",
        "estate_name",
        "block",
        "floor",
        "flat",
        "layout",
        "previous_rent_hkd",
        "rent_hkd",
        "rent_delta_hkd",
        "rent_delta_pct",
        "local_age_days",
        "ever_rent_decreased",
        "first_rent_decrease_at",
        "last_rent_decrease_at",
        "max_seen_rent_hkd",
        "min_seen_rent_hkd",
        "usable_area_sqft",
        "price_per_sqft",
        "sample_source_site",
        "sample_source_url",
        "title",
    ]
    _write_dict_csv(path, rows, columns)


def _write_budget_stats_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    extra_columns = sorted({key for row in rows for key in row if key.startswith("layout_")})
    columns = [
        "rent_band",
        "active_count",
        "active_delta_day",
        "new_count",
        "removed_count",
        "avg_rent",
        "avg_psf",
        *extra_columns,
    ]
    _write_dict_csv(path, rows, columns)


def _write_withdrawal_lag_stats_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = ["lag_bucket", "source_missing_count", "share_pct", "avg_rent", "avg_psf"]
    _write_dict_csv(path, rows, columns)


def _write_dict_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def _send_telegram(markdown_path: Path) -> None:
    _load_private_env()
    token = os.environ.get("HK_RENTAL_TRACKER_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("HK_RENTAL_TRACKER_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram 发送需要设置 HK_RENTAL_TRACKER_TELEGRAM_BOT_TOKEN 和 HK_RENTAL_TRACKER_TELEGRAM_CHAT_ID。")
    text = markdown_path.read_text(encoding="utf-8")
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text[:3900] + ("\n\n完整报告已保存到本地文件。" if len(text) > 3900 else ""),
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=payload, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram 发送失败：{body}")
    _send_telegram_document(token, chat_id, markdown_path)


def _send_telegram_document(token: str, chat_id: str, markdown_path: Path) -> None:
    boundary = "----hk-rental-tracker-boundary"
    content = markdown_path.read_bytes()
    parts = [
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{chat_id}\r\n"
        ).encode("utf-8"),
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{markdown_path.name}"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode("utf-8"),
        content,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram 报告附件发送失败：{body}")


def _send_email(markdown_path: Path) -> None:
    _load_private_env()
    host = os.environ.get("HK_RENTAL_TRACKER_SMTP_HOST")
    port = int(os.environ.get("HK_RENTAL_TRACKER_SMTP_PORT", "465"))
    username = os.environ.get("HK_RENTAL_TRACKER_SMTP_USERNAME")
    password = os.environ.get("HK_RENTAL_TRACKER_SMTP_PASSWORD")
    sender = os.environ.get("HK_RENTAL_TRACKER_EMAIL_FROM") or username
    recipients = [x.strip() for x in os.environ.get("HK_RENTAL_TRACKER_EMAIL_TO", "").split(",") if x.strip()]
    if not host or not sender or not recipients:
        raise RuntimeError("Email 发送需要设置 SMTP host、发件人和收件人环境变量。")

    text = markdown_path.read_text(encoding="utf-8")
    message = EmailMessage()
    message["Subject"] = markdown_path.stem.replace("_", " ")
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    message.add_attachment(text.encode("utf-8"), maintype="text", subtype="markdown", filename=markdown_path.name)

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=context)
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)


def _send_webhook(markdown_path: Path) -> None:
    _load_private_env()
    url = os.environ.get("HK_RENTAL_TRACKER_WEBHOOK_URL")
    if not url:
        raise RuntimeError("Webhook 发送需要设置 HK_RENTAL_TRACKER_WEBHOOK_URL。")

    text = markdown_path.read_text(encoding="utf-8")
    title = markdown_path.stem.replace("_", " ")
    output_format = os.environ.get("HK_RENTAL_TRACKER_WEBHOOK_FORMAT", "json").strip().lower()
    headers = {"User-Agent": "hk-rental-tracker/0.1"}
    token = os.environ.get("HK_RENTAL_TRACKER_WEBHOOK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if output_format == "text":
        data = text.encode("utf-8")
        headers["Content-Type"] = "text/plain; charset=utf-8"
    elif output_format == "json":
        data = json.dumps({"title": title, "text": text, "filename": markdown_path.name}, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    else:
        raise RuntimeError("HK_RENTAL_TRACKER_WEBHOOK_FORMAT 只支持 json 或 text。")

    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Webhook 发送失败：HTTP {exc.code}; {body}") from exc
    if status < 200 or status >= 300:
        raise RuntimeError(f"Webhook 发送失败：HTTP {status}; {body}")


def _age_days(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds() / 86400)


def _fmt_age(value: int | float | None) -> str:
    if value is None:
        return "-"
    if value < 1:
        return "今日"
    return f"{value:.1f}天"


def _action_note(row: dict[str, Any], fresh: bool = False) -> str:
    parts = []
    if fresh:
        parts.append("新增")
    discount = row.get("psf_vs_layout_avg_pct")
    if discount is not None and discount <= -10:
        parts.append(f"尺租低同户型均值 {abs(discount):.0f}%")
    elif discount is not None and discount <= -5:
        parts.append(f"尺租低同户型均值 {abs(discount):.0f}%")
    rent = row.get("rent_hkd")
    if rent is not None and rent <= 16000:
        parts.append("预算友好")
    if row.get("active_source_count", 0) >= 2:
        parts.append("跨来源确认")
    age = row.get("local_age_days")
    if age is not None and age <= 1:
        parts.append("刚出现")
    return "、".join(parts) or "-"


def _lag_bucket(age_days: int | float | None) -> str:
    if age_days is None:
        return "未知"
    if age_days <= 7:
        return "0-7天"
    if age_days <= 14:
        return "8-14天"
    if age_days <= 30:
        return "15-30天"
    return "30天以上"


def _run_label(run: RunRef | None) -> str:
    if not run:
        return "无成功扫描"
    return f"run #{run.id}，{run.started_at}"


def _fmt_money_float(value: int | float | None) -> str:
    if value is None:
        return "-"
    return compact_money(int(round(value)))


def _fmt_money_delta(value: int | float | None) -> str:
    if value is None:
        return "-"
    rounded = int(round(abs(value)))
    if value > 0:
        return f"+{compact_money(rounded)}"
    if value < 0:
        return f"-{compact_money(rounded)}"
    return "$0"


def _fmt_psf(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"${float(value):.1f}/呎"


def _fmt_psf_delta(value: int | float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}${float(value):.1f}/呎"


def _fmt_pct(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.0f}%"


def _fmt_delta(value: int | float | None, *, precision: int = 1) -> str:
    if value is None:
        return "-"
    if precision == 0:
        rounded = int(round(value))
        return f"{rounded:+d}"
    return f"{value:+.{precision}f}"


def _unit(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ["block", "floor", "flat"]).strip() or "-"


def _md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _source_link(row: dict[str, Any]) -> str:
    site = display_name(row.get("sample_source_site") or row.get("source_site") or "")
    url = row.get("sample_source_url") or row.get("source_url")
    if not url:
        return _md(site or "-")
    safe_url = str(url).replace("(", "%28").replace(")", "%29")
    return f"[{_md(site or '来源')}]({safe_url})"
