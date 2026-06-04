from __future__ import annotations

from dataclasses import asdict, dataclass


TEXT_LOCAL_FILTERS = (
    "area_aliases",
    "allowed_estates",
    "required_keywords",
    "excluded_estates",
    "excluded_keywords",
)


@dataclass(frozen=True)
class SourceFilterTrust:
    site: str
    confirmed_pushdown: tuple[str, ...]
    local_only: tuple[str, ...]
    unstable_or_unconfirmed: tuple[str, ...] = ()
    parsed_fields: tuple[str, ...] = ()
    coverage_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


SOURCE_FILTER_TRUST: dict[str, SourceFilterTrust] = {
    "midland": SourceFilterTrust(
        site="midland",
        confirmed_pushdown=("rent", "layout_bedrooms", "usable_area", "gross_area", "price_per_sqft"),
        local_only=("building_age", *TEXT_LOCAL_FILTERS),
        parsed_fields=(
            "source_listing_id",
            "source_url",
            "estate_name",
            "block",
            "floor",
            "flat",
            "layout",
            "rent",
            "usable_area",
            "gross_area",
            "building_age",
            "price_per_sqft",
        ),
        coverage_signals=("api_count", "page_count"),
    ),
    "hkp": SourceFilterTrust(
        site="hkp",
        confirmed_pushdown=("rent", "layout_bedrooms", "usable_area", "gross_area", "price_per_sqft"),
        local_only=("building_age", *TEXT_LOCAL_FILTERS),
        parsed_fields=(
            "source_listing_id",
            "source_url",
            "estate_name",
            "block",
            "floor",
            "flat",
            "layout",
            "rent",
            "usable_area",
            "gross_area",
            "building_age",
            "price_per_sqft",
        ),
        coverage_signals=("api_count", "page_count"),
    ),
    "ricacorp": SourceFilterTrust(
        site="ricacorp",
        confirmed_pushdown=(),
        local_only=(
            "rent",
            "layout_bedrooms",
            "usable_area",
            "gross_area",
            "building_age",
            "price_per_sqft",
            *TEXT_LOCAL_FILTERS,
        ),
        parsed_fields=(
            "source_listing_id",
            "source_url",
            "estate_name",
            "block",
            "floor",
            "flat",
            "layout",
            "rent",
            "usable_area",
            "gross_area",
            "building_age",
            "price_per_sqft",
        ),
        coverage_signals=("html_state_total", "html_state_pages", "page_count"),
    ),
    "centanet": SourceFilterTrust(
        site="centanet",
        confirmed_pushdown=("rent", "layout_bedrooms", "usable_area", "building_age", "price_per_sqft"),
        local_only=("gross_area", *TEXT_LOCAL_FILTERS),
        parsed_fields=(
            "source_listing_id",
            "source_url",
            "estate_name",
            "block",
            "layout",
            "rent",
            "usable_area",
            "gross_area",
            "building_age",
            "price_per_sqft",
        ),
        coverage_signals=("api_count", "offset_pagination"),
    ),
}


def filter_trust(site: str) -> SourceFilterTrust:
    return SOURCE_FILTER_TRUST.get(
        site,
        SourceFilterTrust(
            site=site,
            confirmed_pushdown=(),
            local_only=(
                "rent",
                "layout_bedrooms",
                "usable_area",
                "gross_area",
                "building_age",
                "price_per_sqft",
                *TEXT_LOCAL_FILTERS,
            ),
        ),
    )


def can_pushdown(site: str, filter_name: str) -> bool:
    return filter_name in filter_trust(site).confirmed_pushdown
