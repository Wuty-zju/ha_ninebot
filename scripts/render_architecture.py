"""Render the bilingual README architecture diagrams with the standard library.

The SVGs are documentation, not runtime resources or dashboard screenshots.
Run with --check in documentation CI to detect stale generated diagrams.
"""

import argparse
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/assets/architecture"


class Diagram:
    def __init__(self, title: str, description: str, height: int, language: str) -> None:
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="880" height="{height}" '
            f'viewBox="0 0 880 {height}" role="img" aria-labelledby="title desc" '
            f'xml:lang="{language}">',
            f'<title id="title">{escape(title)}</title>',
            f'<desc id="desc">{escape(description)}</desc>',
            """<defs>
  <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse">
    <path d="M0 0 L8 4 L0 8 Z" fill="#64748b"/>
  </marker>
</defs>
<style>
  text {font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans',
    'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', sans-serif; fill: #172b4d}
  .title {font-size: 27px; font-weight: 700}
  .label {font-size: 21px; font-weight: 650}
  .body {font-size: 17px; fill: #475569}
  .foot {font-size: 16px; fill: #475569}
  .page {fill: #f8fafc; stroke: #dbe4ef}
  .card {fill: #fff; stroke: #cbd5e1; stroke-width: 1.5}
  .blue {fill: #eff6ff; stroke: #93c5fd}
  .green {fill: #f0fdfa; stroke: #5eead4}
  .purple {fill: #faf5ff; stroke: #d8b4fe}
  .line {fill: none; stroke: #64748b; stroke-width: 2; marker-end: url(#arrow)}
  @media (prefers-color-scheme: dark) {
    text {fill: #e2e8f0}
    .body, .foot {fill: #cbd5e1}
    .page {fill: #111827; stroke: #334155}
    .card {fill: #1e293b; stroke: #475569}
    .blue {fill: #172b49; stroke: #3b82f6}
    .green {fill: #103b3b; stroke: #14b8a6}
    .purple {fill: #322345; stroke: #a78bfa}
  }
</style>""",
            f'<rect class="page" x="1" y="1" width="878" height="{height - 2}" rx="20"/>',
        ]
        self.text(36, 48, title, "title")

    def text(self, x: int, y: int, value: str, kind: str = "body") -> None:
        self.parts.append(f'<text class="{kind}" x="{x}" y="{y}">{escape(value)}</text>')

    def box(
        self,
        x: int,
        y: int,
        width: int,
        title: str,
        *lines: str,
        height: int = 105,
        color: str = "card",
    ) -> None:
        self.parts.append(
            f'<rect class="card {color}" x="{x}" y="{y}" '
            f'width="{width}" height="{height}" rx="12"/>'
        )
        self.text(x + 20, y + 32, title, "label")
        for index, line in enumerate(lines):
            self.text(x + 20, y + 60 + index * 24, line)

    def line(self, path: str, *, both: bool = False) -> None:
        start = ' marker-start="url(#arrow)"' if both else ""
        self.parts.append(f'<path class="line" d="{path}"{start}/>')

    def render(self) -> str:
        return "\n".join([*self.parts, "</svg>", ""])


def overview(zh: bool) -> str:
    d = Diagram(
        "账号隔离与数据分层" if zh else "Account isolation and data flow",
        "云端适配分为实时更新和历史采集；各账号独立保存会话与档案。"
        if zh
        else (
            "An account adapter feeds separate realtime and historical paths. "
            "Sessions and archives belong to each account."
        ),
        640,
        "zh-Hans" if zh else "en",
    )
    d.box(
        40,
        85,
        220,
        "九号云服务" if zh else "Ninebot cloud",
        "车辆与行程数据" if zh else "Vehicle and ride data",
        height=85,
        color="blue",
    )
    d.box(
        320,
        85,
        520,
        "账号会话与云端适配" if zh else "Account session and cloud adapter",
        "受管理本地 API · 历史专用分页"
        if zh
        else "Managed local API · history-specific pagination",
        "逐账号隔离 · 共享请求调度" if zh else "Account isolation · shared request scheduling",
        color="blue",
    )
    d.line("M 270 127 H 310", both=True)
    d.line("M 580 195 V 221")
    d.box(
        40,
        230,
        800,
        "原始数据与规范化模型" if zh else "Source evidence and normalized models",
        "保留来源、采集时间与精度；区分原值、计算值及缺报"
        if zh
        else (
            "Keep source, receipt time and precision; "
            "separate reported, calculated and missing values."
        ),
        height=85,
    )
    d.line("M 240 320 V 346")
    d.line("M 640 320 V 346")
    d.box(
        40,
        355,
        380,
        "实时协调器" if zh else "Realtime coordinator",
        "状态 · 电池 · 当前月份" if zh else "Status · battery · current month",
        "需求 · 新鲜度 · 退避" if zh else "Demand · freshness · backoff",
        color="blue",
    )
    d.box(
        460,
        355,
        380,
        "历史采集任务" if zh else "Historical acquisition",
        "月份 · 行程 · 详情 · 轨迹" if zh else "Months · rides · details · tracks",
        "断点 · 增量 · 请求预算" if zh else "Checkpoints · incremental work · budget",
        color="green",
    )
    d.line("M 230 465 V 491")
    d.line("M 650 465 V 491")
    d.box(
        40,
        500,
        380,
        "Home Assistant 实体" if zh else "Home Assistant entities",
        "传感器 · 锁 · 定位 · 图片 · 事件" if zh else "Sensors · locks · tracker · images · events",
        "当前状态与自动化" if zh else "Current state and automations",
        color="blue",
    )
    d.box(
        460,
        500,
        380,
        "账号私有档案" if zh else "Private account archive",
        "SQLite · 原始业务响应 · 行程模型" if zh else "SQLite · business evidence · ride models",
        "本地查询 · 日历 · 聚合统计" if zh else "Local queries · calendar · aggregates",
        color="green",
    )
    return d.render()


def history(zh: bool) -> str:
    d = Diagram(
        "增量采集、归档与本地读取" if zh else "Incremental acquisition and local history",
        "按月发现与分页校验后去重，复用稳定详情，按预算补取，事务保存，再通过本地接口读取。"
        if zh
        else (
            "Discover and validate month pages, deduplicate rides, reuse stable details, "
            "fetch missing data within budget and commit the archive before local access."
        ),
        690,
        "zh-Hans" if zh else "en",
    )
    d.box(
        40,
        85,
        360,
        "发现月份与分页" if zh else "Discover months and pages",
        "首次扫描 · 近期增量 · 缺口补扫" if zh else "Initial scan · recent updates · gap repair",
        "保存进度，重启后续接" if zh else "Persist progress and resume after restart",
        color="blue",
    )
    d.box(
        480,
        85,
        360,
        "校验列表与行程 ID" if zh else "Validate lists and ride IDs",
        "数量 · 去重 · 月份关系 · 覆盖" if zh else "Counts · deduplication · month membership",
        "保留响应与分页依据" if zh else "Retain response and page evidence",
        color="blue",
    )
    d.line("M 405 137 H 470")
    d.line("M 660 195 V 218 H 440 V 236")
    d.box(
        40,
        245,
        800,
        "只补取缺失、变化或尚未稳定的详情" if zh else "Fetch missing, changed or unsettled details",
        "复用稳定详情；共享账号预算；多车轮转；失败退避"
        if zh
        else (
            "Reuse stable details · share the account budget · "
            "rotate vehicles · back off on failure"
        ),
        height=85,
        color="green",
    )
    d.line("M 440 335 V 361")
    d.box(
        40,
        370,
        800,
        "事务化私有 SQLite 档案" if zh else "Transactional private SQLite archive",
        "规范化行程 + 压缩原始响应 + 收据、来源及采集时间"
        if zh
        else "Normalized rides + compressed business originals + receipts, source and capture time",
        "分别保存最新详情与较好轨迹；容量达到上限时暂停写入"
        if zh
        else (
            "Keep latest detail and best track separately; pause writes when the quota is reached."
        ),
        height=110,
        color="green",
    )
    d.line("M 230 485 V 521")
    d.line("M 650 485 V 521")
    d.box(
        40,
        530,
        380,
        "本地查询与轨迹分段" if zh else "Local queries and track chunks",
        "响应型 Actions · 授权 WebSocket" if zh else "Response Actions · authorized WebSocket",
        "读取本地档案，不触发云端补抓" if zh else "Local-only reads do not fetch the cloud",
        color="purple",
    )
    d.box(
        460,
        530,
        380,
        "HA 日历与长期统计" if zh else "HA calendar and statistics",
        "本地行程日历 · 按日/月聚合" if zh else "Local ride calendar · day/month aggregates",
        "显式导入 Recorder 统计" if zh else "Explicit import into Recorder statistics",
        color="purple",
    )
    d.text(
        40,
        665,
        "完整轨迹保留在档案中；实体只显示当前摘要。"
        if zh
        else "Full trails stay in the archive; entities expose current summaries.",
        "foot",
    )
    return d.render()


def controls(zh: bool) -> str:
    d = Diagram(
        "控制指令与状态回读" if zh else "Commands and observed state",
        "显式启用与车辆许可后只发送一次指令，随后短时回读；实体状态以云端观测更新，座桶只能远程解锁。"
        if zh
        else (
            "Explicitly enabled, allowlisted commands are sent once, "
            "followed by bounded status checks. State follows cloud observations; "
            "seat locking is manual."
        ),
        470,
        "zh-Hans" if zh else "en",
    )
    d.box(
        40,
        85,
        240,
        "用户明确开启" if zh else "Explicit opt-in",
        "车辆许可列表" if zh else "Vehicle allowlist",
        "账号归属与权限检查" if zh else "Ownership and access",
        color="blue",
    )
    d.box(
        320,
        85,
        240,
        "发送一次指令" if zh else "Send one command",
        "锁实体与按钮共用" if zh else "Locks and buttons share",
        "同一控制执行器" if zh else "one command executor",
        color="blue",
    )
    d.box(
        600,
        85,
        240,
        "车辆与云端处理" if zh else "Vehicle and cloud",
        "决定执行条件" if zh else "Apply operating rules",
        "返回接受或失败" if zh else "Accept or reject request",
        color="blue",
    )
    d.line("M 285 137 H 310")
    d.line("M 565 137 H 590")
    d.line("M 720 195 V 218 H 440 V 241")
    d.box(
        40,
        250,
        800,
        "短时提高状态查询频率" if zh else "Temporarily increase status checks",
        "约 0 / 2 / 5 秒确认目标；随后短时跟踪自动回锁"
        if zh
        else "Check the target at about 0 / 2 / 5 seconds; briefly follow automatic relocking.",
        "通过新的状态更新实体；整个后续跟踪最多约 30 秒"
        if zh
        else "Update entities from fresh observations; follow-up ends within about 30 seconds.",
        height=110,
        color="green",
    )
    d.text(
        40,
        404,
        "车辆锁：远程开启 / 关闭；座桶锁：远程打开、手动上锁。"
        if zh
        else "Vehicle lock: remote start / stop. Seat lock: remote open, manual close.",
        "foot",
    )
    d.text(
        40,
        435,
        "发送成功与已观测目标状态分别记录。"
        if zh
        else "Command acceptance and observed target state are recorded separately.",
        "foot",
    )
    return d.render()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = {
        f"{name}-{language}.svg": render(language == "zh")
        for name, render in (("overview", overview), ("history", history), ("controls", controls))
        for language in ("en", "zh")
    }
    if not args.check:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, text in expected.items():
            (OUTPUT / name).write_text(text, encoding="utf-8")
    else:
        stale = [
            name
            for name, text in expected.items()
            if not (OUTPUT / name).is_file() or (OUTPUT / name).read_text(encoding="utf-8") != text
        ]
        if stale:
            raise SystemExit("Regenerate architecture diagrams: " + ", ".join(stale))
    state = "match" if args.check else "written"
    print(f"Architecture diagrams: {len(expected)} bilingual SVGs {state}")


if __name__ == "__main__":
    main()
