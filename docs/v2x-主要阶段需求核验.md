# v2.x 主要阶段需求核验

2026-10-04，核验范围为主要设计 Phase 0–8 与本轮连续开发目标。
本表基于当前源码/fixtures/本地完整测试，不将设计建议或 CI 配置当成执行成功。
每阶段准确 main SHA、远程 Checks/Hassfest/HACS 和 prerelease 的实际结果，必须
查对应 GitHub release notes；没有成功发布记录的阶段不能仅凭此表宣布交付。

## 逐级交付与证据

| 要求 | 实际实现与直接验证入口 | 发布 / 限制 |
|---|---|---|
| Phase 0 全字段评估、schema 与证据分层 | 主设计第5/19节，evidence/v2x-field-inventory.json；9份 recorded sanitized payload 和独立 selected image-shape fixture 的 metadata；test_raw 重放、test_image_urls | b1起；97历史已观察路径与别名/候选分开，不伪称未查询endpoint完整枚举 |
| Phase 1 有界 Raw 与 normalized 分离 | raw.py RawRecord/RawStore：1MiB单响应、8MiB全局含metadata预算、128记录、8详情/900秒、深度/节点/schema上限；backend.py；test_raw、test_coordinator | b1；仅内存，隐私字段主动移除；不提供无界落盘debug开关 |
| Phase 2 Ride/Track 数据理解 | ride_models.py、travel.py、v2x-行程字段与解析契约、travel-schema证据；test_travel/recorded replay | b2；真实trail四列结构，raw点速度/delta未知；无候选数组猜测或坐标转换 |
| Phase 3 正式最近行程表达 | sensor.py duration/TIMESTAMP start/end/SPEED servermax/overall average；test_ride_entities；中英strings/translations | b3；默认禁用，旧距离/能量raw IDs保留；无累计统计类或track attrs |
| Phase 4 历史response Actions | services.py、services.yaml、v2x-历史查询Actions契约、test_services；ONLY response、已知vehicle device_id、限页/最多5详情fanout、默认无GPS、scope重检查、卸载取消 | b4；本地分页仅对上游已返回集合，不能宣称全部历史；无新增WebSocket |
| Phase 5 Ride Event | event.py、ride_events.py、event_store.py、v2x-骑行事件契约；test_ride_events/test_event_pipeline | b5；完成证据+baseline、Store/version、disk acknowledgement后emit，重启/乱序/月边界/取消/旧backup去重；明确best-effort/通知可能丢失，不承诺exactly-once |
| Phase 6 电池模型/身份/兼容 | battery.py、adapters.py、compat.py、v2x-电池身份与设备模型契约、battery-field-review；test_battery/test_setup_entities/test_estimation/test_services | b6；支持位false不生成循环计数；单多包转换/乱序/身份消失不串值；物理身份未证实，child创建安全后置 |
| Phase 7 能力/权限/动作语义 | capabilities.py统一ControlDecision；coordinator availability/执行前后/diagnostics复用；test_capabilities、test_coordinator；v2x-权限门禁与能力证据契约 | b7；用户启用+allowlist+present+fresh+明确support/permission/semantics全满足；真实parser未证实，因此硬件控制拒绝，engine不等同Lock |
| Phase 8 图片/位置/原生UX | image.py、image_urls.py、device_tracker.py、demand.py；v2x-图片位置与请求依赖契约；test_image_urls/test_demand/test_setup_entities/test_coordinator | b8目标；同URL缓存、签名移除/HTTPS/不redirect、双坐标opt-in与Zone、禁用无需求组不丢内部依赖；发布以成功远程记录为准 |

### 数据使用覆盖的边界

四个历史 endpoint 每个已观察字段已在主设计矩阵/机器清单分类为 A–J；非空
month/detail 新字段在行程契约逐项补充，全部已观察 BMS 字段在电池契约复核。
图片三个字段补充于 Phase 8 契约：light/original 为 B/F（默认禁用Image和安全
picture metadata），dark为 H/I/J（字段存在但不自动新增重复图片实体，签名值
不保留）。URL没有物理单位、device/state class。未出现候选只留候选，不自动建
sensor；发现新schema仍须补该字段分类与证据。上述清单是有日期的已知覆盖，
不是无限车型/所有上游字段的完整保证。

## 不可退化要求核验

| 要求 | 当前直接证据 / 验证 |
|---|---|
| 生产默认ninecli、backend抽象、精确pin | manifest.json / raw.BACKEND_VERSION均0.1.7；NinebotBackend/NinecliBackend；2026-10-04复核PyPI仍0.1.7，未改为>=或下载未知binary |
| 认证loopback/密码/host边界 | client.py只监听loopback、随机Bearer环境传递、密码本机HTTP body、host/proxy环境清理、禁止redirect、队列/响应/超时有界；test_client/test_session/test_config_flow |
| 会话事务和密码不落ConfigEntry | session.py candidate验证同UID/提交/回滚/journal/recover/private权限；取消/换账号/symlink/失败unload测试；密码不长期存，token文件仍属需备份的凭据 |
| 分组调度稳定性 | coordinator.py per车/组success+error/next_attempt/expiry+jitterbackoff；test_coordinator覆盖partialfailure、deadline、manual coalescing、认证、取消/readback；demand只控制参与组 |
| 最新/旧HA兼容 | compat.py feature检测child API/owner/child route；Tracker公共导出；ONLY/Event/Image下限已有；CI固定2026.1.0、9.4、10b0各自plugin/Python，不比较版本字符串，不提高最低HA |
| 实体历史连续性 | entity.py legacy aliases、冲突Repair；migration.py双v1布局、参数/复制会话、旧估算generation不冒充新计量；test_migration/test_setup_entities/test_estimation；无registry/SQL清理 |
| 小状态、大对象查询 | 当前值Entity、历史ONLY响应、completed小summary事件、有界raw私有内存；GPS默认不返回detail，启用track还需位置选项；test_services/test_event_pipeline/test_raw；无每ride实体 |
| 隐私diagnostics | diagnostics.py白名单固定版本/平台/支持/freshness/错误类型/schema库存/安全决策/需求；raw schema仅审核字段名/类型/计数，无账号/序列号/token/图片签名/精确位置；test_raw/test_setup_entities |
| Repairs正确作用域 | 实体身份冲突、不可识别本地模型/事件存储、会话恢复等要求用户处理的问题保留Repair；普通暂时网络/cloud失败走退避；未知语义不生成假权限允许 |
| 配置和translations | config_flow user/reauth/reconfigure/options+migration测试；data为连接身份，options为功能策略；has_entity_name+translation_key，中英动作/实体/错误与icons在integration内；Phase8无新key |
| HA/HACS包边界 | 单一custom_components/ninebot；所有runtime Python/services/icons/translations/brand在集成目录；manifest.required字段、hacs最低版、LICENSE/NOTICE；Checks/Hassfest/HACS准确提交是发布门槛 |
| Gold参考和可维护性 | 原设计第17节逐项质量映射、分层async I/O/typing/lifecycle/flows/diagnostics/docs/测试；mypy有typed defs但并非宣称完整strict/Platinum，未经Core官方审核不宣称Gold认证 |
| 安全测试与发布 | 三层策略：纯单元+recorded replay为主，缺证据才有界只读；fake控制，阶段必要完整suite/静态检查/准确CI；每重大阶段PR/main递增prerelease，禁止force/reset/生产部署 |

## 明确后置，不能伪造验收

- **电池 Child Device**：当前真实返回无已核实的稳定pack SN及逻辑/物理组成。
  compat能力检测和旧车辆挂载完整保留；将合成pack设备强行加入生产会冒串历史
  风险。未来取得稳定身份/HA child语义证据后独立迁移与降级演练，不以安装新HA
  自动迁移，也不提高最低HA。
- **permissions/controls**：真实opaque/null不等于允许；未核实位图、共享用户
  权限及engine行为。所有真实硬件动作安全不可用，只读刷新正常；具体动作的
  实车验证仍须另获授权。不能通过fake测试宣称可开锁/启动。
- **单位/统计**：score不叫SOH，charging_power/ec/used_electricity无正式能量
  单位；轨迹speed/delta和服务端avg_speed不强转；月里程无证据不设
  TOTAL_INCREASING；不开Energy Dashboard虚假计量。
- **数据完整性/位置**：上游20条与raw times128不证明完整月份，local page不
  解决cloud pagination；未核实CRS、真实geofence偏差、全部车型图片和App UI。
  坐标展示opt-in，没有长轨迹state/recorder或自动转换。
- **更广平台/登录**：wheel发布不等于实测所有平台，新真实密码登录和refresh
  恢复仍须受控验证；现有只读会话测试不能替代这些硬件/协议验收。
- **Phase 9/10**：raw explorer收益不足，暂不开放任意未知字段、selector或
  subentry配置复杂度；NativePythonBackend是长期独立asyncSDK任务，recon仍为
  签名/加密/解析参考，有自测与已记录缺陷，不冒充完整status/BMS/controls SDK。

以上后置是证据约束的功能限制，不是删除目标或通过默认启用猜测功能来“完成”。
继续开发应从对应证据门槛开始；已交付阶段可独立review/revert，生产升级和回滚
由用户按双语README备份说明执行，开发过程不修改生产HA。
