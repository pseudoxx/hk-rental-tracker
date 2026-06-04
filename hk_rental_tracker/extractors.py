from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from .models import ListingObservation
from .normalization import normalize_loose, normalize_text, now_iso, parse_int, stable_hash
from .site_catalog import base_url, id_patterns


MARKETING_TAGS = [
    "中原地产",
    "中原地產",
    "美联物业",
    "美聯物業",
    "香港置业",
    "香港置業",
    "利嘉阁",
    "利嘉閣",
    "AI装修",
    "AI裝修",
    "AI讲房",
    "AI講房",
    "装修及讲房",
    "裝修及講房",
    "独家",
    "獨家",
    "锁匙盘",
    "鎖匙盤",
    "VR",
    "bookmark",
    "靓装即住",
    "靚裝即住",
    "笋盘",
    "筍盤",
    "董事推介",
    "经理心水",
    "經理心水",
    "露台",
    "天台",
    "连租约",
    "連租約",
]

LISTING_HINTS = ("租", "实用", "實用", "/呎", "@", "房", "开放式", "開放式")


@dataclass
class LinkText:
    href: str
    text: str


def strip_tags(value: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", value)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h1|h2|h3|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def clean_card_text(value: str) -> str:
    text = strip_tags(value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_links(source_url: str, html_text: str, site: str) -> list[LinkText]:
    links: list[LinkText] = []
    site_base = base_url(site) or source_url
    for match in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a>", html_text):
        attrs, inner = match.groups()
        href_match = re.search(r"""href\s*=\s*["']([^"']+)["']""", attrs, flags=re.I)
        if not href_match:
            continue
        text = clean_card_text(inner)
        if not text:
            continue
        href = urljoin(site_base, href_match.group(1))
        if any(hint in text for hint in LISTING_HINTS) or looks_like_listing_url(href, site):
            links.append(LinkText(href=href, text=text))
    return links


def looks_like_listing_url(url: str, site: str) -> bool:
    lowered = url.lower()
    if "/property/" in lowered or "/detail/" in lowered:
        return True
    return any(re.search(pattern, url) for pattern in id_patterns(site))


def source_listing_id(site: str, url: str, text: str = "") -> str:
    combined = f"{url} {text}"
    for pattern in id_patterns(site):
        match = re.search(pattern, combined, flags=re.I)
        if match:
            return match.group(1).upper()
    return stable_hash(url or text, 20)


def parse_rent(text: str) -> int | None:
    patterns = [
        r"租\s*\$?\s*([0-9][0-9,]*)",
        r"rent\s*\$?\s*([0-9][0-9,]*)",
        r"\$\s*([0-9][0-9,]*)\s*(?:實|实|@|租)",
    ]
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, flags=re.I))
        if matches:
            return parse_int(matches[-1].group(1))
    return None


def parse_area(text: str) -> int | None:
    patterns = [
        r"(?:实用|實用)\s*(?:实|實)?\s*([0-9][0-9,]*)\s*呎",
        r"(?:实|實)\s*([0-9][0-9,]*)\s*呎",
        r"\b([0-9][0-9,]*)\s*呎\s*@",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return parse_int(match.group(1))
    return None


def parse_price_per_sqft(text: str) -> float | None:
    patterns = [r"@\s*\$?\s*([0-9][0-9,]*)\s*/?\s*呎", r"\$\s*([0-9][0-9,]*)\s*/\s*呎"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = parse_int(match.group(1))
            return float(value) if value is not None else None
    return None


def parse_layout(text: str) -> str | None:
    patterns = [
        r"(開放式|开放式)",
        r"([0-9一二三四五六七八九十]\s*房\s*(?:\([0-9一二三四五六七八九十]\s*套房\))?)",
        r"([0-9一二三四五六七八九十]\s*房\s*[0-9一二三四五六七八九十]?\s*(?:厅|廳))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            layout = re.sub(r"\s+", "", match.group(1))
            bedroom_match = re.match(r"([0-9一二三四五六七八九十]房)", layout)
            if bedroom_match and "厅" not in layout and "廳" not in layout:
                return bedroom_match.group(1)
            return layout
    return None


def parse_floor(text: str) -> str | None:
    match = re.search(r"(高层|高層|中层|中層|低层|低層|顶层|頂層|地下|[0-9]{1,2}\s*(?:楼|樓))", text)
    return normalize_loose(match.group(1)) if match else None


def parse_flat(text: str) -> str | None:
    match = re.search(r"([A-Z][0-9]?室|[0-9]{1,3}室|[A-Z]\d?\b)", text, flags=re.I)
    return normalize_loose(match.group(1).upper()) if match else None


def parse_block(text: str) -> str | None:
    matches = re.findall(
        r"([0-9A-Z一二三四五六七八九十]+(?:A|B|C|D)?座|[0-9A-Z一二三四五六七八九十]+期\s*[0-9A-Z一二三四五六七八九十]+座|大厦第[0-9A-Z一二三四五六七八九十]+座)",
        text,
        flags=re.I,
    )
    return normalize_loose(matches[-1]) if matches else None


def remove_leading_tags(text: str) -> str:
    cleaned = text.strip()
    changed = True
    while changed:
        changed = False
        for tag in MARKETING_TAGS:
            if cleaned.startswith(tag):
                cleaned = cleaned[len(tag) :].strip()
                changed = True
    return cleaned


def parse_estate(text: str, layout: str | None, area_terms: list[str]) -> str | None:
    work = remove_leading_tags(text)
    if "bookmark" in work:
        work = work.split("bookmark", 1)[-1].strip()
    stop_positions = []
    for token in [layout, "实用", "實用", "租 $", "租$", "租 "]:
        if token:
            idx = work.find(token)
            if idx >= 0:
                stop_positions.append(idx)
    if stop_positions:
        work = work[: min(stop_positions)].strip()
    work = re.sub(r"\b(?:VR|AI)\b", " ", work)
    work = re.sub(r"\s+", " ", work).strip()
    if not work:
        return None

    unit_match = re.search(
        r"(.+?)(?:\s+[0-9A-Z一二三四五六七八九十]+(?:A|B|C|D)?座|\s+高层|\s+高層|\s+中层|\s+中層|\s+低层|\s+低層|\s+[A-Z][0-9]?室|\s+[0-9]{1,3}室)",
        work,
        flags=re.I,
    )
    if unit_match:
        candidate = unit_match.group(1).strip()
    else:
        pieces = work.split()
        candidate = " ".join(pieces[: min(4, len(pieces))])

    for term in sorted((term for term in area_terms if term), key=len, reverse=True):
        candidate = re.sub(rf"\s+{re.escape(term)}$", "", candidate).strip()
    candidate = re.sub(r"^(?:主页|主頁|网上找房|網上搵樓)\s+", "", candidate).strip()
    return normalize_loose(candidate) or None


def parse_district(text: str, area_terms: list[str]) -> str | None:
    for term in area_terms:
        if term and normalize_text(term) in normalize_text(text):
            return term
    return None


def parse_observation_from_text(
    site: str,
    source_url: str,
    text: str,
    area_terms: list[str],
    fetched_at: str | None = None,
) -> ListingObservation | None:
    normalized = normalize_loose(text)
    rent = parse_rent(normalized)
    area = parse_area(normalized)
    layout = parse_layout(normalized)
    if rent is None and area is None and layout is None:
        return None
    estate = parse_estate(normalized, layout, area_terms)
    return ListingObservation(
        source_site=site,
        source_url=source_url,
        source_listing_id=source_listing_id(site, source_url, normalized),
        fetched_at=fetched_at or now_iso(),
        title=normalized[:500],
        estate_name=estate,
        building=None,
        block=parse_block(normalized),
        floor=parse_floor(normalized),
        flat=parse_flat(normalized),
        rent_hkd=rent,
        usable_area_sqft=area,
        price_per_sqft=parse_price_per_sqft(normalized),
        layout=layout,
        district=parse_district(normalized, area_terms),
    )


def walk_json(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        keys = {k.lower() for k in value.keys()}
        if keys & {"price", "rent", "saleprice", "grossarea", "saleablearea", "propertyname", "estate"}:
            found.append(value)
        for child in value.values():
            found.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_json(child))
    return found


def observations_from_json(site: str, source_url: str, html_text: str, area_terms: list[str], fetched_at: str) -> list[ListingObservation]:
    observations: list[ListingObservation] = []
    script_matches = re.finditer(
        r"(?is)<script[^>]*(?:application/ld\+json|__NEXT_DATA__)?[^>]*>(.*?)</script>",
        html_text,
    )
    for match in script_matches:
        payload = match.group(1).strip()
        if not payload or not (payload.startswith("{") or payload.startswith("[")):
            continue
        try:
            data = json.loads(payload)
        except Exception:
            continue
        for item in walk_json(data):
            text = " ".join(str(v) for v in item.values() if isinstance(v, (str, int, float)) and v)
            obs = parse_observation_from_text(site, source_url, text, area_terms, fetched_at=fetched_at)
            if obs:
                obs.raw["json"] = item
                observations.append(obs)
    return observations


def observations_from_html(site: str, source_url: str, html_text: str, area_terms: list[str], fetched_at: str | None = None) -> list[ListingObservation]:
    fetched = fetched_at or now_iso()
    observations: list[ListingObservation] = []
    seen = set()

    for link in extract_links(source_url, html_text, site):
        obs = parse_observation_from_text(site, link.href, link.text, area_terms, fetched_at=fetched)
        if obs and obs.source_key not in seen:
            observations.append(obs)
            seen.add(obs.source_key)

    if not observations:
        page_text = strip_tags(html_text)
        chunks = []
        for match in re.finditer(r"(?:租\s*\$?\s*[0-9][0-9,]*)", page_text):
            start = max(0, match.start() - 260)
            end = min(len(page_text), match.end() + 260)
            chunks.append(page_text[start:end])
        if not chunks and any(hint in page_text for hint in LISTING_HINTS):
            chunks = re.split(r"\bbookmark\b|加入书签|加入書籤", page_text)
        for chunk in chunks:
            obs = parse_observation_from_text(site, source_url, chunk, area_terms, fetched_at=fetched)
            if obs and obs.source_key not in seen:
                observations.append(obs)
                seen.add(obs.source_key)

    for obs in observations_from_json(site, source_url, html_text, area_terms, fetched):
        if obs.source_key not in seen:
            observations.append(obs)
            seen.add(obs.source_key)

    return observations
