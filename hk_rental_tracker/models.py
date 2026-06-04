from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .normalization import canonical_layout, layout_bedroom_count, normalize_layout_terms, normalize_loose, normalize_text, split_terms, stable_hash


@dataclass
class SearchFilters:
    min_rent: int | None = None
    max_rent: int | None = None
    min_area_sqft: int | None = None
    max_area_sqft: int | None = None
    min_gross_area_sqft: int | None = None
    max_gross_area_sqft: int | None = None
    min_building_age_years: int | None = None
    max_building_age_years: int | None = None
    min_price_per_sqft: float | None = None
    max_price_per_sqft: float | None = None
    layouts: list[str] = field(default_factory=list)
    estates: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    excluded_estates: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchFilters":
        return cls(
            min_rent=data.get("min_rent"),
            max_rent=data.get("max_rent"),
            min_area_sqft=data.get("min_area_sqft") or data.get("min_usable_area_sqft"),
            max_area_sqft=data.get("max_area_sqft") or data.get("max_usable_area_sqft"),
            min_gross_area_sqft=data.get("min_gross_area_sqft") or data.get("min_gross_area"),
            max_gross_area_sqft=data.get("max_gross_area_sqft") or data.get("max_gross_area"),
            min_building_age_years=data.get("min_building_age_years") or data.get("min_building_age"),
            max_building_age_years=data.get("max_building_age_years") or data.get("max_building_age"),
            min_price_per_sqft=data.get("min_price_per_sqft") or data.get("min_psf"),
            max_price_per_sqft=data.get("max_price_per_sqft") or data.get("max_psf"),
            layouts=normalize_layout_terms(data.get("layouts") or []),
            estates=list(data.get("estates") or []),
            keywords=list(data.get("keywords") or []),
            excluded_estates=list(data.get("excluded_estates") or data.get("estate_blacklist") or []),
            excluded_keywords=list(data.get("excluded_keywords") or data.get("keyword_blacklist") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def matches(self, observation: "ListingObservation", area_terms: list[str]) -> bool:
        haystack = " ".join(
            x
            for x in [
                observation.title,
                observation.estate_name,
                observation.address,
                observation.district,
                observation.layout,
            ]
            if x
        )
        normalized_haystack = normalize_text(haystack)
        if area_terms and not any(normalize_text(term) in normalized_haystack for term in area_terms):
            return False
        if self.min_rent is not None and observation.rent_hkd is not None:
            if observation.rent_hkd < self.min_rent:
                return False
        if self.max_rent is not None and observation.rent_hkd is not None:
            if observation.rent_hkd > self.max_rent:
                return False
        if self.min_area_sqft is not None:
            if observation.usable_area_sqft is None or observation.usable_area_sqft < self.min_area_sqft:
                return False
        if self.max_area_sqft is not None:
            if observation.usable_area_sqft is None or observation.usable_area_sqft > self.max_area_sqft:
                return False
        if self.min_gross_area_sqft is not None:
            if observation.gross_area_sqft is None or observation.gross_area_sqft < self.min_gross_area_sqft:
                return False
        if self.max_gross_area_sqft is not None:
            if observation.gross_area_sqft is None or observation.gross_area_sqft > self.max_gross_area_sqft:
                return False
        if self.min_building_age_years is not None:
            if observation.building_age_years is None or observation.building_age_years < self.min_building_age_years:
                return False
        if self.max_building_age_years is not None:
            if observation.building_age_years is None or observation.building_age_years > self.max_building_age_years:
                return False
        if self.min_price_per_sqft is not None:
            if observation.price_per_sqft is None or observation.price_per_sqft < self.min_price_per_sqft:
                return False
        if self.max_price_per_sqft is not None:
            if observation.price_per_sqft is None or observation.price_per_sqft > self.max_price_per_sqft:
                return False
        if self.layouts:
            desired_counts = {count for count in (layout_bedroom_count(term) for term in self.layouts) if count is not None}
            observed_count = layout_bedroom_count(observation.layout or observation.title or "")
            if desired_counts:
                if observed_count not in desired_counts:
                    return False
            else:
                layout_text = normalize_text(observation.layout or observation.title or "")
                if not any(normalize_text(term) in layout_text for term in self.layouts):
                    return False
        if self.estates:
            estate_text = normalize_text(observation.estate_name or observation.title or "")
            if not any(normalize_text(term) in estate_text for term in self.estates):
                return False
        if self.keywords:
            if not all(normalize_text(term) in normalized_haystack for term in self.keywords):
                return False
        if self.excluded_estates:
            estate_text = normalize_text(" ".join(x for x in [observation.estate_name, observation.title] if x))
            if any(normalize_text(term) in estate_text for term in self.excluded_estates):
                return False
        if self.excluded_keywords:
            if any(normalize_text(term) in normalized_haystack for term in self.excluded_keywords):
                return False
        return True


@dataclass
class ListingObservation:
    source_site: str
    source_url: str
    fetched_at: str
    source_listing_id: str | None = None
    title: str | None = None
    estate_name: str | None = None
    building: str | None = None
    block: str | None = None
    floor: str | None = None
    flat: str | None = None
    rent_hkd: int | None = None
    usable_area_sqft: int | None = None
    gross_area_sqft: int | None = None
    building_age_years: int | None = None
    price_per_sqft: float | None = None
    layout: str | None = None
    address: str | None = None
    district: str | None = None
    updated_at_text: str | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.price_per_sqft is None and self.rent_hkd and self.usable_area_sqft:
            self.price_per_sqft = round(self.rent_hkd / self.usable_area_sqft, 2)
        self.title = normalize_loose(self.title)
        self.estate_name = normalize_loose(self.estate_name)
        self.building = normalize_loose(self.building)
        self.block = normalize_loose(self.block)
        self.floor = normalize_loose(self.floor)
        self.flat = normalize_loose(self.flat)
        self.layout = canonical_layout(self.layout) if self.layout else ""
        self.address = normalize_loose(self.address)
        self.district = normalize_loose(self.district)
        self.raw = {}

    @property
    def source_key(self) -> str:
        if self.source_listing_id:
            return f"{self.source_site}:{self.source_listing_id}"
        return f"{self.source_site}:url:{stable_hash(self.source_url, 20)}"

    @property
    def normalized_estate(self) -> str:
        return normalize_text(self.estate_name or "")

    @property
    def unit_text(self) -> str:
        return " ".join(x for x in [self.building, self.block, self.floor, self.flat] if x)

    @property
    def identity_key(self) -> str:
        parts = [
            normalize_text(self.estate_name or ""),
            normalize_text(self.block or self.building or ""),
            normalize_text(self.floor or ""),
            normalize_text(self.flat or ""),
            str(self.usable_area_sqft or ""),
        ]
        if parts[0] and (parts[1] or parts[2] or parts[3]) and parts[4]:
            return stable_hash("|".join(parts), 20)
        fallback = "|".join(
            [
                normalize_text(self.estate_name or self.title or ""),
                normalize_text(self.layout or ""),
                str(self.usable_area_sqft or ""),
                str(self.rent_hkd or ""),
            ]
        )
        return stable_hash(fallback, 20)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ListingObservation":
        cleaned = dict(data)
        cleaned.pop("agent_name", None)
        return cls(**cleaned)


def make_filters(
    min_rent: int | None = None,
    max_rent: int | None = None,
    layouts: str | list[str] | None = None,
    estates: str | list[str] | None = None,
    keywords: str | list[str] | None = None,
    excluded_estates: str | list[str] | None = None,
    excluded_keywords: str | list[str] | None = None,
    min_area_sqft: int | None = None,
    max_area_sqft: int | None = None,
    min_gross_area_sqft: int | None = None,
    max_gross_area_sqft: int | None = None,
    min_building_age_years: int | None = None,
    max_building_age_years: int | None = None,
    min_price_per_sqft: float | None = None,
    max_price_per_sqft: float | None = None,
) -> SearchFilters:
    return SearchFilters(
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
