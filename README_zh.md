# Ninebot 九号 Home Assistant 集成

[English](README.md) · [发布版本](https://github.com/Wuty-zju/ha_ninebot/releases) · [开发入口](docs/README.md)

独立、非官方集成。v2使用固定 `ninecli==0.1.7` 的App协议替换OpenClaw；当前b50代码为预发布。
后端面向中国区App服务，其他地区未验证；云端API可用性由厂商决定。

## 安装与登录

HACS添加自定义集成仓库 `Wuty-zju/ha_ninebot`，选择测试版；也可复制 `custom_components/ninebot`。
安装后由用户自行重启HA，在“设备与服务”添加Ninebot。最低HA **2026.1.0**，需要兼容的64位ninecli wheel；
不支持ARMv7/32位。Linux/macOS/Windows包已发布不代表每个平台均经过实测。

支持密码和两步短信登录；候选会话隔离验证后提交，密码不入ConfigEntry或argv。
短信已离线验证，真实发送/验证码流程待验证。reauth/reconfigure保持同一账户。
支持添加多个不同账号，新版HA入口显示“添加账号”；条目按上游明确提供的信息显示“用户名：账号[服务地区]”，
缺少信息时回退到账号，不从国家/区号猜服务地区。自定义条目标题保留，登录读取用户名/地区；旧账号缺用户名时可在成功setup后进行一次显示信息补齐，不建立轮询或重试循环。
每条目独立私有token/config目录；同权限/高权限进程仍属于本地信任边界。
受管理serve只监听loopback并使用随机Bearer；native车辆发现命令负责准备业务路由缓存。
细节见[Backend与认证契约](docs/README.md)。

## 当前功能与选项

| 类别 | 当前能力 |
|---|---|
| 车辆状态 | 主电池电量、精准续航、预估续航、AI预估续航（诊断）、充电、电源、车辆锁与座桶锁 |
| BMS | 电压、温度、明确支持的循环、W充电功率、健康评分；评分不是SOH；应急通信电池电量、主电池类型、Apple查找支持 |
| 行程 | 今日/昨日与本月统计，最近行程起止/距离/时长/Wh/最高速度/总平均速度；时长默认h、两位小数 |
| 其它观测 | 电池存在、电门ACC开/关、智能服务有效/到期；未知编码显示未识别 |
| 原生HA表示 | 车型图片、可选GPS、骑行事件、历史查询Action response |
| 额定参数 | 可选V/Ah及一个稳定额定能量；b24已移除SOC累计充放电估算 |

创建实体默认启用/可见；用户主动禁用/隐藏保留。位置展示默认开启，原来明确关闭的选择保留；控制、调试、额定参数仍需功能选项。
名称无“原值/raw”，保留有意义的unique_id和历史。21种语言主要名称/设置/查询动作已本地化；
简中/英文完整，其他语言部分长帮助/错误以英文兜底。

默认status120秒、battery/travel600秒、profile3600秒；保留按车/组freshness、退避、需求context及partial failure。有界调度器共享同键在途读取，优先状态并兼顾其它车辆/后台读取；每账号网络事务1个、全局2个，读取与命令预算独立。取消一个读取者不影响其它读取者，卸载清理在途任务。暂时性GET故障在原deadline内最多额外尝试一次，控制命令不重播；缓存读取不续期，限流元数据与组级退避继续生效。
禁用全部BMS/行程消费者会停止常规对应查询，bootstrap/空匿名inventory稀疏发现及Event需求为有界例外。
不完整车辆列表不证明解绑，不给缺席车辆续期身份。详见[车辆/实体/刷新契约](docs/README.md)。

GPS需双坐标有效且位置展示开启，复用status数据、不增加定位请求；坐标系未确认不转换。图片使用审核过的匿名来源及HA缓存。
调试视图从已有snapshot有界派生，不额外拉云数据；diagnostics不含凭据、身份、精确位置或轨迹。
详见[Raw/诊断契约](docs/README.md)。

## 历史查询、事件与控制

`ninebot.get_trips/get_trip_detail/get_history`选择HA车辆设备并返回结构化response。
日图表、rides、trail不建成大批实体或放入state属性；本地分页只覆盖已返回集合，不保证上游全量。
跨月cursor仅有界内存，重启清空；轨迹同时需要coordinates与include_track，automation trace可能保存响应位置。
服务端最高速度与distance/duration总平均分开，逐点speed/delta单位仍待验证。
Event启动/re-enable建立baseline，不重放历史；分页/上传延迟可能漏报，不能保证exactly-once。
参数与示例见[行程/历史/事件契约](docs/README.md)。

控制必须显式开启、车辆allowlist、认证及身份/状态fresh；明确DENIED或歧义阻止。
UNKNOWN仍未知，满足本地条件后交云端最终鉴权。bell/buck/engine-start/engine-stop Button仅发一次，
原生车辆锁与座桶锁共享同一控制执行器，保留原buttons；start请求车辆解锁、stop请求上锁，停车/P条件由车辆与云端判定，集成不因缺少此遥测禁用stop。座桶仅支持远程解锁，上锁需手动操作。命令只发一次，在约0/2/5秒回读确认，并于10/20/25秒短暂跟随；不自动重发或乐观更新。接口接受或云端状态确认不证明物理动作。
详见[控制契约](docs/README.md)。开发期间不把这些说明当成测试授权。

## 升级与开发

升级前备份匹配的HA配置/storage；精确移除已审阅旧估算/重复ID，保留有意义ID、用户名称和Recorder历史。
b36引入规范生成ID，详见[迁移指南](docs/README.md#canonical-entity-ids-and-upgrade)。自定义或无法确认来源的ID保留；使用改名/移除ID的自动化需要调整。回滚须同时恢复对应旧配置/参数Store，不能只换代码或手改数据库。
初次迁移依据见[历史迁移矩阵](https://github.com/Wuty-zju/ha_ninebot/blob/9ba20bcadea1ea0500d9c080e60a48adc35e2387/docs/2.0-实体迁移矩阵.md)。

开发先读[集成说明](docs/README.md)和[产品约束](AGENTS.md)，再核对相关源码、测试及fixture来源。详细私有开发资料在本地独立工作区维护，不进入产品PR或安装包；[CHANGELOG](CHANGELOG.md)保存发布变化。小改做相关离线回归，重大功能边界再做综合兼容检查；旧绿色结果不代表本轮验收。


“今日里程”从校验通过的月日表投影，按上海业务日期和 travel 新鲜度显示。
必须有当日成功查询；跨午夜旧缓存、缺报或日表不一致时显示 unknown。
实体默认启用，用户禁用选择保留，不新增历史或详情轮询。

可选的[行程详情卡](docs/README.md#optional-ride-detail-card)提供本地日期分页、单趟指标和显式按需详情/单位未确认的采样曲线，与原生日历及统计图配合使用。需手动添加资源，不自动修改仪表盘。

本版会强制迁移已有实体ID；显示规则及账号页面标题限制见[b44升级说明](docs/README.md#b44-display-and-naming-update)。

[b45：日历、电量与行程显示](docs/README.md#b45-calendar-battery-and-ride-presentation)

日统计可使用与月报告核对吻合的本地累计行程，单次20条明细不再阻断已有完整档案；缺报或冲突仍显示未知，升级自动重建投影而不额外请求。见[b50统计说明](docs/README.md#b50-accumulated-daily-statistics)。主电池电量独立于应急通信电池；设备位置不再提供已弃用的battery_level属性，自动化请读取主电池电量实体。
