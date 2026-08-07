"""天文星运引擎。

用真实星历（PyEphem）计算当日行星真实天文位置（黄经），
再映射为运势数值与选项。与「确定性星运引擎」的区别：
- 种子不再只是 sign+period，而是包含行星真实黄经的哈希，
  保证同一日期不同次调用结果完全相同（由天文数据决定）。
- 月相由真实月球位置计算。
- 守护星由行星真实位置决定（当日哪个行星在哪个星座，影响该星座的能量状态）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import random
import re
from typing import Literal

import ephem

from app.services import almanac

# 十二星座（30° 一宫）
_ZODIAC = ["白羊座", "金牛座", "双子座", "巨蟹座", "狮子座", "处女座",
           "天秤座", "天蝎座", "射手座", "摩羯座", "水瓶座", "双鱼座"]

# 守护星映射（古典占星）
_RULER = {
    "白羊座": "火星", "金牛座": "金星", "双子座": "水星", "巨蟹座": "月亮",
    "狮子座": "太阳", "处女座": "水星", "天秤座": "金星", "天蝎座": "冥王星",
    "射手座": "木星", "摩羯座": "土星", "水瓶座": "天王星", "双鱼座": "海王星",
}

# 固定输出字段顺序（retrograde 置于 motto 之后、astro_meta 之前）
_ORDER = ["overall", "love", "career", "wealth", "health", "lucky", "match", "advice", "motto", "retrograde"]

_MIN, _MAX = 36, 95

# 逆行检测覆盖的行星（键 → 中文名，单一权威来源）
_RETRO_NAMES = {"mercury": "水星", "venus": "金星", "saturn": "土星"}


def _rng(seed_bytes: bytes) -> random.Random:
    """用字节种子创建稳定 RNG（Mersenne Twister）。"""
    return random.Random(int.from_bytes(hashlib.sha256(seed_bytes).digest()[:8], "big"))


def _sign_of(lon_deg: float) -> str:
    return _ZODIAC[int(lon_deg) // 30 % 12]


def _clamp(v: int) -> int:
    return max(_MIN, min(_MAX, int(v)))


def _ecl_lon(obj: ephem.Body, dt: datetime.datetime) -> float:
    """返回天体之黄经（度数，0-360）。"""
    obj.compute(dt)
    ec = ephem.Ecliptic(obj)
    return math.degrees(ec.lon) % 360


_ONE_DAY = datetime.timedelta(days=1)

# 灼伤（combust）/合相（conjunction）的黄经角距阈值（度）。
# 传统灼伤为 8°；水星因轨道始终贴日，放宽至 10°。
_COMBUST = 8.0
_COMBUST_MERCURY = 10.0
_CONJ = 8.0


def _ang_sep(a: float, b: float) -> float:
    """两个黄经之间的最短角距（度，0-180），处理 0-360 环绕。"""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _lon_delta(prev: float, nxt: float) -> float:
    """黄经的有符号变化（度），映射到 (-180, 180]，处理 0-360 环绕。

    正值 = 顺行（黄经增大），负值 = 逆行（黄经减小）。
    """
    return (nxt - prev + 180) % 360 - 180


def _moon_phase(dt: datetime.datetime) -> float:
    """返回月面照度（0-1）：0=新月、1.0=满月，上/下弦≈0.5。"""
    moon = ephem.Moon(dt)
    return moon.phase / 100.0


def astro_data(date: datetime.date) -> dict:
    """计算指定日期的天文数据（北京时间 12:00）。"""
    dt = datetime.datetime(date.year, date.month, date.day, 12, 0, 0)
    dt_next = dt + _ONE_DAY

    sun_lon = _ecl_lon(ephem.Sun(), dt)
    moon_lon = _ecl_lon(ephem.Moon(), dt)
    mercury = _ecl_lon(ephem.Mercury(), dt)
    venus = _ecl_lon(ephem.Venus(), dt)
    mars_lon = _ecl_lon(ephem.Mars(), dt)
    jupiter = _ecl_lon(ephem.Jupiter(), dt)
    saturn = _ecl_lon(ephem.Saturn(), dt)
    # 次日黄经：用于判断视运动逆行（黄经较前一日减小即逆行）
    mercury_next = _ecl_lon(ephem.Mercury(), dt_next)
    venus_next = _ecl_lon(ephem.Venus(), dt_next)
    saturn_next = _ecl_lon(ephem.Saturn(), dt_next)
    moon_phase = _moon_phase(dt)

    sun_sign = _sign_of(sun_lon)
    moon_sign = _sign_of(moon_lon)

    return {
        "date": date.isoformat(),
        "sun_lon": sun_lon,
        "moon_lon": moon_lon,
        "moon_sign": moon_sign,
        "moon_phase": round(moon_phase, 3),
        "mercury_sign": _sign_of(mercury),
        "venus_sign": _sign_of(venus),
        "mars_sign": _sign_of(mars_lon),
        "jupiter_sign": _sign_of(jupiter),
        "saturn_sign": _sign_of(saturn),
        "sun_sign": sun_sign,
        # 真实黄经（度数）：供角距灼伤/合相与逆行判定
        "mercury_lon": mercury,
        "venus_lon": venus,
        "mars_lon": mars_lon,
        "jupiter_lon": jupiter,
        "saturn_lon": saturn,
        # 视运动逆行：黄经较次日减小即逆行（环绕安全）
        "retrograde": {
            "mercury": _lon_delta(mercury, mercury_next) < 0,
            "venus": _lon_delta(venus, venus_next) < 0,
            "saturn": _lon_delta(saturn, saturn_next) < 0,
        },
        # 该日各行星所在的星座（用于守护星能量判断）
        "planet_signs": {
            "sun": sun_sign, "moon": moon_sign,
            "mercury": _sign_of(mercury), "venus": _sign_of(venus),
            "mars": _sign_of(mars_lon), "jupiter": _sign_of(jupiter),
            "saturn": _sign_of(saturn),
        },
    }


def period_key_to_date(period: str, period_key: str) -> datetime.date:
    """把缓存键 period_key 还原为代表日期（周→本周一，月→当月1号，日/明日→该日）。"""
    if period == "week":            # YYYY-WNN
        y, w = period_key.split("-W")
        return datetime.date.fromisocalendar(int(y), int(w), 1)
    if period == "month":           # YYYY-MM
        y, m = period_key.split("-")
        return datetime.date(int(y), int(m), 1)
    y, m, d = period_key.split("-")  # today / tomorrow (YYYY-MM-DD)
    return datetime.date(int(y), int(m), int(d))


def compute(sign: str, period: str, period_key: str) -> dict:
    """用真实天文数据驱动确定性计算。

    数值完全由当日行星真实位置决定，跨进程、跨日、跨请求一致。
    若同一日期同一星座两次调用，结果完全相同（仅当日期变化时才改变）。
    幸运色/数字/方位/物品与宜忌由当日黄历干支推定（见 almanac.py）。
    """
    dt = period_key_to_date(period, period_key)
    astro = astro_data(dt)

    # 用「星座 + 天文数据摘要」的哈希做种子（确保同一日期同星座永远相同）
    seed_str = f"{sign}|{period}|{period_key}|sun={astro['sun_lon']:.2f}|moon={astro['moon_sign']}|phase={astro['moon_phase']}"
    rng = _rng(seed_str.encode("utf-8"))

    planets = astro["planet_signs"]
    sun_today = planets["sun"]

    # ---- 综合基调：月相 + 太阳活跃度 ----
    # moon_phase 即月面照度：0=新月、1.0=满月（本身已是 0→1→0 的三角，满月最高、新月最低）
    moon_mod = astro["moon_phase"]
    # 真实黄经角距判定灼伤：水星因始终贴日放宽至 10°，其余行星 8° 内视为灼伤（整体轻微减分）
    mercury_combust = _ang_sep(astro["mercury_lon"], astro["sun_lon"]) < _COMBUST_MERCURY
    base = 61 + round(moon_mod * 6) - (3 if mercury_combust else 0)
    if sun_today == sign:
        base += 3  # 太阳行至本人星座，能量加持

    # ---- 行星相位对各维度的微调（真实黄经角距）----
    # 金星远离太阳（非灼伤 = 独立显赫）利感情；火木合相（角距 < 8°）= 行动力逢扩张，利事业
    venus_combust = _ang_sep(astro["venus_lon"], astro["sun_lon"]) < _COMBUST
    love_boost = 4 if not venus_combust else 0
    career_boost = 4 if _ang_sep(astro["mars_lon"], astro["jupiter_lon"]) < _CONJ else 0

    dims = {
        "love":   _clamp(base + love_boost + rng.randint(-18, 22)),
        "career": _clamp(base + career_boost + rng.randint(-20, 20)),
        "wealth": _clamp(base + rng.randint(-22, 18)),
        "health": _clamp(base + rng.randint(-16, 24)),
    }
    overall = _clamp(round(sum(dims.values()) / 4) + rng.randint(-5, 5))

    # ---- 幸运色/数字/方位/物品 + 宜忌：当日黄历干支推定（见 almanac.py）----
    # 黄历数据只算一次，复用给 derive_*（原先 derive_lucky/derive_yiji/直接调用各算一遍 = 3 次）
    alm = almanac.almanac_data(dt)
    lucky = almanac.derive_lucky(alm, sign)
    yiji = almanac.derive_yiji(alm, sign)

    # ---- 速配/相克（按守护星分组挑选候选）----
    others = [s for s in _ZODIAC if s != sign]
    best_candidates = [s for s in others if _RULER.get(s) in
                       ["金星", "木星"]]  # 幸运星座
    worst_candidates = [s for s in others if _RULER.get(s) in
                        ["火星", "土星"]]  # 压力星座
    best = rng.choice(best_candidates) if best_candidates else rng.choice(others)
    worst = rng.choice(worst_candidates) if worst_candidates else \
        rng.choice([s for s in others if s != best])

    return {
        "overall": {"score": overall},
        "love":   {"score": dims["love"]},
        "career": {"score": dims["career"]},
        "wealth": {"score": dims["wealth"]},
        "health": {"score": dims["health"]},
        "lucky": lucky,
        "yiji": yiji,
        "match": {"best": best, "worst": worst},
        "astro": astro,      # 供 prompt 使用（天体真实位置）
        "almanac": alm,      # 供 prompt 使用（黄历叙事），序列化前 pop
    }


# ---------- 输出侧净化：永不落库/渲染暴露 AI 或系统机制的措辞 ----------
_AI_TELL = re.compile(
    r"作为(?:AI|人工智能|(?:大型)?语言模型|虚拟?助手)|"
    r"我是(?:一个)?(?:AI|人工智能|(?:大型)?语言模型|虚拟?助手)|"
    r"(?:GPT|ChatGPT|GLM-?\d|文心一言|通义千问|Claude|DeepSeek|Kimi)|"
    r"(?:大语言模型|语言模型|大模型|AI(?:模型|助手|程序|生成))|"
    r"系统给定|原样照抄|照抄|根据(?:星历计算|系统(?:给定|数据))|PyEphem|星历计算|"
    r"我(?:不能|无法|没办法|不适合)(?:预测|预知|推算|保证|告诉你)",
    re.IGNORECASE,
)

# 各维度/寄语的安全回退文案（仅当 LLM 文案为空或触发净化时使用）
_DIM_FALLBACK = {
    "overall": "整体节奏平稳，按自己的步调推进就好。",
    "love": "感情上顺其自然，一句真诚的话胜过许多铺垫。",
    "career": "事业学业贵在专注，稳扎稳打自有积累。",
    "wealth": "财务上量入为出，把决定留给清醒的时刻。",
    "health": "留意作息与精力，劳逸结合是今天的底色。",
}
_MOTTO_FALLBACK = "愿你今日心安，步履不停。"


def _safe_text(raw: str, fallback: str) -> str:
    """LLM 文案若为空或含 AI/系统破绽，回退到确定性安全文案。"""
    t = (raw or "").strip()
    if not t or _AI_TELL.search(t):
        return fallback
    return t


def apply_and_serialize(sections: dict, comp: dict) -> str:
    """用预计算的 comp 覆盖 LLM 产出的分数/幸运/速配，保留文案，输出 NDJSON。

    comp 由调用方预先 compute() 一次（与 horoscope_stream 共用，避免天文数据算两遍）。
    astro/almanac 仅在此读取以生成 astro_meta，不会进 NDJSON（_ORDER 未含这两个键）。
    """
    astro = comp["astro"]
    out: dict = {}

    for k in ("overall", "love", "career", "wealth", "health"):
        ll = sections.get(k) or {}
        out[k] = {"k": k, "score": comp[k]["score"], "text": _safe_text(ll.get("text", ""), _DIM_FALLBACK[k])}

    out["lucky"] = {"k": "lucky", **comp["lucky"]}
    out["match"] = {"k": "match", **comp["match"]}

    # 宜/忌由当日黄历干支确定性给定（覆盖 LLM 产出，保证恒定且不雷同）
    out["advice"] = {"k": "advice", "yi": comp["yiji"]["yi"], "ji": comp["yiji"]["ji"]}
    out["motto"] = {"k": "motto", "text": _safe_text((sections.get("motto") or {}).get("text", ""), _MOTTO_FALLBACK)}

    # 逆行行星（中文列表；无逆行则空数组）
    # 字段契约：{"k":"retrograde","planets":[行星中文名...]}
    retro_planets = [name for key, name in _RETRO_NAMES.items() if astro["retrograde"].get(key)]
    out["retrograde"] = {"k": "retrograde", "planets": retro_planets}

    # 把天文元数据也写进最后一行，供 prompt 引用（LLM 写文案时参考真实星象）
    out["astro_meta"] = {
        "k": "astro_meta",
        "sun_sign": astro["sun_sign"],
        "moon_sign": astro["moon_sign"],
        "moon_phase": astro["moon_phase"],
        "mercury_sign": astro["mercury_sign"],
        "venus_sign": astro["venus_sign"],
        "mars_sign": astro["mars_sign"],
        "jupiter_sign": astro["jupiter_sign"],
        "saturn_sign": astro["saturn_sign"],
        "retrograde": retro_planets,
    }

    return "\n".join(json.dumps(out[k], ensure_ascii=False) for k in _ORDER + ["astro_meta"])
