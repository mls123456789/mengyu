"""最小评估执行框架（runner）：发现并运行某个 suite 下的评估脚本，输出 JSON 结果。

用法：
    python evals/harness/runner.py --suite dream
    python evals/harness/runner.py --suite dream --parallel --max-workers 5
    python evals/harness/runner.py --diff evals/results/dream-20260806.json evals/results/dream-20260807.json

发现规则：evals/suites/<suite>/*.py（跳过以 "_" 开头的文件）。
并行模式：--parallel 下多个脚本并发执行，默认并发数 5，可通过 --max-workers 调整。
diff 模式：逐维度对比两次运行结果，输出 score 变化 >1 分的条目。

评估脚本约定（每个 .py 暴露一个 run）：
    async def run(judge) -> list[dict]
脚本内可直接 `from judge import judge`（runner 已把 harness 目录加入 sys.path）。

不引入新依赖；不内置任何业务逻辑。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
ROOT = HARNESS_DIR.parent.parent

for p in (str(HARNESS_DIR), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# 脚本发现与加载
# ---------------------------------------------------------------------------
def discover(suites_dir: Path, suite: str) -> list[Path]:
    d = suites_dir / suite
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"evalsuite_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# 单脚本执行
# ---------------------------------------------------------------------------
async def run_script(path: Path, judge_fn) -> list[dict]:
    try:
        mod = load_module(path)
        run = getattr(mod, "run", None)
        if not callable(run):
            # pytest 型确定性测试（如 horoscope 黄金集）没有 run(judge)——跳过而非计错，
            # 否则 runner --suite 会因「无 run」恒 exit 1，门禁信号失真
            print(f"skip: {path.name} 无 run(judge)，按非 runner 脚本跳过",
                  file=sys.stderr)
            return []
        out = run(judge_fn)
        if inspect.isawaitable(out):
            out = await out
        if not isinstance(out, list):
            return [{"case": path.name, "error": "`run` must return a list[dict]"}]
        return out
    except Exception:
        return [{"case": path.name, "error": traceback.format_exc(limit=5)}]


def summarize(results: list[dict]) -> dict:
    total = len(results)
    errors = sum(1 for r in results if r.get("error"))
    passed = sum(1 for r in results if r.get("pass") is True)
    failed = sum(1 for r in results if r.get("pass") is False)
    return {"total": total, "passed": passed, "failed": failed, "errors": errors}


# ---------------------------------------------------------------------------
# Suite 执行（串行 / 并行）
# ---------------------------------------------------------------------------
async def main_async(args) -> int:
    from judge import judge as judge_fn

    suites_dir = Path(args.suites_dir)
    scripts = discover(suites_dir, args.suite)
    if not scripts:
        print(f"no eval scripts found under {suites_dir / args.suite}", file=sys.stderr)
        return 2

    if args.parallel:
        sem = asyncio.Semaphore(args.max_workers)
        async def _run_one(p: Path) -> list[dict]:
            async with sem:
                return await run_script(p, judge_fn)
        batches = await asyncio.gather(*[_run_one(p) for p in scripts])
        results: list[dict] = [r for b in batches for r in b]
    else:
        results = []
        for path in scripts:
            results.extend(await run_script(path, judge_fn))

    report = {
        "suite": args.suite,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(results),
        "results": results,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"{args.suite}-{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))

    exit_code = 0 if summarize(results)["errors"] == 0 else 1

    # 回归门禁：复用本次运行结果直接判阈值（不重跑，避免双倍 LLM 成本，
    # 且保证落盘 JSON 与被门禁评估的是同一次运行）
    if args.regression_check:
        from regression import run_check  # noqa: E402  (lazy import 避免循环依赖)
        baseline = args.regression_baseline or str(ROOT / "evals" / "results" / f"{args.suite}_baseline_20260806.json")
        rc = await run_check(args.suite, baseline, results=results)
        if rc != 0:
            exit_code = rc

    return exit_code


# ---------------------------------------------------------------------------
# Diff 模式：对比两个结果文件
# ---------------------------------------------------------------------------
def _index_results(results: list[dict]) -> dict[tuple[str, str], dict]:
    """将 results 数组索引为 {(case, dimension): {score, reason}}。"""
    idx: dict[tuple[str, str], dict] = {}
    for r in results:
        key = (r.get("case", ""), r.get("dimension", ""))
        idx[key] = {"score": r.get("score"), "reason": r.get("reason", "")}
    return idx


def _print_diff_table(rows: list[dict]) -> None:
    """打印人类可读的差异表格。"""
    if not rows:
        print("(no significant changes — all score deltas ≤ 1)")
        return
    # 列宽自适应
    cw = max(max(len(r["case"]) for r in rows), 4)
    dw = max(max(len(r["dimension"]) for r in rows), 9)
    sep = f"+{'-'*(cw+2)}+{'-'*(dw+2)}+{'-'*6}+{'-'*6}+{'-'*5}+"
    print(sep)
    print(f"| {'case':<{cw}} | {'dimension':<{dw}} | old   | new   | Δ    |")
    print(sep)
    for r in rows:
        delta = r["new_score"] - r["old_score"]
        ds = f"+{delta}" if delta > 0 else str(delta)
        flag = " ⚠" if abs(delta) >= 2 else ""
        print(f"| {r['case']:<{cw}} | {r['dimension']:<{dw}} | {str(r['old_score']):<5} | {str(r['new_score']):<5} | {ds:<4} |{flag}")
        if r.get("old_reason") != r.get("new_reason"):
            print(f"| {'':<{cw}} | {'':<{dw}} | reason: {r['old_reason'][:60]}")
            print(f"| {'':<{cw}} | {'':<{dw}} |      →: {r['new_reason'][:60]}")
    print(sep)
    print(f"{len(rows)} change(s) with |Δ| > 1")


def diff_main(path_a: str, path_b: str) -> int:
    """对比两个 runner 输出 JSON，逐维度报告 score 变化 >1 分的条目。"""
    a = json.loads(Path(path_a).read_text(encoding="utf-8"))
    b = json.loads(Path(path_b).read_text(encoding="utf-8"))

    idx_a = _index_results(a.get("results", []))
    idx_b = _index_results(b.get("results", []))

    keys_a, keys_b = set(idx_a), set(idx_b)
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a

    if only_a or only_b:
        print("ERROR: result files have mismatched case/dimension sets", file=sys.stderr)
        if only_a:
            print(f"  only in {path_a}: {sorted(only_a)}", file=sys.stderr)
        if only_b:
            print(f"  only in {path_b}: {sorted(only_b)}", file=sys.stderr)
        return 2

    print(f"diff: {path_a}  vs  {path_b}")
    print(f"  suite: {a.get('suite','?')}  |  {a.get('generated_at','?')}  →  {b.get('generated_at','?')}")
    print(f"  total entries compared: {len(keys_a)}")
    print()

    rows: list[dict] = []
    for key in sorted(keys_a):
        sa, sb = idx_a[key], idx_b[key]
        delta = (sb["score"] or 0) - (sa["score"] or 0)
        if abs(delta) > 1:
            rows.append({
                "case": key[0], "dimension": key[1],
                "old_score": sa["score"], "new_score": sb["score"],
                "old_reason": sa["reason"], "new_reason": sb["reason"],
            })

    _print_diff_table(rows)

    # 汇总统计
    all_deltas = [(idx_b[k]["score"] or 0) - (idx_a[k]["score"] or 0) for k in keys_a
                  if idx_a[k]["score"] is not None and idx_b[k]["score"] is not None]
    if all_deltas:
        improved = sum(1 for d in all_deltas if d > 0)
        declined = sum(1 for d in all_deltas if d < 0)
        avg_delta = sum(all_deltas) / len(all_deltas)
        print(f"\nsummary: {improved} improved, {declined} declined, {len(all_deltas)-improved-declined} unchanged")
        print(f"mean Δ = {avg_delta:+.2f}")

    return 0 if not rows else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="mengyu 最小评估 runner")
    parser.add_argument("--suite", help="suite 名，如 dream / journal / horoscope（与 --diff 互斥）")
    parser.add_argument("--suites-dir", default=str(ROOT / "evals" / "suites"))
    parser.add_argument("--out", default=str(ROOT / "evals" / "results"))
    parser.add_argument("--parallel", action="store_true", help="并发执行多个评估脚本")
    parser.add_argument("--max-workers", type=int, default=5, help="并行最大并发数（默认 5）")
    parser.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="对比两个结果 JSON 文件")
    parser.add_argument("--regression-check", action="store_true", help="运行后执行回归门禁检查")
    parser.add_argument("--regression-baseline", help="回归门禁 baseline 路径（默认 evals/results/<suite>_baseline_20260806.json）")

    args = parser.parse_args()

    if args.diff:
        raise SystemExit(diff_main(args.diff[0], args.diff[1]))

    if not args.suite:
        parser.error("either --suite or --diff is required")

    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
