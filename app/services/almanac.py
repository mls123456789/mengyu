"""黄历/干支引擎：用 lunar-python 算当日干支，推定幸运色/数字/方位/物品与宜忌。

设计要点：
- 当日干支对 12 星座相同 → 由当日五行 / 吉神方位定义"吉利候选池"（取值恒合规）；
- 星座仅进入 RNG 种子 → 在池中确定性挑选，使各星座取值不同但仍吉利；
- 种子 = sign + 代表日期 + 干支，无墙钟 → 跨进程/跨请求完全一致（契合全局缓存模型）。

注意：_COLOR_POOL 是「色名↔hex」唯一权威来源；derive_lucky 同时输出 color（色名，进文案）
与 color_hex（供前端圆点直接取色），前端无需再维护色名→颜色映射表。
"""
from __future__ import annotations

import datetime
import hashlib
import random

from lunar_python import Solar

# ---------- 确定性 RNG（与 astro._rng 同款：sha256 → Mersenne Twister）----------
def _rng(seed_bytes: bytes) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(seed_bytes).digest()[:8], "big"))

_ZHI = "子丑寅卯辰巳午未申酉戌亥"

# 日干 → 五行
_GAN_ELEMENT = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土", "己": "土",
                "庚": "金", "辛": "金", "壬": "水", "癸": "水"}

# 五行相生（母→子）；反查得"生我者"=大吉元素
_SHENG = {"水": "木", "木": "火", "火": "土", "土": "金", "金": "水"}
_SHENG_REV = {v: k for k, v in _SHENG.items()}  # 子→母

# 河图洛书数 → 五行
_LUOSHU = {1: "水", 6: "水", 2: "火", 7: "火", 3: "木", 8: "木", 4: "金", 9: "金", 5: "土"}

# 吉神方位（按日干，口诀编码；不依赖库的方位方法）
_CAISHEN = {"甲": "东北", "乙": "西南", "丙": "正西", "丁": "正西", "戊": "正北", "己": "正北",
            "庚": "正东", "辛": "正东", "壬": "正南", "癸": "正南"}
_XISHEN = {"甲": "东北", "己": "东北", "乙": "西北", "庚": "西北", "丙": "西南", "辛": "西南",
           "丁": "正南", "壬": "正南", "戊": "东南", "癸": "东南"}
_FUSHEN = {"甲": "东南", "乙": "东南", "丙": "正东", "丁": "正东", "戊": "东北", "己": "东北",
           "庚": "西南", "辛": "西南", "壬": "西北", "癸": "西北"}

# 建除十二神顺序 + 倾向（驱动宜/忌词库选择）
_ZHIXING_ORDER = "建除满平定执破危成收开闭"
_ZHIXING_TENDENCY = {
    "建": "吉", "满": "吉", "成": "吉", "开": "吉",
    "平": "平", "定": "平", "收": "平",
    "除": "忌", "危": "忌", "破": "忌", "闭": "忌",
}

# ---------- 现代化候选池（贴近生活；非 嫁娶/动土）----------
# 幸运色：按五行分组，每项 (色名, hex)。色名进文案/prompt，hex 供前端圆点；
# 这是「色名↔颜色」唯一权威来源（前端 lucky.color_hex 直接取用，无需另维护色表）。
_COLOR_POOL = {
    "木": [("薄荷绿", "#7bd8b6"), ("墨绿", "#2e6f5e"), ("青草绿", "#8fd14f")],
    "火": [("玫瑰粉", "#e86a92"), ("暖橘色", "#ff8a5b"), ("丁香紫", "#c4b5fd"), ("粉色", "#ff8fb1")],
    "土": [("卡其色", "#c9b68a"), ("棕色", "#a47148"), ("米色", "#e3d7b6"), ("黄色", "#f6d54a")],
    "金": [("银灰色", "#cdd2e0"), ("白色", "#eef0f7"), ("灰色", "#9aa0b4")],
    "水": [("海蓝色", "#2a6f97"), ("天蓝色", "#6ec6ff"), ("黑色", "#3a3a4a"), ("薰衣草", "#b9a4ff")],
}
# 幸运物品：按当日五行分组
_ITEM_POOL = {
    "木": ["绿植盆栽", "木制书签", "纸质手账", "檀木梳子"],
    "火": ["香氛蜡烛", "暖光小灯", "红绳手链", "红色烛台"],
    "土": ["陶瓷水杯", "黄水晶", "陶土花器", "水晶原石"],
    "金": ["银质戒指", "白水晶", "金属书签", "金属腕表"],
    "水": ["蓝砂石手串", "黑曜石", "玻璃水杯", "海盐浴球"],
}
# 宜/忌词库：按建除倾向分桶
_YI_POOL = {
    "吉": ["整理居室", "联系老友", "尝试新食谱", "复盘近期计划", "开启新事项", "果断表达心意", "约人吃饭", "给家人打电话"],
    "平": ["整理书桌", "复盘计划", "散步放空", "规律三餐", "列待办清单", "联系老友"],
    "忌": ["整理居室", "散步放空", "联系老友", "规律三餐"],  # 凶日只宜轻量稳妥
}
_JI_POOL = {
    "吉": ["勉强社交", "钻牛角尖", "熬夜"],
    "平": ["拖延重要事", "熬夜", "勉强社交"],
    "忌": ["冲动消费", "熬夜", "钻牛角尖", "勉强社交", "拖延重要事", "与人争执", "盲目跟风", "做大决定"],
}


def _zhixing_fallback(lunar) -> str:
    """getZhiXing 不可用时，由 (日支 − 月支) mod 12 推建除值神。"""
    di = _ZHI.index(lunar.getDayZhi())
    mi = _ZHI.index(lunar.getMonthZhi())
    return _ZHIXING_ORDER[(di - mi) % 12]


def almanac_data(date: datetime.date) -> dict:
    """与星座无关的当日黄历数据 + 各候选池。同一日期对所有星座相同。"""
    lunar = Solar.fromYmd(date.year, date.month, date.day).getLunar()
    gan, zhi = lunar.getDayGan(), lunar.getDayZhi()
    ganzhi = lunar.getDayInGanZhi()
    nayin = lunar.getDayNaYin()           # 如"覆灯火"，末字即五行
    day_el = _GAN_ELEMENT[gan]
    nayin_el = nayin[-1]
    try:
        zhixing = lunar.getZhiXing()
    except Exception:
        zhixing = _zhixing_fallback(lunar)
    tendency = _ZHIXING_TENDENCY.get(zhixing, "平")

    daji_el = _SHENG_REV[day_el]          # 生我者 = 大吉元素
    ausp_els = (daji_el, day_el)          # 大吉 + 次吉
    return {
        "date": date.isoformat(),
        "ganzhi": ganzhi,
        "gan": gan,
        "zhi": zhi,
        "nayin": nayin,
        "day_element": day_el,
        "nayin_element": nayin_el,
        "zhixing": zhixing,
        "tendency": tendency,
        "caishen": _CAISHEN[gan],
        "xishen": _XISHEN[gan],
        "fushen": _FUSHEN[gan],
        # 候选池（星座在此范围内挑选）
        "color_pool": _COLOR_POOL[daji_el] + _COLOR_POOL[day_el],
        "number_pool": [n for n, e in _LUOSHU.items() if e in ausp_els],
        "direction_pool": [_CAISHEN[gan], _XISHEN[gan], _FUSHEN[gan]],
        "item_pool": _ITEM_POOL[day_el],
        "yi_pool": _YI_POOL[tendency],
        "ji_pool": _JI_POOL[tendency],
    }


def derive_lucky(alm: dict, sign: str) -> dict:
    """星座在当日吉利候选池中确定性挑选幸运色/数字/方位/物品。

    alm 为预先算好的当日黄历（almanac_data 返回值），避免每个 derive 各算一遍。
    """
    rng = _rng(f"lucky|{sign}|{alm['date']}|{alm['ganzhi']}".encode("utf-8"))
    color_name, color_hex = rng.choice(alm["color_pool"])
    return {
        "color": color_name,
        "color_hex": color_hex,
        "number": rng.choice(alm["number_pool"]),
        "direction": rng.choice(alm["direction_pool"]),
        "item": rng.choice(alm["item_pool"]),
    }


def derive_yiji(alm: dict, sign: str) -> dict:
    """由当日建除倾向词库确定性挑选宜/忌（各 2 项）。"""
    rng = _rng(f"yiji|{sign}|{alm['date']}|{alm['ganzhi']}".encode("utf-8"))
    return {
        "yi": rng.sample(alm["yi_pool"], min(2, len(alm["yi_pool"]))),
        "ji": rng.sample(alm["ji_pool"], min(2, len(alm["ji_pool"]))),
    }
