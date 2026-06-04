import unittest

from hk_rental_tracker.config import create_task_config
from hk_rental_tracker.models import ListingObservation
from hk_rental_tracker.normalization import canonical_layout, layout_bedroom_count, normalize_text, text_variants


HK_MARKET_AREA_PAIRS = [
    ("中西区", "中西區"),
    ("上环", "上環"),
    ("中环", "中環"),
    ("金钟", "金鐘"),
    ("西营盘", "西營盤"),
    ("坚尼地城", "堅尼地城"),
    ("湾仔", "灣仔"),
    ("铜锣湾", "銅鑼灣"),
    ("跑马地", "跑馬地"),
    ("天后", "天后"),
    ("炮台山", "炮台山"),
    ("北角", "北角"),
    ("鲗鱼涌", "鰂魚涌"),
    ("太古", "太古"),
    ("西湾河", "西灣河"),
    ("筲箕湾", "筲箕灣"),
    ("柴湾", "柴灣"),
    ("小西湾", "小西灣"),
    ("香港仔", "香港仔"),
    ("鸭脷洲", "鴨脷洲"),
    ("黄竹坑", "黃竹坑"),
    ("深湾", "深灣"),
    ("薄扶林", "薄扶林"),
    ("贝沙湾", "貝沙灣"),
    ("数码港", "數碼港"),
    ("赤柱", "赤柱"),
    ("浅水湾", "淺水灣"),
    ("大潭", "大潭"),
    ("石澳", "石澳"),
    ("寿臣山", "壽臣山"),
    ("山顶", "山頂"),
    ("中半山", "中半山"),
    ("西半山", "西半山"),
    ("东半山", "東半山"),
    ("渣甸山", "渣甸山"),
    ("大坑", "大坑"),
    ("笔架山", "筆架山"),
    ("麦当劳道", "麥當勞道"),
    ("宝云道", "寶雲道"),
    ("尖沙咀", "尖沙咀"),
    ("佐敦", "佐敦"),
    ("油麻地", "油麻地"),
    ("旺角", "旺角"),
    ("奥运", "奧運"),
    ("大角咀", "大角咀"),
    ("太子", "太子"),
    ("深水埗", "深水埗"),
    ("长沙湾", "長沙灣"),
    ("荔枝角", "荔枝角"),
    ("美孚", "美孚"),
    ("南昌", "南昌"),
    ("石硖尾", "石硤尾"),
    ("九龙站", "九龍站"),
    ("西九龙", "西九龍"),
    ("柯士甸", "柯士甸"),
    ("九龙塘", "九龍塘"),
    ("又一村", "又一村"),
    ("何文田", "何文田"),
    ("土瓜湾", "土瓜灣"),
    ("马头围", "馬頭圍"),
    ("马头角", "馬頭角"),
    ("红磡", "紅磡"),
    ("黄埔", "黃埔"),
    ("启德", "啟德"),
    ("九龙城", "九龍城"),
    ("乐富", "樂富"),
    ("横头磡", "橫頭磡"),
    ("钻石山", "鑽石山"),
    ("新蒲岗", "新蒲崗"),
    ("慈云山", "慈雲山"),
    ("牛池湾", "牛池灣"),
    ("观塘", "觀塘"),
    ("牛头角", "牛頭角"),
    ("九龙湾", "九龍灣"),
    ("彩虹", "彩虹"),
    ("蓝田", "藍田"),
    ("油塘", "油塘"),
    ("秀茂坪", "秀茂坪"),
    ("鲤鱼门", "鯉魚門"),
    ("荃湾", "荃灣"),
    ("深井", "深井"),
    ("汀九", "汀九"),
    ("葵涌", "葵涌"),
    ("葵芳", "葵芳"),
    ("青衣", "青衣"),
    ("沙田", "沙田"),
    ("大围", "大圍"),
    ("显径", "顯徑"),
    ("石门", "石門"),
    ("火炭", "火炭"),
    ("马鞍山", "馬鞍山"),
    ("乌溪沙", "烏溪沙"),
    ("西贡", "西貢"),
    ("清水湾", "清水灣"),
    ("蚝涌", "蠔涌"),
    ("将军澳", "將軍澳"),
    ("坑口", "坑口"),
    ("宝琳", "寶琳"),
    ("调景岭", "調景嶺"),
    ("康城", "康城"),
    ("大埔", "大埔"),
    ("太和", "太和"),
    ("白石角", "白石角"),
    ("科学园", "科學園"),
    ("粉岭", "粉嶺"),
    ("上水", "上水"),
    ("古洞", "古洞"),
    ("鹤薮", "鶴藪"),
    ("元朗", "元朗"),
    ("天水围", "天水圍"),
    ("锦田", "錦田"),
    ("凹头", "凹頭"),
    ("洪水桥", "洪水橋"),
    ("屏山", "屏山"),
    ("八乡", "八鄉"),
    ("新田", "新田"),
    ("牛潭尾", "牛潭尾"),
    ("落马洲", "落馬洲"),
    ("锦绣花园", "錦繡花園"),
    ("加州花园", "加州花園"),
    ("鹿茵山庄", "鹿茵山莊"),
    ("屯门", "屯門"),
    ("扫管笏", "掃管笏"),
    ("屯门码头", "屯門碼頭"),
    ("蓝地", "藍地"),
    ("小榄", "小欖"),
    ("龙鼓滩", "龍鼓灘"),
    ("东涌", "東涌"),
    ("愉景湾", "愉景灣"),
    ("珀丽湾", "珀麗灣"),
    ("梅窝", "梅窩"),
    ("长洲", "長洲"),
    ("坪洲", "坪洲"),
    ("南丫岛", "南丫島"),
    ("大屿山", "大嶼山"),
    ("赤鱲角", "赤鱲角"),
    ("大澳", "大澳"),
]


class NormalizationTests(unittest.TestCase):
    def test_layout_terms_normalize_chinese_numbers_and_english(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=30000,
            min_rent=None,
            layouts="一房,两房,兩房,二房,三房,四房,2 bedroom,two-bedroom,开放式,studio",
            estates=None,
            sites="hkp",
        )

        self.assertEqual(config.filters.layouts, ["1房", "2房", "3房", "4房", "开放式"])
        self.assertEqual(canonical_layout("一房"), "1房")
        self.assertEqual(canonical_layout("兩房一廳"), "2房")
        self.assertEqual(canonical_layout("三房"), "3房")
        self.assertEqual(canonical_layout("四房"), "4房")
        self.assertEqual(canonical_layout("2 bedroom"), "2房")
        self.assertEqual(canonical_layout("two-bedroom"), "2房")
        self.assertEqual(canonical_layout("three bedrooms"), "3房")
        self.assertEqual(canonical_layout("两居室"), "2房")
        self.assertEqual(layout_bedroom_count("open-plan"), 0)

    def test_layout_filter_matches_chinese_two_bedroom_input(self) -> None:
        config = create_task_config(
            slug="demo-market",
            area="示例区",
            max_rent=30000,
            min_rent=None,
            layouts="两房",
            estates=None,
            sites="hkp",
        )
        obs = ListingObservation(
            source_site="hkp",
            source_url="https://example.com/H1",
            source_listing_id="H1",
            fetched_at="2026-06-03T00:00:00+08:00",
            title="示例区 示例苑 1座 中层 J室 兩房 实用 450呎 租 $22,000",
            district="示例区",
            rent_hkd=22000,
            usable_area_sqft=450,
        )

        self.assertTrue(config.filters.matches(obs, config.area_terms))

    def test_hk_market_area_traditional_and_simplified_match(self) -> None:
        for simplified, traditional in HK_MARKET_AREA_PAIRS:
            with self.subTest(area=simplified):
                self.assertEqual(normalize_text(simplified), normalize_text(traditional))

    def test_hk_market_area_variants_are_generated(self) -> None:
        for simplified, traditional in HK_MARKET_AREA_PAIRS:
            with self.subTest(area=simplified):
                variants = {normalize_text(value) for value in text_variants(simplified)}
                self.assertIn(normalize_text(traditional), variants)

    def test_area_filter_matches_hk_market_traditional_names(self) -> None:
        for simplified, traditional in HK_MARKET_AREA_PAIRS:
            with self.subTest(area=simplified):
                config = create_task_config(
                    slug="demo-market",
                    area=simplified,
                    max_rent=22000,
                    min_rent=None,
                    layouts="开放式",
                    estates=None,
                    sites="hkp",
                )
                obs = ListingObservation(
                    source_site="hkp",
                    source_url="https://example.com/H1",
                    source_listing_id="H1",
                    fetched_at="2026-06-03T00:00:00+08:00",
                    title=f"{traditional} 示例苑 1座 中层 J室 开放式 实用 204呎 租 $8,500",
                    district=traditional,
                    layout="开放式",
                    rent_hkd=8500,
                    usable_area_sqft=204,
                )
                self.assertTrue(config.filters.matches(obs, config.area_terms))


if __name__ == "__main__":
    unittest.main()
