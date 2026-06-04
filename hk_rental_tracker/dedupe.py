from __future__ import annotations

from sqlite3 import Row

from .models import ListingObservation
from .normalization import normalize_text


def _norm(value: object) -> str:
    return normalize_text(str(value)) if value else ""


def _unit_block(obs: ListingObservation) -> str:
    return _norm(obs.block or obs.building)


def _row_block(row: Row) -> str:
    return _norm(row["block"] or row["building"])


def strict_candidate_match(observation: ListingObservation, row: Row) -> bool:
    if not observation.rent_hkd or not row["last_rent_hkd"]:
        return False
    if abs(int(observation.rent_hkd) - int(row["last_rent_hkd"])) > 500:
        return False

    comparable_pairs = [
        (observation.normalized_estate, row["normalized_estate_name"]),
        (_unit_block(observation), _row_block(row)),
        (_norm(observation.floor), row["floor"]),
        (_norm(observation.flat), row["flat"]),
        (_norm(observation.layout), row["layout"]),
        (str(observation.usable_area_sqft or ""), str(row["usable_area_sqft"] or "")),
    ]
    compared = 0
    for left, right in comparable_pairs:
        left_norm = _norm(left)
        right_norm = _norm(right)
        if not left_norm or not right_norm:
            continue
        compared += 1
        if left_norm != right_norm:
            return False
    return compared >= 3


def score_candidate(observation: ListingObservation, row: Row) -> float:
    return 1.0 if strict_candidate_match(observation, row) else 0.0
