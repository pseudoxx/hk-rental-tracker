from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
import os


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


@dataclass
class FetchResult:
    url: str
    ok: bool
    html: str = ""
    status_code: int | None = None
    error: str | None = None


class PageFetcher:
    def __init__(self, render_javascript: bool = False, delay_seconds: float = 1.0, timeout: int = 30):
        self.render_javascript = render_javascript
        self.delay_seconds = delay_seconds
        self.timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.render_javascript:
            return self._fetch_with_playwright(url)
        return self._fetch_static(url)

    def _fetch_static(self, url: str) -> FetchResult:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-HK,zh-CN;q=0.9,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                html = raw.decode(charset, errors="replace")
                return FetchResult(url=url, ok=True, html=html, status_code=response.status)
        except urllib.error.HTTPError as exc:
            return FetchResult(url=url, ok=False, status_code=exc.code, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - command line tool should report site failures
            return FetchResult(url=url, ok=False, error=str(exc))

    def _fetch_with_playwright(self, url: str) -> FetchResult:
        local_browsers = Path(__file__).resolve().parent.parent / ".cache" / "ms-playwright"
        if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and local_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                url=url,
                ok=False,
                error=f"Playwright is not installed or not ready: {exc}",
            )
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--single-process",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                    ],
                )
                page = browser.new_page(locale="zh-HK")
                page.set_extra_http_headers({"Accept-Language": "zh-HK,zh-CN;q=0.9,en;q=0.8"})
                page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                html = page.content()
                browser.close()
                return FetchResult(url=url, ok=True, html=html, status_code=200)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(url=url, ok=False, error=str(exc))
