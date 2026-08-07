"""星座文案质量评估脚本 —— 按准确性 / 可读性二维度对 LLM 运势文案打分。

约定（供 runner.py 发现）：
    async def run(judge) -> list[dict]
运行方式：
    python evals/harness/runner.py --suite horoscope
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.astro import compute                                          # noqa: E402
from app.services.horoscope import horoscope_stream, period_key                 # noqa: E402

DIMENSIONS = ["accuracy", "readability"]
COPY_TIMEOUT = 60


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _load_fixtures() -> list[dict]:
    path = HERE / "fixtures" / "horoscope_copy_cases.json"
    return list(json.loads(path.read_text(encoding="utf-8")).get("cases", []))


def _load_rubrics() -> dict:
    path = HERE / "rubrics" / "horoscope_copy_quality.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Rubric → prompt
# ---------------------------------------------------------------------------
def _format_rubric(dim: dict, engine_data: str = "") -> str:
    """渲染评分提示；accuracy 维度注入 engine_data 作为 ground truth。"""
    max_score = dim.get("max_score", 5)
    lines = [
        f"评估维度：{dim['name']}（{dim.get('short','')}）",
        f"满分：{max_score}",
        f"说明：{dim.get('description','').strip()}",
    ]
    if engine_data:
        lines.extend(["", "【引擎固定数据——ground truth，必须原样呈现】", engine_data])
    lines.append("")
    lines.append(f"评分标准（1-{max_score}分，每高一级包含上一级所有正面特征）：")
    levels = dim.get("levels", {})
    for lv in range(1, max_score + 1):
        ld = levels.get(lv, levels.get(str(lv), {}))
        if not ld:
            continue
        lines.append(f"  {lv} 分（{ld.get('summary','')}）：{'；'.join(ld.get('criteria',[]))}")
    lines.extend(["", f"请严格按以上标准打分，只输出 1-{max_score} 之间的整数分数。"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 文案获取
# ---------------------------------------------------------------------------
async def _get_copy(sign: str, dt: date, comp: dict) -> str:
    """收集流式运势文案全文（含超时与异常兜底）。"""
    try:
        async def _collect():
            chunks: list[str] = []
            async for chunk in horoscope_stream(sign=sign, period="today", comp=comp):
                chunks.append(chunk)
            return "".join(chunks)
        return await asyncio.wait_for(_collect(), timeout=COPY_TIMEOUT)
    except asyncio.TimeoutError:
        return f"[TIMEOUT] 文案生成超时（>{COPY_TIMEOUT}s）"
    except Exception as exc:
        return f"[LLM_ERROR] 文案生成失败: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 评估入口
# ---------------------------------------------------------------------------
async def run(judge) -> list[dict]:
    """执行星座文案质量评估套件。

    每条用例：1) astro.compute() 获取引擎 ground truth → 2) horoscope_stream() 生成文案
    → 3) 准确性维度注入引擎数据后 judge → 4) 可读性维度 judge。
    """
    cases = _load_fixtures()
    rubrics = _load_rubrics()
    results: list[dict] = []

    for case in cases:
        case_id = case["id"]
        sign = case["sign"]
        dt = date.fromisoformat(case["date"])
        focus = case.get("focus", [])

        comp = compute(sign, "today", period_key("today", dt))
        copy_text = await _get_copy(sign, dt, comp)

        # engine data 摘要（注入 accuracy rubric）
        engine = (
            f"综合指数={comp['overall']['score']}；爱情={comp['love']['score']}；"
            f"事业={comp['career']['score']}；财富={comp['wealth']['score']}；健康={comp['health']['score']}\n"
            f"幸运色={comp['lucky']['color']}；幸运数字={comp['lucky']['number']}；"
            f"幸运方位={comp['lucky']['direction']}；幸运物品={comp['lucky']['item']}\n"
            f"速配星座={comp['match']['best']}；相克星座={comp['match']['worst']}\n"
            f"宜={','.join(comp['yiji']['yi'])}；忌={','.join(comp['yiji']['ji'])}"
        )

        for dim_key in DIMENSIONS:
            dim = rubrics.get(dim_key)
            if not dim:
                results.append({"case": case_id, "dimension": dim_key,
                                "error": f"维度 {dim_key} 缺失"})
                continue

            rubric_text = _format_rubric(dim, engine if dim_key == "accuracy" else "")

            try:
                j = await judge(rubric_text, copy_text, max_score=dim.get("max_score", 5))
            except Exception as exc:
                j = {"score": None, "reason": f"judge 异常: {type(exc).__name__}: {exc}"}

            results.append({
                "case": case_id, "dimension": dim_key,
                "category": case.get("category", ""),
                "sign": sign, "date": case["date"], "focus": focus,
                "copy_length": len(copy_text),
                "score": j.get("score"), "reason": j.get("reason", ""),
            })

    return results
