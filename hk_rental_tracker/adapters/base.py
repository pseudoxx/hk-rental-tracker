from __future__ import annotations

import json
import math
import os
import re
import time
import html
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import TaskConfig
from ..extractors import observations_from_html
from ..fetcher import PageFetcher
from ..models import ListingObservation
from ..normalization import normalize_loose, now_iso
from ..site_catalog import candidate_urls
from ..source_capabilities import can_pushdown


@dataclass
class SiteScanResult:
    site: str
    ok: bool
    observations: list[ListingObservation] = field(default_factory=list)
    fetched_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class GenericSiteAdapter:
    def __init__(self, site: str):
        self.site = site

    def urls_for_task(self, config: TaskConfig) -> list[str]:
        configured = config.source_search_urls.get(self.site) or []
        if configured:
            return configured
        return candidate_urls(self.site, config.area)

    def scan(self, config: TaskConfig, fetcher: PageFetcher) -> SiteScanResult:
        configured = config.source_search_urls.get(self.site) or []
        if configured and any(url.startswith("data:") for url in configured):
            return self._scan_html(config, fetcher)
        if self.site == "midland":
            return self._scan_midland_like(config, fetcher, brand="midland")
        if self.site == "hkp":
            return self._scan_midland_like(config, fetcher, brand="hkp")
        if self.site == "ricacorp":
            return self._scan_ricacorp(config, fetcher)
        if self.site == "centanet":
            return self._scan_centanet(config, fetcher)

        return self._scan_html(config, fetcher)

    def _scan_html(self, config: TaskConfig, fetcher: PageFetcher) -> SiteScanResult:
        urls = self.urls_for_task(config)
        result = SiteScanResult(site=self.site, ok=True)
        if not urls:
            result.ok = False
            result.errors.append("No search URLs configured and no candidate URLs available.")
            return result
        seen_source_keys = set()
        for index, url in enumerate(urls, start=1):
            _emit_progress(fetcher, f"{self.site} html page {index}/{len(urls)} start")
            fetch = fetcher.fetch(url)
            result.fetched_urls.append(url)
            if not fetch.ok:
                result.errors.append(f"{url}: {fetch.error or fetch.status_code}")
                _emit_progress(fetcher, f"{self.site} html page {index}/{len(urls)} failed")
                continue
            before = len(result.observations)
            for observation in observations_from_html(self.site, fetch.url, fetch.html, config.area_terms):
                if observation.source_key in seen_source_keys:
                    continue
                seen_source_keys.add(observation.source_key)
                result.observations.append(observation)
            _emit_progress(
                fetcher,
                f"{self.site} html page {index}/{len(urls)} done: "
                f"new_observations={len(result.observations) - before}",
            )
        if result.errors and not result.observations:
            result.ok = False
        return result

    def _scan_midland_like(self, config: TaskConfig, fetcher: PageFetcher, brand: str) -> SiteScanResult:
        result = SiteScanResult(site=self.site, ok=True)
        landing_url = self._landing_url(config, brand)
        base_url = "https://data.midland.com.hk/search/v2/properties" if brand == "midland" else "https://data.hkp.com.hk/search/v2/properties"
        params = _common_property_params(config, lang="zh-hk", site=self.site)
        params["text"] = config.area
        _emit_progress(fetcher, f"{self.site} token page start")
        token_fetch = _fetch_page_with_retries(fetcher, landing_url)
        result.fetched_urls.append(landing_url)
        token = None
        if token_fetch.ok:
            token = _extract_next_token(token_fetch.html, brand)
            if token and brand == "midland":
                _save_midland_build_token(token)
            _emit_progress(fetcher, f"{self.site} token page done: token={'yes' if token else 'no'}")
        elif brand != "midland":
            result.ok = False
            result.errors.append(f"{landing_url}: {token_fetch.error or token_fetch.status_code}")
            _emit_progress(fetcher, f"{self.site} token page failed")
            return result

        if not token and brand == "midland":
            token = _load_midland_build_token()
            if token:
                result.fetched_urls.append(f"{landing_url}: using cached build token")
                _emit_progress(fetcher, f"{self.site} using cached build token")

        if not token and brand == "midland":
            if not token_fetch.ok:
                result.errors.append(f"{landing_url}: {token_fetch.error or token_fetch.status_code}")
            _emit_progress(fetcher, f"{self.site} browser fallback start")
            return _scan_midland_with_browser_fallback(config, result, landing_url, base_url, params, fetcher.timeout)
        if not token:
            result.ok = False
            result.errors.append(f"{landing_url}: unable to extract API token")
            _emit_progress(fetcher, f"{self.site} token extraction failed")
            return result

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "zh-HK,zh-CN;q=0.9,en;q=0.8",
            "Authorization": f"Bearer {token}",
            "Origin": "https://www.midland.com.hk" if brand == "midland" else "https://www.hkp.com.hk",
            "Referer": landing_url,
        }
        fetched_at = now_iso()
        pages = _api_pages(base_url, params, headers, fetcher, result)
        if not pages and brand == "midland":
            if not token_fetch.ok:
                result.errors.insert(0, f"{landing_url}: {token_fetch.error or token_fetch.status_code}")
            _emit_progress(fetcher, f"{self.site} browser fallback start")
            return _scan_midland_with_browser_fallback(config, result, landing_url, base_url, params, fetcher.timeout)
        for page in pages:
            for item in page.get("result") or []:
                obs = _observation_from_midland_like_item(self.site, item, fetched_at)
                if obs:
                    result.observations.append(obs)
        if result.errors and not result.observations:
            result.ok = False
        return result

    def _scan_ricacorp(self, config: TaskConfig, fetcher: PageFetcher) -> SiteScanResult:
        result = SiteScanResult(site=self.site, ok=True)
        fetched_at = now_iso()
        seen_source_ids: set[str] = set()
        urls = self.urls_for_task(config)
        if not urls:
            result.ok = False
            result.errors.append("No search URLs configured and no candidate URLs available.")
            return result
        for url_index, base_url in enumerate(urls, start=1):
            _emit_progress(fetcher, f"{self.site} base url {url_index}/{len(urls)} start")
            page_no = 1
            total_pages = 1
            while page_no <= total_pages:
                url = _ricacorp_page_url(base_url, page_no)
                _emit_progress(fetcher, f"{self.site} page {page_no}/{total_pages} start")
                fetch = _fetch_page_with_retries(fetcher, url)
                result.fetched_urls.append(url)
                if not fetch.ok:
                    result.errors.append(f"{url}: {fetch.error or fetch.status_code}")
                    _emit_progress(fetcher, f"{self.site} page {page_no}/{total_pages} failed")
                    if page_no == 1:
                        break
                    page_no += 1
                    continue
                state = _ricacorp_state_from_html(fetch.html)
                posts = state.get("POSTS") or []
                if not isinstance(posts, list):
                    result.errors.append(f"{url}: Ricacorp page state did not contain a POSTS list")
                    _emit_progress(fetcher, f"{self.site} page {page_no}/{total_pages} parse failed")
                    if page_no == 1:
                        break
                    page_no += 1
                    continue
                total_pages = max(1, int(state.get("POSTSNUMPAGES") or 1))
                if page_no == 1 and not posts:
                    result.errors.append(f"{url}: Ricacorp page state contained zero posts")
                    _emit_progress(fetcher, f"{self.site} page {page_no}/{total_pages} returned zero posts")
                    break
                if page_no > 1 and not posts:
                    result.errors.append(f"{url}: Ricacorp page state contained zero posts")
                    _emit_progress(fetcher, f"{self.site} page {page_no}/{total_pages} returned zero posts")
                    page_no += 1
                    continue
                before = len(result.observations)
                for item in posts:
                    if not isinstance(item, dict):
                        continue
                    source_id = str(item.get("postNo") or "")
                    if source_id and source_id in seen_source_ids:
                        continue
                    obs = _observation_from_ricacorp_item(item, fetched_at)
                    if obs:
                        if source_id:
                            seen_source_ids.add(source_id)
                        result.observations.append(obs)
                _emit_progress(
                    fetcher,
                    f"{self.site} page {page_no}/{total_pages} done: "
                    f"new_observations={len(result.observations) - before}",
                )
                page_no += 1
        if result.errors and not result.observations:
            result.ok = False
        return result

    def _scan_centanet(self, config: TaskConfig, fetcher: PageFetcher) -> SiteScanResult:
        result = SiteScanResult(site=self.site, ok=True)
        url = "https://hk.centanet.com/findproperty/api/Post/Search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh-HK;q=0.9,en;q=0.8",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": "https://hk.centanet.com/findproperty/zh-cn/list/rent/" + urllib.parse.quote(config.area),
        }
        payload = {
            "postType": "Rent",
            "sort": "Ranking",
            "order": "Ascending",
            "size": 100,
            "displayTextStyle": "WebResultList",
            "bigPhotoMode": False,
            "keyword": config.area,
            "pageSource": "search",
        }
        payload.update(_centanet_filter_payload(config))
        fetched_at = now_iso()
        offset = 0
        total = None
        while total is None or offset < total:
            page_payload = dict(payload)
            page_payload["offset"] = offset
            result.fetched_urls.append(f"{url} offset={offset}")
            _emit_progress(fetcher, f"{self.site} api offset {offset} start")
            data, error = _post_json_with_retries(
                url,
                payload=page_payload,
                headers=headers,
                delay=fetcher.delay_seconds,
                timeout=fetcher.timeout,
                progress=getattr(fetcher, "emit_progress", None),
            )
            if data is None:
                result.errors.append(f"{url}: {error or f'unable to fetch or parse JSON at offset {offset}'}")
                _emit_progress(fetcher, f"{self.site} api offset {offset} failed")
                break
            total = int(data.get("count") or 0)
            rows = data.get("data") or []
            before = len(result.observations)
            for item in rows:
                obs = _observation_from_centanet_item(item, fetched_at)
                if obs:
                    result.observations.append(obs)
            _emit_progress(
                fetcher,
                f"{self.site} api offset {offset} done: "
                f"rows={len(rows)}, total={total}, new_observations={len(result.observations) - before}",
            )
            if not rows:
                break
            offset += int(payload["size"])
        if result.errors and not result.observations:
            result.ok = False
        return result

    def _landing_url(self, config: TaskConfig, brand: str) -> str:
        configured = config.source_search_urls.get(self.site) or []
        if configured:
            return configured[0]
        if brand == "midland":
            return "https://www.midland.com.hk/zh-hk/list/rent"
        return "https://www.hkp.com.hk/zh-hk/list/rent"


def _extract_next_token(html_text: str, brand: str) -> str | None:
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_text, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if brand == "midland":
        return (payload.get("runtimeConfig") or {}).get("BUILD_TOKEN")
    return ((payload.get("props") or {}).get("pageProps") or {}).get("userToken")


def _emit_progress(fetcher: object, message: str) -> None:
    emitter = getattr(fetcher, "emit_progress", None)
    if callable(emitter):
        emitter(message)


def _midland_build_token_cache_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".cache" / "midland-build-token.txt"


def _load_midland_build_token() -> str | None:
    token = os.environ.get("HK_RENTAL_TRACKER_MIDLAND_BUILD_TOKEN")
    if token and token.strip():
        return token.strip()
    path = _midland_build_token_cache_path()
    try:
        cached = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return cached or None


def _save_midland_build_token(token: str) -> None:
    if not token.strip():
        return
    path = _midland_build_token_cache_path()
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == token.strip():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token.strip() + "\n", encoding="utf-8")
    except OSError:
        return


def _fetch_page_with_retries(fetcher: PageFetcher, url: str, attempts: int = 3):
    result = None
    for attempt in range(max(1, attempts)):
        if attempt:
            _emit_progress(fetcher, f"fetch retry wait: attempt={attempt + 1}/{attempts}, delay={attempt * 2:.1f}s")
            time.sleep(float(attempt * 2))
        _emit_progress(fetcher, f"fetch attempt: {attempt + 1}/{attempts}")
        result = fetcher.fetch(url)
        if result.ok:
            return result
        if result.status_code is not None and result.status_code not in {403, 429, 500, 502, 503, 504}:
            return result
    return result


def _common_property_params(config: TaskConfig, lang: str, site: str = "midland") -> dict[str, str]:
    params = {
        "lang": lang,
        "tx_type": "L",
        "currency": "HKD",
        "unit": "feet",
        "limit": "100",
        "page": "1",
    }
    if can_pushdown(site, "rent") and config.filters.max_rent is not None:
        params["price_to"] = str(config.filters.max_rent)
    if can_pushdown(site, "rent") and config.filters.min_rent is not None:
        params["price_from"] = str(config.filters.min_rent)
    if can_pushdown(site, "usable_area") and config.filters.max_area_sqft is not None:
        params["net_area_to"] = str(config.filters.max_area_sqft)
    if can_pushdown(site, "usable_area") and config.filters.min_area_sqft is not None:
        params["net_area_from"] = str(config.filters.min_area_sqft)
    if can_pushdown(site, "gross_area") and config.filters.max_gross_area_sqft is not None:
        params["area_to"] = str(config.filters.max_gross_area_sqft)
    if can_pushdown(site, "gross_area") and config.filters.min_gross_area_sqft is not None:
        params["area_from"] = str(config.filters.min_gross_area_sqft)
    if can_pushdown(site, "price_per_sqft") and config.filters.max_price_per_sqft is not None:
        params["net_ft_price_to"] = str(config.filters.max_price_per_sqft)
    if can_pushdown(site, "price_per_sqft") and config.filters.min_price_per_sqft is not None:
        params["net_ft_price_from"] = str(config.filters.min_price_per_sqft)
    bedrooms = _bedroom_values(config.filters.layouts)
    if can_pushdown(site, "layout_bedrooms") and bedrooms:
        params["bedroom"] = ",".join(str(value) for value in bedrooms)
    return params


def _ricacorp_page_url(base_url: str, page_no: int) -> str:
    if page_no <= 1:
        return base_url
    url = base_url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    url = re.sub(r";page=\d+", "", url)
    return f"{url};page={page_no}"


def _ricacorp_state_from_html(html_text: str) -> dict:
    match = re.search(r'<script id="serverApp-state" type="application/json">(.*?)</script>', html_text, re.S)
    if not match:
        return {}
    raw = (
        match.group(1)
        .replace("&q;", '"')
        .replace("&a;", "&")
        .replace("&l;", "<")
        .replace("&g;", ">")
    )
    try:
        data = json.loads(html.unescape(raw))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _centanet_filter_payload(config: TaskConfig) -> dict[str, object]:
    payload: dict[str, object] = {}
    bedrooms = _bedroom_values(config.filters.layouts)
    if can_pushdown("centanet", "layout_bedrooms") and bedrooms:
        payload["bedroomCount"] = bedrooms
    if can_pushdown("centanet", "rent") and (config.filters.max_rent is not None or config.filters.min_rent is not None):
        payload["AmountRange"] = {
            "min": config.filters.min_rent or 0,
            "max": config.filters.max_rent,
        }
    if can_pushdown("centanet", "usable_area") and (
        config.filters.max_area_sqft is not None or config.filters.min_area_sqft is not None
    ):
        payload["nSizeRange"] = {
            "min": config.filters.min_area_sqft or 0,
            "max": config.filters.max_area_sqft,
        }
    if can_pushdown("centanet", "building_age") and (
        config.filters.max_building_age_years is not None or config.filters.min_building_age_years is not None
    ):
        payload["buildingAgeRange"] = {
            "min": config.filters.min_building_age_years or 0,
            "max": config.filters.max_building_age_years,
        }
    if can_pushdown("centanet", "price_per_sqft") and (
        config.filters.max_price_per_sqft is not None or config.filters.min_price_per_sqft is not None
    ):
        payload["nUnitPriceRange"] = {
            "min": config.filters.min_price_per_sqft or 0,
            "max": config.filters.max_price_per_sqft,
        }
    return payload


def _bedroom_values(layouts: list[str]) -> list[int]:
    values: set[int] = set()
    for layout in layouts:
        normalized = normalize_loose(layout)
        if "开放式" in normalized:
            values.add(0)
        match = re.search(r"(\d+)\s*房", normalized)
        if match:
            values.add(int(match.group(1)))
    return sorted(values)


def _room_range(layouts: list[str]) -> tuple[int | None, int | None]:
    values = _bedroom_values(layouts)
    if not values:
        return None, None
    return min(values), max(values)


def _api_pages(
    base_url: str,
    params: dict[str, str],
    headers: dict[str, str],
    fetcher: PageFetcher,
    result: SiteScanResult,
) -> list[dict]:
    pages: list[dict] = []
    page_no = 1
    total_pages = 1
    limit = int(params.get("limit") or 100)
    while page_no <= total_pages:
        page_params = dict(params)
        page_params["page"] = str(page_no)
        url = f"{base_url}?{urllib.parse.urlencode(page_params)}"
        result.fetched_urls.append(url)
        _emit_progress(fetcher, f"{result.site} api page {page_no}/{total_pages} start")
        data, error = _fetch_json_with_retries(
            url,
            headers=headers,
            delay=fetcher.delay_seconds,
            timeout=fetcher.timeout,
            progress=getattr(fetcher, "emit_progress", None),
        )
        if data is None:
            result.errors.append(f"{url}: {error or 'unable to fetch or parse JSON'}")
            _emit_progress(fetcher, f"{result.site} api page {page_no}/{total_pages} failed")
            break
        count = int(data.get("count") or len(data.get("result") or []))
        total_pages = max(1, math.ceil(count / limit))
        pages.append(data)
        _emit_progress(
            fetcher,
            f"{result.site} api page {page_no}/{total_pages} done: "
            f"rows={len(data.get('result') or [])}, total={count}",
        )
        page_no += 1
    return pages


def _scan_midland_with_browser_fallback(
    config: TaskConfig,
    result: SiteScanResult,
    landing_url: str,
    base_url: str,
    params: dict[str, str],
    timeout: int,
) -> SiteScanResult:
    original_errors = list(result.errors)
    result.errors = []
    try:
        pages, urls = _midland_pages_via_browser(landing_url, base_url, params, timeout)
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        result.errors = original_errors + [f"{landing_url}: browser fallback failed: {exc}"]
        return result

    fetched_at = now_iso()
    for url in urls:
        if url not in result.fetched_urls:
            result.fetched_urls.append(url)
    for page in pages:
        for item in page.get("result") or []:
            obs = _observation_from_midland_like_item(result.site, item, fetched_at)
            if obs:
                result.observations.append(obs)
    if not result.observations:
        result.ok = False
        result.errors.append(f"{landing_url}: browser fallback returned zero observations")
    return result


def _midland_pages_via_browser(
    landing_url: str,
    base_url: str,
    params: dict[str, str],
    timeout: int,
) -> tuple[list[dict], list[str]]:
    from pathlib import Path
    import os

    local_browsers = Path(__file__).resolve().parents[2] / ".cache" / "ms-playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and local_browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)

    from playwright.sync_api import sync_playwright

    browser_params = dict(params)
    browser_params.pop("page", None)
    query = urllib.parse.urlencode(browser_params)
    timeout_ms = timeout * 1000
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--single-process", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page(locale="zh-HK")
            page.set_extra_http_headers({"Accept-Language": "zh-HK,zh-CN;q=0.9,en;q=0.8"})
            page.goto(landing_url, wait_until="domcontentloaded", timeout=timeout_ms)
            payload = page.evaluate(
                """async ({ baseUrl, query, limit }) => {
                    const node = document.querySelector('#__NEXT_DATA__');
                    const nextData = node ? JSON.parse(node.textContent || '{}') : {};
                    const token = nextData?.runtimeConfig?.BUILD_TOKEN;
                    if (!token) {
                        throw new Error('unable to extract API token');
                    }
                    const pages = [];
                    const urls = [];
                    let pageNo = 1;
                    let totalPages = 1;
                    while (pageNo <= totalPages) {
                        const url = `${baseUrl}?${query}&page=${pageNo}`;
                        urls.push(url);
                        const response = await fetch(url, {
                            headers: {
                                'Accept': 'application/json',
                                'Accept-Language': 'zh-HK,zh-CN;q=0.9,en;q=0.8',
                                'Authorization': `Bearer ${token}`,
                            },
                        });
                        if (!response.ok) {
                            throw new Error(`${url}: HTTP ${response.status}`);
                        }
                        const data = await response.json();
                        const count = Number(data.count || (data.result || []).length || 0);
                        totalPages = Math.max(1, Math.ceil(count / limit));
                        pages.push(data);
                        pageNo += 1;
                    }
                    return { pages, urls, token };
                }""",
                {"baseUrl": base_url, "query": query, "limit": int(params.get("limit") or 100)},
            )
            token = payload.get("token")
            if token:
                _save_midland_build_token(str(token))
            return list(payload.get("pages") or []), list(payload.get("urls") or [])
        finally:
            browser.close()


def _fetch_json(url: str, headers: dict[str, str], delay: float, timeout: int) -> tuple[dict | None, str | None]:
    if delay:
        time.sleep(delay)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            try:
                return json.loads(text), None
            except json.JSONDecodeError as exc:
                snippet = text[:500].replace("\n", " ") if text else ""
                return None, f"JSON decode error: {exc}; body_snippet={snippet!r}"
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            charset = exc.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            snippet = text[:500].replace("\n", " ") if text else ""
        except Exception:  # noqa: BLE001
            snippet = ""
        return None, f"HTTP {exc.code}: {exc.reason}; body_snippet={snippet!r}"
    except urllib.error.URLError as exc:
        return None, f"URL error: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _fetch_json_with_retries(
    url: str,
    headers: dict[str, str],
    delay: float,
    timeout: int,
    attempts: int = 3,
    progress=None,
) -> tuple[dict | None, str | None]:
    last_error: str | None = None
    for attempt in range(max(1, attempts)):
        retry_delay = delay if attempt == 0 else float(attempt * 2)
        if progress:
            progress(f"json get attempt {attempt + 1}/{attempts} start: {url}")
        data, error = _fetch_json(url, headers=headers, delay=retry_delay, timeout=timeout)
        if data is not None:
            if progress:
                progress(f"json get attempt {attempt + 1}/{attempts} ok")
            return data, None
        last_error = error
        if progress:
            progress(f"json get attempt {attempt + 1}/{attempts} failed: {error}")
        if error and error.startswith("HTTP 4") and "HTTP 429" not in error and not _looks_like_temporary_block(error):
            break
    return None, last_error


def _post_json(url: str, payload: dict, headers: dict[str, str], delay: float, timeout: int) -> tuple[dict | None, str | None]:
    if delay:
        time.sleep(delay)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            try:
                return json.loads(text), None
            except json.JSONDecodeError as exc:
                snippet = text[:500].replace("\n", " ") if text else ""
                return None, f"JSON decode error: {exc}; body_snippet={snippet!r}"
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            charset = exc.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            snippet = text[:500].replace("\n", " ") if text else ""
        except Exception:  # noqa: BLE001
            snippet = ""
        return None, f"HTTP {exc.code}: {exc.reason}; body_snippet={snippet!r}"
    except urllib.error.URLError as exc:
        return None, f"URL error: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _post_json_with_retries(
    url: str,
    payload: dict,
    headers: dict[str, str],
    delay: float,
    timeout: int,
    attempts: int = 3,
    progress=None,
) -> tuple[dict | None, str | None]:
    last_error: str | None = None
    for attempt in range(max(1, attempts)):
        retry_delay = delay if attempt == 0 else float(attempt * 2)
        if progress:
            progress(f"json post attempt {attempt + 1}/{attempts} start: {url}")
        data, error = _post_json(
            url,
            payload=payload,
            headers=headers,
            delay=retry_delay,
            timeout=timeout,
        )
        if data is not None:
            if progress:
                progress(f"json post attempt {attempt + 1}/{attempts} ok")
            return data, None
        last_error = error
        if progress:
            progress(f"json post attempt {attempt + 1}/{attempts} failed: {error}")
        if error and error.startswith("HTTP 4") and "HTTP 429" not in error and not _looks_like_temporary_block(error):
            break
    return None, last_error


def _looks_like_temporary_block(error: str) -> bool:
    lowered = error.lower()
    return "request blocked" in lowered or "request could not be satisfied" in lowered


def _name(value: object) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
        return normalize_loose(str(name)) if name else None
    return None


def _layout_from_bedroom(value: object) -> str | None:
    try:
        bedroom = int(value)
    except (TypeError, ValueError):
        return None
    if bedroom == 0:
        return "开放式"
    return f"{bedroom}房"


def _age_from_iso_date(value: object) -> int | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        completed_at = datetime.fromisoformat(text)
    except ValueError:
        return None
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    today = datetime.now(timezone.utc).date()
    completed = completed_at.date()
    age = today.year - completed.year
    if (today.month, today.day) < (completed.month, completed.day):
        age -= 1
    return max(0, age)


def _age_from_millis(value: object) -> int | None:
    if value in (None, "", 0):
        return None
    try:
        completed_at = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return _age_from_iso_date(completed_at.isoformat())


def _int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _observation_from_midland_like_item(site: str, item: dict, fetched_at: str) -> ListingObservation | None:
    source_id = item.get("serial_no")
    source_url = item.get("url_desc")
    if not source_id or not source_url:
        return None
    estate = _name(item.get("estate"))
    phase = _name(item.get("phase"))
    building = _name(item.get("building"))
    floor = _name(item.get("floor_level"))
    flat = normalize_loose(str(item.get("flat"))) if item.get("flat") else None
    if flat and not flat.endswith("室"):
        flat = f"{flat}室"
    layout = _layout_from_bedroom(item.get("bedroom"))
    district = _name(item.get("district")) or _name(item.get("sm_district")) or _name(item.get("int_sm_district"))
    rent = item.get("rent_hkd") or item.get("rent")
    area = item.get("net_area")
    gross_area = item.get("area")
    building_age = _age_from_iso_date((item.get("building") or {}).get("first_op_date"))
    price_per_sqft = item.get("rent_over_net_area")
    title_parts = [
        estate,
        phase,
        building,
        floor,
        flat,
        layout,
        district,
        f"实用 {area} 呎" if area else None,
        f"建筑 {gross_area} 呎" if gross_area else None,
        f"租 $ {rent}" if rent else None,
    ]
    title = " ".join(part for part in title_parts if part)
    return ListingObservation(
        source_site=site,
        source_url=str(source_url),
        source_listing_id=str(source_id),
        fetched_at=fetched_at,
        title=title,
        estate_name=estate,
        building=phase,
        block=building,
        floor=floor,
        flat=flat,
        rent_hkd=int(rent) if rent is not None else None,
        usable_area_sqft=int(area) if area is not None else None,
        gross_area_sqft=int(gross_area) if gross_area is not None else None,
        building_age_years=building_age,
        price_per_sqft=float(price_per_sqft) if price_per_sqft is not None else None,
        layout=layout,
        district=district,
    )


def _observation_from_ricacorp_item(item: dict, fetched_at: str) -> ListingObservation | None:
    source_id = item.get("postNo")
    alias = item.get("aliasV4") or item.get("alias")
    if not source_id or not alias:
        return None
    source_url = "https://www.ricacorp.com/zh-hk/property/detail/" + urllib.parse.quote(str(alias), safe="/-")
    names = item.get("publicLocationNamesHk") or []
    estate = normalize_loose(names[4]) if len(names) >= 5 else normalize_loose(item.get("displayTextHk") or item.get("displayText"))
    district = normalize_loose(names[3]) if len(names) >= 4 else None
    block = normalize_loose(names[-1]) if len(names) >= 6 else None
    floor = normalize_loose(item.get("floorZoneTextHk") or item.get("floorZoneText"))
    flat = normalize_loose(item.get("flatZoneTextHk") or item.get("flatZoneText"))
    layout = _layout_from_bedroom(item.get("room"))
    rent = item.get("marketPrice")
    area = item.get("saleableArea")
    gross_area = item.get("grossArea")
    building_age = _age_from_millis(item.get("opDate"))
    title_parts = [
        item.get("displayTextHk") or item.get("displayText"),
        floor,
        flat,
        layout,
        district,
        f"实用 {area} 呎" if area else None,
        f"建筑 {gross_area} 呎" if gross_area else None,
        f"租 $ {rent}" if rent else None,
    ]
    title = normalize_loose(" ".join(str(part) for part in title_parts if part))
    return ListingObservation(
        source_site="ricacorp",
        source_url=source_url,
        source_listing_id=str(source_id),
        fetched_at=fetched_at,
        title=title,
        estate_name=estate,
        block=block,
        floor=floor or None,
        flat=flat or None,
        rent_hkd=int(rent) if rent is not None else None,
        usable_area_sqft=int(area) if area is not None else None,
        gross_area_sqft=int(gross_area) if gross_area is not None else None,
        building_age_years=building_age,
        price_per_sqft=float(item["unitPrice"]) if item.get("unitPrice") is not None else None,
        layout=layout,
        district=district,
    )


def _observation_from_centanet_item(item: dict, fetched_at: str) -> ListingObservation | None:
    source_id = item.get("refNo")
    source_url = item.get("detailUrl")
    if not source_id or not source_url:
        return None
    estate_parts = [item.get("bigEstateName"), item.get("estateName")]
    estate = normalize_loose(" ".join(str(part) for part in estate_parts if part))
    block = normalize_loose(item.get("buildingName"))
    layout = _layout_from_bedroom(item.get("bedroomCount"))
    rent = item.get("rentPrice") or (item.get("priceInfo") or {}).get("rent")
    area = item.get("nSize") or (item.get("areaInfo") or {}).get("nSize")
    gross_area = item.get("size") or (item.get("areaInfo") or {}).get("size")
    building_age = _int_or_none(item.get("buildingAge"))
    price_per_sqft = item.get("nUnitRent") or (item.get("unitPriceInfo") or {}).get("nUnitRent")
    district = normalize_loose(item.get("districtName"))
    title_parts = [
        estate,
        block,
        layout,
        district,
        f"实用 {area} 呎" if area else None,
        f"建筑 {gross_area} 呎" if gross_area else None,
        f"租 $ {rent}" if rent else None,
    ]
    return ListingObservation(
        source_site="centanet",
        source_url=str(source_url),
        source_listing_id=str(source_id),
        fetched_at=fetched_at,
        title=" ".join(part for part in title_parts if part),
        estate_name=estate or None,
        block=block or None,
        rent_hkd=int(float(rent)) if rent is not None else None,
        usable_area_sqft=int(float(area)) if area is not None else None,
        gross_area_sqft=int(float(gross_area)) if gross_area is not None else None,
        building_age_years=building_age,
        price_per_sqft=float(price_per_sqft) if price_per_sqft is not None else None,
        layout=layout,
        district=district or None,
    )
