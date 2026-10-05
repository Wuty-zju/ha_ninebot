# Raw数据、字段证据、调试与诊断

现行基线：b24。代码已存在RawStore，不重新执行旧Phase1。
唯一字段表是[FIELD_INVENTORY](../reference/FIELD_INVENTORY.md)；进度与新增设计理由放[PROGRESS](../development/PROGRESS.md)。

## 数据层与边界

NinecliBackend → BackendResult → RawRecord/RawStore → strict adapters/domain → coordinator → HA representation。
Raw与normalized分开，未理解字段先保留有界内存，不能因为model没有属性就无说明丢弃。
RawRecord包含endpoint、received_at、query_month、encoded payload、schema、shape、schema_fingerprint、
unknown/redacted计数、backend/parser版本、可空endpoint version。来源/version动态溯源仍是NR改进项。

| 限制 | 当前策略 |
|---|---|
| 全局raw缓存 | 8MiB保守预算、最多128记录、LRU；没有磁盘raw持久化 |
| detail | 最多8份、TTL900秒；month query缓存600秒 |
| 单响应/结构 | 1MiB；25000 nodes、depth12、schema256；超限为协议/预算错误 |
| 卸载/移除 | 清内存与对应车辆归属数据；旧cache不冒充新成功 |
| 读者 | payload独立副本；不让entity getter发请求或写缓存 |

secret/个人资料/图片及签名URL等在保留前删除；坐标/轨迹即使私有runtime中有，也不能由diagnostics输出。
unknown字段名本身可能含个人信息：运行时只导出审核过的schema名称/类型，不是所有key。
证据索引的hash证明文件未变，不证明字段单位/语义；recorded、脱敏替换和synthetic必须分别标注。

## HA表达与调试

| 数据 | 唯一适合的出口 |
|---|---|
| 已确认当前标量 | sensor/binary_sensor/number等；按真实量设置unit/class |
| 历史rides/日表/track | 有界Action response，见[TRAVEL](TRAVEL.md) |
| 新完成ride | 小摘要Event，不含GPS/raw |
| 大对象/未知schema | bounded runtime；diagnostics仅安全schema与计数 |
| 车型图片 | 安全URL→HA ImageEntity缓存 |
| 元数据/低价值重复字段 | DeviceInfo或安全调试摘要，不强行建entity |

调试选项已在初始/Options flow；每车格式化解析视图从已有snapshot生成，白名单、有限大小，不增加云查询。
raw_data_summary仅有缓存记录数及schema/unknown/redacted/bytes整数，不把完整raw放attributes。
创建实体默认可见，调试功能仍opt-in；用户主动禁用保持。名称不包含“原值/raw/experimental”，历史unique_id不改。

Diagnostics只输出版本/平台、固定endpoint支持、freshness、固定错误类型、成功时间、审核schema/field availability、
包计数与能力证据等主动安全结构；不含password/token/account/phone/SN、精确位置、trail、住址、任意错误原文。
async_redact_data只能作为补充，不替代出口设计。普通网络/5xx不生成Repair；不兼容Store/迁移冲突等用户可处理问题才Repair。

## 后续增量，不能假称已实现

NR优先评审source/version一致溯源与有界schema drift摘要；未知key不得直接输出或自动创建sensor。
fixture capture/replay只保存经审阅脱敏样本，并明确原始与替换字段；不能把静态field inventory当真实response fixture。
高级raw explorer、native transport、未知单位归一化需独立证据和范围，不由新field自动启用。
来源：自有recorded关系 → recon取证 → server透传 → NinePlus parser → 字段名推断。
[专项研究第9/19节](../research/NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md#9-当前-raw-层审计与明确改进方案)保留详细理由和待验证项；不默认全文加载。
