from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
import socket
import urllib.parse

from .adapters import GenericSiteAdapter, SiteScanResult
from .config import load_task_config
from .exporter import export_task
from .fetcher import PageFetcher
from .models import ListingObservation
from .storage import RentalStore


@dataclass
class SiteValidationSummary:
    site: str
    ok: bool
    raw_observations: int = 0
    filtered_observations: int = 0
    rejected_observations: int = 0
    missing_marking_enabled: bool = False
    missing_sources_marked: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "site": self.site,
            "ok": self.ok,
            "raw_observations": self.raw_observations,
            "filtered_observations": self.filtered_observations,
            "rejected_observations": self.rejected_observations,
            "missing_marking_enabled": self.missing_marking_enabled,
            "missing_sources_marked": self.missing_sources_marked,
            "errors": self.errors,
        }


@dataclass
class ScanReport:
    task_dir: Path
    run_id: int
    mode: str
    inserted_observations: int = 0
    matched_observations: int = 0
    missing_sources_marked: int = 0
    site_results: list[SiteScanResult] = field(default_factory=list)
    site_validation: list[SiteValidationSummary] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def scan_task(
    task_dir: str | Path,
    mode: str = "daily",
    render_javascript: bool | None = None,
    sites: list[str] | None = None,
) -> ScanReport:
    task_path = Path(task_dir)
    config = load_task_config(task_path)
    options = config.scan_options
    render = bool(options.get("render_javascript", False) if render_javascript is None else render_javascript)
    delay = float(options.get("request_delay_seconds", 1.0))
    safe_missing = bool(options.get("mark_missing_only_when_source_has_results", True))
    preflight = bool(options.get("preflight_network", True))

    store = RentalStore(task_path / "rental.db")
    run_id = store.start_run(mode=mode, task_slug=config.slug, filters=config.filters.to_dict())
    report = ScanReport(task_dir=task_path, run_id=run_id, mode=mode)
    fetcher = PageFetcher(render_javascript=render, delay_seconds=delay)

    try:
        selected_sites = sites or config.sites
        if preflight:
            ok, notes = _preflight_network(selected_sites, config.source_search_urls)
            if not ok:
                report.errors.append(notes)
                write_snapshot(task_path, report)
                export_task(task_path, store)
                store.finish_run(run_id, status="blocked", notes=notes)
                return report
        for site in selected_sites:
            if not config.site_is_authorized(site):
                site_result = SiteScanResult(
                    site=site,
                    ok=False,
                    errors=["source is disabled by task source policy"],
                )
                report.site_results.append(site_result)
                report.errors.extend(f"{site}: {err}" for err in site_result.errors)
                report.site_validation.append(
                    SiteValidationSummary(
                        site=site,
                        ok=False,
                        errors=list(site_result.errors),
                    )
                )
                continue
            adapter = GenericSiteAdapter(site)
            site_result = adapter.scan(config, fetcher)
            report.site_results.append(site_result)
            if not site_result.ok:
                report.errors.extend(f"{site}: {err}" for err in site_result.errors)
                report.site_validation.append(
                    SiteValidationSummary(
                        site=site,
                        ok=False,
                        raw_observations=len(site_result.observations),
                        errors=list(site_result.errors),
                    )
                )
                continue
            if site_result.errors:
                report.errors.extend(f"{site}: {err}" for err in site_result.errors)
            filtered = filter_observations(site_result.observations, config.area_terms, config.filters)
            seen_source_keys = set()
            for observation in filtered:
                store.upsert_observation(observation, run_id)
                seen_source_keys.add(observation.source_key)
                report.inserted_observations += 1
            should_mark_missing = mode != "initial" and not site_result.errors and (bool(filtered) or not safe_missing)
            missing_marked = store.mark_missing_sources(
                site,
                seen_source_keys,
                run_id,
                enabled=should_mark_missing,
            )
            report.missing_sources_marked += missing_marked
            report.site_validation.append(
                SiteValidationSummary(
                    site=site,
                    ok=True,
                    raw_observations=len(site_result.observations),
                    filtered_observations=len(filtered),
                    rejected_observations=max(0, len(site_result.observations) - len(filtered)),
                    missing_marking_enabled=should_mark_missing,
                    missing_sources_marked=missing_marked,
                    errors=list(site_result.errors),
                )
            )
        write_snapshot(task_path, report)
        export_task(task_path, store)
        status = "ok" if not report.errors else "partial"
        notes = json.dumps(
            {
                "inserted_observations": report.inserted_observations,
                "missing_sources_marked": report.missing_sources_marked,
                "errors": report.errors,
            },
            ensure_ascii=False,
        )
        store.finish_run(run_id, status=status, notes=notes)
    except Exception as exc:  # noqa: BLE001 - CLI should persist failure details
        report.errors.append(str(exc))
        store.finish_run(run_id, status="failed", notes=str(exc))
        raise
    finally:
        store.close()
    return report


def filter_observations(observations: list[ListingObservation], area_terms: list[str], filters) -> list[ListingObservation]:
    filtered = []
    for observation in observations:
        if filters.matches(observation, area_terms):
            filtered.append(observation)
    return filtered


def _preflight_network(selected_sites: list[str], source_search_urls: dict[str, list[str]]) -> tuple[bool, str]:
    hosts: set[str] = set()
    for site in selected_sites:
        for url in source_search_urls.get(site) or []:
            try:
                parsed = urllib.parse.urlparse(url)
                if parsed.hostname:
                    hosts.add(parsed.hostname)
            except Exception:  # noqa: BLE001
                continue

    # Add known API hosts that may not appear in source_search_urls.
    for site in selected_sites:
        if site == "midland":
            hosts.update({"www.midland.com.hk", "data.midland.com.hk"})
        elif site == "hkp":
            hosts.update({"www.hkp.com.hk", "data.hkp.com.hk"})
        elif site == "ricacorp":
            hosts.add("www.ricacorp.com")
        elif site == "centanet":
            hosts.add("hk.centanet.com")

    if not hosts:
        return True, ""

    dns_failures: list[str] = []
    any_resolved = False
    for host in sorted(hosts):
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            any_resolved = True
        except socket.gaierror as exc:
            dns_failures.append(f"{host}: {exc}")
        except Exception:  # noqa: BLE001
            # Non-DNS errors are not treated as fatal for preflight.
            any_resolved = True

    if not any_resolved:
        notes = "network preflight failed: all hosts DNS resolution failed: " + "; ".join(dns_failures[:10])
        if len(dns_failures) > 10:
            notes += f"; ...(+{len(dns_failures) - 10} more)"
        return False, notes
    return True, ""


def write_snapshot(task_path: Path, report: ScanReport) -> Path:
    snapshot_dir = task_path / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": report.run_id,
        "mode": report.mode,
        "inserted_observations": report.inserted_observations,
        "missing_sources_marked": report.missing_sources_marked,
        "errors": report.errors,
        "validation": [summary.to_dict() for summary in report.site_validation],
        "sites": [
            {
                "site": site_result.site,
                "ok": site_result.ok,
                "fetched_urls": site_result.fetched_urls,
                "errors": site_result.errors,
                "observations": [obs.to_dict() for obs in site_result.observations],
            }
            for site_result in report.site_results
        ],
    }
    path = snapshot_dir / f"run-{report.run_id:06d}-{report.mode}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
