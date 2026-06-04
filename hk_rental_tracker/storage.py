from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .dedupe import score_candidate, strict_candidate_match
from .models import ListingObservation
from .normalization import normalize_text, now_iso, stable_hash


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    mode TEXT NOT NULL,
    task_slug TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT NOT NULL UNIQUE,
    identity_key TEXT NOT NULL,
    title TEXT,
    estate_name TEXT,
    normalized_estate_name TEXT,
    building TEXT,
    block TEXT,
    floor TEXT,
    flat TEXT,
    layout TEXT,
    district TEXT,
    address TEXT,
    usable_area_sqft INTEGER,
    first_seen_at TEXT NOT NULL,
    first_delisted_at TEXT,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    last_rent_hkd INTEGER,
    last_price_per_sqft REAL,
    ever_rent_decreased INTEGER NOT NULL DEFAULT 0,
    first_rent_decrease_at TEXT,
    last_rent_decrease_at TEXT,
    max_seen_rent_hkd INTEGER,
    min_seen_rent_hkd INTEGER,
    sources_count INTEGER NOT NULL DEFAULT 0,
    source_summary_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_estate ON listings(normalized_estate_name);
CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(active);

CREATE TABLE IF NOT EXISTS listing_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    source_site TEXT NOT NULL,
    source_listing_id TEXT,
    source_url TEXT NOT NULL,
    source_key TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    title TEXT,
    estate_name TEXT,
    building TEXT,
    block TEXT,
    floor TEXT,
    flat TEXT,
    layout TEXT,
    district TEXT,
    address TEXT,
    rent_hkd INTEGER,
    usable_area_sqft INTEGER,
    price_per_sqft REAL,
    updated_at_text TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_observations_listing ON listing_observations(listing_id);
CREATE INDEX IF NOT EXISTS idx_observations_source ON listing_observations(source_site, source_listing_id);

CREATE TABLE IF NOT EXISTS source_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    source_site TEXT NOT NULL,
    source_listing_id TEXT,
    source_url TEXT NOT NULL,
    source_key TEXT NOT NULL UNIQUE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_missing_at TEXT,
    missing_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_source_state_listing ON source_state(listing_id);
CREATE INDEX IF NOT EXISTS idx_source_state_site ON source_state(source_site, active);
"""


class RentalStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        migrated = self._ensure_listing_history_columns()
        if migrated:
            self._backfill_rent_history_fields()
        self.conn.commit()

    def _ensure_listing_history_columns(self) -> bool:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(listings)").fetchall()}
        migrations = [
            ("ever_rent_decreased", "INTEGER NOT NULL DEFAULT 0"),
            ("first_rent_decrease_at", "TEXT"),
            ("last_rent_decrease_at", "TEXT"),
            ("max_seen_rent_hkd", "INTEGER"),
            ("min_seen_rent_hkd", "INTEGER"),
        ]
        migrated = False
        for name, definition in migrations:
            if name in columns:
                continue
            try:
                self.conn.execute(f"ALTER TABLE listings ADD COLUMN {name} {definition}")
                migrated = True
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        return migrated

    def _backfill_rent_history_fields(self) -> None:
        rows = self.conn.execute(
            """
            SELECT o.listing_id, o.rent_hkd, r.started_at, o.id
            FROM listing_observations o
            JOIN runs r ON r.id = o.run_id
            WHERE o.rent_hkd IS NOT NULL
            ORDER BY o.listing_id, r.started_at, o.id
            """
        ).fetchall()
        by_listing: dict[int, dict[str, Any]] = {}
        previous_by_listing: dict[int, int] = {}
        for row in rows:
            listing_id = int(row["listing_id"])
            rent = int(row["rent_hkd"])
            info = by_listing.setdefault(
                listing_id,
                {
                    "min_seen_rent_hkd": rent,
                    "max_seen_rent_hkd": rent,
                    "ever_rent_decreased": 0,
                    "first_rent_decrease_at": None,
                    "last_rent_decrease_at": None,
                },
            )
            info["min_seen_rent_hkd"] = min(int(info["min_seen_rent_hkd"]), rent)
            info["max_seen_rent_hkd"] = max(int(info["max_seen_rent_hkd"]), rent)
            previous = previous_by_listing.get(listing_id)
            if previous is not None and rent < previous:
                info["ever_rent_decreased"] = 1
                if info["first_rent_decrease_at"] is None:
                    info["first_rent_decrease_at"] = row["started_at"]
                info["last_rent_decrease_at"] = row["started_at"]
            previous_by_listing[listing_id] = rent

        listing_rows = self.conn.execute("SELECT id, last_rent_hkd FROM listings").fetchall()
        for row in listing_rows:
            listing_id = int(row["id"])
            info = by_listing.get(listing_id)
            if info is None:
                rent = row["last_rent_hkd"]
                info = {
                    "min_seen_rent_hkd": rent,
                    "max_seen_rent_hkd": rent,
                    "ever_rent_decreased": 0,
                    "first_rent_decrease_at": None,
                    "last_rent_decrease_at": None,
                }
            self.conn.execute(
                """
                UPDATE listings
                SET ever_rent_decreased = ?,
                    first_rent_decrease_at = ?,
                    last_rent_decrease_at = ?,
                    max_seen_rent_hkd = ?,
                    min_seen_rent_hkd = ?
                WHERE id = ?
                """,
                (
                    info["ever_rent_decreased"],
                    info["first_rent_decrease_at"],
                    info["last_rent_decrease_at"],
                    info["max_seen_rent_hkd"],
                    info["min_seen_rent_hkd"],
                    listing_id,
                ),
            )

    def start_run(self, mode: str, task_slug: str, filters: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(started_at, mode, task_slug, filters_json, status) VALUES (?, ?, ?, ?, ?)",
            (now_iso(), mode, task_slug, json.dumps(filters, ensure_ascii=False), "running"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, notes: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET ended_at = ?, status = ?, notes = ? WHERE id = ?",
            (now_iso(), status, notes, run_id),
        )
        self.conn.commit()

    def run_started_at(self, run_id: int) -> str:
        row = self.conn.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row["started_at"]

    def find_listing_for_observation(self, obs: ListingObservation) -> int | None:
        source = self.conn.execute(
            "SELECT listing_id FROM source_state WHERE source_key = ?",
            (obs.source_key,),
        ).fetchone()
        if source:
            return int(source["listing_id"])

        exact = self.conn.execute(
            "SELECT * FROM listings WHERE identity_key = ?",
            (obs.identity_key,),
        ).fetchone()
        if exact and strict_candidate_match(obs, exact):
            return int(exact["id"])

        candidates = self.conn.execute(
            """
            SELECT * FROM listings
            WHERE normalized_estate_name = ?
               OR usable_area_sqft = ?
               OR active = 1
            ORDER BY active DESC, last_seen_at DESC
            LIMIT 100
            """,
            (obs.normalized_estate, obs.usable_area_sqft),
        ).fetchall()
        best_id = None
        best_score = 0.0
        for row in candidates:
            score = score_candidate(obs, row)
            if score > best_score:
                best_id = int(row["id"])
                best_score = score
        return best_id if best_score >= 0.68 else None

    def create_listing(self, obs: ListingObservation, seen_at: str) -> int:
        canonical_id = stable_hash(f"{obs.identity_key}|{obs.source_key}", 20)
        cur = self.conn.execute(
            """
            INSERT INTO listings(
                canonical_id, identity_key, title, estate_name, normalized_estate_name,
                building, block, floor, flat, layout, district, address, usable_area_sqft,
                first_seen_at, last_seen_at, active, last_rent_hkd, last_price_per_sqft,
                max_seen_rent_hkd, min_seen_rent_hkd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                canonical_id,
                obs.identity_key,
                obs.title,
                obs.estate_name,
                normalize_text(obs.estate_name or ""),
                obs.building,
                obs.block,
                obs.floor,
                obs.flat,
                obs.layout,
                obs.district,
                obs.address,
                obs.usable_area_sqft,
                seen_at,
                seen_at,
                obs.rent_hkd,
                obs.price_per_sqft,
                obs.rent_hkd,
                obs.rent_hkd,
            ),
        )
        return int(cur.lastrowid)

    def upsert_observation(self, obs: ListingObservation, run_id: int) -> int:
        seen_at = self.run_started_at(run_id)
        listing_id = self.find_listing_for_observation(obs)
        if listing_id is None:
            listing_id = self.create_listing(obs, seen_at)
        self.conn.execute(
            """
            INSERT INTO listing_observations(
                listing_id, run_id, source_site, source_listing_id, source_url, source_key,
                fetched_at, title, estate_name, building, block, floor, flat, layout, district,
                address, rent_hkd, usable_area_sqft, price_per_sqft, updated_at_text,
                tags_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id,
                run_id,
                obs.source_site,
                obs.source_listing_id,
                obs.source_url,
                obs.source_key,
                obs.fetched_at,
                obs.title,
                obs.estate_name,
                obs.building,
                obs.block,
                obs.floor,
                obs.flat,
                obs.layout,
                obs.district,
                obs.address,
                obs.rent_hkd,
                obs.usable_area_sqft,
                obs.price_per_sqft,
                obs.updated_at_text,
                json.dumps(obs.tags, ensure_ascii=False),
                json.dumps(obs.raw, ensure_ascii=False),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO source_state(
                listing_id, source_site, source_listing_id, source_url, source_key,
                first_seen_at, last_seen_at, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source_key) DO UPDATE SET
                listing_id=excluded.listing_id,
                source_url=excluded.source_url,
                last_seen_at=excluded.last_seen_at,
                active=1
            """,
            (
                listing_id,
                obs.source_site,
                obs.source_listing_id,
                obs.source_url,
                obs.source_key,
                seen_at,
                seen_at,
            ),
        )
        self._refresh_listing_from_observation(listing_id, obs, seen_at)
        self.conn.commit()
        return listing_id

    def _refresh_listing_from_observation(self, listing_id: int, obs: ListingObservation, seen_at: str) -> None:
        current = self.conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        previous_rent = current["last_rent_hkd"]
        current_max = current["max_seen_rent_hkd"]
        current_min = current["min_seen_rent_hkd"]
        max_seen = current_max
        min_seen = current_min
        ever_rent_decreased = int(current["ever_rent_decreased"] or 0)
        first_rent_decrease_at = current["first_rent_decrease_at"]
        last_rent_decrease_at = current["last_rent_decrease_at"]
        if obs.rent_hkd is not None:
            rent = int(obs.rent_hkd)
            max_seen = rent if max_seen is None else max(int(max_seen), rent)
            min_seen = rent if min_seen is None else min(int(min_seen), rent)
            if previous_rent is not None and rent < int(previous_rent):
                ever_rent_decreased = 1
                first_rent_decrease_at = first_rent_decrease_at or seen_at
                last_rent_decrease_at = seen_at

        values = {
            "title": current["title"] or obs.title,
            "estate_name": current["estate_name"] or obs.estate_name,
            "normalized_estate_name": current["normalized_estate_name"] or normalize_text(obs.estate_name or ""),
            "building": current["building"] or obs.building,
            "block": current["block"] or obs.block,
            "floor": current["floor"] or obs.floor,
            "flat": current["flat"] or obs.flat,
            "layout": current["layout"] or obs.layout,
            "district": current["district"] or obs.district,
            "address": current["address"] or obs.address,
            "usable_area_sqft": current["usable_area_sqft"] or obs.usable_area_sqft,
            "last_seen_at": seen_at,
            "active": 1,
            "last_rent_hkd": obs.rent_hkd or current["last_rent_hkd"],
            "last_price_per_sqft": obs.price_per_sqft or current["last_price_per_sqft"],
            "ever_rent_decreased": ever_rent_decreased,
            "first_rent_decrease_at": first_rent_decrease_at,
            "last_rent_decrease_at": last_rent_decrease_at,
            "max_seen_rent_hkd": max_seen,
            "min_seen_rent_hkd": min_seen,
        }
        self.conn.execute(
            """
            UPDATE listings
            SET title=:title, estate_name=:estate_name, normalized_estate_name=:normalized_estate_name,
                building=:building, block=:block, floor=:floor, flat=:flat, layout=:layout,
                district=:district, address=:address, usable_area_sqft=:usable_area_sqft,
                last_seen_at=:last_seen_at, active=:active, last_rent_hkd=:last_rent_hkd,
                last_price_per_sqft=:last_price_per_sqft,
                ever_rent_decreased=:ever_rent_decreased,
                first_rent_decrease_at=:first_rent_decrease_at,
                last_rent_decrease_at=:last_rent_decrease_at,
                max_seen_rent_hkd=:max_seen_rent_hkd,
                min_seen_rent_hkd=:min_seen_rent_hkd
            WHERE id=:listing_id
            """,
            {**values, "listing_id": listing_id},
        )
        self.refresh_source_summary(listing_id)

    def refresh_source_summary(self, listing_id: int) -> None:
        rows = self.conn.execute(
            """
            SELECT source_site, source_url, source_listing_id, first_seen_at, last_seen_at, first_missing_at, active
            FROM source_state
            WHERE listing_id = ?
            ORDER BY source_site
            """,
            (listing_id,),
        ).fetchall()
        summary = {
            row["source_site"]: {
                "url": row["source_url"],
                "id": row["source_listing_id"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "first_missing_at": row["first_missing_at"],
                "active": bool(row["active"]),
            }
            for row in rows
        }
        active = any(row["active"] for row in rows)
        self.conn.execute(
            """
            UPDATE listings
            SET sources_count = ?, source_summary_json = ?, active = ?
            WHERE id = ?
            """,
            (len(rows), json.dumps(summary, ensure_ascii=False), 1 if active else 0, listing_id),
        )

    def mark_missing_sources(self, site: str, seen_source_keys: set[str], run_id: int, enabled: bool = True) -> int:
        if not enabled:
            return 0
        as_of = self.run_started_at(run_id)
        active_rows = self.conn.execute(
            "SELECT * FROM source_state WHERE source_site = ? AND active = 1",
            (site,),
        ).fetchall()
        changed = 0
        for row in active_rows:
            if row["source_key"] in seen_source_keys:
                continue
            self.conn.execute(
                """
                UPDATE source_state
                SET active = 0,
                    first_missing_at = COALESCE(first_missing_at, ?),
                    missing_count = missing_count + 1
                WHERE id = ?
                """,
                (as_of, row["id"]),
            )
            listing = self.conn.execute("SELECT first_delisted_at FROM listings WHERE id = ?", (row["listing_id"],)).fetchone()
            if listing and listing["first_delisted_at"] is None:
                self.conn.execute(
                    "UPDATE listings SET first_delisted_at = ? WHERE id = ?",
                    (as_of, row["listing_id"]),
                )
            self.refresh_source_summary(int(row["listing_id"]))
            changed += 1
        self.conn.commit()
        return changed

    def active_listings(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM listings WHERE active = 1 ORDER BY last_rent_hkd ASC, last_seen_at DESC"
        params: tuple[Any, ...] = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(sql, params).fetchall()

    def all_listings(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM listings ORDER BY active DESC, last_seen_at DESC").fetchall()

    def latest_changes(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT o.*, l.canonical_id
            FROM listing_observations o
            JOIN listings l ON l.id = o.listing_id
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def active_by_estate(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            WITH site_counts AS (
                SELECT listing_id, COUNT(DISTINCT source_site) AS distinct_sites
                FROM source_state
                WHERE active = 1
                GROUP BY listing_id
            )
            SELECT
                COALESCE(l.estate_name, '(unknown)') AS estate_name,
                COUNT(*) AS count,
                MIN(l.last_rent_hkd) AS min_rent,
                AVG(l.last_rent_hkd) AS avg_rent,
                MIN(l.last_price_per_sqft) AS min_psf,
                AVG(l.last_price_per_sqft) AS avg_psf,
                AVG(l.usable_area_sqft) AS avg_area,
                SUM(CASE WHEN COALESCE(sc.distinct_sites, 0) >= 2 THEN 1 ELSE 0 END) AS cross_source_count
            FROM listings l
            LEFT JOIN site_counts sc ON sc.listing_id = l.id
            WHERE l.active = 1
            GROUP BY COALESCE(l.estate_name, '(unknown)')
            ORDER BY count DESC, min_rent ASC
            LIMIT 30
            """
        ).fetchall()

    def active_by_source(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT source_site, COUNT(*) AS active_count
            FROM source_state
            WHERE active = 1
            GROUP BY source_site
            ORDER BY active_count DESC
            """
        ).fetchall()

    def recently_delisted_sources(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT s.source_site, s.first_missing_at, l.estate_name, l.block, l.floor, l.flat,
                   l.last_rent_hkd, l.usable_area_sqft, s.source_url
            FROM source_state s
            JOIN listings l ON l.id = s.listing_id
            WHERE s.first_missing_at IS NOT NULL
            ORDER BY s.first_missing_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def summary_counts(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN first_delisted_at IS NOT NULL THEN 1 ELSE 0 END) AS with_delist,
                SUM(CASE WHEN ever_rent_decreased = 1 THEN 1 ELSE 0 END) AS with_rent_decrease,
                MIN(last_rent_hkd) AS min_rent,
                AVG(last_rent_hkd) AS avg_rent,
                MAX(last_rent_hkd) AS max_rent,
                AVG(last_price_per_sqft) AS avg_psf
            FROM listings
            """
        ).fetchone()
        return dict(row)
