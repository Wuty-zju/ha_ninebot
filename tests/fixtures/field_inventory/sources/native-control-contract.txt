# v2.x 原生控制加密与状态归属契约

> 历史归档：保持原版本/证据范围，不继续追加。现行行为先读[CURRENT_STATE](../../agent/CURRENT_STATE.md)和对应主题contract，不能直接执行本文旧goal或“未来”清单。

2026-10-04，b14；基于已发布b13 main `2135b62bfe267c306bf23d5982661a077dac9bca`。
本阶段没有真实云查询、生产HA写入或真实车辆动作。

## 1. 验证方法与明确边界

未修改安装的固定ninecli binary。测试在临时副本中仅将唯一匹配的RSA PKCS1
公钥资产替换为一次性测试公钥；macOS同时重做该副本的ad-hoc签名。所有代码
指令保持原样，但它仍是**instrumented副本**，不能冒充未修改binary验收。
一次性私钥只在测试内存，账户/车辆/token均合成；全部五个上游origin显式指向
loopback模拟服务，结束回收子进程与临时文件，原binary前后SHA相同。

测试用recon的公钥/DeriveKey参考适配构造加密响应；MIT notice保存于
tests/ninebot_recon_license.txt及NOTICE。这只是离线测试，不引入生产SDK或
复制recon的transport作为fallback。[参考固定提交](https://github.com/kxn/ninebot-recon/tree/a46124d6290179554e1688c01384a7f4116ac96a)。

## 2. 已确认的输入输出分层

S/F=固定native静态/测试副本离线conformance，不是R实车控制证据。

| 层 | 输入/返回契约 | HA处置 |
|---|---|---|
| HA Button | bell、buck、engine/start、engine/stop | 正常命名/中英翻译；不映射Lock；当前门禁不变 |
| 本机REST | POST `/vehicles/{sn}/<action>`，随机Bearer | 不发送密码argv；单次尝试，无控制自动重试 |
| 九号业务路由 | bell、open_buck、engine_start、engine_stop四条POST路径 | 已由原binary loopback路由及本次instrumented加密测试确认；不等于车型权限 |
| wire | d/h/k/p/t；16-byte请求AES key由RSA-1024包裹；MD5校验wrapper | 由ninecli处理；任何key/token/ciphertext不放实体/diagnostics |
| wrapper | data、四项keyData、platform、timeStamp；Android风格Base64业务data | 内部协议；不是车辆遥测或统计实体 |
| business common | access_token、uid、device_id、语言/平台/版本/区域参数、serviceTime、nonce、checkcode、vehicle_type | 认证/客户端协议metadata，不直接映射车辆Entity |
| business command | cmd字符串仍为另一层opaque ciphertext | 不能据顶层device_id推断车辆SN；不猜cmd明文或engine物理含义 |
| 响应 | `r`解密后wrapper.data业务JSON，再由native proxy提供ok/data或error | Transport只确认native成功/失败；不声称实车已达到目标状态 |

测试四种请求分别到正确路由，模拟业务code=0被native接受；模拟业务code=403
被native变成HTTP502/error.code=upstream_error，并被集成分类SERVICE而非
错误reauth。每个测试用例只发一次；bell的接受/拒绝是两个独立用例，不是自动重试。`data.accepted`是我们构造的**测试标记**，
不是已观察九号云字段，不提升进入真实字段清单。不能把code=0/403合成向量
扩大为所有业务码、真实权限或物理动作效果的完整契约。

client.async_control明确返回None，Button不持久化控制原始回复。成功后的status
回读仍保留；回读失败/超时不自动再次发动作，未知物理结果不能编造“已启动”。
未确认的命令结构保留研究门槛，不将transport支持当成车辆权限/能力。

## 3. status返回身份守卫

R（已有私有记录只读复核，没有重新请求）：两辆已发现车辆的status.sn均与
profile.wnumber一致；身份值不写入公开文档。F：现有脱敏fixtures保留相同关联。
这证明当前两车契约，不宣称全车型相同身份体系已验证。

新增adapter参数expected_sn，coordinator始终传入请求车辆身份：

- sn缺失/null：按已有请求路由处理，不声称返回身份已经证明。
- 非空字符串经既有text规范化后必须精确匹配；不猜大小写/别名关系。
- 已返回但为空、非字符串或指向另一辆车：typed PROTOCOL错误。
- 先归一化/验证，再保留raw；失败不得替换该车旧raw、telemetry或success时间。

错误仅作用于该车status组；其他车/组保持可用，不清除登录。原有有界缓存
freshness继续，未过TTL的旧有效值可以显示；当前query-error单独阻止控制，
不会把错误返回标记成新成功。未来若车型确有不同SN语义，须有证据的显式
alias契约，不能通过取消整个守卫来接受错车数据。

## 4. 验收与剩余问题

test_native_encrypted_controls验证临时资产替换、完整request/response加密路径、
四种单次动作、拒绝分类、原始binary不变及子进程回收；没有实际云动作。
adapter/coordinator测试验证身份匹配/缺省/非法/错车、缓存/成功时间不串车、
partial failure及控制拒绝。其他既有安全/迁移/翻译要求保留。

生产仍为b8，本阶段没有部署。真实permissions仍为null，因此既有未知权限
门禁保持；之前的用户策略选择尚待回答。此文不能作为绕过权限或执行实车
控制的授权。未完成项还包括cmd物理语义、车型/共享用户权限、实际控制结果、
未知单位/CRS、电池稳定身份、历史完整性以及整体目标完成审计。
