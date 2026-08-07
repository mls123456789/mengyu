"""解梦质量评估脚本 —— 按安全性 / 共情度 / 一致性三维度对 LLM 梦境解读打分。

约定（供 runner.py 发现）：
    async def run(judge) -> list[dict]
      - judge: harness/judge.py 的 judge(rubric, content, max_score=5) -> {"score", "reason"}
      - 返回的每个 dict 至少含 "case"，并含 score / reason / dimension / category

运行方式（通过 runner）：
    python evals/harness/runner.py --suite dream

自测（单文件运行，需先加载 .env）：
    python -c "
    from dotenv import load_dotenv; load_dotenv('.env')
    from judge import judge
    import asyncio, importlib.util as iu
    spec = iu.spec_from_file_location('m', 'evals/suites/dream/test_dream_quality.py')
    m = iu.module_from_spec(spec); spec.loader.exec_module(m)
    print(asyncio.run(m.run(judge)))
    "
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# 路径准备：确保 app.* 可导入（runner.py 已做，此处为自测兜底）
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent          # evals/suites/dream/
ROOT = HERE.parent.parent.parent                # mengyu 仓库根目录
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.dream import interpret_dream_stream  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DIMENSIONS = ["safety", "empathy", "consistency"]
INTERPRET_TIMEOUT = 90  # 单条梦境解读的最长等待秒数
INTERPRET_RETRIES = 2   # 空输出/超时重试（DeepSeek 偶发空 completion 与瞬态慢响应）


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _load_fixtures() -> list[dict]:
    """加载合成测试用例。"""
    path = HERE / "fixtures" / "dream_cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("cases", []))


def _load_rubrics() -> dict:
    """加载评分量表 YAML。"""
    path = HERE / "rubrics" / "dream_quality.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Rubric → prompt
# ---------------------------------------------------------------------------
def _format_rubric(dim: dict) -> str:
    """将单个维度的 YAML 量表结构渲染为 judge 可理解的评分提示。

    输出格式：
        评估维度：{name}（{short}）
        满分：{max_score}
        说明：{description}

        评分标准（1-5分，每高一级必须包含上一级所有正面特征）：
          1 分（{summary}）：{criteria}
          ...
        请严格按以上标准打分，只输出1-{max_score}之间的整数分数。
    """
    max_score = dim.get("max_score", 5)
    lines = [
        f"评估维度：{dim['name']}（{dim.get('short', '')}）",
        f"满分：{max_score}",
        f"说明：{dim.get('description', '').strip()}",
        "",
        f"评分标准（1-{max_score}分，每高一级必须包含上一级所有正面特征）：",
    ]
    levels = dim.get("levels", {})
    for level in range(1, max_score + 1):
        ld = levels.get(level, levels.get(str(level), {}))
        if not ld:
            continue
        criteria = "；".join(ld.get("criteria", []))
        lines.append(f"  {level} 分（{ld.get('summary', '')}）：{criteria}")

    lines.append("")
    lines.append(f"请严格按以上标准打分，只输出 1-{max_score} 之间的整数分数。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解读获取（调用解梦服务）
# ---------------------------------------------------------------------------
async def _collect_stream(title: str, content: str) -> str:
    """收集流式解读的完整文本。"""
    chunks: list[str] = []
    async for chunk in interpret_dream_stream(title=title, content=content):
        chunks.append(chunk)
    return "".join(chunks)


async def _get_interpretation(title: str, content: str) -> str:
    """获取梦境解读全文（含超时/异常兜底与空输出重试）。"""
    for _attempt in range(INTERPRET_RETRIES + 1):
        try:
            text = await asyncio.wait_for(
                _collect_stream(title, content),
                timeout=INTERPRET_TIMEOUT,
            )
            if text.strip():
                return text
            # 空输出：DeepSeek 偶发空 completion，重试
        except asyncio.TimeoutError:
            continue  # 瞬态慢响应，重试
        except Exception as exc:
            return f"[LLM_ERROR] 解读调用失败: {type(exc).__name__}: {exc}"
    return "[FAIL] 解读生成重试耗尽（超时或空输出）"


# ---------------------------------------------------------------------------
# 评估入口（runner.py 约定）
# ---------------------------------------------------------------------------
async def run(judge) -> list[dict]:
    """执行解梦质量评估套件。

    对每条合成梦境：
      1. 调用 interpret_dream_stream 获取解读全文
      2. 对安全 / 共情 / 一致三个维度分别调用 LLM-as-Judge 打分
      3. 汇总所有维度 × 用例的结果列表返回
    """
    cases = _load_fixtures()
    rubrics = _load_rubrics()

    results: list[dict] = []

    for case in cases:
        case_id = case["id"]
        category = case.get("category", "")
        title = case.get("title", "")
        content = case.get("content", "")
        focus = case.get("focus", [])

        interpretation = await _get_interpretation(title, content)

        for dim_key in DIMENSIONS:
            dim = rubrics.get(dim_key)
            if not dim:
                results.append({
                    "case": case_id,
                    "dimension": dim_key,
                    "error": f"维度 {dim_key} 在 rubrics YAML 中缺失",
                })
                continue

            rubric_text = _format_rubric(dim)

            try:
                judgement = await judge(rubric_text, interpretation, max_score=dim.get("max_score", 5))
            except Exception as exc:
                judgement = {"score": None, "reason": f"judge 异常: {type(exc).__name__}: {exc}"}

            results.append({
                "case": case_id,
                "dimension": dim_key,
                "category": category,
                "input_title": title,
                "input_length": len(content),
                "focus": focus,
                "interpretation_length": len(interpretation),
                "score": judgement.get("score"),
                "reason": judgement.get("reason", ""),
            })

    return results
