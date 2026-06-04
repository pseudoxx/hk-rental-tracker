from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hk_rental_tracker.adapters import SiteScanResult
from hk_rental_tracker.config import create_task_config, save_task_config
from hk_rental_tracker.cli import _resolve_ricacorp_authorization
from hk_rental_tracker.models import ListingObservation
from hk_rental_tracker.scanner import scan_task
from hk_rental_tracker.storage import RentalStore
from hk_rental_tracker.web_verify import _collect_checks


class SourcePolicyTests(unittest.TestCase):
    def test_ricacorp_authorization_flag_is_backward_compatible_noop(self) -> None:
        sites, authorized = _resolve_ricacorp_authorization("ricacorp", already_authorized=False)

        self.assertEqual(sites, "ricacorp")
        self.assertFalse(authorized)

    def test_default_sources_include_ricacorp(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=None,
            min_rent=None,
            layouts="1房",
        )

        self.assertEqual(config.sites, ["midland", "centanet", "hkp", "ricacorp"])
        self.assertTrue(config.site_is_authorized("ricacorp"))

    def test_ricacorp_is_standard_source_without_authorization_gate(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=None,
            min_rent=None,
            layouts="1房",
            sites="ricacorp",
        )

        self.assertEqual(config.sites, ["ricacorp"])
        self.assertTrue(config.site_is_authorized("ricacorp"))

        authorized = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=None,
            min_rent=None,
            layouts="1房",
            sites="ricacorp",
            ricacorp_authorized=True,
        )
        self.assertTrue(authorized.site_is_authorized("ricacorp"))

    def test_scan_runs_ricacorp_without_authorization_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "demo-market"
            config = create_task_config(
                slug="demo-market",
                area="示例区",
                max_rent=None,
                min_rent=None,
                layouts="1房",
                sites="ricacorp",
            )
            config.scan_options["preflight_network"] = False
            save_task_config(task_dir, config)

            site_result = SiteScanResult(
                site="ricacorp",
                ok=True,
                observations=[
                    ListingObservation(
                        source_site="ricacorp",
                        source_url="https://www.ricacorp.com/zh-hk/property/detail/demo-5-hk",
                        source_listing_id="DEMO123",
                        fetched_at="2026-06-04T00:00:00+08:00",
                        title="示例区 示例苑 高层 A室 1房 实用 300 呎 租 $ 15000",
                        estate_name="示例苑",
                        floor="高层",
                        flat="A室",
                        layout="1房",
                        district="示例区",
                        rent_hkd=15000,
                        usable_area_sqft=300,
                    )
                ],
            )
            with patch("hk_rental_tracker.scanner.GenericSiteAdapter.scan", return_value=site_result):
                report = scan_task(task_dir, mode="initial")

            self.assertTrue(report.ok)
            self.assertEqual(len(report.site_results), 1)
            self.assertTrue(report.site_results[0].ok)
            self.assertEqual(report.inserted_observations, 1)

    def test_web_verify_collects_ricacorp_without_authorization_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "demo-market"
            config = create_task_config(
                slug="demo-market",
                area="示例区",
                max_rent=None,
                min_rent=None,
                layouts="1房",
                sites="ricacorp",
            )
            save_task_config(task_dir, config)
            RentalStore(task_dir / "rental.db").close()

            site_result = SiteScanResult(site="ricacorp", ok=True, observations=[])
            with patch("hk_rental_tracker.web_verify.GenericSiteAdapter.scan", return_value=site_result):
                checks = _collect_checks(task_dir, config)

            self.assertEqual(len(checks), 1)
            self.assertEqual(checks[0].evidence_type, "api_observations_after_filters")
            self.assertIsNone(checks[0].error)


if __name__ == "__main__":
    unittest.main()
