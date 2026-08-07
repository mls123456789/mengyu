"""确定性引擎单元测试：astro / almanac / horoscope 序列化。

纯函数测试，不依赖 LLM / 数据库 / 网络；验证确定性、结构契约与边界。
运行：pytest tests/
"""
import json
from datetime import date

from app.services import almanac, astro
from app.services.horoscope import parse_fortune, period_key

D = date(2026, 8, 4)


# ---------- almanac ----------

def test_almanac_data_structure():
    a = almanac.almanac_data(D)
    assert a["ganzhi"] and len(a["ganzhi"]) >= 2
    assert a["day_element"] in "木火土金水"
    for k in ("color_pool", "number_pool", "direction_pool", "item_pool", "yi_pool", "ji_pool"):
        assert a[k], f"{k} 候选池为空"


def test_derive_lucky_in_pool_and_deterministic():
    alm = almanac.almanac_data(D)
    l1 = almanac.derive_lucky(alm, "白羊座")
    l2 = almanac.derive_lucky(alm, "白羊座")
    assert l1 == l2, "derive_lucky 不稳定"
    names = [n for n, _ in alm["color_pool"]]
    assert l1["color"] in names
    # color_hex 必须与候选池里同名色的 hex 一致
    hexmap = dict(alm["color_pool"])
    assert l1["color_hex"] == hexmap[l1["color"]]
    assert l1["number"] in alm["number_pool"]
    assert l1["direction"] in alm["direction_pool"]
    assert l1["item"] in alm["item_pool"]


def test_derive_yiji_two_each_and_in_pool():
    alm = almanac.almanac_data(D)
    yj = almanac.derive_yiji(alm, "天秤座")
    assert len(yj["yi"]) == 2 and len(yj["ji"]) == 2
    assert all(x in alm["yi_pool"] for x in yj["yi"])
    assert all(x in alm["ji_pool"] for x in yj["ji"])


# ---------- astro / compute ----------

def test_compute_deterministic_and_bounded():
    pkey = period_key("today", D)
    c1 = astro.compute("狮子座", "today", pkey)
    c2 = astro.compute("狮子座", "today", pkey)
    assert c1 == c2, "compute 不稳定"
    for d in ("overall", "love", "career", "wealth", "health"):
        assert 36 <= c1[d]["score"] <= 95, f"{d} 分数 {c1[d]['score']} 越界"
    assert c1["match"]["best"] != c1["match"]["worst"]
    assert c1["match"]["best"] != "狮子座" and c1["match"]["worst"] != "狮子座"


def test_astro_data_signs_and_phase():
    ad = astro.astro_data(D)
    assert ad["sun_sign"] in astro._ZODIAC
    assert ad["moon_sign"] in astro._ZODIAC
    assert 0.0 <= ad["moon_phase"] <= 1.0
    for k in ("mercury", "venus", "saturn"):
        assert isinstance(ad["retrograde"][k], bool)


def test_period_key_roundtrip():
    for period in ("today", "tomorrow", "week", "month"):
        pk = period_key(period, D)
        d = astro.period_key_to_date(period, pk)  # 还原为代表日期
        assert isinstance(d, date)


# ---------- apply_and_serialize / parse_fortune ----------

_EXPECTED_KEYS = ["overall", "love", "career", "wealth", "health",
                  "lucky", "match", "advice", "motto", "retrograde", "astro_meta"]


def test_apply_and_serialize_contract():
    pkey = period_key("today", D)
    comp = astro.compute("双鱼座", "today", pkey)
    sections = {"overall": {"text": "平稳"}, "motto": {"text": "心安"}}
    out = astro.apply_and_serialize(sections, comp)
    lines = [json.loads(l) for l in out.splitlines()]
    assert [o["k"] for o in lines] == _EXPECTED_KEYS
    ov = next(o for o in lines if o["k"] == "overall")
    assert ov["score"] == comp["overall"]["score"] and ov["text"] == "平稳"
    # astro 原始天文数据不泄漏进 NDJSON
    assert all("sun_lon" not in o and "planet_signs" not in o for o in lines)


def test_parse_fortune_roundtrip():
    pkey = period_key("today", D)
    comp = astro.compute("白羊座", "today", pkey)
    parsed = parse_fortune(astro.apply_and_serialize({}, comp))
    assert set(parsed) == set(_EXPECTED_KEYS)
