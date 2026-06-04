# 香港租盘追踪器

语言：[繁體中文（香港）](README.zh-HK.md) | [简体中文](README.zh-CN.md)

香港租盘追踪器为一个运行于本地的香港租务市场分析工具及 Codex Skill。本项目旨在按区域或屋苑追踪美联物业、中原地产、香港置业及利嘉阁的租盘信息。系统将自动记录房源的首次出现时间、首次从来源下架时间、租金、实用面积、尺租、具体楼层与室号，以及其于各平台的历史动态。

当前版本已构建基础通用框架，包含本地任务管理、SQLite 数据库、站点适配器、网页解析、数据去重、每日扫描状态维护及报表导出等功能。各中介网站的具体搜索 URL 与网页元素选择器均集中于 `hk_rental_tracker/adapters/` 目录，以便于网站改版时进行集中调整。

有关完整扫描工作流的详细说明，请参阅 `docs/scanner-workflow.md`。该文档详细定义了任务创建、日常扫描、来源可靠度评估、验证信号及报告生成等规范，亦为后续封装 Codex Skill 的基础。

**免责声明：** 本项目为独立、非官方、本地运行的信息整理工具，仅供个人租务市场观察与分析；不提供地产代理、中介、撮合、推介、带看、议价、订约或收佣服务，亦不与任何地产代理公司存在从属、授权、背书或合作关系。使用者须自行核验租盘信息及任何 AI 输出，并确保其使用方式符合适用法律、相关网站的服务条款、授权范围及访问限制；不得用于违规自动化访问、批量抓取、绕过技术措施、商业转售或其他滥用。在法律允许范围内，因使用或滥用本工具产生的责任与后果由使用者自行承担。

## 安装指南

建议使用 Python 3.10 或以上版本：

```bash
git clone https://github.com/pseudoxx/hk-rental-tracker.git
cd hk-rental-tracker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

若需执行人工网页诊断工具 `verify-web`，请安装浏览器可选依赖项：

```bash
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

执行日常扫描、摘要生成及日终报告时，无需使用浏览器。

## Agent Skills

本仓库内置支持多个 AI 编程助手的 Markdown Skill：

```text
skills/hk-rental-tracker/
```

在支持本地 Skills 的环境中，可将该目录安装或链接为 `hk-rental-tracker` Skill。此 Skill 将指导 Agent 遵循本项目的安全边界规范：例如执行前应先阅读项目说明与扫描工作流，日常扫描中不默认调用浏览器，且遭遇来源请求失败或零结果时不应误判为房源下架。

## Agent 兼容性

`skills/hk-rental-tracker/SKILL.md` 的核心内容为 Markdown 格式的工作流说明，具备高可移植性。不同 Agent 的识别方式如下：

- **Codex**：使用 `skills/hk-rental-tracker/SKILL.md` 及 `skills/hk-rental-tracker/agents/openai.yaml`。
- **Claude Code**：可使用 `CLAUDE.md` 与 `.claude/skills/hk-rental-tracker/SKILL.md` 供自动发现。
- **OpenClaw**：自动读取工作目录下的 `skills/` 目录。本项目的 `SKILL.md` 已包含 `metadata.openclaw` 以便提取所需信息。
- **其他支持仓库指令的 Agent**：可读取 `AGENTS.md` 作为入口；若不支持自动读取，可手动将 `skills/hk-rental-tracker/SKILL.md` 的内容加入至该 Agent 的自定义指令中。

上述所有兼容入口均共享同一原则：日常扫描不默认执行浏览器验证，且来源异常不应解释为真实下架。

## 快速入门

### 使用 Agent Skill 启动

Clone 本仓库后，可直接用支持仓库指令或本地 Skills 的 Agent 打开此文件夹，然后用自然语言说明想启动租盘追踪即可，例如：

```text
开始
帮我建立一个启德两房租盘追踪
我想追踪日出康城三万以下的盘
```

只要语意是在建立、启动、初始化或操作香港租盘追踪任务，Agent 应读取 `AGENTS.md`、`README.md` 与 `skills/hk-rental-tracker/SKILL.md`，并进入租盘追踪任务初始化流程：先询问缺失的区域或屋苑、户型及必要预算条件，再按需建立 `tasks/<slug>/`、执行首次扫描及生成摘要。

### 使用 CLI 启动

若希望手动执行命令，可创建跟踪任务（请将 `<...>` 替换为实际参数）：

```bash
python3 -m hk_rental_tracker init-task --slug <slug> --area "<区域或屋苑>" --max-rent <最高租金> --min-area <最低实用面积> --max-area <最高实用面积> --min-gross-area <最低建筑面积> --max-gross-area <最高建筑面积> --min-building-age <最低楼龄> --max-building-age <最高楼龄> --max-psf <最高实用尺租> --layouts "<户型1,户型2>" --keywords "<关键词1,关键词2>"
```

执行后将生成以下目录结构：

```text
tasks/<slug>/
├── README.md
├── tracker.json
└── rental.db
```

首次扫描：

```bash
python3 -m hk_rental_tracker scan --task tasks/<slug> --mode initial
```

每日例行扫描：

```bash
python3 -m hk_rental_tracker scan --task tasks/<slug> --mode daily
```

查看数据摘要：

```bash
python3 -m hk_rental_tracker summarize --task tasks/<slug>
```

生成日终报告：

```bash
python3 -m hk_rental_tracker daily-report --task tasks/<slug>
```

查询当前活跃租盘：

```bash
python3 -m hk_rental_tracker query --task tasks/<slug> --active-only --limit 50
```

导出的文件将储存于 `tasks/<slug>/exports/`，包括：

- `active_listings.csv`
- `all_listings.csv`
- `latest_changes.csv`
- `summary.md`
- `daily_report_<YYYY-MM-DD>.md`
- `daily_report_latest.md`
- `daily_new_listings_<YYYY-MM-DD>.csv`
- `daily_removed_listings_<YYYY-MM-DD>.csv`
- `daily_source_disappeared_<YYYY-MM-DD>.csv`
- `daily_rent_changes_<YYYY-MM-DD>.csv`
- `daily_rent_decreases_<YYYY-MM-DD>.csv`
- `daily_watchlist_<YYYY-MM-DD>.csv`
- `daily_fresh_value_watchlist_<YYYY-MM-DD>.csv`
- `daily_budget_stats_<YYYY-MM-DD>.csv`
- `daily_withdrawal_lag_stats_<YYYY-MM-DD>.csv`
- `daily_stale_value_watchlist_<YYYY-MM-DD>.csv`

日终报告将依照尺租递增排序当日新增的租盘，并提供以下关键指标：

- **优先联络名单**：具备低尺租、预算友善，且经跨来源确认的新增租盘。
- **降价清单**：列出降价房源的降幅、比例、过往与当前租金、来源及本地盘龄。
- 各预算区间的活跃供应量及增减情况。
- 过去 30 天内来源下架纪录（按本地盘龄分组），以利观察网页撤盘延迟及市场消化速度。
- 盘龄逾 14 天且价格显著偏低的待复核清单（可能为已租未撤或引流的旧盘）。
- 本地盘龄统计，协助区分全新房源与长期挂牌房源。

## 任务配置说明

各任务的 `tracker.json` 可独立配置，关键字段说明如下：

- `area`：区域或屋苑名称。
- `area_aliases`：区域名称别名或繁简转换写法。
- `filters.max_rent`：最高租金限制。
- `filters.min_area_sqft` / `filters.max_area_sqft`：实用面积上下限。系统将于本地强制过滤，若来源 API 支持，则同步下推至 API 执行。
- `filters.min_gross_area_sqft` / `filters.max_gross_area_sqft`：建筑面积上下限。本地强制过滤，确认支持的 API 将同步下推。
- `filters.min_building_age_years` / `filters.max_building_age_years`：楼龄上下限。本地强制过滤，确认支持的 API 将同步下推。
- `filters.min_price_per_sqft` / `filters.max_price_per_sqft`：实用尺租上下限。根据 API 回传的尺租或本地计算结果进行过滤。
- `filters.layouts`：目标户型。
- `filters.keywords`：必须包含的关键词，用于精确锁定屋苑、地址、设施或描述。
- `filters.excluded_estates`：屋苑黑名单，符合此名单的房源将被排除。
- `filters.excluded_keywords`：通用关键词黑名单，用于排除特定区域、地址或描述。
- `sites`：启用的数据来源。默认包含 `midland`、`centanet`、`hkp` 及 `ricacorp`（利嘉阁）。
- `source_search_urls`：各来源的搜索入口 URL。若网站搜索页面变更，应优先于此处更新。

系统于执行筛选时，将尽可能将条件下推至来源 API 或公开搜索页，以减少分页请求与无效数据获取。数据入库前，本地端将再次执行严格复核。目前确认可下推过滤的条件包括：租金、户型、实用面积；美联/香港置业支持建筑面积与尺租；中原支持楼龄与尺租。利嘉阁使用公开服务器渲染租盘列表页，所有筛选均保留于本地端执行，直至稳定的网页参数获确认。未能确认稳定性的参数将仅于本地端进行过滤。

若只想扫描部分来源，可于建立任务时指定：

```bash
python3 -m hk_rental_tracker init-task --area "<区域或屋苑>" --layouts "<户型>" --sites "midland,centanet,hkp"
```

若需手动新增经验证的搜索页面：

```bash
python3 -m hk_rental_tracker add-url --task tasks/<slug> --site centanet --url "https://..."
```

## 数据字段定义

- `first_seen_at`：本地数据库首次记录该房源的时间。
- `first_delisted_at`：本地首次发现该房源从任一平台下架的时间。**请注意：此字段并非实际成交日期，亦不完全等同于真实下架日期。**
- `active`：若至少单一平台仍显示该房源，则标记为活跃（Active）。
- `source_state`：记录该房源于各平台的出现或下架状态。
- `ever_rent_decreased`：若本地纪录曾观察到该房源租金下调，则标记为 1。建议于查询或导出数据时包含此标记。
- `first_rent_decrease_at` / `last_rent_decrease_at`：首次与最近一次记录到租金下调的时间。

## 去重机制

系统将优先比对房源的专属编号或 URL。于跨平台比对时，系统将综合考量以下信息：

- 屋苑名称
- 座、层、室等单位具体信息
- 实用面积
- 租金
- 户型
- 标题相似度

若来源网站仅提供模糊的楼层信息（如“高层/中层/低层”）而缺乏完整室号，系统将调降合并信赖度，以避免误判。

## 自动化工作流建议

建议可利用 Codex 建立自动化脚本以定时执行每日扫描。日常扫描仅需执行并检查本地结果，**切勿**于默认流程中调用浏览器进行验证。完成每日最终扫描后，再行生成日终报告。脚本流程建议如下：

```text
于项目目录中执行 `python3 -m hk_rental_tracker scan --task tasks/<slug> --mode daily`，最后依需求执行 `python3 -m hk_rental_tracker daily-report --task tasks/<slug>`。检查扫描输出、最新 snapshots、exports/summary.md 及来源错误状态；若发生来源请求失败或结果为 0，请先报告异常，不应将其直接解释为真实下架。
```

日终报告默认仅存储于本地端。若需自动发送，提供以下选项：

- **Telegram 发送**：配置 `HK_RENTAL_TRACKER_TELEGRAM_BOT_TOKEN` 及 `HK_RENTAL_TRACKER_TELEGRAM_CHAT_ID` 环境变量，随后执行 `python3 -m hk_rental_tracker daily-report --task tasks/<slug> --send telegram`。
- **电子邮件发送**：配置 SMTP 相关环境变量，随后执行 `python3 -m hk_rental_tracker daily-report --task tasks/<slug> --send email`。

## 浏览器验证规范

`verify-web` 仅作为辅助人工诊断之用，主要应用于适配器开发或需获取网页截图存证的情境。于日常扫描、自动扫描及日终报告等常规流程中，**严禁**默认调用浏览器（如 Browser / Chrome）。

## 开发指南

执行测试：

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

提交代码前，请务必确认未包含任何本地任务数据。`.gitignore` 已默认排除以下项目：

- `tasks/*`
- `*.db`, `*.db-wal`, `*.db-shm`
- `.cache/`
- `.venv/`

## 合规性与使用声明

本系统仅供个人进行本地市场观察与分析之用。使用时应控制请求频率、保留来源链接，并严格遵守各网站的服务条款、授权范围、robots 指引及其他访问限制；如相关网站禁止或限制自动化抓取，使用者不得以本工具规避该等限制。请勿将 `first_delisted_at` 视为真实成交时间。系统默认不采集、不存储经纪人姓名及电话等个人资料，亦不持久化来源 API 或网页的原始数据（Payload）。

## 授权协议

本项目采用 MIT License 开源，详细内容请参阅 `LICENSE` 文件。
