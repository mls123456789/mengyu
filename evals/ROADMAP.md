# 评估体系演进路线图

> 基于 PROJECT_AUDIT.md v1、harness v1（judge + runner）、dream suite 已验证模式。
> 约束：不重构业务代码；每项有明确交付物。

---

## 1. 模块接入优先级

### 日记（journal）— 1 人天，LLM-as-Judge

- **评估类型**：纯 LLM-as-Judge，与 dream 模式完全相同（`respond_to_journal_stream` 替代 `interpret_dream_stream`）。
- **交付物**：`evals/suites/journal/` 下 test + fixtures + rubrics，6 条合成日记覆盖正常/敏感/模糊/超长/空输入/纯情绪标签。
- **Rubric 维度**：共情度（复用）、安全性（复用）、**实用性**（新增——建议是否具体可操作、视角是否轻盈不鸡汤）。
- **前置条件**：无。`respond_to_journal_stream(mood, content)` 接口已稳定。

### 星座（horoscope）— 2 人天，混合型

分批交付：

| 子任务 | 类型 | 人天 | 交付物 |
|---|---|---|---|
| `test_horoscope_engine.py` | **确定性回归** | 1 | 黄金集 24 条（12 星座 × 2 日期），断言总分/幸运色/幸运物品/宜忌数量。复用 `tests/test_engines.py` 已有脚手架。 |
| `test_horoscope_copy.py` | **LLM-as-Judge** | 1 | 4 条合成输入，评估 LLM 生成的运势文案质量。Rubric：**准确性**（文案是否与引擎输出的分数/宜忌一致）、**可读性**（是否自然流畅不模板化）。 |

- **前置条件**：无——确定性引擎接口 `astro.compute()` 为纯函数；文案生成走 `stream_chat`，与 dream 模式一致。

### 汇总

| 模块 | 类型 | 工作量 | 交付物 |
|---|---|---|---|
| 解梦 | LLM-as-Judge | ✅ 已完成 | `evals/suites/dream/` |
| 日记 | LLM-as-Judge | 1 天 | `evals/suites/journal/` |
| 星座 | 确定性 + LLM-as-Judge | 2 天 | `evals/suites/horoscope/` |

---

## 2. Harness 扩展计划

### P0 — 下个迭代（批量 + 对比）

| 能力 | 交付物 | 说明 |
|---|---|---|
| **批量并行** | `runner.py --parallel` | 多用例并发调用 LLM（当前串行），单 suite 耗时从 N×T 降至 max(T)。 | ✅ 已完成 |
| **结果对比** | `runner.py --diff <run1.json> <run2.json>` | 逐维度 diff，输出 score 变化 >1 分的条目及 reason 变更。 | ✅ 已完成 |

### P1 — 一个月内（回归 + CI + 成本）

| 能力 | 交付物 | 说明 |
|---|---|---|
| **回归门禁** | `harness/regression.py` | 复用当次运行结果按 per-category 阈值检查，低于阈值（如 safety<4）→ exit 1；judge 覆盖率 <80% 直接判失败。baseline 差异对比留待 P2。 | ✅ 已完成 |
| **阈值配置（Per-Category）** | `suites/<suite>/thresholds.yaml` | 按 category 分层设置阈值，不同 category（normal/sensitive/edge/vague）使用不同标准，解决 Edge/Vague 误报。 | ✅ 已完成 |
| **CI 集成** | `.github/workflows/evals.yml` | 每日定时跑确定性 suite（星座引擎），PR 触发跑全量。 | ✅ 已完成（2026-08-07） |
| **成本追踪** | runner 输出追加 `tokens_used` | 复用 `stream_chat` 已有的 usage 回调，每次 judge 调用后累加。 | 🔜 待实现 |

### P2 — 长期（可视化 + 实验）

- **趋势看板**：`evals/results/` 历史 JSON → 单页 HTML 折线图（score 变化曲线）。
- **A/B prompt 测试**：runner 支持 `--prompt-version v2`，同用例跑两组 prompt，对比 score 分布。

---

## 3. 风险与缓解

| 风险 | 缓解 |
|---|---|
| **LLM-as-Judge 评分波动**（同一输入两次得分差 ≥2） | (1) judge 已设 `temperature=0`；(2) P1 加入 **三裁判投票**模式——取中位数，三人分歧大则标记 "inconclusive"；(3) 确定性 golden set 不依赖 judge。 |
| **业务接口变更导致 suite 静默失效** | runner 启动时执行 `import` 冒烟检查——`interpret_dream_stream` / `respond_to_journal_stream` / `astro.compute` 三个入口函数签名校验，失败则 runner exit 2 并报告缺失接口。 |
| **Token 成本失控**（dream suite: 5 条 × (1 解读 + 3 裁判) = 20 次 LLM 调用） | (1) P0 加 `--dry-run` 模式只校验结构和导入；(2) 裁判调用共用原始解读（不重复生成）；(3) CI 定时跑而非每 PR 全量跑 LLM suite。预估单次 full run ~50K tokens。 |

---

**下一步**：按日记 → 星座引擎 → 星座文案 → harness P0 的顺序推进，每个迭代有独立可跑的交付物。

---

## 4. 关键决策 / 里程碑

| 日期 | 事件 |
|---|---|
| 2026-08-06 | 完成 Journal Rubric 校准（拆分日常/敏感双路径）；实现 Per-Category 阈值机制，解决 Edge/Vague 用例误报；修复 sensitive 生成截断问题。v1.0 核心门禁闭环。 |
| 2026-08-07 | CI 门禁上线：`.github/workflows/evals.yml` 每日定时 + PR 触发全量 LLM suite + regression check；历史异常版本归档。v1.0 收尾。 |
| 2026-08-07 | v1.0 架构审查修复三项致命缺陷：runner 误吞 pytest 型黄金集文件（horoscope 门禁恒红）；regression 门禁重跑整套 LLM（CI 成本×2、产物与门禁不一致）；CI 漏跑星座引擎黄金集。同期加固：judge 分数校验/超时、评分覆盖率闸、Secrets 预检、token 统计独立脚本。 |
