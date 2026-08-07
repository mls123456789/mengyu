"""星座引擎确定性回归测试 —— 24条黄金集（12星座 × 2日期）逐字段断言。

运行：
    pytest evals/suites/horoscope/test_horoscope_engine.py -v
    pytest evals/suites/horoscope/test_horoscope_engine.py -v --tb=long  # 失败时展示完整返回值

约定：
    - 不使用 LLM-as-Judge，纯确定性断言
    - 无网络调用，24 条用例 <1 秒完成
    - 黄金集由当前 astro.compute() 生成并人工校验，存放于 fixtures/horoscope_golden.json
    - 若测试失败，说明引擎输出已变更——确认是有意改动后更新黄金集文件
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services import astro
from app.services.horoscope import period_key

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "horoscope_golden.json"

SCORE_DIMS = ["overall", "love", "career", "wealth", "health"]
SCORE_MIN, SCORE_MAX = 36, 95


# ---------------------------------------------------------------------------
# 黄金集加载（供 parametrize 使用）
# ---------------------------------------------------------------------------
def _load_golden() -> list[tuple[str, str, dict]]:
    """返回 [(sign, date_str, expected_dict), ...] 共 24 条。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [(c["sign"], c["date"], c) for c in data["cases"]]


# ---------------------------------------------------------------------------
# 辅助：生成可读的失败诊断信息
# ---------------------------------------------------------------------------
def _diagnose(result: dict, expected: dict) -> str:
    """对比实际结果与期望值，返回多行差异报告。"""
    lines = ["", "--- 差异诊断 ---"]
    # 分数对比
    for dim in SCORE_DIMS:
        act = result[dim]["score"]
        exp = expected["scores"][dim]
        mark = "  <== 不一致" if act != exp else ""
        lines.append(f"  {dim}: actual={act} expected={exp}{mark}")
    # lucky 对比
    for key in ["color", "item", "number", "direction"]:
        act = result["lucky"][key]
        exp = expected[f"lucky_{key}"]
        mark = "  <== 不一致" if act != exp else ""
        lines.append(f"  lucky.{key}: actual={act!r} expected={exp!r}{mark}")
    # 宜忌对比
    act_yi = result["yiji"]["yi"]
    exp_yi = expected["yi"]
    if act_yi != exp_yi:
        lines.append(f"  yi: actual={act_yi} expected={exp_yi}  <== 不一致")
    act_ji = result["yiji"]["ji"]
    exp_ji = expected["ji"]
    if act_ji != exp_ji:
        lines.append(f"  ji: actual={act_ji} expected={exp_ji}  <== 不一致")
    # 速配对比
    for key in ["best", "worst"]:
        act = result["match"][key]
        exp = expected[f"match_{key}"]
        mark = "  <== 不一致" if act != exp else ""
        lines.append(f"  match.{key}: actual={act} expected={exp}{mark}")
    # 完整 compute() 返回值
    lines.append("")
    lines.append("--- 完整 compute() 返回值 ---")
    lines.append(json.dumps(
        {k: result[k] for k in SCORE_DIMS + ["lucky", "yiji", "match"]},
        ensure_ascii=False, indent=2,
    ))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sign,date_str,expected", _load_golden(),
                         ids=[f"{c['sign']}/{c['date']}" for _, _, c in _load_golden()])
def test_golden_regression(sign: str, date_str: str, expected: dict):
    """黄金集回归：astro.compute() 输出必须与预计算值逐字段完全一致。

    覆盖 12 星座 × 2 日期 = 24 条用例。
    """
    d = date.fromisoformat(date_str)
    pkey = period_key("today", d)
    result = astro.compute(sign, "today", pkey)

    # ---- 结构契约 ----
    # 分数范围
    for dim in SCORE_DIMS:
        s = result[dim]["score"]
        assert SCORE_MIN <= s <= SCORE_MAX, (
            f"{sign} {date_str} {dim} 分数 {s} 越界 [{SCORE_MIN}, {SCORE_MAX}]"
        )

    # 幸运字段非空
    assert isinstance(result["lucky"]["color"], str) and result["lucky"]["color"], (
        f"{sign} {date_str} lucky.color 为空"
    )
    assert isinstance(result["lucky"]["item"], str) and result["lucky"]["item"], (
        f"{sign} {date_str} lucky.item 为空"
    )
    assert isinstance(result["lucky"]["number"], int) and result["lucky"]["number"] > 0, (
        f"{sign} {date_str} lucky.number 非法"
    )
    assert isinstance(result["lucky"]["direction"], str) and result["lucky"]["direction"], (
        f"{sign} {date_str} lucky.direction 为空"
    )

    # 宜忌数量 ≥ 1
    assert len(result["yiji"]["yi"]) >= 1, (
        f"{sign} {date_str} yi 数量 {len(result['yiji']['yi'])} < 1"
    )
    assert len(result["yiji"]["ji"]) >= 1, (
        f"{sign} {date_str} ji 数量 {len(result['yiji']['ji'])} < 1"
    )

    # 速配互斥且不含自身
    assert result["match"]["best"] != result["match"]["worst"], (
        f"{sign} {date_str} match best==worst=={result['match']['best']}"
    )
    assert result["match"]["best"] != sign, (
        f"{sign} {date_str} match.best 不能是自己"
    )
    assert result["match"]["worst"] != sign, (
        f"{sign} {date_str} match.worst 不能是自己"
    )

    # ---- 黄金值精确回归 ----
    mismatch = []

    for dim in SCORE_DIMS:
        if result[dim]["score"] != expected["scores"][dim]:
            mismatch.append(dim)

    if result["lucky"]["color"] != expected["lucky_color"]:
        mismatch.append("lucky.color")
    if result["lucky"]["item"] != expected["lucky_item"]:
        mismatch.append("lucky.item")
    if result["lucky"]["number"] != expected["lucky_number"]:
        mismatch.append("lucky.number")
    if result["lucky"]["direction"] != expected["lucky_direction"]:
        mismatch.append("lucky.direction")
    if result["yiji"]["yi"] != expected["yi"]:
        mismatch.append("yi")
    if result["yiji"]["ji"] != expected["ji"]:
        mismatch.append("ji")
    if result["match"]["best"] != expected["match_best"]:
        mismatch.append("match.best")
    if result["match"]["worst"] != expected["match_worst"]:
        mismatch.append("match.worst")

    assert not mismatch, (
        f"{sign} {date_str} 黄金值不匹配: {', '.join(mismatch)}"
        + _diagnose(result, expected)
    )
