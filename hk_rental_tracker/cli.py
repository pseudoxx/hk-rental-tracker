from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import create_task_config, load_task_config, save_task_config
from .daily_report import generate_daily_report
from .exporter import export_task
from .normalization import compact_money, parse_int, split_terms
from .scanner import scan_task
from .storage import RentalStore
from .web_verify import verify_web_totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hk_rental_tracker", description="香港租盘本地追踪器")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-task", help="创建一个区域/屋苑追踪任务")
    init.add_argument("--slug", help="任务文件夹名，例如 target-market")
    init.add_argument("--root", default="tasks", help="任务根目录，默认 tasks")
    init.add_argument("--area", required=True, help="区域或屋苑")
    init.add_argument("--max-rent", type=int, help="最高租金")
    init.add_argument("--min-rent", type=int, help="最低租金")
    init.add_argument("--max-area", type=int, help="最高实用面积(呎)")
    init.add_argument("--min-area", type=int, help="最低实用面积(呎)")
    init.add_argument("--max-gross-area", type=int, help="最高建筑面积(呎)")
    init.add_argument("--min-gross-area", type=int, help="最低建筑面积(呎)")
    init.add_argument("--max-building-age", type=int, help="最高楼龄(年)")
    init.add_argument("--min-building-age", type=int, help="最低楼龄(年)")
    init.add_argument("--max-psf", type=float, help="最高实用尺租")
    init.add_argument("--min-psf", type=float, help="最低实用尺租")
    init.add_argument("--layouts", help="户型，例如 '1房,开放式'")
    init.add_argument("--estates", help="限制屋苑，逗号分隔")
    init.add_argument("--keywords", help="必须包含的关键词，逗号分隔")
    init.add_argument("--exclude-estates", help="排除屋苑黑名单，逗号分隔")
    init.add_argument("--exclude-keywords", help="排除关键词黑名单，逗号分隔")
    init.add_argument("--sites", help="来源，例如 'midland,centanet,hkp'")
    init.add_argument(
        "--ricacorp-authorized",
        action="store_true",
        help="兼容旧脚本；ricacorp 现在按标准公开列表来源处理，无需此参数",
    )
    init.add_argument("--notes", default="", help="备注")
    init.add_argument("--force", action="store_true", help="允许覆盖 tracker.json")

    add_url = sub.add_parser("add-url", help="给任务加入已确认的来源搜索页")
    add_url.add_argument("--task", required=True)
    add_url.add_argument("--site", required=True)
    add_url.add_argument("--url", required=True)

    scan = sub.add_parser("scan", help="运行首次或每日扫描")
    scan.add_argument("--task", required=True)
    scan.add_argument("--mode", default="daily", choices=["initial", "daily", "manual"])
    scan.add_argument("--render", action="store_true", help="用浏览器渲染 JavaScript 页面")
    scan.add_argument("--sites", help="只扫描部分来源，例如 centanet,hkp")
    scan.add_argument("--no-progress", action="store_true", help="不打印实时扫描进度")

    summarize = sub.add_parser("summarize", help="重新生成并显示摘要")
    summarize.add_argument("--task", required=True)

    query = sub.add_parser("query", help="查询本地租盘")
    query.add_argument("--task", required=True)
    query.add_argument("--active-only", action="store_true")
    query.add_argument("--limit", type=int, default=30)

    export = sub.add_parser("export", help="重新导出 CSV 和摘要")
    export.add_argument("--task", required=True)

    daily_report = sub.add_parser("daily-report", help="生成日终市场报告")
    daily_report.add_argument("--task", required=True)
    daily_report.add_argument("--date", help="报告日期 YYYY-MM-DD；默认使用最新一次成功扫描所在日期")
    daily_report.add_argument("--send", help="发送通道，例如 telegram,email；默认只保存本地文件")
    daily_report.add_argument("--print", action="store_true", help="生成后同时打印报告正文")

    verify_web = sub.add_parser("verify-web", help="核对网页端总数并保存截图")
    verify_web.add_argument("--task", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-task":
        return command_init_task(args)
    if args.command == "add-url":
        return command_add_url(args)
    if args.command == "scan":
        return command_scan(args)
    if args.command == "summarize":
        return command_summarize(args)
    if args.command == "query":
        return command_query(args)
    if args.command == "export":
        return command_export(args)
    if args.command == "daily-report":
        return command_daily_report(args)
    if args.command == "verify-web":
        return command_verify_web(args)
    parser.error("Unknown command")
    return 2


def command_init_task(args: argparse.Namespace) -> int:
    sites_arg, ricacorp_authorized = _resolve_ricacorp_authorization(args.sites, args.ricacorp_authorized)
    config = create_task_config(
        slug=args.slug,
        area=args.area,
        max_rent=args.max_rent,
        min_rent=args.min_rent,
        max_area_sqft=args.max_area,
        min_area_sqft=args.min_area,
        max_gross_area_sqft=args.max_gross_area,
        min_gross_area_sqft=args.min_gross_area,
        max_building_age_years=args.max_building_age,
        min_building_age_years=args.min_building_age,
        max_price_per_sqft=args.max_psf,
        min_price_per_sqft=args.min_psf,
        layouts=args.layouts,
        estates=args.estates,
        keywords=args.keywords,
        sites=sites_arg,
        excluded_estates=args.exclude_estates,
        excluded_keywords=args.exclude_keywords,
        ricacorp_authorized=ricacorp_authorized,
        notes=args.notes,
    )
    task_dir = Path(args.root) / config.slug
    if (task_dir / "tracker.json").exists() and not args.force:
        raise SystemExit(f"任务已存在：{task_dir}")
    save_task_config(task_dir, config)
    RentalStore(task_dir / "rental.db").close()
    (task_dir / "README.md").write_text(task_readme(config.slug), encoding="utf-8")
    (task_dir / "snapshots").mkdir(exist_ok=True)
    (task_dir / "exports").mkdir(exist_ok=True)
    print(f"已创建任务：{task_dir}")
    area_range = "-"
    if config.filters.min_area_sqft is not None or config.filters.max_area_sqft is not None:
        lower = str(config.filters.min_area_sqft) if config.filters.min_area_sqft is not None else "不限"
        upper = str(config.filters.max_area_sqft) if config.filters.max_area_sqft is not None else "不限"
        area_range = f"{lower}-{upper} 呎"
    gross_area_range = "-"
    if config.filters.min_gross_area_sqft is not None or config.filters.max_gross_area_sqft is not None:
        lower = str(config.filters.min_gross_area_sqft) if config.filters.min_gross_area_sqft is not None else "不限"
        upper = str(config.filters.max_gross_area_sqft) if config.filters.max_gross_area_sqft is not None else "不限"
        gross_area_range = f"{lower}-{upper} 呎"
    age_range = "-"
    if config.filters.min_building_age_years is not None or config.filters.max_building_age_years is not None:
        lower = str(config.filters.min_building_age_years) if config.filters.min_building_age_years is not None else "不限"
        upper = str(config.filters.max_building_age_years) if config.filters.max_building_age_years is not None else "不限"
        age_range = f"{lower}-{upper} 年"
    psf_range = "-"
    if config.filters.min_price_per_sqft is not None or config.filters.max_price_per_sqft is not None:
        lower = str(config.filters.min_price_per_sqft) if config.filters.min_price_per_sqft is not None else "不限"
        upper = str(config.filters.max_price_per_sqft) if config.filters.max_price_per_sqft is not None else "不限"
        psf_range = f"{lower}-{upper}"
    print(
        f"区域：{config.area}；租金上限：{compact_money(config.filters.max_rent)}；"
        f"实用面积：{area_range}；建筑面积：{gross_area_range}；楼龄：{age_range}；尺租：{psf_range}；"
        f"户型：{', '.join(config.filters.layouts) or '-'}；关键词：{', '.join(config.filters.keywords) or '-'}"
    )
    return 0


def _resolve_ricacorp_authorization(sites_arg: str | None, already_authorized: bool) -> tuple[str | None, bool]:
    return sites_arg, already_authorized


def command_add_url(args: argparse.Namespace) -> int:
    task_dir = Path(args.task)
    config = load_task_config(task_dir)
    urls = config.source_search_urls.setdefault(args.site, [])
    if args.url not in urls:
        urls.append(args.url)
    if args.site not in config.sites:
        config.sites.append(args.site)
    save_task_config(task_dir, config)
    print(f"已加入 {args.site} 搜索页：{args.url}")
    return 0


def command_scan(args: argparse.Namespace) -> int:
    progress = None if args.no_progress else lambda message: print(f"[scan-progress] {message}", flush=True)
    report = scan_task(
        args.task,
        mode=args.mode,
        render_javascript=args.render or None,
        sites=split_terms(args.sites) if args.sites else None,
        progress=progress,
    )
    print(f"扫描完成：run #{report.run_id}")
    print(f"记录到的来源观察：{report.inserted_observations}")
    print(f"标记来源消失：{report.missing_sources_marked}")
    for site_result in report.site_results:
        status = "OK" if site_result.ok else "FAILED"
        validation = next((item for item in report.site_validation if item.site == site_result.site), None)
        validation_text = ""
        if validation:
            validation_text = (
                f", 过滤后 {validation.filtered_observations}, "
                f"本地排除 {validation.rejected_observations}, "
                f"缺失标记 {'启用' if validation.missing_marking_enabled else '未启用'}"
            )
        print(f"- {site_result.site}: {status}, 页面 {len(site_result.fetched_urls)}, 原始观察 {len(site_result.observations)}{validation_text}")
        for error in site_result.errors:
            print(f"  error: {error}")
    print(f"报表目录：{Path(args.task) / 'exports'}")
    return 0 if report.ok else 1


def command_summarize(args: argparse.Namespace) -> int:
    task_dir = Path(args.task)
    store = RentalStore(task_dir / "rental.db")
    try:
        export_dir = export_task(task_dir, store)
        summary = export_dir / "summary.md"
        print(summary.read_text(encoding="utf-8"))
    finally:
        store.close()
    return 0


def command_query(args: argparse.Namespace) -> int:
    store = RentalStore(Path(args.task) / "rental.db")
    try:
        rows = store.active_listings(limit=args.limit) if args.active_only else store.all_listings()[: args.limit]
        if not rows:
            print("没有符合条件的本地租盘。")
            return 0
        print("租金 | 尺租 | 屋苑 | 座/层/室 | 户型 | 面积 | 曾降价 | 首见 | 来源记录数")
        print("--- | --- | --- | --- | --- | --- | --- | --- | ---")
        for row in rows:
            unit = " ".join(str(row[x] or "") for x in ["block", "floor", "flat"]).strip() or "-"
            psf = f"${row['last_price_per_sqft']:.1f}" if row["last_price_per_sqft"] else "-"
            print(
                " | ".join(
                    [
                        compact_money(row["last_rent_hkd"]),
                        psf,
                        row["estate_name"] or "-",
                        unit,
                        row["layout"] or "-",
                        str(row["usable_area_sqft"] or "-"),
                        "是" if row["ever_rent_decreased"] else "否",
                        row["first_seen_at"],
                        str(row["sources_count"] or 0),
                    ]
                )
            )
    finally:
        store.close()
    return 0


def command_export(args: argparse.Namespace) -> int:
    store = RentalStore(Path(args.task) / "rental.db")
    try:
        export_dir = export_task(args.task, store)
        print(f"已导出：{export_dir}")
    finally:
        store.close()
    return 0


def command_daily_report(args: argparse.Namespace) -> int:
    channels = split_terms(args.send) if args.send else []
    result = generate_daily_report(args.task, report_date=args.date, send_channels=channels, print_report=args.print)
    print(f"已生成日终报告：{result.markdown_path}")
    print("明细 CSV：")
    for label, path in result.csv_paths.items():
        print(f"- {label}: {path}")
    if result.sent_channels:
        print("已发送：" + ", ".join(result.sent_channels))
    else:
        print("发送：未配置，已保存到本地。")
    return 0


def command_verify_web(args: argparse.Namespace) -> int:
    output_dir, checks = verify_web_totals(args.task)
    print(f"交叉验证目录：{output_dir}")
    ok = True
    for check in checks:
        status = "OK" if check.matched and not check.error else "FAILED"
        print(f"- {check.site}: {status}, 网页端 {check.web_total}, 本地 {check.db_total}")
        if check.screenshot:
            print(f"  screenshot: {check.screenshot}")
        if check.frontend_screenshot:
            print(f"  frontend: {check.frontend_screenshot}")
        if check.frontend_filter_status:
            print(f"  frontend filters: {check.frontend_filter_status}")
        if check.error:
            print(f"  error: {check.error}")
        ok = ok and check.matched and not check.error
    return 0 if ok else 1


def task_readme(slug: str) -> str:
    return f"""# Rental task: {slug}

运行首次扫描：

```bash
python3 -m hk_rental_tracker scan --task tasks/{slug} --mode initial
```

运行每日扫描：

```bash
python3 -m hk_rental_tracker scan --task tasks/{slug} --mode daily
```

查看摘要：

```bash
python3 -m hk_rental_tracker summarize --task tasks/{slug}
```

生成日终报告：

```bash
python3 -m hk_rental_tracker daily-report --task tasks/{slug}
```

日终报告会列出今日租盘降价；查询和导出会标注曾经降价过的盘。

任务筛选条件保存在 tracker.json，可维护租金、实用面积、建筑面积、楼龄、实用尺租、户型、屋苑、包含关键词和排除关键词。

如果某个网站的默认搜索入口不准，使用：

```bash
python3 -m hk_rental_tracker add-url --task tasks/{slug} --site centanet --url "https://..."
```

日常扫描不需要浏览器验证；只有在调试站点适配器或用户明确要求网页证据时才运行 verify-web。
"""
