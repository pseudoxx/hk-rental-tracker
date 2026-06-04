import json
import unittest

from hk_rental_tracker.adapters.base import (
    GenericSiteAdapter,
    _centanet_filter_payload,
    _common_property_params,
    _observation_from_ricacorp_item,
    _ricacorp_page_url,
    _ricacorp_state_from_html,
)
from hk_rental_tracker.config import create_task_config
from hk_rental_tracker.fetcher import FetchResult
from hk_rental_tracker.source_capabilities import can_pushdown, filter_trust


class AdapterPayloadTests(unittest.TestCase):
    def test_midland_like_params_separate_net_and_gross_area(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=30000,
            min_rent=12000,
            min_area_sqft=400,
            max_area_sqft=700,
            min_gross_area_sqft=500,
            max_gross_area_sqft=800,
            min_price_per_sqft=35,
            max_price_per_sqft=45,
            layouts="2房",
            estates=None,
            sites="midland,hkp",
        )

        params = _common_property_params(config, lang="zh-hk")

        self.assertEqual(params["price_from"], "12000")
        self.assertEqual(params["price_to"], "30000")
        self.assertEqual(params["net_area_from"], "400")
        self.assertEqual(params["net_area_to"], "700")
        self.assertEqual(params["area_from"], "500")
        self.assertEqual(params["area_to"], "800")
        self.assertEqual(params["net_ft_price_from"], "35")
        self.assertEqual(params["net_ft_price_to"], "45")
        self.assertEqual(params["bedroom"], "2")

    def test_layout_chinese_number_pushes_bedroom_filter(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=30000,
            min_rent=None,
            layouts="一房,两房,三房,四房",
            estates=None,
            sites="midland,centanet",
        )

        midland_params = _common_property_params(config, lang="zh-hk")
        centanet_payload = _centanet_filter_payload(config)

        self.assertEqual(config.filters.layouts, ["1房", "2房", "3房", "4房"])
        self.assertEqual(midland_params["bedroom"], "1,2,3,4")
        self.assertEqual(centanet_payload["bedroomCount"], [1, 2, 3, 4])

    def test_ricacorp_state_parser_reads_server_rendered_posts(self) -> None:
        html = """
        <script id="serverApp-state" type="application/json">
        {&q;POSTS&q;:[{&q;postNo&q;:&q;CO55783214&q;,&q;aliasV4&q;:&q;康城-hma-日出康城-co55783214-5-hk&q;,&q;displayTextHk&q;:&q;日出康城&q;,&q;marketPrice&q;:29500,&q;saleableArea&q;:984,&q;grossArea&q;:1302,&q;room&q;:4,&q;publicLocationNamesHk&q;:[&q;利嘉閣&q;,&q;住宅&q;,&q;九龍&q;,&q;將軍澳&q;,&q;日出康城&q;]}],&q;POSTSNUMPAGES&q;:36,&q;POSTSTOTAL&q;:358}
        </script>
        """

        state = _ricacorp_state_from_html(html)
        observation = _observation_from_ricacorp_item(state["POSTS"][0], "2026-06-04T00:00:00+00:00")

        self.assertEqual(state["POSTSTOTAL"], 358)
        self.assertEqual(state["POSTSNUMPAGES"], 36)
        self.assertIsNotNone(observation)
        self.assertEqual(observation.source_listing_id, "CO55783214")
        self.assertEqual(observation.rent_hkd, 29500)
        self.assertEqual(observation.usable_area_sqft, 984)
        self.assertEqual(observation.layout, "4房")

    def test_ricacorp_page_url_uses_public_list_pagination_route(self) -> None:
        self.assertEqual(
            _ricacorp_page_url("https://www.ricacorp.com/zh-hk/property/list/rent/康城-hma-hk", 2),
            "https://www.ricacorp.com/zh-hk/property/list/rent/康城-hma-hk;page=2",
        )
        self.assertEqual(
            _ricacorp_page_url("https://www.ricacorp.com/zh-hk/property/list/rent/康城-hma-hk;page=2", 3),
            "https://www.ricacorp.com/zh-hk/property/list/rent/康城-hma-hk;page=3",
        )

    def test_ricacorp_scan_continues_after_later_page_timeout(self) -> None:
        base_url = "https://www.ricacorp.com/zh-hk/property/list/rent/康城-hma-hk"

        class FakeFetcher:
            timeout = 0

            def __init__(self) -> None:
                self.delay_seconds = 0

            def fetch(self, url: str) -> FetchResult:
                if url.endswith(";page=2"):
                    return FetchResult(url=url, ok=False, error="The read operation timed out")
                post_no = "PAGE3" if url.endswith(";page=3") else "PAGE1"
                return FetchResult(
                    url=url,
                    ok=True,
                    html=_ricacorp_state_html(post_no, total_pages=3),
                    status_code=200,
                )

        config = create_task_config(
            slug="demo-market",
            area="康城",
            max_rent=None,
            min_rent=None,
            layouts=None,
            estates=None,
            sites="ricacorp",
        )
        config.source_search_urls["ricacorp"] = [base_url]

        result = GenericSiteAdapter("ricacorp").scan(config, FakeFetcher())

        self.assertTrue(result.ok)
        self.assertEqual([obs.source_listing_id for obs in result.observations], ["PAGE1", "PAGE3"])
        self.assertEqual(len(result.fetched_urls), 3)
        self.assertEqual(len(result.errors), 1)
        self.assertIn(";page=2", result.errors[0])

    def test_centanet_payload_uses_confirmed_filter_names(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=30000,
            min_rent=12000,
            min_area_sqft=400,
            max_area_sqft=700,
            min_building_age_years=5,
            max_building_age_years=25,
            min_price_per_sqft=35,
            max_price_per_sqft=45,
            layouts="2房",
            estates=None,
            sites="centanet",
        )

        payload = _centanet_filter_payload(config)

        self.assertEqual(payload["bedroomCount"], [2])
        self.assertEqual(payload["AmountRange"], {"min": 12000, "max": 30000})
        self.assertEqual(payload["nSizeRange"], {"min": 400, "max": 700})
        self.assertEqual(payload["buildingAgeRange"], {"min": 5, "max": 25})
        self.assertEqual(payload["nUnitPriceRange"], {"min": 35, "max": 45})
        self.assertNotIn("BuildingAgeRange", payload)
        self.assertNotIn("nUnitRentRange", payload)

    def test_filter_trust_records_control_local_only_pushdown(self) -> None:
        self.assertTrue(can_pushdown("centanet", "price_per_sqft"))
        self.assertFalse(can_pushdown("centanet", "gross_area"))
        self.assertFalse(can_pushdown("ricacorp", "rent"))
        self.assertFalse(can_pushdown("ricacorp", "usable_area"))
        self.assertFalse(can_pushdown("ricacorp", "price_per_sqft"))

        centanet = filter_trust("centanet")
        self.assertIn("gross_area", centanet.local_only)
        self.assertIn("price_per_sqft", centanet.confirmed_pushdown)
        ricacorp = filter_trust("ricacorp")
        self.assertIn("rent", ricacorp.local_only)
        self.assertEqual(ricacorp.confirmed_pushdown, ())

    def test_midland_like_params_respect_site_trust_record(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=30000,
            min_rent=12000,
            min_area_sqft=400,
            max_area_sqft=700,
            min_gross_area_sqft=500,
            max_gross_area_sqft=800,
            min_building_age_years=5,
            max_building_age_years=25,
            min_price_per_sqft=35,
            max_price_per_sqft=45,
            layouts="2房",
            estates=None,
            sites="midland",
        )

        params = _common_property_params(config, lang="zh-hk", site="midland")

        self.assertEqual(params["net_ft_price_from"], "35")
        self.assertEqual(params["net_ft_price_to"], "45")
        self.assertNotIn("building_age_from", params)
        self.assertNotIn("building_age_to", params)


def _ricacorp_state_html(post_no: str, total_pages: int = 1) -> str:
    state = {
        "POSTS": [
            {
                "postNo": post_no,
                "aliasV4": f"康城-hma-日出康城-{post_no.lower()}-5-hk",
                "displayTextHk": "日出康城",
                "marketPrice": 15000,
                "saleableArea": 300,
                "grossArea": 400,
                "room": 1,
                "publicLocationNamesHk": ["利嘉閣", "住宅", "九龍", "將軍澳", "日出康城"],
            }
        ],
        "POSTSNUMPAGES": total_pages,
        "POSTSTOTAL": total_pages,
    }
    return f'<script id="serverApp-state" type="application/json">{json.dumps(state)}</script>'


if __name__ == "__main__":
    unittest.main()
