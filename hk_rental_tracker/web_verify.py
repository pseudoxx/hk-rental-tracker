from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters import GenericSiteAdapter
from .adapters.base import (
    _centanet_filter_payload,
    _common_property_params,
    _extract_next_token,
    _fetch_json,
    _post_json,
    _room_range,
)
from .config import TaskConfig, load_task_config
from .fetcher import PageFetcher
from .normalization import normalize_loose
from .scanner import filter_observations


@dataclass
class WebTotalCheck:
    site: str
    web_total: int | None
    db_total: int
    matched: bool
    url: str
    evidence_type: str = "api_total"
    screenshot: str | None = None
    frontend_screenshot: str | None = None
    frontend_url: str | None = None
    frontend_filter_status: str | None = None
    frontend_title: str | None = None
    frontend_excerpt: str | None = None
    frontend_error: str | None = None
    error: str | None = None


def verify_web_totals(task_dir: str | Path) -> tuple[Path, list[WebTotalCheck]]:
    task_path = Path(task_dir)
    config = load_task_config(task_path)
    output_dir = task_path / "screenshots" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    checks = _collect_checks(task_path, config)
    _write_screenshots(output_dir, config, checks)
    _write_report(output_dir, config, checks)
    return output_dir, checks


def _collect_checks(task_path: Path, config: TaskConfig) -> list[WebTotalCheck]:
    db_counts = _db_source_counts(task_path / "rental.db")
    checks: list[WebTotalCheck] = []
    for site in config.sites:
        if not config.site_is_authorized(site):
            checks.append(
                WebTotalCheck(
                    site=site,
                    web_total=None,
                    db_total=db_counts.get(site, 0),
                    matched=False,
                    url="",
                    evidence_type="source_policy_disabled",
                    error="source is disabled by task source policy",
                )
            )
            continue
        try:
            site_result = GenericSiteAdapter(site).scan(config, PageFetcher(delay_seconds=0))
            if not site_result.ok:
                raise RuntimeError("; ".join(site_result.errors) or "site scan failed")
            filtered = filter_observations(site_result.observations, config.area_terms, config.filters)
            web_total = len({observation.source_key for observation in filtered})
            url = "\n".join(site_result.fetched_urls)
            db_total = db_counts.get(site, 0)
            checks.append(
                WebTotalCheck(
                    site=site,
                    web_total=web_total,
                    db_total=db_total,
                    matched=web_total == db_total,
                    url=url,
                    evidence_type="api_observations_after_filters",
                )
            )
        except Exception as exc:  # noqa: BLE001 - verification should report every source
            checks.append(WebTotalCheck(site=site, web_total=None, db_total=db_counts.get(site, 0), matched=False, url="", error=str(exc)))
    return checks


def _db_source_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            str(site): int(count)
            for site, count in conn.execute(
                "SELECT source_site, COUNT(*) FROM source_state WHERE active = 1 GROUP BY source_site"
            )
        }
    finally:
        conn.close()


def _midland_like_total(config: TaskConfig, brand: str) -> tuple[int, str]:
    landing_url = "https://www.midland.com.hk/zh-hk/list/rent" if brand == "midland" else "https://www.hkp.com.hk/zh-hk/list/rent"
    token_fetch = PageFetcher(delay_seconds=0).fetch(landing_url)
    if not token_fetch.ok:
        raise RuntimeError(f"{landing_url}: {token_fetch.error or token_fetch.status_code}")
    token = _extract_next_token(token_fetch.html, brand)
    if not token:
        raise RuntimeError(f"{landing_url}: unable to extract API token")
    params = _common_property_params(config, lang="zh-hk")
    params["text"] = config.area
    base_url = (
        "https://data.midland.com.hk/search/v2/properties"
        if brand == "midland"
        else "https://data.hkp.com.hk/search/v2/properties"
    )
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Accept-Language": "zh-HK,zh-CN;q=0.9,en;q=0.8",
        "Authorization": f"Bearer {token}",
        "Origin": "https://www.midland.com.hk" if brand == "midland" else "https://www.hkp.com.hk",
        "Referer": landing_url,
    }
    data, error = _fetch_json(url, headers=headers, delay=0, timeout=30)
    if data is None:
        raise RuntimeError(f"{url}: {error or 'unable to fetch or parse JSON'}")
    return int(data.get("count") or 0), url


def _centanet_total(config: TaskConfig) -> tuple[int, str]:
    url = "https://hk.centanet.com/findproperty/api/Post/Search"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh-HK;q=0.9,en;q=0.8",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": "https://hk.centanet.com/findproperty/zh-cn/list/rent/" + urllib.parse.quote(config.area),
    }
    payload: dict[str, Any] = {
        "postType": "Rent",
        "sort": "Ranking",
        "order": "Ascending",
        "size": 1,
        "displayTextStyle": "WebResultList",
        "bigPhotoMode": False,
        "keyword": config.area,
        "pageSource": "search",
        "offset": 0,
    }
    payload.update(_centanet_filter_payload(config))
    data, error = _post_json(url, payload=payload, headers=headers, delay=0, timeout=30)
    if data is None:
        raise RuntimeError(f"{url}: {error or 'unable to fetch or parse JSON'}")
    return int(data.get("count") or 0), url


def _write_screenshots(output_dir: Path, config: TaskConfig, checks: list[WebTotalCheck]) -> None:
    local_browsers = Path(__file__).resolve().parent.parent / ".cache" / "ms-playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and local_browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        for check in checks:
            check.error = f"{check.error}; Playwright unavailable: {exc}" if check.error else f"Playwright unavailable: {exc}"
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--single-process", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(locale="zh-HK", viewport={"width": 1280, "height": 900})
        context.set_extra_http_headers({"Accept-Language": "zh-HK,zh-CN;q=0.9,en;q=0.8"})
        for check in checks:
            frontend_path = output_dir / f"{check.site}-frontend.png"
            evidence_path = output_dir / f"{check.site}-api-total.png"
            frontend_page = None
            if check.evidence_type != "source_policy_disabled":
                try:
                    frontend_page = context.new_page()
                    _render_frontend_page(frontend_page, config, check)
                    check.frontend_url = frontend_page.url
                    check.frontend_title = frontend_page.title()
                    check.frontend_excerpt = frontend_page.locator("body").inner_text(timeout=5000)[:2500]
                    if _looks_blocked(check.frontend_title, check.frontend_excerpt):
                        check.frontend_error = "frontend appears blocked"
                        check.frontend_filter_status = "blocked"
                    else:
                        check.frontend_filter_status = _frontend_filter_status(config, check)
                    frontend_page.screenshot(path=str(frontend_path), full_page=True)
                    check.frontend_screenshot = str(frontend_path)
                except Exception as exc:  # noqa: BLE001
                    check.frontend_error = str(exc)
                    check.frontend_filter_status = "failed"
                finally:
                    if frontend_page is not None:
                        try:
                            frontend_page.close()
                        except Exception:  # noqa: BLE001
                            pass
            evidence_page = None
            try:
                evidence_page = context.new_page()
                _render_check_page(evidence_page, config, check)
                evidence_page.screenshot(path=str(evidence_path), full_page=True)
                check.screenshot = str(evidence_path)
            except Exception as exc:  # noqa: BLE001
                check.error = f"{check.error}; screenshot failed: {exc}" if check.error else f"screenshot failed: {exc}"
            finally:
                if evidence_page is not None:
                    try:
                        evidence_page.close()
                    except Exception:  # noqa: BLE001
                        pass
        context.close()
        browser.close()


def _frontend_url(config: TaskConfig, check: WebTotalCheck) -> str:
    configured = config.source_search_urls.get(check.site) or []
    if configured:
        return configured[0]
    if check.site == "ricacorp":
        return _ricacorp_filtered_frontend_url(config)
    if check.site == "centanet":
        return "https://hk.centanet.com/findproperty/zh-cn/list/rent/" + urllib.parse.quote(config.area)
    return {
        "midland": "https://www.midland.com.hk/zh-hk/list/rent",
        "ricacorp": "https://www.ricacorp.com/zh-hk/property/list/rent",
        "hkp": "https://www.hkp.com.hk/zh-hk/list/rent",
    }.get(check.site, "about:blank")


def _ricacorp_filtered_frontend_url(config: TaskConfig) -> str:
    url = "https://www.ricacorp.com/zh-hk/property/list/rent"
    route_params: list[str] = []
    if config.filters.max_rent is not None:
        route_params.append(f"priceTo={config.filters.max_rent}")
    if config.filters.min_area_sqft is not None:
        route_params.append(f"saleableAreaFrom={config.filters.min_area_sqft}")
    if config.filters.max_area_sqft is not None:
        route_params.append(f"saleableAreaTo={config.filters.max_area_sqft}")
    if config.filters.min_gross_area_sqft is not None:
        route_params.append(f"grossAreaFrom={config.filters.min_gross_area_sqft}")
    if config.filters.max_gross_area_sqft is not None:
        route_params.append(f"grossAreaTo={config.filters.max_gross_area_sqft}")
    room_from, room_to = _room_range(config.filters.layouts)
    if room_from is not None:
        route_params.append(f"roomFrom={room_from}")
    if room_to is not None:
        route_params.append(f"roomTo={room_to}")
    if route_params:
        url += ";" + ";".join(route_params)
    return url


def _render_frontend_page(page: Any, config: TaskConfig, check: WebTotalCheck) -> None:
    page.goto(_frontend_url(config, check), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    _accept_cookie_if_present(page)
    if check.site == "midland":
        _apply_midland_frontend_filters(page, config)
    elif check.site == "centanet":
        _apply_centanet_frontend_filters(page, config)
    elif check.site == "hkp":
        _apply_hkp_frontend_filters(page, config)


def _apply_midland_frontend_filters(page: Any, config: TaskConfig) -> None:
    _apply_midland_like_layout_filter(page, config)
    _apply_midland_like_price_filter(page, config)


def _apply_hkp_frontend_filters(page: Any, config: TaskConfig) -> None:
    _apply_hkp_area_search(page, config)
    _apply_midland_like_price_filter(page, config)
    _apply_midland_like_layout_filter(page, config)


def _apply_midland_like_layout_filter(page: Any, config: TaskConfig) -> None:
    labels = _frontend_layout_labels(config)
    if not labels:
        return
    clickable = "button,[role=button],label,div,span"
    if not _click_visible_text(page, "間隔", selector=clickable, exact=True, min_y=240, max_y=380):
        return
    page.wait_for_timeout(800)
    selected = False
    for label in labels:
        selected = _click_visible_text(page, label, selector=clickable, exact=False, min_y=330) or selected
        page.wait_for_timeout(300)
    if selected:
        if not _click_popup_search(page):
            page.mouse.click(500, 500)
        page.wait_for_load_state("domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)


def _apply_midland_like_price_filter(page: Any, config: TaskConfig) -> None:
    if config.filters.max_rent is None:
        return
    if not _click_visible_text(page, "價格", selector="button,[role=button],div,span", exact=True, min_y=240, max_y=380):
        return
    page.wait_for_timeout(800)
    if _type_into_visible_input(page, str(config.filters.max_rent), placeholder="最大價格"):
        page.keyboard.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)


def _apply_centanet_frontend_filters(page: Any, config: TaskConfig) -> None:
    if config.filters.max_rent is not None and _click_visible_text(page, "租金", selector="button,[role=button]", exact=True):
        page.wait_for_timeout(800)
        if _type_into_visible_input(page, str(config.filters.max_rent), current_value="140000"):
            page.mouse.click(500, 500)
            page.wait_for_timeout(4500)
    if not _click_visible_text(page, "间隔", selector="button,[role=button]", exact=True):
        return
    page.wait_for_timeout(800)
    selected = False
    for label in _frontend_layout_labels(config):
        selected = _click_visible_text(page, label, selector="label,button,[role=button]", exact=False, min_y=200) or selected
        page.wait_for_timeout(300)
    if selected:
        page.mouse.click(500, 500)
        page.wait_for_timeout(4500)


def _apply_hkp_area_search(page: Any, config: TaskConfig) -> None:
    inputs = page.get_by_placeholder("請輸入地區/屋苑/港鐵/學校/發展商")
    if inputs.count() < 2:
        return
    inputs.nth(1).fill(config.area)
    buttons = page.get_by_role("button", name="搜尋", exact=True)
    if buttons.count() >= 2:
        buttons.nth(1).click()
        page.wait_for_load_state("domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)


def _accept_cookie_if_present(page: Any) -> None:
    if _click_visible_text(page, "同意", selector="button,[role=button]", exact=True, max_y=850):
        page.wait_for_timeout(500)


def _click_popup_search(page: Any) -> bool:
    boxes = _visible_boxes(page, "button,[role=button]", text="搜尋", exact=True, min_y=330) or _visible_boxes(
        page, "button,[role=button]", text="搜寻", exact=True, min_y=330
    )
    if not boxes:
        return False
    box = boxes[-1]
    page.mouse.click(float(box["x"]) + float(box["width"]) / 2, float(box["y"]) + float(box["height"]) / 2)
    return True


def _click_visible_text(
    page: Any,
    text: str,
    *,
    selector: str,
    exact: bool = False,
    min_y: float | None = None,
    max_y: float | None = None,
) -> bool:
    boxes = _visible_boxes(page, selector, text=text, exact=exact, min_y=min_y, max_y=max_y)
    if not boxes:
        return False
    box = boxes[0]
    page.mouse.click(float(box["x"]) + float(box["width"]) / 2, float(box["y"]) + float(box["height"]) / 2)
    return True


def _type_into_visible_input(
    page: Any,
    typed_value: str,
    *,
    placeholder: str | None = None,
    current_value: str | None = None,
) -> bool:
    boxes = _visible_input_boxes(page, placeholder=placeholder, current_value=current_value)
    if not boxes:
        return False
    box = boxes[-1]
    page.mouse.click(float(box["x"]) + float(box["width"]) / 2, float(box["y"]) + float(box["height"]) / 2)
    page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
    page.keyboard.type(typed_value)
    page.wait_for_timeout(500)
    return True


def _visible_boxes(
    page: Any,
    selector: str,
    *,
    text: str | None = None,
    exact: bool = False,
    min_y: float | None = None,
    max_y: float | None = None,
) -> list[dict[str, float]]:
    return page.evaluate(
        """({ selector, text, exact, minY, maxY }) => {
            const visible = [];
            for (const el of document.querySelectorAll(selector)) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') {
                    continue;
                }
                if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) {
                    continue;
                }
                if (minY !== null && rect.top < minY) {
                    continue;
                }
                if (maxY !== null && rect.top > maxY) {
                    continue;
                }
                const raw = `${el.innerText || el.textContent || el.getAttribute('aria-label') || ''}`.trim();
                const compact = raw.replace(/\\s+/g, '');
                const needle = text === null ? null : `${text}`.replace(/\\s+/g, '');
                if (needle !== null && (exact ? compact !== needle : !compact.includes(needle))) {
                    continue;
                }
                visible.push({ x: rect.left, y: rect.top, width: rect.width, height: rect.height });
            }
            return visible.sort((a, b) => (a.y - b.y) || (a.x - b.x));
        }""",
        {"selector": selector, "text": text, "exact": exact, "minY": min_y, "maxY": max_y},
    )


def _visible_input_boxes(
    page: Any,
    *,
    placeholder: str | None = None,
    current_value: str | None = None,
) -> list[dict[str, float]]:
    return page.evaluate(
        """({ placeholder, currentValue }) => {
            const visible = [];
            for (const el of document.querySelectorAll('input')) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') {
                    continue;
                }
                if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) {
                    continue;
                }
                if (placeholder !== null && el.getAttribute('placeholder') !== placeholder) {
                    continue;
                }
                if (currentValue !== null && el.value !== currentValue) {
                    continue;
                }
                visible.push({ x: rect.left, y: rect.top, width: rect.width, height: rect.height });
            }
            return visible.sort((a, b) => (a.y - b.y) || (a.x - b.x));
        }""",
        {"placeholder": placeholder, "currentValue": current_value},
    )


def _looks_blocked(title: str | None, text: str | None) -> bool:
    haystack = f"{title or ''}\n{text or ''}".lower()
    return "service unavailable" in haystack or "request is blocked" in haystack


def _frontend_filter_status(config: TaskConfig, check: WebTotalCheck) -> str:
    text = normalize_loose(check.frontend_excerpt or "")
    if not text:
        return "unknown: no frontend text captured"
    area_ok = any(normalize_loose(term) in text for term in config.area_terms)
    max_rent_ok = True
    if config.filters.max_rent:
        max_rent = str(config.filters.max_rent)
        max_rent_ok = max_rent in text or f"${config.filters.max_rent:,}" in (check.frontend_excerpt or "")
    layouts = _frontend_layout_terms(config)
    layout_ok = not layouts or any(term in text for term in layouts)
    if area_ok and max_rent_ok and layout_ok:
        return "appears filtered"
    missing = []
    if not area_ok:
        missing.append("area")
    if not max_rent_ok:
        missing.append("max_rent")
    if not layout_ok:
        missing.append("layout")
    return "partial frontend evidence; missing visible " + ", ".join(missing)


def _frontend_layout_terms(config: TaskConfig) -> list[str]:
    terms: list[str] = []
    for layout in config.filters.layouts:
        normalized = normalize_loose(layout)
        if "开放式" in normalized or "開放式" in normalized:
            terms.extend(["开放式", "開放式"])
        if "1房" in normalized or "一房" in normalized:
            terms.extend(["1房", "一房"])
    return [normalize_loose(term) for term in terms]


def _frontend_layout_labels(config: TaskConfig) -> list[str]:
    labels: list[str] = []
    for layout in config.filters.layouts:
        normalized = normalize_loose(layout)
        if "开放式" in normalized or "開放式" in normalized:
            labels.extend(["開放式", "开放式"])
        match = None if "开放式" in normalized or "開放式" in normalized else re.search(r"(\d+)\s*房", normalized)
        if match:
            labels.append(f"{match.group(1)}房")
    seen = set()
    return [label for label in labels if not (label in seen or seen.add(label))]


def _render_check_page(page: Any, config: TaskConfig, check: WebTotalCheck) -> None:
    payload = {
        "site": check.site,
        "area": config.area,
        "max_rent": config.filters.max_rent,
        "min_area_sqft": config.filters.min_area_sqft,
        "max_area_sqft": config.filters.max_area_sqft,
        "min_gross_area_sqft": config.filters.min_gross_area_sqft,
        "max_gross_area_sqft": config.filters.max_gross_area_sqft,
        "min_building_age_years": config.filters.min_building_age_years,
        "max_building_age_years": config.filters.max_building_age_years,
        "min_price_per_sqft": config.filters.min_price_per_sqft,
        "max_price_per_sqft": config.filters.max_price_per_sqft,
        "layouts": config.filters.layouts,
        "keywords": config.filters.keywords,
        "excluded_estates": config.filters.excluded_estates,
        "excluded_keywords": config.filters.excluded_keywords,
        "web_total": check.web_total,
        "db_total": check.db_total,
        "matched": check.matched,
        "evidence_type": check.evidence_type,
        "frontend_url": check.frontend_url or _frontend_url(config, check),
        "frontend_filter_status": check.frontend_filter_status,
        "source_url": check.url,
        "error": check.error,
    }
    page.set_content(
        """
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <title>API total verification</title>
          </head>
          <body></body>
        </html>
        """,
        wait_until="load",
    )
    page.evaluate(
        """payload => {
            document.title = `${payload.site} API total verification`;
            document.body.innerHTML = `
              <main style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 32px; line-height: 1.45;">
                <h1 style="margin: 0 0 18px; font-size: 28px;">${payload.site} API total verification</h1>
                <p><strong>Evidence type:</strong> ${payload.evidence_type}</p>
                <p><strong>Area:</strong> ${payload.area}</p>
                <p><strong>Max rent:</strong> ${payload.max_rent}</p>
                <p><strong>Usable area:</strong> ${payload.min_area_sqft || '-'} - ${payload.max_area_sqft || '-'}</p>
                <p><strong>Gross area:</strong> ${payload.min_gross_area_sqft || '-'} - ${payload.max_gross_area_sqft || '-'}</p>
                <p><strong>Building age:</strong> ${payload.min_building_age_years || '-'} - ${payload.max_building_age_years || '-'}</p>
                <p><strong>Price per sqft:</strong> ${payload.min_price_per_sqft || '-'} - ${payload.max_price_per_sqft || '-'}</p>
                <p><strong>Layouts:</strong> ${payload.layouts.join(', ')}</p>
                <p><strong>Keywords:</strong> ${payload.keywords.join(', ') || '-'}</p>
                <p><strong>Excluded estates:</strong> ${payload.excluded_estates.join(', ') || '-'}</p>
                <p><strong>Excluded keywords:</strong> ${payload.excluded_keywords.join(', ') || '-'}</p>
                <p><strong>API observations after local filters:</strong> ${payload.web_total}</p>
                <p><strong>Local DB source total:</strong> ${payload.db_total}</p>
                <p><strong>Matched:</strong> ${payload.matched}</p>
                <p><strong>Frontend URL separately screenshotted:</strong> ${payload.frontend_filter_status || '-'}</p>
                <pre style="white-space: pre-wrap; border: 1px solid #999; padding: 12px;">${payload.frontend_url}</pre>
                <p><strong>API source URL:</strong></p>
                <pre style="white-space: pre-wrap; border: 1px solid #999; padding: 12px;">${payload.source_url}</pre>
                ${payload.error ? `<p><strong>Error:</strong> ${payload.error}</p>` : ''}
              </main>`;
        }""",
        payload,
    )


def _write_report(output_dir: Path, config: TaskConfig, checks: list[WebTotalCheck]) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": config.slug,
        "filters": config.filters.to_dict(),
        "checks": [asdict(check) for check in checks],
    }
    (output_dir / "web-total-verification.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Web Total Verification",
        "",
        f"Generated at: {payload['generated_at']}",
        "",
        "| Source | Evidence | API total | Local DB total | Match | API evidence screenshot | Frontend screenshot | Frontend filter status | Frontend title | Frontend status |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for check in checks:
        screenshot = Path(check.screenshot).name if check.screenshot else "-"
        frontend = Path(check.frontend_screenshot).name if check.frontend_screenshot else "-"
        frontend_filter = (check.frontend_filter_status or "-").replace("|", "\\|")
        frontend_title = (check.frontend_title or "-").replace("|", "\\|")
        frontend_error = (check.frontend_error or "ok").replace("|", "\\|")
        lines.append(
            f"| {check.site} | {check.evidence_type} | {check.web_total if check.web_total is not None else '-'} | "
            f"{check.db_total} | {check.matched} | {screenshot} | {frontend} | {frontend_filter} | {frontend_title} | {frontend_error} |"
        )
    for check in checks:
        if check.site == "ricacorp" and check.frontend_filter_status == "blocked":
            lines.extend(
                [
                    "",
                    "Ricacorp headless frontend is blocked. Use Browser skill fallback with this URL for manual frontend evidence:",
                    "",
                    _frontend_url(config, check),
                    "",
                    "The Browser fallback page should show the configured area"
                    + (f" `{config.area}`" if config.area else "")
                    + (f", max rent `{config.filters.max_rent}`" if config.filters.max_rent else "")
                    + (f", and layouts `{', '.join(config.filters.layouts)}`" if config.filters.layouts else "")
                    + ". Its visible frontend count may not include this tracker's local blacklist filters, so the automated Ricacorp total remains the API/local-filter total above.",
                ]
            )
            break
    (output_dir / "web-total-verification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
