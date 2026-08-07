1. 技术栈与架构

后端 FastAPI(Python)+SQLite(WAL)+SSE；前端 Jinja2 服务端渲染+原生 JS（无框架，app.js 单文件 IIFE）；LLM 走 OpenAI 兼容协议（AsyncOpenAI 流式）。部署：uvicorn 多 worker+nginx+systemd（deploy/ 已有产物），静态资源自托管、严格 CSP。
核心目录：app/{routers,services,templates,static}、tests/、deploy/、scripts/。
已按业务分离：解梦/日记/星座各有 router+service；公共层为 llm/sse/auth/db/astro/almanac。

2. 三大功能模块

- 解梦：入口 routers/dream.py（GET /dream、POST /api/interpret SSE、DELETE）；逻辑 services/dream.py（prompt+parse_dream）。数据=LLM 流+dreams 表。测试 test_dream.py（parse 单元+路由集成，LLM mock）。脆弱点：prompt 硬编码在源码、输出为纯文本需 NDJSON 解析、内容为敏感心理数据。
- 日记：routers/journal.py+services/journal.py（复用 parse_dream）。LLM+journals 表，测试 test_journal.py。脆弱点同上。
- 星座：routers/horoscope.py+services/{horoscope,astro,almanac}.py。确定性引擎（ephem/lunar-python）定分数/幸运/宜忌，LLM 仅写文案，horoscopes 表全局缓存。测试 test_engines.py+test_horoscope.py。脆弱点：依赖外部库 ephem/lunar。

3. 可评估性

- 确定性可测：astro/almanac 引擎、parse_dream、认证/哈希、限流配额、DB 迁移、SSE 帧——均已 mock 测试，80 用例全绿。
- 须 LLM-as-Judge：解梦/日记生成文案质量（共情/语气/安全）、星座文案。
- 外部依赖风险：ephem、lunar-python、LLM 可用性/时延/额度。
- 已有观测：app.* logger（main/llm/sse/alert）、LLM 连续失败 webhook（alert.py）、LOG_FILE 轮转日志、/healthz；无 metrics/埋点。

4. 接入评估的阻碍

stream_chat 是唯一 LLM 出口（易 mock）；prompt 与解析逻辑同文件耦合；评估需环境隔离（.env/SQLite/APP_ENV）；解梦/日记输入为自由中文文本、需构造语料；隐私：用户内容敏感、.env 含明文真实 key。

5. MVP 切入点

首选星座模块的确定性引擎（astro/almanac），评估类型=黄金集确定性回归（输入日期+星座→断言分数/幸运色/宜忌）。理由：唯一纯确定性输出、零 LLM 成本与抖动、已有 test_engines 脚手架与可复现数据；跑通后再扩展到解梦/日记的 LLM-as-Judge。

---（补充：全量测试当前 80 用例通过；test_me.py/test_db.py 亦已就绪。另提示 .env 真实 API key 明文在磁盘，评估环境勿直接复用。）