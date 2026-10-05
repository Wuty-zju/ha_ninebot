# 按问题定位代码与证据

不必按文件名顺序通读；`scripts/agent_context.py --topic ...` 从[catalog](catalog.json)给出以下路径的具体最小集合。

| 主题 | 主要代码职责 | 合同/证据入口 |
|---|---|---|
| raw | backend.py/client.py返回；coordinator捕获；raw.py限制/安全schema；diagnostics.py/debug_view.py出口 | NinePlus第9/19节、当前字段清单、nineplus-source-review；不要先加载两份136路径长表 |
| travel | travel.py/ride_models.py解析/合并；month_summary.py日表与coverage；services/history_actions/history按需query；ride_events/event_store去重 | 行程契约+Historical Actions+Event；日表旧文本判断由NinePlus核验纠正，Wh/W由b19覆盖早期unknown |
| battery | adapters/battery身份；entity/registry/compat绑定；storage/battery_parameters只存规格 | 电池身份+b24；不是旧estimation.py（已退役） |
| auth | config_flow/session/client/runtime；data/options隔离，reauth/reconfigure/SMS | 全面适配D+输入输出契约；SMS真实未测试 |
| controls | capabilities、control_results、button、coordinator/status identity | b15云鉴权→b16回读→native status归属；原Phase7只有历史适用范围 |
| entities | entity/migration/registry/sensor/binary/number + strings/translations/icons | b24优先，旧2.0实体矩阵仅迁移基线；保留仍有效ID |
| polling | demand/coordinator、image/image_urls、device_tracker | 图片/GPS/需求graph；现行per-group退避不替换成App刷新节奏 |
| release | manifest/hacs.json、pyproject、checks/hassfest/hacs workflows | 分级测试、版本实施记录、具体版本validation；本地报告与remoteCI分开 |

运行数据链：

`native API → ninecli → authenticated loopback/client → BackendResult → RawStore + strict adapters → domain → coordinator freshness/demand → entities/Actions/Event`

文件定位先 `rg -n 'symbol' custom_components tests`；测试先看同主题test文件，不从fixtures遍历开始。
实体/Action/API目录都在集成目录，安装运行不依赖repo root脚本、references或本机private资料。

| 要回答的问题 | 所需证据 | 不足时的处理 |
|---|---|---|
| 字段真实出现吗 | fixture metadata、recorded shape或schema inventory | N-only alias留候选，不升级为正式字段 |
| 单位/枚举真的吗 | 原始关系、recon实际取证、维护者单位确认 | 返回raw/unknown；不猜W/%/CRS |
| parser安全吗 | recorded replay +恶意/边界synthetic tests | 问题定位在source+fixture，不重复云查询 |
| 实体已实现吗 | current源码、translation_key、migration/test | 老报告“未来”不作缺口证据 |
| 某版本验证了吗 | validation中版本/SHA/suite/skip/CI链接 | 不引用旧绿色结果当新提交测试 |
| 实车真的完成吗 | 用户指定动作的物理反馈/专门证据 | HTTP接受、退出0、mock都不等于物理完成 |

[evidence-index](evidence-index.json)只记录路径/hash/类型/少量日期，不复制原始证据的数值。
公开fixture的recorded/synthetic区别看metadata；本地private原样本只有按需再读，不进公共索引。
大报告作为深层参考保存；每次query读取相关标题/行段，避免“全文+附录+JSON”三次重复输入。
