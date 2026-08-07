"""Token 用量估算：汇总 evals/results/ 中每个 suite 最新一份结果的规模。

runner 输出的结果文件名带时间戳（<suite>-YYYYMMDD-HHMMSS.json），
按文件名降序排列后取每个 suite 的首条即最新一次运行。

估算口径：(回应字符 + 输入字符) / 2.5 —— 中文约 1.3 字符/token，
保守取 2.5；实际消耗约为估算值的 1.5–2×（含 system prompt / rubric / 输出开销）。
精确口径待 P1 成本追踪（stream usage 回调）落地后替换。

用法：python evals/harness/token_stats.py [results_dir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

SUITES = ("dream", "journal", "horoscope")

# 各 suite 记录回应长度的字段名不一致，逐一兜底
_RESP_KEYS = ("response_length", "interpretation_length", "copy_length")


def _resp_chars(results: list[dict]) -> int:
    return sum(
        next((r[k] for k in _RESP_KEYS if r.get(k)), 0)
        for r in results
    )


def main() -> int:
    results_dir = (Path(sys.argv[1]) if len(sys.argv) > 1
                   else ROOT / "evals" / "results")
    rows: list[tuple[str, str, int, int, int]] = []
    for suite in SUITES:
        files = sorted(results_dir.glob(f"{suite}-*.json"), reverse=True)
        if not files:
            continue
        path = files[0]  # 时间戳命名，降序首条即最新
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[WARN] 无法解析 {path.name}: {exc}", file=sys.stderr)
            continue
        results = data.get("results", [])
        judges = sum(1 for r in results if r.get("score") is not None)
        rows.append((suite, path.name, judges, _resp_chars(results),
                     sum(r.get("input_length") or 0 for r in results)))

    if not rows:
        print(f"{results_dir} 下无 suite 结果文件")
        return 0

    print(f"{'suite':<10} {'结果文件':<28} {'judge次数':>9} "
          f"{'回应字符':>10} {'输入字符':>10} {'估算tokens':>10}")
    print("-" * 92)
    t_judges = t_resp = t_input = 0
    for suite, name, judges, resp, inp in rows:
        est = int((resp + inp) / 2.5)
        print(f"{suite:<10} {name:<28} {judges:>9} {resp:>10} {inp:>10} {est:>10}")
        t_judges += judges
        t_resp += resp
        t_input += inp
    print("-" * 92)
    print(f"{'TOTAL':<10} {'':<28} {t_judges:>9} {t_resp:>10} {t_input:>10} "
          f"{int((t_resp + t_input) / 2.5):>10}")
    print("\n口径：估算tokens=(回应+输入字符)/2.5；"
          "实际消耗约 1.5–2×（含 rubric/system prompt/输出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
