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
        "实时更新与历史采集" if zh else "Live updates and historical acquisition",
        "逐账号隔离，实时和历史分别调度，共享请求协调。"
        if zh
        else "Account-isolated sessions share bounded scheduling across live and historical work.",
        700,
        "zh-Hans" if zh else "en",
    )
    d.box(
        40,
        85,
        800,
        "01  九号云服务" if zh else "01  Ninebot cloud",
        "车辆 · 状态 · 电池 · 行程列表与详情"
        if zh
        else "Vehicles · status · battery · ride lists and details",
        height=85,
        color="blue",
    )
    d.line("M 440 170 V 205", both=True)
    d.box(
        40,
        215,
        800,
        "02  账号与请求协调" if zh else "02  Account and request coordination",
        "独立会话与授权 · 合并相同在途请求 · 有界并发与退避"
        if zh
        else "Isolated sessions · shared reads · bounded concurrency and backoff",
        height=85,
        color="blue",
    )
    d.line("M 240 300 V 335")
    d.line("M 640 300 V 335")
    d.box(
        40,
        345,
        380,
        "03A  实时更新" if zh else "03A  Realtime updates",
        "逐车、逐数据组刷新" if zh else "Per-vehicle and per-group scheduling",
        "需求 · 响应年龄 · 部分失败恢复" if zh else "Demand · response age · partial recovery",
        color="blue",
    )
    d.box(
        460,
        345,
        380,
        "03B  历史采集" if zh else "03B  History acquisition",
        "月份与分页 · 缺失详情 · 增量" if zh else "Month pages · missing details · updates",
        "账号预算 · 断点 · 校验" if zh else "Account budget · checkpoints · validation",
        color="green",
    )
    d.line("M 230 450 V 485")
    d.line("M 650 450 V 485")
    d.box(
        40,
        495,
        380,
        "04A  当前状态" if zh else "04A  Current state",
        "实体 · 位置 · 状态事件" if zh else "Entities · location · state events",
        "原生 HA 自动化与设备页面" if zh else "Native HA automations and device pages",
        color="purple",
    )
    d.box(
        460,
        495,
        380,
        "04B  本地账号档案" if zh else "04B  Local account archive",
        "SQLite · 来源证据 · 较好轨迹" if zh else "SQLite · source evidence · best track",
        "查询 Action · 日历 · 统计导入" if zh else "Query Actions · calendar · statistics import",
        color="green",
    )
    d.text(
        40,
        650,
        "实体共享查询结果；浏览已保存历史使用本地档案。"
        if zh
        else "Entities share reads. Browsing saved history uses the local archive.",
        "foot",
    )
    return d.render()


def history(zh: bool) -> str:
    d = Diagram(
        "行程同步与本地复用" if zh else "Ride synchronization and local reuse",
        "校验分页后增量补详情，事务保存标量、轨迹和来源，供本地查询。"
        if zh
        else "Validated pages feed incremental detail acquisition and transactional local storage.",
        780,
        "zh-Hans" if zh else "en",
    )
    d.box(
        40,
        85,
        380,
        "01  发现与补扫" if zh else "01  Discover and reconcile",
        "首次月份扫描 · 近期增量" if zh else "Initial month scan · recent updates",
        "复用已验证分页 · 保存断点" if zh else "Reuse verified pages · persist checkpoints",
        color="blue",
    )
    d.box(
        460,
        85,
        380,
        "02  校验覆盖" if zh else "02  Validate coverage",
        "总数 · 行程 ID · 月份关系" if zh else "Counts · ride IDs · month membership",
        "重复／缺页进入补扫" if zh else "Repeated or missing pages need repair",
        color="blue",
    )
    d.line("M 420 137 H 450")
    d.line("M 650 190 V 220 H 440 V 240")
    d.box(
        40,
        250,
        800,
        "03  只补取需要的详情" if zh else "03  Fetch only details that need work",
        "稳定详情复用 · 车辆轮转 · 共享额度 · 失败重试"
        if zh
        else "Reuse stable details · rotate vehicles · share request budget · retry gaps",
        height=85,
        color="green",
    )
    d.line("M 440 335 V 370")
    d.box(
        40,
        380,
        800,
        "04  事务提交至账号 SQLite" if zh else "04  Commit to the account SQLite archive",
        "规范化行程 + 压缩来源 + 分页依据 + 同步状态"
        if zh
        else "Normalized rides + compressed sources + page evidence + sync state",
        "最新标量与较好轨迹分别保留；达到容量上限暂停写入"
        if zh
        else "Keep latest facts and best geometry separately; pause writes at the capacity limit",
        height=110,
        color="green",
    )
    d.line("M 240 490 V 530")
    d.line("M 640 490 V 530")
    d.box(
        40,
        540,
        380,
        "05A  本地读取" if zh else "05A  Local reads",
        "响应 Action · 分页与轨迹分段" if zh else "Response Actions · pages and track chunks",
        "授权 WebSocket · 日历" if zh else "Authorized WebSocket · calendar",
        color="purple",
    )
    d.box(
        460,
        540,
        380,
        "05B  摘要与趋势" if zh else "05B  Summaries and trends",
        "本月／今日／昨日／最近摘要" if zh else "Month · today · yesterday · latest ride",
        "显式导入 HA 长期统计" if zh else "Explicit import to HA long-term statistics",
        color="purple",
    )
    d.text(
        40,
        700,
        "完整轨迹保存在档案中；实体展示小体积的当前摘要。"
        if zh
        else "Full trails stay in the archive; entities expose compact current summaries.",
        "foot",
    )
    d.text(
        40,
        732,
        "本地浏览与云端补取分别调度。"
        if zh
        else "Local browsing and cloud acquisition have separate request paths.",
        "foot",
    )
    return d.render()


def controls(zh: bool) -> str:
    d = Diagram(
        "控制指令与状态确认" if zh else "Commands and state confirmation",
        "一次发送，十五秒内确认；座桶解锁显示保持五秒并有界观察锁舌。"
        if zh
        else "One command, a 15-second confirmation window and bounded seat-latch observation.",
        635,
        "zh-Hans" if zh else "en",
    )
    d.box(
        40,
        85,
        380,
        "01  控制请求" if zh else "01  Control request",
        "Lock 实体与按钮共用执行器" if zh else "Locks and buttons share one executor",
        "开关 · 车辆许可 · 归属与权限" if zh else "Enablement · allowlist · ownership/access",
        color="blue",
    )
    d.box(
        460,
        85,
        380,
        "02  发送一次" if zh else "02  Send once",
        "车辆／云端判断执行条件" if zh else "Vehicle/cloud applies operating rules",
        "新指令可替换旧确认观察" if zh else "New commands replace older observers",
        color="blue",
    )
    d.line("M 420 137 H 450")
    d.line("M 650 190 V 220 H 440 V 240")
    d.box(
        40,
        250,
        800,
        "03  最多十五秒加速回读" if zh else "03  Accelerated readback for up to 15 seconds",
        "新状态一经确认立即显示 · 不自动重发控制指令"
        if zh
        else "Publish the confirmed transition immediately · no automatic command replay",
        "分别记录接受、未确认与失败"
        if zh
        else "Keep acceptance, unconfirmed outcome and failure distinct",
        height=110,
        color="green",
    )
    d.line("M 240 360 V 400")
    d.line("M 640 360 V 400")
    d.box(
        40,
        410,
        380,
        "04A  车辆锁" if zh else "04A  Vehicle lock",
        "上锁＝远程关闭；解锁＝远程开启" if zh else "Lock = remote stop; unlock = remote start",
        "短期跟随自动回锁" if zh else "Brief follow-up observes auto-relocking",
        color="purple",
    )
    d.box(
        460,
        410,
        380,
        "04B  座桶锁" if zh else "04B  Seat lock",
        "确认解锁后显示至少五秒" if zh else "Hold a verified unlock display for 5 seconds",
        "锁舌最多观察六十秒；本地上锁" if zh else "Observe latch up to 60 seconds; close locally",
        color="purple",
    )
    d.text(
        40,
        565,
        "锁舌上报与座桶盖是否打开是两种不同信息。"
        if zh
        else "A latch report does not establish whether the compartment lid is open.",
        "foot",
    )
    d.text(
        40,
        597,
        "读取异常、新指令或卸载会结束对应的后续观察。"
        if zh
        else "Read errors, replacement commands or unloading end the corresponding observer.",
        "foot",
    )
    return d.render()


def archive(zh: bool) -> str:
    d = Diagram(
        "账号数据库的生命周期" if zh else "Account archive lifecycle",
        "认证账号拥有唯一主库；旧来源校验合并；条目移除与导出导入分开管理。"
        if zh
        else "One account archive across entry removal, upgrades and transfers.",
        720,
        "zh-Hans" if zh else "en",
    )
    d.box(
        40,
        85,
        380,
        "账号认证" if zh else "Authenticated account",
        "稳定账号身份 · 多辆车辆" if zh else "Stable account identity · multiple vehicles",
        "删除重加可接回原档案" if zh else "Reconnect after removal and sign-in",
        color="blue",
    )
    d.box(
        460,
        85,
        380,
        "旧库与导入来源" if zh else "Legacy and imported sources",
        "隔离升级 · 归属／摘要／引用校验" if zh else "Isolated upgrade · owner/hash/link checks",
        "去重合并后清理已验证副本" if zh else "Merge, then remove verified source copies",
        color="blue",
    )
    d.line("M 230 190 V 220 H 330 V 250")
    d.line("M 650 190 V 220 H 550 V 250")
    d.box(
        40,
        260,
        800,
        "一个账号，一个私有 SQLite 主库" if zh else "One private SQLite database per account",
        "行程与月份 · 最新详情 · 较好轨迹 · 压缩原始证据 · 同步断点"
        if zh
        else "Rides · latest details · best geometry · compressed evidence · checkpoints",
        "串行数据库执行器 · 事务提交 · 容量限制 · 本地校验"
        if zh
        else "Serialized database worker · transactions · capacity limit · local verification",
        height=110,
        color="green",
    )
    d.line("M 230 370 V 415")
    d.line("M 650 370 V 415")
    d.box(
        40,
        425,
        380,
        "移除账号" if zh else "Remove an account",
        "默认保留；同账号再次登录复用" if zh else "Retain by default; reuse on same-account login",
        "取消保留则清理受管历史" if zh else "Opt out to delete managed account history",
        color="purple",
    )
    d.box(
        460,
        425,
        380,
        "导出与恢复" if zh else "Export and restore",
        "按账号／车辆选择一致性快照" if zh else "Consistent snapshots by account/vehicle",
        "ZIP + SHA256 清单 · 校验后合并" if zh else "ZIP + SHA256 manifest · validated merge",
        color="purple",
    )
    d.text(
        40,
        590,
        "失败或未知来源保留待处理；已保存记录无按时间自动过期。"
        if zh
        else "Failed sources stay for review. Saved records have no age-based expiry.",
        "foot",
    )
    d.text(
        40,
        624,
        "账号档案、登录会话与 HA Recorder 分别管理。"
        if zh
        else "Account archives, sign-in sessions and HA Recorder have separate lifecycles.",
        "foot",
    )
    d.text(
        40,
        658,
        "升级前备份完整 HA；导出包可用于跨机器迁移。"
        if zh
        else "Back up HA before upgrading; use exports for migration between installations.",
        "foot",
    )
    return d.render()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = {
        f"{name}-{language}.svg": render(language == "zh")
        for name, render in (
            ("overview", overview),
            ("history", history),
            ("controls", controls),
            ("archive", archive),
        )
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
