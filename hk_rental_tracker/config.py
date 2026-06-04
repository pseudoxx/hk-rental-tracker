from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import SearchFilters
from .normalization import normalize_layout_terms, now_iso, split_terms, text_variants


DEFAULT_SITES = ["midland", "centanet", "hkp", "ricacorp"]
AUTHORIZATION_REQUIRED_SITES: set[str] = set()


def default_source_policy(site: str, authorized: bool = False) -> dict[str, Any]:
    return {"mode": "standard", "enabled": True}


def site_is_authorized(site: str, source_policies: dict[str, dict[str, Any]]) -> bool:
    if site not in AUTHORIZATION_REQUIRED_SITES:
        return True
    policy = source_policies.get(site) or {}
    return bool(policy.get("enabled") and policy.get("attested_by_user"))


@dataclass
class TaskConfig:
    slug: str
    area: str
    created_at: str
    area_aliases: list[str] = field(default_factory=list)
    filters: SearchFilters = field(default_factory=SearchFilters)
    sites: list[str] = field(default_factory=lambda: list(DEFAULT_SITES))
    source_search_urls: dict[str, list[str]] = field(default_factory=dict)
    source_policies: dict[str, dict[str, Any]] = field(default_factory=dict)
    scan_options: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @property
    def area_terms(self) -> list[str]:
        terms = []
        for value in [self.area, *self.area_aliases]:
            terms.extend(text_variants(value))
        seen = set()
        result = []
        for term in terms:
            if term and term not in seen:
                seen.add(term)
                result.append(term)
        return result

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["filters"] = self.filters.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskConfig":
        raw_policies = data.get("source_policies") or {}
        source_policies = {k: dict(v) for k, v in raw_policies.items()}
        for site in AUTHORIZATION_REQUIRED_SITES:
            source_policies.setdefault(site, default_source_policy(site, authorized=False))
        return cls(
            slug=data["slug"],
            area=data["area"],
            created_at=data.get("created_at") or now_iso(),
            area_aliases=list(data.get("area_aliases") or []),
            filters=SearchFilters.from_dict(data.get("filters") or {}),
            sites=list(data.get("sites") or DEFAULT_SITES),
            source_search_urls={k: list(v) for k, v in (data.get("source_search_urls") or {}).items()},
            source_policies=source_policies,
            scan_options=dict(data.get("scan_options") or {}),
            notes=data.get("notes") or "",
        )

    def site_is_authorized(self, site: str) -> bool:
        return site_is_authorized(site, self.source_policies)


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "rental-task"


def task_config_path(task_dir: str | Path) -> Path:
    return Path(task_dir) / "tracker.json"


def load_task_config(task_dir: str | Path) -> TaskConfig:
    path = task_config_path(task_dir)
    with path.open("r", encoding="utf-8") as f:
        return TaskConfig.from_dict(json.load(f))


def save_task_config(task_dir: str | Path, config: TaskConfig) -> None:
    path = task_config_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
        f.write("\n")


def create_task_config(
    slug: str | None,
    area: str,
    max_rent: int | None,
    min_rent: int | None,
    min_area_sqft: int | None = None,
    max_area_sqft: int | None = None,
    min_gross_area_sqft: int | None = None,
    max_gross_area_sqft: int | None = None,
    min_building_age_years: int | None = None,
    max_building_age_years: int | None = None,
    min_price_per_sqft: float | None = None,
    max_price_per_sqft: float | None = None,
    layouts: str | None = None,
    estates: str | None = None,
    keywords: str | None = None,
    sites: str | None = None,
    excluded_estates: str | None = None,
    excluded_keywords: str | None = None,
    ricacorp_authorized: bool = False,
    notes: str = "",
) -> TaskConfig:
    area_aliases = []
    for variant in text_variants(area):
        if variant != area:
            area_aliases.append(variant)
    filters = SearchFilters(
        min_rent=min_rent,
        max_rent=max_rent,
        min_area_sqft=min_area_sqft,
        max_area_sqft=max_area_sqft,
        min_gross_area_sqft=min_gross_area_sqft,
        max_gross_area_sqft=max_gross_area_sqft,
        min_building_age_years=min_building_age_years,
        max_building_age_years=max_building_age_years,
        min_price_per_sqft=min_price_per_sqft,
        max_price_per_sqft=max_price_per_sqft,
        layouts=normalize_layout_terms(layouts),
        estates=split_terms(estates),
        keywords=split_terms(keywords),
        excluded_estates=split_terms(excluded_estates),
        excluded_keywords=split_terms(excluded_keywords),
    )
    selected_sites = split_terms(sites) if sites is not None else list(DEFAULT_SITES)
    source_policies = {site: default_source_policy(site, authorized=ricacorp_authorized) for site in AUTHORIZATION_REQUIRED_SITES}
    return TaskConfig(
        slug=slugify(slug or area),
        area=area,
        created_at=now_iso(),
        area_aliases=area_aliases,
        filters=filters,
        sites=selected_sites,
        source_search_urls={site: [] for site in selected_sites},
        source_policies=source_policies,
        scan_options={
            "render_javascript": False,
            "request_delay_seconds": 1.0,
            "mark_missing_only_when_source_has_results": True,
            "preflight_network": True,
        },
        notes=notes,
    )
