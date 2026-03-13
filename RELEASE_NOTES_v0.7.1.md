# Ninebot Integration Release Notes v0.7.1

发布日期：2026-03-14

## 修复摘要

v0.7.1 是一个稳定性修复版本，重点修复 v0.7 在初始化阶段可能导致集成加载失败的问题。

## 关键修复

- 修复 `NinebotDataUpdateCoordinator` 初始化顺序问题。
- 在读取轮询配置前，确保 `config_entry` 已正确挂载到 coordinator 实例。
- 避免 Home Assistant 在加载配置项时出现以下错误：

```text
AttributeError: 'NinebotDataUpdateCoordinator' object has no attribute 'config_entry'
```

## 影响范围

- 受影响版本：v0.7
- 修复版本：v0.7.1

## 升级建议

- 所有 v0.7 用户建议升级到 v0.7.1。
- 升级后无需重新配置账号，重载集成即可生效。
