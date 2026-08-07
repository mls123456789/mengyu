"""星座运势服务：多时段、结构化（NDJSON 流式）运势生成。

模型按行输出独立 JSON 对象（NDJSON），前端逐行解析、分块渲染，
既保留流式「逐段浮现」的体验，又能驱动评分环、进度条、幸运色块等结构化组件。
注意：输出不含任何 emoji；星级用文字符号「★」表示。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import AsyncIterator

from app.services.astro import compute
from app.services.llm import stream_chat

# 十二星座（含日期范围，供页面展示）
SIGNS = [
    ("白羊座", "3.21 - 4.19"),
    ("金牛座", "4.20 - 5.20"),
    ("双子座", "5.21 - 6.21"),
    ("巨蟹座", "6.22 - 7.22"),
    ("狮子座", "7.23 - 8.22"),
    ("处女座", "8.23 - 9.22"),
    ("天秤座", "9.23 - 10.23"),
    ("天蝎座", "10.24 - 11.22"),
    ("射手座", "11.23 - 12.21"),
    ("摩羯座", "12.22 - 1.19"),
    ("水瓶座", "1.20 - 2.18"),
    ("双鱼座", "2.19 - 3.20"),
]
SIGN_NAMES = [name for name, _ in SIGNS]

# 星座属性（静态，无需 LLM）
SIGN_META = {
    "白羊座": {"symbol": "♈", "icon": "aries", "element": "火", "ruler": "火星", "keyword": "勇敢 · 开创"},
    "金牛座": {"symbol": "♉", "icon": "taurus", "element": "土", "ruler": "金星", "keyword": "稳重 · 享受"},
    "双子座": {"symbol": "♊", "icon": "gemini", "element": "风", "ruler": "水星", "keyword": "灵动 · 好奇"},
    "巨蟹座": {"symbol": "♋", "icon": "cancer", "element": "水", "ruler": "月亮", "keyword": "温柔 · 顾家"},
    "狮子座": {"symbol": "♌", "icon": "leo", "element": "火", "ruler": "太阳", "keyword": "自信 · 耀眼"},
    "处女座": {"symbol": "♍", "icon": "virgo", "element": "土", "ruler": "水星", "keyword": "细致 · 完美"},
    "天秤座": {"symbol": "♎", "icon": "libra", "element": "风", "ruler": "金星", "keyword": "优雅 · 平衡"},
    "天蝎座": {"symbol": "♏", "icon": "scorpio", "element": "水", "ruler": "冥王星", "keyword": "深邃 · 专注"},
    "射手座": {"symbol": "♐", "icon": "sagittarius", "element": "火", "ruler": "木星", "keyword": "自由 · 乐观"},
    "摩羯座": {"symbol": "♑", "icon": "capricorn", "element": "土", "ruler": "土星", "keyword": "坚韧 · 务实"},
    "水瓶座": {"symbol": "♒", "icon": "aquarius", "element": "风", "ruler": "天王星", "keyword": "独立 · 创新"},
    "双鱼座": {"symbol": "♓", "icon": "pisces", "element": "水", "ruler": "海王星", "keyword": "浪漫 · 共情"},
}

# 时段
PERIODS = ["today", "tomorrow", "week", "month"]
PERIOD_LABELS = {"today": "今日", "tomorrow": "明日", "week": "本周", "month": "本月"}
_WEEKDAYS = "一二三四五六日"

SYSTEM_PROMPT = """你是「梦语」的星座运势解读师，风格温暖、轻盈、有分寸。
请为指定星座的指定时段生成运势，严格输出 9 行 NDJSON：每行一个独立的 JSON 对象，按下列顺序与字段输出，除此之外不要输出任何内容（不要解释、不要 markdown 代码块、不要 emoji、不要多余空行）：

{"k":"overall","score":82,"text":"一句话综合运势总评"}
{"k":"love","score":65,"text":"爱情运势简述"}
{"k":"career","score":70,"text":"事业学业运势简述"}
{"k":"wealth","score":55,"text":"财运简述"}
{"k":"health","score":80,"text":"健康运势简述"}
{"k":"lucky","color":"薄荷绿","number":7,"direction":"东南","item":"一本好书"}
{"k":"match","best":"天秤座","worst":"巨蟹座"}
{"k":"advice","yi":["表达心意","整理桌面"],"ji":["冲动消费","熬夜"]}
{"k":"motto","text":"一句温柔而积极的寄语"}

规则：
- 当日的 score、lucky（色/数字/方位/物品）、advice（宜/忌）、match（速配/相克）均为系统给定值，必须原样照抄，不得改写、替换、增删；你只需撰写各维度的文字解读（text）与寄语（motto）。
- score 为 0-100 的整数；text 为一两句简体中文。
- match.best / match.worst 必须是十二星座全名之一，且与本人星座不同。
- motto 一句话，温柔、积极、不浮夸；可自然引用当日黄历（干支/建除）与真实星象，但不得照搬古语。
- 所有内容禁止包含 emoji 或彩色表情符号。
- 你是运势解读师，绝不暴露身份或生成机制：不得出现“作为AI/我是语言模型/系统给定/根据数据/星历计算”等措辞，不得提及任何模型名称或解释内容如何产生；始终直接以解读师口吻给出解读。
- 每行必须是合法 JSON，键名与上面完全一致。
"""


def period_key(period: str, today: date | None = None) -> str:
    """时段缓存键：日/明日按日期，周按 ISO 周，月按年月。"""
    today = today or date.today()
    if period == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if period == "week":
        iso = today.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "month":
        return f"{today.year:04d}-{today.month:02d}"
    return today.isoformat()  # today


def period_human(period: str, today: date | None = None) -> str:
    """时段的人类可读标题。"""
    today = today or date.today()
    if period == "tomorrow":
        d = today + timedelta(days=1)
        return f"明日 · {d.month}月{d.day}日"
    if period == "week":
        return "本周运势"
    if period == "month":
        return f"{today.month}月运势"
    return f"今日 · {today.month}月{today.day}日 周{_WEEKDAYS[today.weekday()]}"


async def horoscope_stream(*, sign: str, period: str = "today", comp: dict | None = None) -> AsyncIterator[str]:
    """逐段 yield NDJSON 文本（天文 + 黄历数据驱动，文案由 LLM 撰写）。

    comp 由调用方预先 compute() 一次并传入，与 apply_and_serialize 共用，
    避免 cache-miss 时天文 + 黄历数据算两遍；未传则内部兜底计算。
    """
    today = date.today()
    label = PERIOD_LABELS.get(period, "今日")
    if comp is None:
        comp = compute(sign, period, period_key(period, today))
    ad = comp["astro"]                    # 当时段真实星象
    alm = comp["almanac"]                 # 当日黄历（干支/建除/纳音/吉神方位）

    # 逆行行星（中文），供 prompt 自然引用
    _retro_zh = {"mercury": "水星", "venus": "金星", "saturn": "土星"}
    retro = [name for k, name in _retro_zh.items() if ad["retrograde"].get(k)]
    retro_line = (
        f"逆行行星：{'、'.join(retro)}（提示沟通/计划/节奏需多留余量）\n"
        if retro else ""
    )

    astro_info = (
        f"【当日真实星象】（由天文星历计算，非人为设定）\n"
        f"太阳所在星座：{ad['sun_sign']}（黄经 {ad['sun_lon']:.1f}°）\n"
        f"月亮所在星座：{ad['moon_sign']}（月相 {ad['moon_phase']:.0%}）\n"
        f"水星：{ad['mercury_sign']} | 金星：{ad['venus_sign']} | "
        f"火星：{ad['mars_sign']} | 木星：{ad['jupiter_sign']} | 土星：{ad['saturn_sign']}\n"
        f"{retro_line}"
    )
    almanac_info = (
        f"【当日黄历】（由干支推定，供行文引用，勿照搬古语宜忌）\n"
        f"干支 {alm['ganzhi']}；建除 {alm['zhixing']}（{alm['tendency']}）；纳音 {alm['nayin']}\n"
        f"财神方位 {alm['caishen']}；喜神方位 {alm['xishen']}；福神方位 {alm['fushen']}\n"
    )
    fixed = (
        f"【固定数值与项目】（系统给定，必须原样照抄，不得更改）\n"
        f"综合指数={comp['overall']['score']}；爱情={comp['love']['score']}；"
        f"事业={comp['career']['score']}；财富={comp['wealth']['score']}；健康={comp['health']['score']}\n"
        f"幸运色={comp['lucky']['color']}；幸运数字={comp['lucky']['number']}；"
        f"幸运方位={comp['lucky']['direction']}；幸运物品={comp['lucky']['item']}\n"
        f"速配星座={comp['match']['best']}；相克星座={comp['match']['worst']}\n"
        f"宜={','.join(comp['yiji']['yi'])}；忌={','.join(comp['yiji']['ji'])}\n"
    )
    user_msg = (
        f"星座：{sign}\n时段：{label}（{period_human(period, today)}）\n"
        f"{astro_info}\n{almanac_info}\n{fixed}\n"
        f"请基于上述真实星象与黄历撰写文案，严格按 9 行 NDJSON 输出。"
        f"数值字段、幸运项（色/数字/方位/物品）与宜忌均已给定，必须照抄；"
        f"你只需撰写各维度的文字解读与寄语，并可自然引用当日真实行星位置与黄历干支，"
        f"增加解读的深度与可信度。"
    )
    async for delta in stream_chat(SYSTEM_PROMPT, user_msg, temperature=0.9):
        yield delta


def _clean_line(line: str) -> str:
    line = line.strip()
    # 容错：去掉可能的 ``` 或 ```json 代码围栏
    if line.startswith("```"):
        line = line.lstrip("`")
        if line[:4].lower() == "json":
            line = line[4:]
        line = line.strip()
    if line.endswith("```"):
        line = line[:-3].strip()
    return line


def parse_fortune(raw: str) -> dict:
    """把 NDJSON 原文解析成 {key: obj}；容错跳过非法行。"""
    sections: dict = {}
    if not raw:
        return sections
    for line in raw.splitlines():
        line = _clean_line(line)
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        k = obj.get("k")
        if isinstance(k, str):
            sections[k] = obj
    return sections
