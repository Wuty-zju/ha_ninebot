# 开发进度与决策账本

唯一进度账本；当前实现摘要在[CURRENT_STATE](../agent/CURRENT_STATE.md)，正式变更面向用户写根[CHANGELOG](../../CHANGELOG.md)。
每个重大阶段增加一行“问题→决定→实现→验证/限制”；小修更新主题contract/已有行，不新建阶段Markdown。
证据与时间基线保留，未执行检查不写通过。下表是既有结果整理，不是本次重跑。

| 阶段 | 问题/决定 | 已完成结果 | 证据/后续边界 |
|---|---|---|---|
| v2.0 b0 | 旧OpenClaw不可用→固定ninecli受管理backend | 会话隔离/迁移/loopback、分组freshness | [初始记录](../archive/development/2.0-实施记录.md)、[验收](../evidence/v2-validation.json) |
| b1–b3 | raw与domain分层，最高/平均分开 | RawStore/严格Ride、LastRide实体 | [b3](../evidence/v2x-b3-validation.json)，CRS/逐点单位未知 |
| b4–b5 | 大历史不能进入state；新事件须去重 | Action response、baseline/持久化ack/Event | [b4](../evidence/v2x-b4-validation.json)、[b5](../evidence/v2x-b5-validation.json)，非完整历史/exactly-once |
| b6–b8 | 包身份不猜、HA兼容渐进、请求按需 | 包观察、compat、Image/GPS、context需求图 | [b6](../evidence/v2x-b6-validation.json)、[b8](../evidence/v2x-b8-validation.json)；child延期 |
| b9–b13 | 无效实体/可见性/REST-native cache分歧 | 精确清理、native发现、字段清单/安全调试 | [b11](../evidence/v2x-b11-validation.json)、[b13](../evidence/v2x-b13-validation.json) |
| b14–b16 | 错车/控制接受与物理完成不可混同 | 归属守卫、cloud最终鉴权、一次状态协调 | [b14](../evidence/v2x-b14-validation.json)、[b16](../evidence/v2x-b16-validation.json)；UNKNOWN仍未知 |
| b17–b18 | exit0/partial不证明解绑；减少重复全测 | cache恢复、每车profile时效、分级CI | [b17](../evidence/v2x-b17-validation.json)、[b18](../evidence/v2x-b18-validation.json) |
| b19–b23 / A–F | 已有Wh/W及月/调试/SMS/历史能力适配 | 默认可见、聚合日表、SMS、跨月cursor | [既有执行记录](../archive/development/v2x-实施与验收记录.md#b19b23af-连续开发完成记录)，462项/96.72%；SMS未实测 |
| b24 | SOC累计低信度/重复实体/selector错误 | 稳定额定参数、续航合一、精简、21语种 | [b24](../evidence/v2x-b24-validation.json)，464项/96.66%；未重跑全部HA矩阵 |
| NinePlus专项 | 更严格利用已有ninecli数据，避免抄未许可parser | 源码/alias/NR设计、既有日表关系核对 | [源码审阅](../evidence/nineplus-source-review.json)，9 server mock+78产品相关离线；无新云请求 |
| 工作区整理 | 多入口/版本稿/重复手册导致接手成本 | 分层入口、5主题contract、归档、统一字段表/进度 | [组织记录](../evidence/agent-workspace-validation.json)；只文档/工具，不发功能版本 |

## 下一轮候选与证据门槛

以下为设计，只有用户另行要求开发才实施；不是本轮自动goal。

| 顺序 | 工作 | 状态/验收门槛 |
|---|---|---|
| NR1 | raw source/version溯源、私有schema drift | 待开发；有界、安全名称/类型、无新state/raw泄露、fixtures可完成 |
| NR2 | RideDetail与严格merge/provenance | 待开发；ID/时间/单位冲突、恶意alias、fallback边界fixture |
| NR3 | Wh/km统计与条件性today | 待开发；有效距离/日序列、fresh/current month、失败unknown、ID稳定 |
| NR4 | 新BMS/status能力与正式remaining duration | 等非空schema/单位/枚举证据；不用未知字段造占位entity |
| 长期 | stable pack device、native backend、预测 | 独立范围/许可/身份/兼容证据；不承诺Native能取得未公开的完整历史 |

详细NR依据只在[NinePlus研究第19节](../research/NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md#19-按收益风险和证据重新排序的路线)。
原Phase0–10、A–F与NR编号不可混用；旧私有goal不应再次执行已完成功能。
跨会话的未提交工作/owner/检查命令写本地handoff，不粘进此账本。实际发布仍核对main/tag精确SHA与CI。
