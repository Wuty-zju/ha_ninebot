"""Generate accessible bilingual architecture SVGs in explicit light/dark palettes.

Edit the diagram content here. --check is deterministic and needs only stdlib.
README uses GitHub picture/source selection rather than CSS inside the SVG.
"""

import argparse
import re
import unicodedata
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/assets/architecture"
PALETTES = {
    "light": {
        "page": "#ffffff",
        "text": "#182536",
        "muted": "#475569",
        "border": "#cbd5e1",
        "line": "#64748b",
        "blue": "#edf5ff",
        "green": "#edf9f4",
        "purple": "#f5f1fc",
        "accent": "#245da8",
    },
    "dark": {
        "page": "#0d1117",
        "text": "#e6edf3",
        "muted": "#aebdce",
        "border": "#475569",
        "line": "#94a3b8",
        "blue": "#15263b",
        "green": "#142d28",
        "purple": "#28203a",
        "accent": "#91bfff",
    },
}


def text_width(value: str, size: int) -> float:
    """Conservative layout estimate; final SVGs also receive a rendered review."""
    return sum(
        size
        * (
            1
            if unicodedata.east_asian_width(c) in {"W", "F"}
            else 0.32
            if c.isspace()
            else 0.72
            if c.isupper()
            else 0.60
        )
        for c in value
    )


def wrap(value: str, width: int, size: int) -> list[str]:
    tokens = re.findall(r"[\u2e80-\uffff]|[^\s\u2e80-\uffff]+|\s+", value)
    result, current = [], ""
    for token in tokens:
        if current and text_width(current + token, size) > width:
            result.append(current.rstrip())
            current = token.lstrip()
        else:
            current += token
    if current.strip():
        result.append(current.rstrip())
    if any(text_width(line, size) > width for line in result):
        raise ValueError(f"Unbreakable diagram label: {value}")
    return result


# Each row has one full-width stage or two aligned parallel stages.
# A card contains a heading and two short descriptions, never implementation dumps.
CONTENT = {
    "overview": (
        ("Live state and historical data", "实时状态与历史数据"),
        (
            "Account isolation · shared request coordination · separate data lifecycles",
            "账号隔离 · 共享请求协调 · 独立的数据生命周期",
        ),
        [
            [
                (
                    "Ninebot cloud",
                    "九号云服务",
                    "Vehicles, status, battery and travel",
                    "车辆、状态、电池与行程",
                    "Account-authenticated access",
                    "账号认证访问",
                )
            ],
            [
                (
                    "Account & request coordination",
                    "账号与请求协调",
                    "Managed local service; controlled history pagination",
                    "受管理的本机服务；受控历史分页",
                    "Shared reads, bounded concurrency and backoff",
                    "共享读取、有界并发与退避",
                )
            ],
            [
                (
                    "Adaptive live polling",
                    "自适应实时调度",
                    "Vehicle/group deadlines and boosts",
                    "逐车逐组周期与短时加速",
                    "Shared reads, freshness and recovery",
                    "共享读取、有效期与恢复",
                ),
                (
                    "History acquisition",
                    "历史采集",
                    "Month pages and missing details",
                    "月份分页与缺失详情",
                    "Budgets, checkpoints and validation",
                    "额度、断点与校验",
                ),
            ],
            [
                (
                    "Home Assistant state",
                    "Home Assistant 状态",
                    "Sensors, locks, tracker and events",
                    "传感器、锁、定位与事件",
                    "Device pages and automations",
                    "设备页面与自动化",
                ),
                (
                    "Private account archive",
                    "私有账号档案",
                    "SQLite, source evidence and tracks",
                    "SQLite、来源证据与轨迹",
                    "Local queries, calendar and statistics",
                    "本地查询、日历与统计",
                ),
            ],
        ],
        (
            "Entities share reads; browsing saved history uses the local archive.",
            "实体共享查询结果；浏览已保存历史使用本地档案。",
        ),
    ),
    "history": (
        ("Incremental ride preservation", "行程增量保全"),
        (
            "Discover → validate → fill gaps → commit → reuse locally",
            "发现 → 校验 → 补齐缺口 → 提交 → 本地复用",
        ),
        [
            [
                (
                    "Discover and validate",
                    "发现与校验",
                    "Month scan, recent updates and reusable page evidence",
                    "月份扫描、近期增量与可复用的分页依据",
                    "Check counts, ride identities and month membership",
                    "核对数量、行程身份与月份关联",
                )
            ],
            [
                (
                    "Fetch missing details",
                    "补取缺失详情",
                    "Reuse saved details; rotate work across vehicles",
                    "复用已保存详情，在车辆间轮转任务",
                    "Shared budget, durable retries and restart checkpoints",
                    "共享额度、持久化重试与重启断点",
                )
            ],
            [
                (
                    "Commit the account archive",
                    "提交账号档案",
                    "Transactional rides, compressed sources and sync state",
                    "事务保存行程、压缩来源与同步状态",
                    "Latest facts and best geometry retained independently",
                    "最新标量与较好轨迹分别保留",
                )
            ],
            [
                (
                    "Local history access",
                    "本地历史访问",
                    "Paged Actions and track chunks",
                    "分页 Action 与轨迹分段",
                    "Authorized WebSocket and calendar",
                    "鉴权 WebSocket 与日历",
                ),
                (
                    "Summaries and trends",
                    "摘要与趋势",
                    "Month, today, yesterday and latest ride",
                    "本月、今日、昨日与最近行程",
                    "Explicit HA statistics import",
                    "显式导入 HA 长期统计",
                ),
            ],
        ],
        (
            "Capacity limits pause writes; retained records remain available locally.",
            "达到容量上限暂停写入；已保存记录仍可在本地读取。",
        ),
    ),
    "controls": (
        ("Commands and observed state", "控制指令与观察状态"),
        (
            "Shared executor · immediate confirmed updates · bounded follow-up",
            "共享执行器 · 确认后立即更新 · 有界跟随",
        ),
        [
            [
                (
                    "Authorize and send once",
                    "授权并发送一次",
                    "Locks and buttons share enablement, allowlist and access checks",
                    "锁与按钮共享启用、车辆许可及访问检查",
                    "A new command can replace an older confirmation observer",
                    "新指令可以替换旧的确认观察任务",
                )
            ],
            [
                (
                    "Accelerated status readback",
                    "加速状态回读",
                    "Up to 15 seconds; publish the observed transition immediately",
                    "最多十五秒；观察到状态变化立即发布",
                    "Distinguish acceptance, unconfirmed outcome and failure",
                    "区分接口接受、状态未确认与失败",
                )
            ],
            [
                (
                    "Vehicle lock",
                    "车辆锁",
                    "Remote stop locks; remote start unlocks",
                    "远程关闭上锁；远程开启解锁",
                    "Briefly follow automatic relocking",
                    "短时跟随自动回锁",
                ),
                (
                    "Seat lock",
                    "座桶锁",
                    "Verified unlock display held for 5 s",
                    "确认解锁后显示至少五秒",
                    "Observe latch up to 60 s; close locally",
                    "最多观察锁舌六十秒；本地上锁",
                ),
            ],
        ],
        (
            "Latch position does not establish lid position. "
            "Control commands are never replayed automatically.",
            "锁舌位置不能证明座桶盖是否打开。控制指令不自动重发。",
        ),
    ),
    "archive": (
        ("One account, one durable archive", "一个账号，一份长期档案"),
        (
            "Stable account identity · validated consolidation · portable snapshots",
            "稳定账号身份 · 校验整合 · 可迁移快照",
        ),
        [
            [
                (
                    "Authenticated account",
                    "认证账号",
                    "Stable identity and multiple vehicles",
                    "稳定身份与多辆车辆",
                    "Reconnect after account re-addition",
                    "重新添加同账号后接回档案",
                ),
                (
                    "Legacy or imported sources",
                    "旧库或导入来源",
                    "Owner, schema, hash and reference checks",
                    "归属、结构、摘要与引用校验",
                    "Remove copies after validated merge",
                    "校验合并成功后清理副本",
                ),
            ],
            [
                (
                    "Private account SQLite database",
                    "私有账号 SQLite 数据库",
                    "Rides, details, best geometry, source evidence and checkpoints",
                    "行程、详情、较好轨迹、来源证据与断点",
                    "Serialized worker, transactions, capacity limits and local audits",
                    "串行执行器、事务、容量限制与本地校验",
                )
            ],
            [
                (
                    "Remove and reconnect",
                    "移除与接回",
                    "History retained by default",
                    "默认保留历史",
                    "Opt out to remove managed history",
                    "取消保留后清理受管历史",
                ),
                (
                    "Export and merge",
                    "导出与合并",
                    "Choose accounts and vehicles",
                    "选择账号与车辆",
                    "Versioned ZIP and SHA256 manifest",
                    "版本化 ZIP 与 SHA256 清单",
                ),
            ],
        ],
        (
            "Saved records have no age-based expiry. "
            "Account archives, sessions and Recorder remain separate.",
            "已保存记录无按时间过期。账号档案、登录会话与 Recorder 分别管理。",
        ),
    ),
}


def render(name: str, language: str, theme: str) -> str:
    title, subtitle, rows, foot = CONTENT[name]
    index = language == "zh"
    palette = PALETTES[theme]
    parts: list[str] = []

    def text(x: int, y: int, value: str, size: int, color: str, weight: int = 400) -> None:
        parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'fill="{palette[color]}">{escape(value)}</text>'
        )

    def arrow(x1: int, y1: int, x2: int, y2: int) -> None:
        parts.append(
            f'<path d="M{x1} {y1} V{(y1 + y2) // 2} H{x2} V{y2}" '
            f'fill="none" stroke="{palette["line"]}" stroke-width="2"/>'
        )
        parts.append(
            f'<path d="M{x2 - 4} {y2 - 6} L{x2} {y2} L{x2 + 4} {y2 - 6}" '
            f'fill="none" stroke="{palette["line"]}" stroke-width="2"/>'
        )

    text(40, 46, title[index], 27, "text", 700)
    text(40, 77, subtitle[index], 16, "muted")
    y, previous = 112, []
    for number, row in enumerate(rows, 1):
        width = 880 if len(row) == 1 else 428
        cards = []
        height = 0
        for column, card in enumerate(row):
            heading = wrap(card[index], width - 48, 21)
            body = [
                line
                for value in (card[2 + index], card[4 + index])
                for line in wrap(value, width - 48, 17)
            ]
            height = max(height, 60 + len(heading) * 27 + len(body) * 25)
            cards.append((40 + column * 452, heading, body))
        centers = [x + width // 2 for x, _, _ in cards]
        if previous:
            old_y, old_centers = previous
            pairs = (
                zip(old_centers, centers, strict=True)
                if len(old_centers) == len(centers)
                else ((a, b) for a in old_centers for b in centers)
            )
            for start, end in pairs:
                arrow(start, old_y, end, y - 10)
        fill = "blue" if number == 1 else "purple" if number == len(rows) else "green"
        for column, (x, heading, body) in enumerate(cards):
            parts.append(
                f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
                f'rx="12" fill="{palette[fill]}" stroke="{palette["border"]}"/>'
            )
            stage = f"{number:02}" + (f" / {chr(65 + column)}" if len(row) > 1 else "")
            text(x + 24, y + 25, stage, 12, "accent", 700)
            baseline = y + 54
            for line in heading:
                text(x + 24, baseline, line, 21, "text", 650)
                baseline += 27
            baseline += 4
            for line in body:
                text(x + 24, baseline, line, 17, "muted")
                baseline += 25
        previous = (y + height, centers)
        y += height + 48
    for line in wrap(foot[index], 880, 16):
        text(40, y - 7, line, 16, "muted")
        y += 24
    height = y + 12
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="{height}" '
            f'viewBox="0 0 960 {height}" role="img" aria-labelledby="title desc" '
            f'xml:lang="{"zh-Hans" if index else "en"}">',
            f'<title id="title">{escape(title[index])}</title>',
            f'<desc id="desc">{escape(subtitle[index])}</desc>',
            '<style>text {font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", '
            '"Noto Sans", "PingFang SC", "Microsoft YaHei", sans-serif}</style>',
            f'<rect width="960" height="{height}" rx="16" fill="{palette["page"]}"/>',
            *parts,
            "</svg>",
            "",
        ]
    )


def outputs() -> dict[str, str]:
    return {
        f"{name}-{language}{'-dark' if theme == 'dark' else ''}.svg": render(name, language, theme)
        for name in CONTENT
        for language in ("en", "zh")
        for theme in PALETTES
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = outputs()
    if args.check:
        stale = [
            name
            for name, value in expected.items()
            if not (OUTPUT / name).is_file() or (OUTPUT / name).read_text(encoding="utf-8") != value
        ]
        if stale:
            raise SystemExit("Regenerate architecture diagrams: " + ", ".join(stale))
    else:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, value in expected.items():
            (OUTPUT / name).write_text(value, encoding="utf-8")
    state = "match" if args.check else "written"
    print(f"Architecture: {len(expected)} bilingual light/dark SVGs {state}")


if __name__ == "__main__":
    main()
