from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from hk_rental_tracker.adapters import SiteScanResult
from hk_rental_tracker.config import create_task_config, save_task_config
from hk_rental_tracker.daily_report import _budget_stats, generate_daily_report, send_report
from hk_rental_tracker.models import ListingObservation
from hk_rental_tracker.scanner import scan_task
from hk_rental_tracker.storage import RentalStore


def make_obs(site: str, source_id: str, rent: int) -> ListingObservation:
    return ListingObservation(
        source_site=site,
        source_url=f"https://example.com/{source_id}",
        source_listing_id=source_id,
        fetched_at="2026-05-18T00:00:00+08:00",
        title="示例区 示例苑 2B座 高层 D室 1房 实用 306呎 租 $18,000",
        estate_name="示例苑",
        block="2B座",
        floor="高层",
        flat="D室",
        layout="1房",
        district="示例区",
        rent_hkd=rent,
        usable_area_sqft=306,
        gross_area_sqft=420,
        building_age_years=20,
    )


def make_obs_with_block(site: str, source_id: str, rent: int, block: str, flat: str = "D室") -> ListingObservation:
    obs = make_obs(site, source_id, rent)
    obs.block = block
    obs.flat = flat
    obs.title = f"示例区 示例苑 {block} 高层 {flat} 1房 实用 306呎 租 ${rent:,}"
    return obs


def make_obs_without_flat(site: str, source_id: str, rent: int, area: int = 306) -> ListingObservation:
    return ListingObservation(
        source_site=site,
        source_url=f"https://example.com/{source_id}",
        source_listing_id=source_id,
        fetched_at="2026-05-18T00:00:00+08:00",
        title=f"示例区 示例苑 2B座 1房 实用 {area}呎 租 ${rent:,}",
        estate_name="示例苑",
        block="2B座",
        layout="1房",
        district="示例区",
        rent_hkd=rent,
        usable_area_sqft=area,
        gross_area_sqft=420,
        building_age_years=20,
    )


class StorageTests(unittest.TestCase):
    def test_observation_drops_personal_agent_fields_and_raw_payload(self) -> None:
        obs = ListingObservation.from_dict(
            {
                "source_site": "demo",
                "source_url": "https://example.com/listing",
                "source_listing_id": "D1",
                "fetched_at": "2026-05-18T00:00:00+08:00",
                "title": "示例区 示例苑 1房 实用 306呎 租 $18,000",
                "estate_name": "示例苑",
                "rent_hkd": 18000,
                "usable_area_sqft": 306,
                "agent_name": "示例经纪",
                "raw": {"agentName": "示例经纪", "phone": "12345678"},
            }
        )
        payload = obs.to_dict()
        self.assertNotIn("agent_name", payload)
        self.assertEqual(payload["raw"], {})

    def test_area_filter_matches_traditional_variant(self) -> None:
        obs = make_obs("hkp", "H1", 8500)
        obs.title = "屯門 示例苑 1座 中层 J室 开放式 实用 204呎 租 $8,500"
        obs.district = "屯門"
        obs.layout = "开放式"
        filters = create_task_config(
            slug="demo-market",
            area="屯门",
            max_rent=22000,
            min_rent=None,
            layouts="开放式",
            estates=None,
            sites="hkp",
        ).filters
        self.assertTrue(filters.matches(obs, ["屯门", "屯門"]))

    def test_usable_area_filter_bounds(self) -> None:
        filters = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=22000,
            min_rent=None,
            min_area_sqft=300,
            max_area_sqft=500,
            layouts="1房",
            estates=None,
            sites="hkp",
        ).filters
        self.assertTrue(filters.matches(make_obs("hkp", "H1", 18000), ["示例区"]))

        small = make_obs("hkp", "H2", 18000)
        small.usable_area_sqft = 299
        self.assertFalse(filters.matches(small, ["示例区"]))

        large = make_obs("hkp", "H3", 18000)
        large.usable_area_sqft = 501
        self.assertFalse(filters.matches(large, ["示例区"]))

        missing = make_obs("hkp", "H4", 18000)
        missing.usable_area_sqft = None
        self.assertFalse(filters.matches(missing, ["示例区"]))

    def test_price_per_sqft_filter_bounds(self) -> None:
        filters = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=22000,
            min_rent=None,
            min_price_per_sqft=55,
            max_price_per_sqft=60,
            layouts="1房",
            estates=None,
            sites="hkp",
        ).filters
        self.assertTrue(filters.matches(make_obs("hkp", "H1", 18000), ["示例区"]))

        cheap = make_obs("hkp", "H2", 16000)
        self.assertFalse(filters.matches(cheap, ["示例区"]))

        expensive = make_obs("hkp", "H3", 19000)
        self.assertFalse(filters.matches(expensive, ["示例区"]))

        missing = make_obs("hkp", "H4", 18000)
        missing.price_per_sqft = None
        self.assertFalse(filters.matches(missing, ["示例区"]))

    def test_gross_area_filter_bounds(self) -> None:
        filters = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=22000,
            min_rent=None,
            min_gross_area_sqft=400,
            max_gross_area_sqft=500,
            layouts="1房",
            estates=None,
            sites="hkp",
        ).filters
        self.assertTrue(filters.matches(make_obs("hkp", "H1", 18000), ["示例区"]))

        small = make_obs("hkp", "H2", 18000)
        small.gross_area_sqft = 399
        self.assertFalse(filters.matches(small, ["示例区"]))

        large = make_obs("hkp", "H3", 18000)
        large.gross_area_sqft = 501
        self.assertFalse(filters.matches(large, ["示例区"]))

        missing = make_obs("hkp", "H4", 18000)
        missing.gross_area_sqft = None
        self.assertFalse(filters.matches(missing, ["示例区"]))

    def test_building_age_filter_bounds(self) -> None:
        filters = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=22000,
            min_rent=None,
            min_building_age_years=10,
            max_building_age_years=25,
            layouts="1房",
            estates=None,
            sites="hkp",
        ).filters
        self.assertTrue(filters.matches(make_obs("hkp", "H1", 18000), ["示例区"]))

        too_new = make_obs("hkp", "H2", 18000)
        too_new.building_age_years = 9
        self.assertFalse(filters.matches(too_new, ["示例区"]))

        too_old = make_obs("hkp", "H3", 18000)
        too_old.building_age_years = 26
        self.assertFalse(filters.matches(too_old, ["示例区"]))

        missing = make_obs("hkp", "H4", 18000)
        missing.building_age_years = None
        self.assertFalse(filters.matches(missing, ["示例区"]))

    def test_required_keywords_filter(self) -> None:
        filters = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=22000,
            min_rent=None,
            layouts="1房",
            estates=None,
            keywords="高层,D室",
            sites="hkp",
        ).filters
        self.assertTrue(filters.matches(make_obs("hkp", "H1", 18000), ["示例区"]))

        obs = make_obs("hkp", "H2", 18000)
        obs.flat = "E室"
        obs.title = "示例区 示例苑 2B座 高层 E室 1房 实用 306呎 租 $18,000"
        self.assertFalse(filters.matches(obs, ["示例区"]))

    def test_cross_source_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RentalStore(Path(tmp) / "rental.db")
            run_id = store.start_run("initial", "demo-market", {})
            first = store.upsert_observation(make_obs("midland", "M1", 18000), run_id)
            second = store.upsert_observation(make_obs("hkp", "H1", 18100), run_id)
            self.assertEqual(first, second)
            rows = store.all_listings()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sources_count"], 2)
            store.close()

    def test_missing_unit_detail_can_dedupe_when_available_fields_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RentalStore(Path(tmp) / "rental.db")
            run_id = store.start_run("initial", "demo-market", {})
            first = store.upsert_observation(make_obs_without_flat("centanet", "C1", 18000), run_id)
            second = store.upsert_observation(make_obs_without_flat("centanet", "C2", 18100), run_id)
            self.assertEqual(first, second)
            rows = store.all_listings()
            self.assertEqual(len(rows), 1)
            store.close()

    def test_same_flat_in_different_blocks_does_not_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RentalStore(Path(tmp) / "rental.db")
            run_id = store.start_run("initial", "demo-market", {})
            first = store.upsert_observation(make_obs_with_block("midland", "M1", 18000, "1A座"), run_id)
            second = store.upsert_observation(make_obs_with_block("hkp", "H1", 18100, "1B座"), run_id)
            self.assertNotEqual(first, second)
            rows = store.all_listings()
            self.assertEqual(len(rows), 2)
            store.close()

    def test_rent_difference_over_500_does_not_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RentalStore(Path(tmp) / "rental.db")
            run_id = store.start_run("initial", "demo-market", {})
            first = store.upsert_observation(make_obs("midland", "M1", 18000), run_id)
            second = store.upsert_observation(make_obs("hkp", "H1", 18501), run_id)
            self.assertNotEqual(first, second)
            rows = store.all_listings()
            self.assertEqual(len(rows), 2)
            store.close()

    def test_area_difference_does_not_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RentalStore(Path(tmp) / "rental.db")
            run_id = store.start_run("initial", "demo-market", {})
            first_obs = make_obs("midland", "M1", 18000)
            second_obs = make_obs("hkp", "H1", 18100)
            second_obs.usable_area_sqft = 307
            second = store.upsert_observation(second_obs, run_id)
            first = store.upsert_observation(first_obs, run_id)
            self.assertNotEqual(first, second)
            rows = store.all_listings()
            self.assertEqual(len(rows), 2)
            store.close()

    def test_mark_missing_sets_first_delisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RentalStore(Path(tmp) / "rental.db")
            run_id = store.start_run("initial", "demo-market", {})
            store.upsert_observation(make_obs("midland", "M1", 18000), run_id)
            run_id_2 = store.start_run("daily", "demo-market", {})
            changed = store.mark_missing_sources("midland", set(), run_id_2, enabled=True)
            self.assertEqual(changed, 1)
            row = store.all_listings()[0]
            self.assertIsNotNone(row["first_delisted_at"])
            self.assertEqual(row["active"], 0)
            store.close()

    def test_rent_decrease_history_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RentalStore(Path(tmp) / "rental.db")
            try:
                run_id_1 = store.start_run("initial", "demo-market", {})
                store.upsert_observation(make_obs("midland", "M1", 18000), run_id_1)
                run_id_2 = store.start_run("daily", "demo-market", {})
                store.upsert_observation(make_obs("midland", "M1", 17500), run_id_2)
                row = store.all_listings()[0]
                self.assertEqual(row["ever_rent_decreased"], 1)
                self.assertEqual(row["max_seen_rent_hkd"], 18000)
                self.assertEqual(row["min_seen_rent_hkd"], 17500)
                self.assertIsNotNone(row["first_rent_decrease_at"])
            finally:
                store.close()

    def test_scan_task_from_data_url(self) -> None:
        html = """
        <a href="/findproperty/zh-cn/detail/foo_ABC123?theme=rent">
          中原地产 bookmark 示例居 2期 示例居 II 1座 高层 G室 1房 示例区
          实用 实 380 呎 380呎 @ $61 /呎 租 $ 19,500
        </a>
        """
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "demo-market"
            config = create_task_config(
                slug="demo-market",
                area="示例区",
                max_rent=20000,
                min_rent=None,
                layouts="1房,开放式",
                estates=None,
                sites="centanet",
            )
            config.source_search_urls = {"centanet": ["data:text/html;charset=utf-8," + quote(html)]}
            config.scan_options["request_delay_seconds"] = 0
            save_task_config(task_dir, config)
            report = scan_task(task_dir, mode="initial")
            self.assertTrue(report.ok)
            self.assertEqual(report.inserted_observations, 1)
            self.assertEqual(report.site_validation[0].raw_observations, 1)
            self.assertEqual(report.site_validation[0].filtered_observations, 1)
            self.assertEqual(report.site_validation[0].rejected_observations, 0)
            self.assertTrue((task_dir / "exports" / "summary.md").exists())
            self.assertTrue((task_dir / "snapshots" / "run-000001-initial.json").exists())
            store = RentalStore(task_dir / "rental.db")
            rows = store.active_listings()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["last_rent_hkd"], 19500)
            store.close()

    def test_scan_task_reports_progress(self) -> None:
        html = """
        <a href="/findproperty/zh-cn/detail/foo_ABC123?theme=rent">
          中原地产 bookmark 示例居 2期 示例居 II 1座 高层 G室 1房 示例区
          实用 实 380 呎 380呎 @ $61 /呎 租 $ 19,500
        </a>
        """
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "demo-market"
            config = create_task_config(
                slug="demo-market",
                area="示例区",
                max_rent=20000,
                min_rent=None,
                layouts="1房",
                estates=None,
                sites="centanet",
            )
            config.source_search_urls = {"centanet": ["data:text/html;charset=utf-8," + quote(html)]}
            config.scan_options["request_delay_seconds"] = 0
            save_task_config(task_dir, config)
            messages: list[str] = []

            report = scan_task(task_dir, mode="initial", progress=messages.append)

            self.assertTrue(report.ok)
            self.assertTrue(any("scan started" in message for message in messages))
            self.assertTrue(any("site 1/1 adapter scan start: centanet" in message for message in messages))
            self.assertTrue(any("fetch start:" in message for message in messages))
            self.assertTrue(any("local filter done" in message for message in messages))
            self.assertTrue(any("scan finished" in message for message in messages))

    def test_scan_task_excludes_estate_blacklist(self) -> None:
        html = """
        <a href="/findproperty/zh-cn/detail/runway_ABC123?theme=rent">
          中原地产 bookmark 排除屋苑 1期 2座 高层 E室 开放式 示例区
          实用 实 250 呎 250呎 @ $51 /呎 租 $ 12,800
        </a>
        <a href="/findproperty/zh-cn/detail/centre_DEF456?theme=rent">
          中原地产 bookmark 保留屋苑 1座 高层 G室 开放式 示例区
          实用 实 250 呎 250呎 @ $60 /呎 租 $ 15,000
        </a>
        """
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "demo-market"
            config = create_task_config(
                slug="demo-market",
                area="示例区",
                max_rent=20000,
                min_rent=None,
                layouts="开放式",
                estates=None,
                sites="centanet",
                excluded_estates="排除屋苑",
            )
            config.source_search_urls = {"centanet": ["data:text/html;charset=utf-8," + quote(html)]}
            config.scan_options["request_delay_seconds"] = 0
            save_task_config(task_dir, config)
            report = scan_task(task_dir, mode="initial")
            self.assertTrue(report.ok)
            self.assertEqual(report.inserted_observations, 1)
            self.assertEqual(report.site_validation[0].raw_observations, 2)
            self.assertEqual(report.site_validation[0].filtered_observations, 1)
            self.assertEqual(report.site_validation[0].rejected_observations, 1)
            snapshot = json.loads((task_dir / "snapshots" / "run-000001-initial.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["validation"][0]["raw_observations"], 2)
            self.assertEqual(snapshot["validation"][0]["filtered_observations"], 1)
            self.assertEqual(snapshot["validation"][0]["rejected_observations"], 1)
            store = RentalStore(task_dir / "rental.db")
            rows = store.active_listings()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["estate_name"], "保留屋苑")
            store.close()

    def test_partial_site_errors_do_not_mark_missing_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "demo-market"
            config = create_task_config(
                slug="demo-market",
                area="示例区",
                max_rent=30000,
                min_rent=None,
                layouts=None,
                estates=None,
                sites="centanet",
            )
            config.scan_options["request_delay_seconds"] = 0
            save_task_config(task_dir, config)

            store = RentalStore(task_dir / "rental.db")
            run_id = store.start_run("initial", "demo-market", {})
            old_obs = make_obs("centanet", "OLD123", 18000)
            store.upsert_observation(old_obs, run_id)
            store.finish_run(run_id, "ok")
            store.close()

            partial_result = SiteScanResult(
                site="centanet",
                ok=True,
                observations=[make_obs("centanet", "NEW456", 19000)],
                errors=["https://example.com/page/2: The read operation timed out"],
            )
            with patch("hk_rental_tracker.scanner.GenericSiteAdapter.scan", return_value=partial_result):
                report = scan_task(task_dir, mode="daily")

            self.assertFalse(report.ok)
            self.assertEqual(report.inserted_observations, 1)
            self.assertEqual(report.missing_sources_marked, 0)
            self.assertFalse(report.site_validation[0].missing_marking_enabled)
            store = RentalStore(task_dir / "rental.db")
            rows = store.conn.execute(
                "SELECT source_listing_id, active, first_missing_at FROM source_state ORDER BY source_listing_id"
            ).fetchall()
            self.assertEqual([(row["source_listing_id"], row["active"], row["first_missing_at"]) for row in rows], [
                ("NEW456", 1, None),
                ("OLD123", 1, None),
            ])
            store.close()

    def test_daily_report_writes_markdown_and_detail_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "tasks" / "demo-market"
            config = create_task_config(
                slug="demo-market",
                area="示例区",
                max_rent=20000,
                min_rent=None,
                layouts="1房,开放式",
                estates=None,
                sites="midland",
            )
            save_task_config(task_dir, config)
            store = RentalStore(task_dir / "rental.db")
            try:
                run_id_1 = store.start_run("daily", "demo-market", {})
                listing_id = store.upsert_observation(make_obs("midland", "M1", 18000), run_id_1)
                store.conn.execute(
                    "UPDATE runs SET started_at = ?, ended_at = ?, status = ? WHERE id = ?",
                    ("2026-05-18T18:00:00+08:00", "2026-05-18T18:01:00+08:00", "ok", run_id_1),
                )
                store.conn.execute(
                    "UPDATE listings SET first_seen_at = ?, last_seen_at = ? WHERE id = ?",
                    ("2026-05-18T18:00:00+08:00", "2026-05-18T18:00:00+08:00", listing_id),
                )
                store.conn.execute(
                    "UPDATE source_state SET first_seen_at = ?, last_seen_at = ? WHERE listing_id = ?",
                    ("2026-05-18T18:00:00+08:00", "2026-05-18T18:00:00+08:00", listing_id),
                )

                run_id_2 = store.start_run("daily", "demo-market", {})
                store.upsert_observation(make_obs("midland", "M1", 17900), run_id_2)
                store.upsert_observation(make_obs_with_block("midland", "M2", 16500, "1A座", "E室"), run_id_2)
                store.conn.execute(
                    "UPDATE runs SET started_at = ?, ended_at = ?, status = ? WHERE id = ?",
                    ("2026-05-19T18:00:00+08:00", "2026-05-19T18:01:00+08:00", "ok", run_id_2),
                )
                store.conn.execute(
                    "UPDATE listings SET first_seen_at = ? WHERE canonical_id != (SELECT canonical_id FROM listings WHERE id = ?)",
                    ("2026-05-19T18:00:00+08:00", listing_id),
                )
                store.conn.commit()
            finally:
                store.close()

            result = generate_daily_report(task_dir, report_date="2026-05-19")
            text = result.markdown_path.read_text(encoding="utf-8")
            self.assertIn("示例区租盘日终报告", text)
            self.assertIn("户型价格", text)
            self.assertIn("今日租盘降价", text)
            self.assertIn("| 降幅 | 降幅比例 | 当前租金 | 昨日租金 | 实用面积(呎) | 本地盘龄 |", text)
            self.assertIn("本地盘龄", text)
            self.assertTrue(result.csv_paths["new_listings"].exists())
            self.assertTrue(result.csv_paths["rent_changes"].exists())
            self.assertTrue(result.csv_paths["rent_decreases"].exists())

    def test_budget_stats_uses_database_distribution_bands(self) -> None:
        current_rows = [{"rent_hkd": 33000, "layout": "2房", "price_per_sqft": 55.0}]
        reference_rows = [
            {"rent_hkd": 33000, "layout": "2房", "price_per_sqft": 55.0},
            {"rent_hkd": 62000, "layout": "3房", "price_per_sqft": 70.0},
            {"rent_hkd": 88000, "layout": "3房", "price_per_sqft": 85.0},
            {"rent_hkd": 146000, "layout": "4房", "price_per_sqft": 95.0},
            {"rent_hkd": 238000, "layout": "4房", "price_per_sqft": 120.0},
        ]

        rows = _budget_stats(current_rows, [], [], [], reference_rows)

        self.assertGreater(len(rows), 1)
        self.assertNotEqual([row["rent_band"] for row in rows], ["<=15k", "15k-16k", "16k-18k", "18k-20k", ">20k"])
        self.assertEqual(sum(row["active_count"] for row in rows), 1)
        self.assertTrue(all("rent_lower" in row and "rent_upper" in row for row in rows))

    def test_send_report_posts_webhook_json(self) -> None:
        class FakeResponse:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b""

        with tempfile.TemporaryDirectory() as tmp:
            markdown_path = Path(tmp) / "daily_report_2026-05-19.md"
            markdown_path.write_text("# 示例区租盘日终报告\n", encoding="utf-8")
            requests = []

            def fake_urlopen(request, timeout=0):
                requests.append((request, timeout))
                return FakeResponse()

            env = {
                "HK_RENTAL_TRACKER_WEBHOOK_URL": "https://example.com/hook",
                "HK_RENTAL_TRACKER_WEBHOOK_FORMAT": "json",
                "HK_RENTAL_TRACKER_WEBHOOK_TOKEN": "secret-token",
            }
            with patch.dict(os.environ, env, clear=False), patch("hk_rental_tracker.daily_report.urllib.request.urlopen", fake_urlopen):
                sent = send_report(markdown_path, ["webhook"])

            self.assertEqual(sent, ["webhook"])
            self.assertEqual(len(requests), 1)
            request, timeout = requests[0]
            self.assertEqual(timeout, 30)
            self.assertEqual(request.full_url, "https://example.com/hook")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["title"], "daily report 2026-05-19")
            self.assertEqual(payload["filename"], markdown_path.name)
            self.assertIn("示例区租盘日终报告", payload["text"])


if __name__ == "__main__":
    unittest.main()
