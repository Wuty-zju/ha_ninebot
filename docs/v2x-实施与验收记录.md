# v2.x 实施与验收记录

## 2.0.0b1：Phase 0 / Phase 1 / Phase 7 安全门禁

基于 main `1bba9eb`，开发分支 `feature/v2x-raw-safety`。设计文档与机器字段
清单作为独立 v2.x 依据提交，保留历史报告基线。

- Phase 0：已留存七条历史 payload 转换为 `tests/fixtures/ninecli/0.1.7/`
  可重放样本；metadata 区分记录来源和合成坐标/身份/URL替换。97路径/86叶
  inventory 不等于 raw fixtures。非空 travel/detail 缺项明确，未新增云查询。
- Phase 1：RawStore 与 normalized snapshots 分离，未知业务字段可保留供后续
  解析；token/password/蓝牙secret/个人资料和URL先替换。private GPS 内存值
  不向 diagnostics 导出。缓存默认不落盘、不进入 entity attrs/state/recorder。
  保留数据预算8MiB、记录128、detail8/15分钟，HTTP仍1MiB；深度12、节点25000、
  schema路径256有界。预算含保守metadata估算，不声称Python进程总内存限制。
  CPU准备在executor，取消后不允许后台工作重新写入卸载缓存。raw超限拒绝计数，
  不使既有可归一化数据失效；仍显示旧raw成功时间，不伪装为当前raw。
- Phase 7 第一部分：三态support/permission+semantics/evidence。选项、allowlist、
  present、profile/status新鲜且无当前错误、动作证据全部通过才允许。排队后
  再检查门禁。当前生产parser不解释未知权限，因此所有硬件动作默认拒绝。
  不把 endpoint 存在当作车辆权限。旧Lock身份/状态保留，actuator返回翻译错误。
  权限真实解析、逐动作实车语义验证仍待证据；不能将mock当成真实授权。

实体 unique_id、ConfigEntry/schema、会话事务、分组轮询周期和backoff均保持。
minimum HA2026.1.0，依赖ninecli==0.1.7，HACS运行资源仅integration目录。
本阶段没有生产HA写入、重启、部署或真实控制。

验证记录在 `evidence/v2x-b1-validation.json`；发布前完整pytest、Ruff/format、
mypy及对应提交的Hassfest/HACS CI必须通过。下一阶段是travel domain/detail
查询与证据补齐，不提前将未确认单位变成正式物理实体。
