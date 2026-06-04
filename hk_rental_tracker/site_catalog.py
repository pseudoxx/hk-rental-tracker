from __future__ import annotations

from urllib.parse import quote


SITE_CATALOG = {
    "midland": {
        "display_name": "美联物业",
        "base_url": "https://www.midland.com.hk",
        "candidate_urls": [
            "https://www.midland.com.hk/zh-hk/list/rent",
        ],
        "listing_id_patterns": [r"\b(M\d{6,})\b"],
    },
    "ricacorp": {
        "display_name": "利嘉阁",
        "base_url": "https://www.ricacorp.com",
        "candidate_urls": [
            "https://www.ricacorp.com/zh-hk/property/list/rent",
        ],
        "listing_id_patterns": [r"\b(R[A-Z0-9]{5,})\b", r"\b([A-Z]{2,4}\d{3,})\b"],
    },
    "centanet": {
        "display_name": "中原地产",
        "base_url": "https://hk.centanet.com",
        "candidate_urls": [
            "https://hk.centanet.com/findproperty/zh-cn/list/rent/{area_q}",
            "https://hk.centanet.com/findproperty/zh-cn/list/rent?keyword={area_q}",
        ],
        "listing_id_patterns": [r"/detail/[^?_/]+_([A-Z]{2,}\d+)", r"\b([A-Z]{2,}\d{3,})\b"],
    },
    "hkp": {
        "display_name": "香港置业",
        "base_url": "https://www.hkp.com.hk",
        "candidate_urls": [
            "https://www.hkp.com.hk/zh-hk/list/rent",
            "https://www.hkp.com.hk/zh-hk/list/property",
        ],
        "listing_id_patterns": [r"\b(H\d{6,})\b"],
    },
}


def candidate_urls(site: str, area: str) -> list[str]:
    recipe = SITE_CATALOG.get(site, {})
    area_q = quote(area)
    return [url.format(area_q=area_q) for url in recipe.get("candidate_urls", [])]


def display_name(site: str) -> str:
    return SITE_CATALOG.get(site, {}).get("display_name", site)


def base_url(site: str) -> str:
    return SITE_CATALOG.get(site, {}).get("base_url", "")


def id_patterns(site: str) -> list[str]:
    return list(SITE_CATALOG.get(site, {}).get("listing_id_patterns", []))
