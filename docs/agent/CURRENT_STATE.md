# 当前状态：接手时先核对 Git

状态日期：2026-10-05。此页是**唯一公共当前状态摘要**；实际branch/HEAD/dirty由启动命令读取，不硬编码本页所属提交。

| 范围 | 已核实基线 | 不代表什么 |
|---|---|---|
| 发布代码 | 2.0.0b24；main/tag基线 `9ba20bcadea1ea0500d9c080e60a48adc35e2387` | 不是本次网络重新核验，也不说明生产HA已更新 |
| 本地新增研究 | `5eee907` NinePlus专项文档，未推送/未发布；当前工作区整理从它继续 | docs分支不等于下一beta代码；不要假定main已有专项文档 |
| 依赖/平台 | ninecli==0.1.7；最低HA2026.1.0；21语言主要名称，en/简中完整 | 其它语言长帮助/错误部分英文fallback |
| 已完成 | 旧Phase0–8及A–F主体、b24精简 | 不是再执行旧goal/旧“未来”清单 |

当前安全/架构：受管理ninecli serve+随机Bearer loopback；密码body；不允许任意上游host。
车辆发现有必要的受控CLI例外以维护native缓存，不能机械删除或退回每请求subprocess。
按车/组freshness、需求context、partial failure、backoff、reauth、手动refresh/控制回读均已实现。

已有数据能力：bounded RawStore、Ride/严格trail、月汇总/次数/时长/日表Action、LastRide起止/距离/时长/max/avg/Wh；
get_trips/get_trip_detail/get_history、baseline防重复Event、Image/GPS opt-in、SMS、包身份及compat能力检测。
历史仅有界内存；月128次而返回20rows等缺页证据仍在，不能宣称全量历史。

最新行为见[b24契约](../v2x-实体精简与额定参数迁移.md)：

- 删除SOC采样充放电累计/质量/generation及冗余实体；保留固定ID的V/Ah额定能量，非SOH/电表。
- 续航统一precise>estimated>AI，三量仍可调试；不恢复三份重复实体。
- DeviceSelector修复model_vehicle；用户主动禁用/隐藏、名称/identity/Recorder不改写。
- 创建实体默认可见；位置/控制/调试功能仍有opt-in。

当前单位：ec=Wh、charging_power=W依据维护者确认；score不叫SOH。
服务器speed=最高速度；总平均=distance/duration。used_electricity、avg_speed真实含义、逐点速度/距离单位、CRS、非空remaining仍待验证。
本次NinePlus研究只读核对既有日表：30/31数字项、和匹配月总、9月20/20行匹配结束日day_total；支持条件性today，不代表全车型/App实测。

控制策略以[b15后续契约](../v2x-云端鉴权控制与实测契约.md)及当前源码为准：明确DENIED/歧义阻止；UNKNOWN保留未知，在显式用户配置/allowlist及fresh归属条件下交云最终鉴权。
旧“未知全部fail closed”的报告不是当前策略；也不是本次整理允许实车控制。

后续候选顺序：[NinePlus NR路线](../NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md#19-按收益风险和证据重新排序的路线)的raw溯源/安全drift→RideDetail/merge→Wh/km统计与valid今日里程。
充电时间、非nullnodes/权限、多包、CRS等守证据门槛；NativeBackend/预测仍是长期选题，不因本页自动启动开发。

最近验证是不同批次：

| 证据 | 真实范围 |
|---|---|
| [b24](../evidence/v2x-b24-validation.json) | 464项/96.66%附近coverage及静态/CI记录；多HA矩阵未重跑 |
| [NinePlus审阅](../evidence/nineplus-source-review.json) | server9项mock +产品78项相关离线；无新云/SMS/控制/生产写入 |
| [本次工作区整理](../evidence/agent-workspace-validation.json) | 4项工具检查、目录/引用/hash通过；未重跑产品测试 |

更新规则：发生实现、发布、证据状态改变时更新本页及相应contract/evidence，不覆盖历史测试数或旧raw。
使用 `git show main:custom_components/ninebot/manifest.json` 和实际Release/CI核实发布；发布授权与生产部署授权分开。
