<div align="center">

# Ninebot for Home Assistant

**在 Home Assistant 中管理九号车辆、电池与骑行历史。**

[English](README.md) · 简体中文

[![Release](https://img.shields.io/github/v/release/Wuty-zju/ha_ninebot?include_prereleases&label=release&color=0ea5e9)](https://github.com/Wuty-zju/ha_ninebot/releases)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-2026.1.0%2B-18bcf2)](docs/guide_zh.md#环境要求)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom_repository-orange)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)
[![Checks](https://github.com/Wuty-zju/ha_ninebot/actions/workflows/checks.yaml/badge.svg?branch=main)](https://github.com/Wuty-zju/ha_ninebot/actions/workflows/checks.yaml)
[![License](https://img.shields.io/github/license/Wuty-zju/ha_ninebot)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Wuty-zju/ha_ninebot?style=flat&color=f5b301)](https://github.com/Wuty-zju/ha_ninebot/stargazers)
[![Total release downloads](https://img.shields.io/github/downloads/Wuty-zju/ha_ninebot/total?label=downloads&color=22c55e)](https://github.com/Wuty-zju/ha_ninebot/releases)

[安装](#安装) · [历史同步](#历史行程轨迹与详情同步) · [文档](docs/README.md) · [开发方向](docs/roadmap.md)

</div>

让九号车辆参与日常 HA 自动化：查看电量、充电与车辆状态，将历史行程、精细 GPS 轨迹与详细指标同步到私有本地存储。

> **2.0 预发布 · 独立社区集成。** 面向中国区 App 服务，未验证所有车型/其他地区。云端采集需互联网，已有本地历史可不抓云浏览。

## 可以做什么

| 能力 | HA 中的表现 |
|---|---|
| **了解车辆** | 主电池电量、精准/预估/AI 续航、充电、上电、ACC、智能服务和设备位置。 |
| **了解电池** | 电压、温度、支持的循环次数、充电功率、健康评分、电池类型/数量和独立通信电量。 |
| **留住历史** | 可重启续接的首次发现、增量同步、私有历史/轨迹证据、空间限额与较好几何保护。 |
| **保留详细信息** | 骑行起止、里程、时长、能耗、最高/平均速度及云端返回的 GPS 点，保留来源精度与可用性。 |
| **HA 原生能力** | 行程日历、骑行事件、响应 Action、显式日/月统计导入与标准图表。 |
| **控制与反馈** | 主动启用车辆锁/座桶锁和远程按钮，经归属检查、短期回读确认。 |

字段受车型/实际上报影响，健康评分**不是 SOH**，额定能量是规格。缺证据保持未知，不伪装成 0。[实体、单位与选项 →](docs/guide_zh.md#实体与数据)

## 安装

### HACS：推荐方式

1. HACS **⋮ → 自定义仓库**。
2. 添加 `https://github.com/Wuty-zju/ha_ninebot`，类型 **集成 Integration**。
3. 下载 Ninebot，选择所需 **2.0 prerelease**，必要时开启预发布版本选择。
4. 按提示重启 HA。
5. **设置 → 设备与服务 → 添加集成 → Ninebot**。

[![打开 HACS 仓库](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)
[![添加集成](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ninebot)

需先安装 HACS；目前以**自定义仓库**分发，不宣称已进入默认商店。

### 手动安装

下载 [Release](https://github.com/Wuty-zju/ha_ninebot/releases)，复制 `custom_components/ninebot` 到 `<config>/custom_components/ninebot`，重启后添加。运行/卡片资源随包提供，无需独立服务或另装卡片仓库。

**兼容要求：** HA **2026.1.0+**、兼容依赖 wheel 的 64 位平台及中国区云访问。不支持 ARMv7/32 位；其他地区/平台组合未验证。[完整安装说明 →](docs/guide_zh.md)

## 添加账号

密码或两步短信登录；每账号添加独立集成条目，会话/归属/历史隔离。重新认证/配置仍针对原账号。密码不保存在 ConfigEntry 或进程参数；短信有离线覆盖，实机验证待补。

选项包含位置、日历、详情保存/自动同步、历史预算/空间、控制/允许车辆、额定参数和调试。新账号默认保存/同步，已有关闭选择保留；控制默认关闭，需显式开启。[配置选项 →](docs/guide_zh.md#配置选项)

<a id="在地图上探索骑行历史"></a>

## 历史行程、轨迹与详情同步

在账号选项启用**后台保存行程详情**与**自动历史同步**。新账号默认开启，保留已有账号的关闭选择。

- **首次发现，后续增量：** 按月份/分页可续接地发现可用行程，复用已知记录，只补缺失/变化详情，共享账号请求预算。
- **详细行程：** 按实际返回保存起止时间、里程、时长、能耗、服务端最高速度和计算平均速度；保留来源精度，缺失不当零。
- **精细 GPS 轨迹：** 云端有详细轨迹时保存完整点及关联采样数据；后续简化/空响应不覆盖已保存的更好路线，不做未经验证的坐标转换。
- **本地结构化归档：** 业务原件与归一化事实保存在私有账号库，不塞进实体属性/Recorder；容量不足暂停写入，不静默淘汰历史。
- **进度与管理：** 每车同步进度、手动触发、暂停/恢复、预算化重试和本地完整性核验。详情进度只覆盖已发现行程，不保证云端全部历史。

通过原生日历与响应 Action 查询已保存信息。[同步、存储与查询说明 →](docs/history_zh.md#开启保存与同步)

可视化、UI 与自定义卡片目前均为**早期 alpha**，后续可能大幅完善、重构、修改或随时撤回，不作为稳定产品或公开 demo 展示。

## 2.0 重构了什么

将认证、原始证据、领域模型、实时更新和长期历史分层：

- **基础架构：** 隔离会话、受管理异步 I/O、按需 freshness 分组、在途合并、有界队列、退避容错。
- **HA 模型：** 翻译实体、独立续航、原生锁/日历/事件/图片和响应 Action，不为历史点创建实体。
- **历史生命周期：** 可续接/增量采集、事实与业务原件并存、几何保护、备份钩子与有界校验恢复。
- **本地查询基础：** 有界历史读取、可追溯的详情分块、分范围 continuation 版本和实测解析复用。
- **迁移与控制：** 已审核旧身份迁移、持久日志、显式启用、每次一次指令、有界回读。

[升级与回滚 →](docs/migration_zh.md) · [更新记录 →](CHANGELOG.md)

## 工作原理

```mermaid
flowchart LR
  Cloud[九号云] --> Adapter[账号适配层]
  Adapter --> Models[原始证据与领域模型]
  Models --> Live[实时协调器]
  Models --> Archive[私有历史归档]
  Live --> Entities[HA实体与自动化]
  Archive --> Views[本地查询、日历与统计]
```

云采集与本地浏览分开，实体按需求有界更新；历史详情在私有存储，不塞进 state/Recorder，只有显式汇总导入写历史统计。[架构与依赖披露 →](docs/architecture.md)

## 隐私与限制

- 位置默认开启，请检查选项。详情/HA 备份可含精确路线，保护备份及自动化 trace。
- 底图默认关闭，开启会向 OpenStreetMap 请求可见区域；不做未经验证的坐标转换。
- 逐点速度/距离单位未确认，曲线/播放按序号；最高与总平均速度分开。
- 详情百分比/核验月份/任务完成**不证明全部云历史**。简化或丢失的路线不能还原，本地仍有配额/磁盘限制。
- 控制需启用/允许列表/权限，车辆/云端决定条件；接受/观察不是物理保证。座桶只能远程解锁。

[排障 →](docs/guide_zh.md#常见问题) · [覆盖与统计 →](docs/history_zh.md#覆盖与统计)

## 后续方向

**已交付：** 历史行程/详情/GPS 同步、私有结构化存储、原生日历/统计、响应查询与有界恢复。

**持续开发：** 随时可能重构或撤回的早期 alpha 可视化/UI/卡片、更清晰的同步/覆盖反馈、更多有证据的实体/车型与实测存储优化。

**研究：** 已验证逐点指标、空间/几何扩展和更可审计的协议层，不承诺日期。[完整路线 →](docs/roadmap.md)

## 支持与贡献

先读[文档](docs/README.md)和[排障](docs/guide_zh.md#常见问题)。复现问题用 [Issues](https://github.com/Wuty-zju/ha_ninebot/issues)，使用/设计用 [Discussions](https://github.com/Wuty-zju/ha_ninebot/discussions)。提供版本与检查后的诊断，**不要上传凭据或完整私人轨迹**。

贡献者与 Agent：[贡献说明](CONTRIBUTING.md) · [开发地图](docs/development.md) · [AGENTS](AGENTS.md)。敏感问题读[安全政策](SECURITY.md)。小改相关离线检查，重大边界一次兼容矩阵；Checks 徽章不表示已跑全矩阵。

下载量徽章统计各 GitHub Release 附件的累计下载，不包含 HACS 源码包下载或 Git 克隆。

MIT 许可。Ninebot/Segway 商标/图片归原权利人，本项目无官方关联/支持。感谢 HA/HACS 社区及 [NOTICE](NOTICE)、[架构归属](docs/architecture.md#dependencies-and-attribution)、随包许可证列明的协议/库贡献。

<details>
<summary>旧版首页链接</summary>

- <a id="ninebot-九号-home-assistant-集成"></a>[Ninebot 九号 Home Assistant 集成](docs/history_zh.md)
- <a id="安装与登录"></a>[安装与登录](docs/guide_zh.md)
- <a id="当前功能与选项"></a>[当前功能与选项](docs/guide_zh.md)
- <a id="历史查询事件与控制"></a>[历史查询、事件与控制](docs/history_zh.md)
- <a id="升级与开发"></a>[升级与开发](docs/migration_zh.md)
- <a id="复用已验证的历史月份"></a>[复用已验证的历史月份](docs/history_zh.md)
- <a id="当前月份来源核验"></a>[当前月份来源核验](docs/history_zh.md)

</details>
