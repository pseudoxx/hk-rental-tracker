# 香港租盤追蹤器

語言：[繁體中文（香港）](README.zh-HK.md) | [简体中文](README.zh-CN.md)

香港租盤追蹤器為一本地運行之香港租務市場分析工具及 Codex Skill。本項目旨在按區域或屋苑追蹤美聯物業、中原地產、香港置業及利嘉閣之租盤資訊。系統將自動記錄房源之首次出現時間、首次從來源下架時間、租金、實用面積、呎租、具體樓層與室號，以及其於各平台之歷史動態。

當前版本已構建基礎通用框架，包含本地任務管理、SQLite 數據庫、站點適配器、網頁解析、數據去重、每日掃描狀態維護及報表導出等功能。各中介網站之具體搜索 URL 與網頁元素選擇器均集中於 `hk_rental_tracker/adapters/` 目錄，以便於網站改版時進行集中調整。

有關完整掃描工作流之詳細說明，請參閱 `docs/scanner-workflow.md`。該文檔詳細定義了任務創建、日常掃描、來源可靠度評估、驗證信號及報告生成等規範，亦為後續封裝 Codex Skill 之基礎。

**免責聲明：** 本項目為獨立、非官方、本地運行之資訊整理工具，僅供個人租務市場觀察與分析；不提供地產代理、中介、撮合、推介、帶看、議價、訂約或收佣服務，亦不與任何地產代理公司存在從屬、授權、背書或合作關係。使用者須自行核驗租盤資訊及任何 AI 輸出，並確保其使用方式符合適用法律、相關網站之服務條款、授權範圍及訪問限制；不得用於違規自動化訪問、批量抓取、繞過技術措施、商業轉售或其他濫用。於法律允許範圍內，因使用或濫用本工具產生之責任與後果由使用者自行承擔。

## 安裝指南

建議使用 Python 3.10 或以上版本：

```bash
git clone https://github.com/pseudoxx/hk-rental-tracker.git
cd hk-rental-tracker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

若需執行人工網頁診斷工具 `verify-web`，請安裝瀏覽器可選依賴項：

```bash
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

執行日常掃描、摘要生成及日終報告時，無需使用瀏覽器。

## Agent Skills

本倉庫內置支援多個 AI 編程助手之 Markdown Skill：

```text
skills/hk-rental-tracker/
```

於支援本地 Skills 之環境中，可將該目錄安裝或連結為 `hk-rental-tracker` Skill。此 Skill 將指導 Agent 遵循本項目之安全邊界規範：例如執行前應先閱讀項目說明與掃描工作流，日常掃描中不預設調用瀏覽器，且遭遇來源請求失敗或零結果時不應誤判為房源下架。

## Agent 兼容性

`skills/hk-rental-tracker/SKILL.md` 之核心內容為 Markdown 格式之工作流說明，具備高可移植性。不同 Agent 之識別方式如下：

- **Codex**：使用 `skills/hk-rental-tracker/SKILL.md` 及 `skills/hk-rental-tracker/agents/openai.yaml`。
- **Claude Code**：可使用 `CLAUDE.md` 與 `.claude/skills/hk-rental-tracker/SKILL.md` 供自動發現。
- **OpenClaw**：自動讀取工作目錄下之 `skills/` 目錄。本項目之 `SKILL.md` 已包含 `metadata.openclaw` 以便提取所需資訊。
- **其他支援倉庫指令之 Agent**：可讀取 `AGENTS.md` 作為入口；若不支援自動讀取，可手動將 `skills/hk-rental-tracker/SKILL.md` 之內容加入至該 Agent 之自定義指令中。

上述所有兼容入口均共享同一原則：日常掃描不預設執行瀏覽器驗證，且來源異常不應解釋為真實下架。

## 快速入門

### 使用 Agent Skill 啟動

Clone 本倉庫後，可直接用支援倉庫指令或本地 Skills 的 Agent 打開此資料夾，然後用自然語言說明想啟動租盤追蹤即可，例如：

```text
開始
幫我建立一個啟德兩房租盤追蹤
我想追蹤日出康城三萬以下的盤
```

只要語意是在建立、啟動、初始化或操作香港租盤追蹤任務，Agent 應讀取 `AGENTS.md`、`README.md` 與 `skills/hk-rental-tracker/SKILL.md`，並進入租盤追蹤任務初始化流程：先詢問缺失的區域或屋苑、戶型及必要預算條件，再按需建立 `tasks/<slug>/`、執行首次掃描及生成摘要。

### 使用 CLI 啟動

若希望手動執行命令，可建立跟蹤任務（請將 `<...>` 替換為實際參數）：

```bash
python3 -m hk_rental_tracker init-task --slug <slug> --area "<區域或屋苑>" --max-rent <最高租金> --min-area <最低實用面積> --max-area <最高實用面積> --min-gross-area <最低建築面積> --max-gross-area <最高建築面積> --min-building-age <最低樓齡> --max-building-age <最高樓齡> --max-psf <最高實用呎租> --layouts "<戶型1,戶型2>" --keywords "<關鍵詞1,關鍵詞2>"
```

執行後將生成以下目錄結構：

```text
tasks/<slug>/
├── README.md
├── tracker.json
└── rental.db
```

首次掃描：

```bash
python3 -m hk_rental_tracker scan --task tasks/<slug> --mode initial
```

每日例行掃描：

```bash
python3 -m hk_rental_tracker scan --task tasks/<slug> --mode daily
```

查看數據摘要：

```bash
python3 -m hk_rental_tracker summarize --task tasks/<slug>
```

生成日終報告：

```bash
python3 -m hk_rental_tracker daily-report --task tasks/<slug>
```

查詢當前活躍租盤：

```bash
python3 -m hk_rental_tracker query --task tasks/<slug> --active-only --limit 50
```

導出之文件將儲存於 `tasks/<slug>/exports/`，包括：

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

日終報告將依照呎租遞增排序當日新增之租盤，並提供以下關鍵指標：

- **優先聯絡名單**：具備低呎租、預算友善，且經跨來源確認之新增租盤。
- **降價清單**：列出降價房源之降幅、比例、過往與當前租金、來源及本地盤齡。
- 各預算區間之活躍供應量及增減情況。
- 過去 30 天內來源下架紀錄（按本地盤齡分組），以利觀察網頁撤盤延遲及市場消化速度。
- 盤齡逾 14 天且價格顯著偏低之待覆核清單（可能為已租未撤或引流之舊盤）。
- 本地盤齡統計，協助區分全新房源與長期掛牌房源。

## 任務配置說明

各任務之 `tracker.json` 可獨立配置，關鍵欄位說明如下：

- `area`：區域或屋苑名稱。
- `area_aliases`：區域名稱別名或繁簡轉換寫法。
- `filters.max_rent`：最高租金限制。
- `filters.min_area_sqft` / `filters.max_area_sqft`：實用面積上下限。系統將於本地強制過濾，若來源 API 支援，則同步下推至 API 執行。
- `filters.min_gross_area_sqft` / `filters.max_gross_area_sqft`：建築面積上下限。本地強制過濾，確認支援之 API 將同步下推。
- `filters.min_building_age_years` / `filters.max_building_age_years`：樓齡上下限。本地強制過濾，確認支援之 API 將同步下推。
- `filters.min_price_per_sqft` / `filters.max_price_per_sqft`：實用呎租上下限。根據 API 回傳之呎租或本地計算結果進行過濾。
- `filters.layouts`：目標戶型。
- `filters.keywords`：必須包含之關鍵詞，用於精確鎖定屋苑、地址、設施或描述。
- `filters.excluded_estates`：屋苑黑名單，符合此名單之房源將被排除。
- `filters.excluded_keywords`：通用關鍵詞黑名單，用於排除特定區域、地址或描述。
- `sites`：啟用的數據來源。預設包含 `midland`、`centanet`、`hkp` 及 `ricacorp`（利嘉閣）。
- `source_search_urls`：各來源之搜尋入口 URL。若網站搜尋頁面變更，應優先於此處更新。

系統於執行篩選時，將盡可能將條件下推至來源 API 或公開搜尋頁，以減少分頁請求與無效數據擷取。數據入庫前，本地端將再次執行嚴格覆核。目前確認可下推過濾之條件包括：租金、戶型、實用面積；美聯/香港置業支援建築面積與呎租；中原支援樓齡與呎租。利嘉閣使用公開伺服器渲染租盤列表頁，所有篩選均保留於本地端執行，直至穩定的網頁參數獲確認。未能確認穩定性之參數將僅於本地端進行過濾。

若只想掃描部分來源，可於建立任務時指定：

```bash
python3 -m hk_rental_tracker init-task --area "<區域或屋苑>" --layouts "<戶型>" --sites "midland,centanet,hkp"
```

若需手動新增經驗證之搜尋頁面：

```bash
python3 -m hk_rental_tracker add-url --task tasks/<slug> --site centanet --url "https://..."
```

## 數據欄位定義

- `first_seen_at`：本地數據庫首次記錄該房源之時間。
- `first_delisted_at`：本地首次發現該房源從任一平台下架之時間。**請注意：此欄位並非實際成交日期，亦不完全等同於真實下架日期。**
- `active`：若至少單一平台仍顯示該房源，則標記為活躍（Active）。
- `source_state`：記錄該房源於各平台之出現或下架狀態。
- `ever_rent_decreased`：若本地紀錄曾觀察到該房源租金下調，則標記為 1。建議於查詢或導出數據時包含此標記。
- `first_rent_decrease_at` / `last_rent_decrease_at`：首次與最近一次記錄到租金下調之時間。

## 去重機制

系統將優先比對房源之專屬編號或 URL。於跨平台比對時，系統將綜合考量以下資訊：

- 屋苑名稱
- 座、層、室等單位具體資訊
- 實用面積
- 租金
- 戶型
- 標題相似度

若來源網站僅提供模糊之樓層資訊（如「高層/中層/低層」）而缺乏完整室號，系統將調降合併信賴度，以避免誤判。

## 自動化工作流建議

建議可利用 Codex 建立自動化腳本以定時執行每日掃描。日常掃描僅需執行並檢查本地結果，**切勿**於預設流程中調用瀏覽器進行驗證。完成每日最終掃描後，再行生成日終報告。腳本流程建議如下：

```text
於專案目錄中執行 `python3 -m hk_rental_tracker scan --task tasks/<slug> --mode daily`，最後依需求執行 `python3 -m hk_rental_tracker daily-report --task tasks/<slug>`。檢查掃描輸出、最新 snapshots、exports/summary.md 及來源錯誤狀態；若發生來源請求失敗或結果為 0，請先報告異常，不應將其直接解釋為真實下架。
```

日終報告預設僅儲存於本地端。若需自動發送，提供以下選項：

- **Telegram 發送**：配置 `HK_RENTAL_TRACKER_TELEGRAM_BOT_TOKEN` 及 `HK_RENTAL_TRACKER_TELEGRAM_CHAT_ID` 環境變數，隨後執行 `python3 -m hk_rental_tracker daily-report --task tasks/<slug> --send telegram`。
- **電子郵件發送**：配置 SMTP 相關環境變數，隨後執行 `python3 -m hk_rental_tracker daily-report --task tasks/<slug> --send email`。

## 瀏覽器驗證規範

`verify-web` 僅作為輔助人工診斷之用，主要應用於適配器開發或需擷取網頁截圖存證之情境。於日常掃描、自動掃描及日終報告等常規流程中，**嚴禁**預設調用瀏覽器（如 Browser / Chrome）。

## 開發指南

執行測試：

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

提交代碼前，請務必確認未包含任何本地任務數據。`.gitignore` 已預設排除以下項目：

- `tasks/*`
- `*.db`, `*.db-wal`, `*.db-shm`
- `.cache/`
- `.venv/`

## 合規性與使用聲明

本系統僅供個人進行本地市場觀察與分析之用。使用時應控制請求頻率、保留來源連結，並嚴格遵守各網站之服務條款、授權範圍、robots 指引及其他訪問限制；如相關網站禁止或限制自動化抓取，使用者不得以本工具規避該等限制。請勿將 `first_delisted_at` 視為真實成交時間。系統預設不採集、不儲存經紀人姓名及電話等個人資料，亦不持久化來源 API 或網頁之原始數據（Payload）。

## 授權協議

本項目採用 MIT License 開源，詳細內容請參閱 `LICENSE` 文件。
