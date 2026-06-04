from __future__ import annotations

import csv
from pathlib import Path
from sqlite3 import Row

from .normalization import compact_money, now_iso
from .site_catalog import display_name
from .storage import RentalStore


LISTING_COLUMNS = [
    "canonical_id",
    "active",
    "first_seen_at",
    "first_delisted_at",
    "last_seen_at",
    "estate_name",
    "block",
    "floor",
    "flat",
    "layout",
    "district",
    "last_rent_hkd",
    "usable_area_sqft",
    "last_price_per_sqft",
    "ever_rent_decreased",
    "first_rent_decrease_at",
    "last_rent_decrease_at",
    "max_seen_rent_hkd",
    "min_seen_rent_hkd",
    "sources_count",
    "source_summary_json",
    "title",
]


def write_csv(path: Path, rows: list[Row], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in columns})


def export_task(task_dir: str | Path, store: RentalStore) -> Path:
    base = Path(task_dir) / "exports"
    base.mkdir(parents=True, exist_ok=True)
    write_csv(base / "active_listings.csv", store.active_listings(), LISTING_COLUMNS)
    write_csv(base / "all_listings.csv", store.all_listings(), LISTING_COLUMNS)
    changes = store.latest_changes()
    if changes:
        write_csv(
            base / "latest_changes.csv",
            changes,
            [
                "canonical_id",
                "source_site",
                "source_listing_id",
                "source_url",
                "fetched_at",
                "estate_name",
                "block",
                "floor",
                "flat",
                "layout",
                "rent_hkd",
                "usable_area_sqft",
                "price_per_sqft",
                "title",
            ],
        )
    write_summary(base / "summary.md", store)
    return base


def write_summary(path: Path, store: RentalStore) -> None:
    counts = store.summary_counts()
    active = store.active_listings(limit=20)
    by_estate = store.active_by_estate()
    by_source = store.active_by_source()
    recently_delisted = store.recently_delisted_sources(limit=10)
    lines = [
        "# 租盘追踪摘要",
        "",
        f"生成时间：{now_iso()}",
        "",
        "## 总览",
        "",
        f"- 租盘总数：{counts.get('total') or 0}",
        f"- 当前活跃：{counts.get('active') or 0}",
        f"- 曾在至少一个来源消失：{counts.get('with_delist') or 0}",
        f"- 曾降价：{counts.get('with_rent_decrease') or 0}",
        f"- 当前最低租金：{compact_money(counts.get('min_rent'))}",
        f"- 当前平均租金：{compact_money(int(counts['avg_rent'])) if counts.get('avg_rent') else '-'}",
        f"- 当前最高租金：{compact_money(counts.get('max_rent'))}",
        f"- 平均尺租：${counts['avg_psf']:.1f}/呎" if counts.get("avg_psf") else "- 平均尺租：-",
        "",
        "## 来源覆盖",
        "",
    ]
    if not by_source:
        lines.append("暂无来源覆盖数据。")
    else:
        for row in by_source:
            lines.append(f"- {display_name(row['source_site'])}：{row['active_count']} 个活跃来源记录")
    lines.extend(
        [
            "",
            "## 活跃屋苑分布",
            "",
        ]
    )
    if not by_estate:
        lines.append("暂无活跃屋苑数据。")
    else:
        lines.append("| 屋苑 | 活跃盘 | 最低租金 | 平均租金 | 平均尺租 | 平均面积 | 跨来源确认 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in by_estate:
            avg_rent = compact_money(int(row["avg_rent"])) if row["avg_rent"] else "-"
            avg_psf = f"${row['avg_psf']:.1f}" if row["avg_psf"] else "-"
            avg_area = f"{row['avg_area']:.0f}" if row["avg_area"] else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["estate_name"] or "-",
                        str(row["count"] or 0),
                        compact_money(row["min_rent"]),
                        avg_rent,
                        avg_psf,
                        avg_area,
                        str(row["cross_source_count"] or 0),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## 最低租金活跃盘",
            "",
        ]
    )
    if not active:
        lines.append("暂无活跃租盘。")
    else:
        lines.append("| 租金 | 尺租 | 屋苑 | 座/层/室 | 户型 | 面积 | 曾降价 | 来源记录数 |")
        lines.append("| ---: | ---: | --- | --- | --- | ---: | --- | ---: |")
        for row in active[:20]:
            unit = " ".join(str(row[x] or "") for x in ["block", "floor", "flat"]).strip() or "-"
            psf = f"${row['last_price_per_sqft']:.1f}" if row["last_price_per_sqft"] else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        compact_money(row["last_rent_hkd"]),
                        psf,
                        row["estate_name"] or "-",
                        unit,
                        row["layout"] or "-",
                        str(row["usable_area_sqft"] or "-"),
                        "是" if row["ever_rent_decreased"] else "否",
                        str(row["sources_count"] or 0),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## 最近来源消失",
            "",
        ]
    )
    if not recently_delisted:
        lines.append("暂无来源消失记录。")
    else:
        lines.append("| 时间 | 来源 | 屋苑 | 座/层/室 | 租金 | 面积 |")
        lines.append("| --- | --- | --- | --- | ---: | ---: |")
        for row in recently_delisted:
            unit = " ".join(str(row[x] or "") for x in ["block", "floor", "flat"]).strip() or "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["first_missing_at"] or "-",
                        display_name(row["source_site"]),
                        row["estate_name"] or "-",
                        unit,
                        compact_money(row["last_rent_hkd"]),
                        str(row["usable_area_sqft"] or "-"),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## 来源说明",
            "",
            ", ".join(display_name(site) for site in ["midland", "ricacorp", "centanet", "hkp"]),
            "",
            "`first_delisted_at` 是本地首次发现某来源消失的时间，不代表成交日期。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
