# 代码与证据路由

`agent_context.py --topic <topic>`只列路径，默认每主题一个contract；不要将证据列表自动全文加载。

| Topic | 唯一contract | 代码边界 |
|---|---|---|
| raw | [RAW_DATA](../contracts/RAW_DATA.md) | raw/backend/client、diagnostics/debug_view、coordinator capture |
| travel | [TRAVEL](../contracts/TRAVEL.md) | travel/ride_models/month_summary/history/history_actions/ride_events/event_store |
| auth | [BACKEND_AUTH](../contracts/BACKEND_AUTH.md) | session/config_flow/runtime/client、native vehicle cache |
| controls | [CONTROLS](../contracts/CONTROLS.md) | capabilities/control_results/button/services、coordinator ownership/readback |
| battery/entities/polling | [VEHICLES_ENTITIES](../contracts/VEHICLES_ENTITIES.md) | battery/entity/registry/migration/compat、sensor/number、demand/image/tracker |
| release/workspace | [WORKFLOW](WORKFLOW.md) | manifest/hacs/CI/tooling；[PROGRESS](../development/PROGRESS.md)记录实际范围 |

数据链：ninecli→authenticated loopback→BackendResult→RawStore+strict adapters→domain→coordinator→HA entities/Actions/Event。
运行需要的文件均在custom_components/ninebot；不依赖本地references/private或仓库根脚本。

遇具体字段先rg [FIELD_INVENTORY](../reference/FIELD_INVENTORY.md)；遇候选alias/协议疑点再查[NinePlus研究](../research/NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md)指定节。
fixture recorded/synthetic区别查metadata；[证据索引](evidence-index.json)是hash/来源目录，不是新的实测。
max≠avg；score≠SOH；CRS/逐点单位未知；accepted≠物理完成；全月扫描≠全量rides。
旧Phase/A–F已完成部分不重复开发，archive不默认读取。旧文件路径使用`--resolve`，不创建重复入口稿。
