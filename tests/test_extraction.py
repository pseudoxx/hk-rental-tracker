import unittest

from hk_rental_tracker.extractors import observations_from_html, parse_observation_from_text


class ExtractionTests(unittest.TestCase):
    def test_parse_centanet_card(self) -> None:
        text = (
            "中原地产 装修及讲房 独家 锁匙盘 露台 bookmark "
            "Example Residence 1期 Example Residence I 3座 1房 (1套房) 示例区 示例区 "
            "2年楼龄 露台 实用 实 325 呎 325呎 @ $52 /呎 租 $ 17,000"
        )
        obs = parse_observation_from_text("centanet", "https://hk.centanet.com/findproperty/detail/X_ABC123", text, ["示例区"])
        self.assertIsNotNone(obs)
        assert obs is not None
        self.assertEqual(obs.rent_hkd, 17000)
        self.assertEqual(obs.usable_area_sqft, 325)
        self.assertEqual(obs.layout, "1房")
        self.assertEqual(obs.district, "示例区")
        self.assertIn("Example Residence", obs.estate_name or "")

    def test_parse_chinese_two_bedroom_layout_as_canonical_layout(self) -> None:
        text = "中原地产 bookmark 示例苑 1座 中层 J室 两房 示例区 实用 实 450 呎 租 $ 22,000"

        obs = parse_observation_from_text("centanet", "https://hk.centanet.com/findproperty/detail/X_ABC123", text, ["示例区"])

        self.assertIsNotNone(obs)
        assert obs is not None
        self.assertEqual(obs.layout, "2房")

    def test_parse_midland_detail_text(self) -> None:
        text = (
            "示例苑 1B座 低层 E室 location 示例区 示例道21号 租 $16,000 "
            "实用 330呎 $48/呎 同小区 1房 1房 (1套房) 户型 楼盘编号: M201316048"
        )
        obs = parse_observation_from_text("midland", "https://www.midland.com.hk/zh-cn/property/foo-M201316048", text, ["示例区"])
        self.assertIsNotNone(obs)
        assert obs is not None
        self.assertEqual(obs.source_listing_id, "M201316048")
        self.assertEqual(obs.rent_hkd, 16000)
        self.assertEqual(obs.usable_area_sqft, 330)
        self.assertEqual(obs.block, "1B座")
        self.assertEqual(obs.floor, "低层")
        self.assertEqual(obs.flat, "E室")

    def test_extract_anchor_from_html(self) -> None:
        html = """
        <a href="/findproperty/zh-cn/detail/foo_ABC123?theme=rent">
          中原地产 bookmark 示例居 2期 示例居 II 1座 高层 G室 1房 示例区
          实用 实 380 呎 380呎 @ $61 /呎 租 $ 23,500
        </a>
        """
        observations = observations_from_html("centanet", "https://hk.centanet.com/findproperty/zh-cn/list/rent", html, ["示例区"])
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].source_listing_id, "ABC123")
        self.assertEqual(observations[0].rent_hkd, 23500)


if __name__ == "__main__":
    unittest.main()
