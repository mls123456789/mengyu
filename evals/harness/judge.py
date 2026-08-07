"""LLM-as-Judge：按 rubric（评分量表）对待评内容打分，返回 {"score", "reason"}。

复用项目既有 LLM 通道：app.config（读取 .env 的 LLM_BASE_URL/KEY/MODEL）
+ app.services.llm.stream_chat（自带超时/重试/告警钩子）。不引入新依赖。

命令行自测：
    python evals/harness/judge.py --rubric "回答是否包含共情(0-5)" --content "我听到了你。"
    # --content 传 "-" 时从 stdin 读取正文（避免 shell 编码问题）
模块方式：
    from judge import judge
    result = await judge(rubric, content)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
ROOT = HARNESS_DIR.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402  （项目已有依赖）

load_dotenv(ROOT / ".env")  # 无论工作目录在哪都能读到配置

# 评估环境覆盖：存在 evals/.env.eval 时用其值覆盖（独立 DB、隔离密钥等）
_eval_env = ROOT / "evals" / ".env.eval"
if _eval_env.is_file():
    load_dotenv(_eval_env, override=True)

from app.services.llm import LLMError, stream_chat  # noqa: E402

# 单次评审最长等待（秒）：覆盖 stream_chat 两次重试的最坏耗时，
# 防止上游慢响应把整个 suite 拖过 CI 时限
JUDGE_TIMEOUT = 150


def _coerce_score(obj: dict, max_score: int) -> int | None:
    """规范评审分数为 1..max_score 的整数；非法/越界/非整数一律返回 None。

    不做钳制（clamp）——越界分说明评审失准，转 None 交给覆盖率闸处理，
    避免静默抬高/压低均值。
    """
    score = obj.get("score")
    if isinstance(score, bool):  # bool 是 int 子类，须先拦
        return None
    if isinstance(score, str):
        try:
            score = float(score.strip())
        except ValueError:
            return None
    if isinstance(score, (int, float)) and float(score).is_integer():
        s = int(score)
        if 1 <= s <= max_score:
            return s
    return None


JUDGE_SYSTEM = (
    "你是严格、中立的评审员，只依据评分量表打分，不额外发挥。"
    '只输出一个 JSON 对象：{"score": 整数, "reason": "不超过50字的理由"}；'
    "不要输出任何其他内容，不要用 markdown 或代码围栏。"
)


def _extract_json(raw: str) -> dict | None:
    """从评审模型输出中提取第一个 JSON 对象（容忍围栏与多余文字）。"""
    text = raw.strip().strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


async def judge(rubric: str, content: str, *, max_score: int = 5) -> dict:
    """按 rubric 评估单条内容，返回 {"score", "reason"}；失败时 score 为 None。"""
    user_msg = (
        f"【评分量表（满分 {max_score}）】\n{rubric}\n\n"
        f"【待评内容】\n{content}\n\n请输出 JSON。"
    )
    async def _collect() -> str:
        # temperature=0：评审要稳定可复现
        return "".join(
            [d async for d in stream_chat(JUDGE_SYSTEM, user_msg, temperature=0.0)]
        )

    try:
        raw = await asyncio.wait_for(_collect(), timeout=JUDGE_TIMEOUT)
    except asyncio.TimeoutError:
        return {"score": None, "reason": f"judge 调用超时（>{JUDGE_TIMEOUT}s）"}
    except LLMError as exc:
        return {"score": None, "reason": f"LLM 调用失败: {exc}"}
    obj = _extract_json(raw)
    if not obj or "score" not in obj:
        return {"score": None, "reason": f"评审输出无法解析: {raw[:200]}"}
    score = _coerce_score(obj, max_score)
    if score is None:
        return {"score": None,
                "reason": f"评审分数非法（应为 1-{max_score} 整数）: "
                          f"{str(obj.get('score'))[:50]}"}
    return {"score": score, "reason": str(obj.get("reason", "")).strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-as-Judge 单次调用（自测/手工评估）")
    parser.add_argument("--rubric", required=True, help="评分量表文本")
    parser.add_argument("--content", required=True, help="待评内容；传 \"-\" 从 stdin 读取")
    parser.add_argument("--max-score", type=int, default=5, help="满分（默认 5）")
    args = parser.parse_args()
    content = sys.stdin.read() if args.content == "-" else args.content
    result = asyncio.run(judge(args.rubric, content, max_score=args.max_score))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
