from __future__ import annotations

import re
import unicodedata
from hashlib import sha1
from typing import Iterable


_TRAD_TO_SIMPLE = str.maketrans(
    {
        "啟": "启",
        "區": "区",
        "樓": "楼",
        "層": "层",
        "實": "实",
        "廳": "厅",
        "開": "开",
        "盤": "盘",
        "價": "价",
        "呎": "呎",
        "號": "号",
        "廈": "厦",
        "龍": "龙",
        "譽": "誉",
        "灣": "湾",
        "維": "维",
        "港": "港",
        "壹": "一",
        "玺": "玺",
        "璽": "玺",
        "滙": "汇",
        "匯": "汇",
        "峯": "峰",
        "臺": "台",
        "台": "台",
        "麗": "丽",
        "園": "园",
        "單": "单",
        "間": "间",
        "營": "营",
        "堅": "坚",
        "環": "环",
        "鐘": "钟",
        "門": "门",
        "黃": "黄",
        "銅": "铜",
        "鑼": "锣",
        "鰂": "鲗",
        "魚": "鱼",
        "鴨": "鸭",
        "淺": "浅",
        "數": "数",
        "碼": "码",
        "紅": "红",
        "樂": "乐",
        "橫": "横",
        "鑽": "钻",
        "崗": "岗",
        "雲": "云",
        "觀": "观",
        "頭": "头",
        "鯉": "鲤",
        "將": "将",
        "軍": "军",
        "寶": "宝",
        "調": "调",
        "嶺": "岭",
        "圍": "围",
        "錦": "锦",
        "橋": "桥",
        "東": "东",
        "馬": "马",
        "藍": "蓝",
        "貢": "贡",
        "窩": "窝",
        "長": "长",
        "島": "岛",
        "嶼": "屿",
        "鷄": "鸡",
        "雞": "鸡",
        "掃": "扫",
        "鴉": "鸦",
        "烏": "乌",
        "瀝": "沥",
        "華": "华",
        "濱": "滨",
        "豐": "丰",
        "慶": "庆",
        "筆": "笔",
        "莊": "庄",
        "學": "学",
        "灘": "滩",
        "顯": "显",
        "徑": "径",
        "頂": "顶",
        "當": "当",
        "勞": "劳",
        "蠔": "蚝",
        "鄉": "乡",
        "藪": "薮",
        "駿": "骏",
        "欖": "榄",
        "蘭": "兰",
        "壩": "坝",
        "廟": "庙",
        "鶴": "鹤",
        "蓮": "莲",
        "窰": "窑",
        "舊": "旧",
        "硤": "硖",
        "繡": "绣",
        "櫃": "柜",
        "壽": "寿",
        "貝": "贝",
        "瑤": "瑶",
        "奧": "奥",
        "運": "运",
        "麥": "麦",
        "鳳": "凤",
        "荔": "荔",
        "葵": "葵",
        "罕": "罕",
    }
)

_SIMPLE_TO_TRAD_LIGHT = str.maketrans(
    {
        "启": "啟",
        "区": "區",
        "楼": "樓",
        "层": "層",
        "实": "實",
        "厅": "廳",
        "开": "開",
        "盘": "盤",
        "价": "價",
        "号": "號",
        "厦": "廈",
        "龙": "龍",
        "誉": "譽",
        "湾": "灣",
        "维": "維",
        "汇": "匯",
        "峰": "峯",
        "丽": "麗",
        "园": "園",
        "单": "單",
        "间": "間",
        "营": "營",
        "坚": "堅",
        "环": "環",
        "钟": "鐘",
        "门": "門",
        "黄": "黃",
        "铜": "銅",
        "锣": "鑼",
        "鲗": "鰂",
        "鱼": "魚",
        "鸭": "鴨",
        "浅": "淺",
        "数": "數",
        "码": "碼",
        "红": "紅",
        "乐": "樂",
        "横": "橫",
        "钻": "鑽",
        "岗": "崗",
        "云": "雲",
        "观": "觀",
        "头": "頭",
        "鲤": "鯉",
        "将": "將",
        "军": "軍",
        "宝": "寶",
        "调": "調",
        "岭": "嶺",
        "围": "圍",
        "锦": "錦",
        "桥": "橋",
        "东": "東",
        "马": "馬",
        "蓝": "藍",
        "贡": "貢",
        "窝": "窩",
        "长": "長",
        "岛": "島",
        "屿": "嶼",
        "鸡": "雞",
        "扫": "掃",
        "鸦": "鴉",
        "乌": "烏",
        "沥": "瀝",
        "华": "華",
        "滨": "濱",
        "丰": "豐",
        "庆": "慶",
        "笔": "筆",
        "庄": "莊",
        "学": "學",
        "滩": "灘",
        "显": "顯",
        "径": "徑",
        "顶": "頂",
        "当": "當",
        "劳": "勞",
        "蚝": "蠔",
        "乡": "鄉",
        "薮": "藪",
        "骏": "駿",
        "榄": "欖",
        "兰": "蘭",
        "坝": "壩",
        "庙": "廟",
        "鹤": "鶴",
        "莲": "蓮",
        "窑": "窰",
        "旧": "舊",
        "硖": "硤",
        "绣": "繡",
        "柜": "櫃",
        "寿": "壽",
        "贝": "貝",
        "瑶": "瑤",
        "奥": "奧",
        "运": "運",
        "麦": "麥",
        "凤": "鳳",
    }
)


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def stable_hash(value: str, length: int = 16) -> str:
    return sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(_TRAD_TO_SIMPLE)
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[·．.。,:：;；|｜/\\\-_\(\)（）\[\]【】#]+", "", text)
    return text


def normalize_loose(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).translate(_TRAD_TO_SIMPLE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "壹": 1,
    "二": 2,
    "贰": 2,
    "貳": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "叁": 3,
    "參": 3,
    "四": 4,
    "肆": 4,
    "五": 5,
    "伍": 5,
    "六": 6,
    "陆": 6,
    "陸": 6,
    "七": 7,
    "柒": 7,
    "八": 8,
    "捌": 8,
    "九": 9,
    "玖": 9,
}

_ENGLISH_DIGITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def parse_chinese_int(value: str | None) -> int | None:
    if not value:
        return None
    text = normalize_text(value)
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1:
        return _CHINESE_DIGITS.get(text)
    return None


def layout_bedroom_count(value: str | None) -> int | None:
    text = normalize_loose(value)
    if not text:
        return None
    lowered = text.lower()
    if "开放式" in lowered or "開放式" in lowered or "studio" in lowered or "open plan" in lowered or "open-plan" in lowered:
        return 0
    digit_match = re.search(r"(\d+)[\s-]*(?:房|室|居室|居|bedrooms?|beds?|br\b)", lowered)
    if digit_match:
        return int(digit_match.group(1))
    english_match = re.search(r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten)[\s-]*(?:bedrooms?|beds?|br)\b", lowered)
    if english_match:
        return _ENGLISH_DIGITS.get(english_match.group(1))
    chinese_match = re.search(r"([零〇一二两兩三四五六七八九十壹贰貳叁參肆伍陆陸柒捌玖]+)\s*(?:房|室|居室|居)", text)
    if chinese_match:
        return parse_chinese_int(chinese_match.group(1))
    return None


def canonical_layout(value: str | None) -> str:
    count = layout_bedroom_count(value)
    if count == 0:
        return "开放式"
    if count is not None:
        return f"{count}房"
    return normalize_loose(value)


def text_variants(value: str) -> list[str]:
    base = unicodedata.normalize("NFKC", value).strip()
    variants = {base, base.translate(_TRAD_TO_SIMPLE), base.translate(_SIMPLE_TO_TRAD_LIGHT)}
    if base.endswith("新区"):
        variants.add(base[:-2] + "新區")
    if base.endswith("新區"):
        variants.add(base[:-2] + "新区")
    return [v for v in variants if v]


def split_terms(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,，+＋/、\n]+", value)
    else:
        raw = []
        for item in value:
            raw.extend(re.split(r"[,，+＋/、\n]+", str(item)))
    return [x.strip() for x in raw if x and x.strip()]


def normalize_layout_terms(value: str | Iterable[str] | None) -> list[str]:
    terms = split_terms(value)
    normalized: list[str] = []
    seen = set()
    for term in terms:
        layout = canonical_layout(term)
        if layout and layout not in seen:
            seen.add(layout)
            normalized.append(layout)
    return normalized


def parse_int(value: str | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"([0-9][0-9,]*)", value.replace(" ", ""))
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def compact_money(value: int | None) -> str:
    if value is None:
        return "-"
    return f"${value:,.0f}"


def contains_any(text: str, terms: Iterable[str]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(term) in normalized for term in terms if term)


def ratio_close(a: int | float | None, b: int | float | None, tolerance: float) -> bool:
    if a in (None, 0) or b in (None, 0):
        return False
    return abs(float(a) - float(b)) / max(float(a), float(b)) <= tolerance
