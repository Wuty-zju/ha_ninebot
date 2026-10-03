# ninecli 实现解读与依赖审计

> 本文为固定基线的审阅材料。重构已在独立分支开始，当前实现差异与验证状态见[2.0实施记录](2.0-实施记录.md)。

日期：2026-10-03；对象：`ninecli==0.1.7`，作为 ha_ninebot 重构的 App 协议后端。本文件补充[完全重构与开发报告](完全重构与开发报告.md)，与[接口和实体对照](接口与实体逻辑对照开发文档.md)配套。

## 1. 已读源码和审计边界

“源码解读”必须说明材料来源，不能将反汇编推断伪装为完整源码审查。

| 材料 | 已核验内容 | 局限 |
|---|---|---|
| fork Python 集成源码 | config flow、CLI subprocess、JSON 解析、字段转换、coordinator、实体与控制后的刷新 | 能完整阅读，但不含底层 HTTP/加密实现 |
| ninecli Python 发布代码 | `__init__.py`、`__main__.py` 完整读取 | 只是平台执行 shim；不是 Python HTTP SDK |
| PyPI 0.1.7 发布文件 | 8 个平台 wheel；本次列表无 sdist；musllinux arm64 wheel SHA-256 校验成功 | wheel 中无 .go 源码；不能本地从这些 wheel 重新构建 Go 程序 |
| 本机 Go ELF | build info、Go pclntab 的209条项目函数记录、源码路径/行号元数据、选定函数调用与反汇编 | 元数据不等于源码；只核验了关键链，不宣称逐行审过整个 Go 项目 |
| 官网上行/下行 | 已有会话隔离查询两车的状态、BMS、行程与列表；离线假登录生成请求 | 未进行新的真实密码登录、短信登录、刷新测试或车辆控制 |

本轮再次尝试匿名打开 `https://gitea.com/ninebot/cli` 及固定 revision 的 go.mod，未获得完整源码；PyPI 公共 JSON 也没有源码项目链接。**完整 Go 源码审查、第三方许可清单和可重复构建仍是后续依赖审计工作，不算已完成。** [作者发布页](https://pypi.org/project/ninecli/0.1.7/)说明其为非官方 CLI，不能视为九号官方支持承诺。

PyPI 对应平台程序与 HA 中读取出的副本逐字节一致，binary SHA-256：`78665c4a72a5edbdf3a9e07d81b578afcacdd8dfe3f7cd23911a01613b879271`。build info：Go 1.25.0、Linux arm64、CGO_ENABLED=0，模块 `gitea.com/ninebot/cli`，revision `3ec468a5a739030d94b9366ada210e6d7017b44e`，`vcs.modified=true`。因此固定 revision 仍不足以重现发布程序，必须取得构建时的本地改动与构建配置。

公共包元数据、平台清单及验证摘要已收录于[审计摘要](evidence/reconstruction-audit.json)。详细二进制函数证据与原始请求保存在本地审阅证据包，未将私有凭据或精确坐标收入公开文档。

## 2. Python shim：选择程序并替换进程

`__init__.py` 仅作包说明。`__main__.py` 的 `binary_name()` 在 Windows 返回 `ninecli.exe`，其他平台返回 `ninecli`；`main()` 使用 `Path(__file__).resolve().parent / "bin" / binary_name()` 定位 wheel 内程序。

执行流程：

```text
python -m ninecli <args>
  → 检查 bundled binary 是否存在
  → argv = [binary, *sys.argv[1:]]
  → os.execv(binary, argv)
```

不存在程序时 stderr 提示并退出1；exec OSError 时退出126。`execv` 会以 Go 程序替换 Python 进程，不是再启动一层 Python 子进程。HA 异步启动的是一条命令的进程；取消 Python 调用并不会自动终止已经运行的 Go 子进程。

shim 没有自行下载二进制、选择 HTTP 地址、签名或读取 HA 配置。正确的平台 wheel 由依赖安装系统选择；若无适配 wheel，不应在集成内用任意 shell 安装脚本补救。

## 3. Go 模块职责：依据符号与关键调用链

下表中的文件和行号来自 Go ELF 元数据，用于未来取得源码后定位；不是可点击的已取得源码。

| 模块路径 / 已发现函数 | 可确认的职责 | HA 应怎样使用 |
|---|---|---|
| `cmd/root.go`，JSONOutput；`cmd/vehicles.go`，vehicleCacheFromLists/business UID helpers | Cobra 命令参数、host overrides、输出模式、列表缓存和业务身份选择 | 显式传 `--config` 与 `--json`；只允许业务所需命令 |
| `internal/config/store.go`，ResolveDir/Load/applyDefaults/randomDeviceID | 解析会话目录，加载配置与默认值，生成客户端 device_id | 每个 config entry 的私有目录，首次生成后保持 device_id 稳定 |
| 同上，LoadTokens/SaveTokens、LoadVehicleCache/SaveVehicleCache | tokens.json、vehicles.json 持久化 | 调用即可能更新 CLI 自有缓存；隔离测试必须用副本；文件操作和目录提交要有边界 |
| `cmd/login.go`，finishLogin | 保存 Passport token → Business.Login → 再保存业务 uid | 登录只有两段都成功才提交新会话；中间文件不是最终登录成功证据 |
| `internal/api/passport.go`；`internal/crypto/passport.go` | Passport HTTP、参数规范化、canonical/sign | 留在 CLI 后端，不向旧 OpenClaw 随意添 sign |
| `cmd/refresh.go`，ensureFreshTokens | 命令运行前存在 token 新鲜度处理路径 | 查询不必每次密码登录；刷新失败要分清认证、网络、服务和解析问题 |
| `internal/api/business.go`，CommonParams/PostWithExtras | App 公共参数、包装、业务 HTTP、解密与响应处理 | Python 获取的是解密后业务 data，不应再把它当 envelope 解密 |
| `internal/crypto/netease.go`，EncryptRequest/DecryptResponse/GenKReq/GenNonce/ComputeCheckcode | 业务包装加密与随机参数、摘要、解密 | 不将内置常量或业务密钥复制到 HA 诊断和代码示例 |
| `internal/crypto/wrapper.go`、`keyderive.go`、`keys.go` | 包装与密钥派生、内置 key 的初始化 | 全数学细节未独立重写验证，保留外部依赖边界 |
| `internal/api/vehicle.go`、`travel.go` | 车辆列表/状态、电池和行程业务请求 | HA 在 adapter 层校验类型、能力、分页与时区，不假设 raw JSON 完整可靠 |
| `cmd/control.go`，businessForCachedVehicle/runControl；`internal/api/control.go` | 从车辆缓存选择业务、控制命令与确认流程 | 单独能力与许可路径，不能因为 CLI 有命令就默认开放每车控制 |
| `cmd/serve.go`、`cmd/mcp.go` 与 internal/proxy/mcp | HTTP 代理和 MCP 模式 | HA 重构不需要启用；避免为查询额外维护明文代理服务 |

build info 另显示 Cobra/pflag、segmentio encoding/asm、MCP SDK、jsonschema-go、uritemplate 与 x/sys 等依赖。模块版本可从本地 `ninecli-go-build-info.txt` 审阅；这些依赖的许可、安全和可重复构建审查不能只靠 PyPI 的一条 License 标签代替。

## 4. 登录、身份与 token

CLI `login --help` 实测只声明 `--user/-u`、`--password/-p`、`--area/-a`；没有声明 stdin/password-file 输入选项。fork 通过 argv 传真实密码。HA 日志脱敏无法消除同权限进程观察 argv 的可能性。计划应优先请求或提交上游 stdin 输入支持，或以获授权的依赖分支实现；在验证之前不写成“当前已经安全地通过 stdin 登录”。不要改为环境变量后就声称完全解决同权限观察。

Passport 密码登录：`POST /v6/user/login`，含 areaCode、device=ANDROID、username/password；clientId 为 App 客户端、timestamp 毫秒、sign 为64位 SHA-256 hex。静态核验的签名是：加入 clientKey/url，合并公共参数和 body，加 timestamp，按键排序，拼接参数，再 SHA-256。与用户申请的内测 api_key 是两套身份体系。

随后 App 业务登录 `https://api-jhcx-v6-bj.ninebot.com/user/user/login` 取得 uid。HA fork 从 tokens.json.business_uid 确定 config entry unique_id。磁盘字段还包括 access/refresh token、uuid、username/phone、saved_at 与 accessTokenValidity；它们不是本次网络登录原文。

`accessTokenValidity` 观察到的字段形态为13位数，不能因为名字就把它当“有效秒数”；应从完整 CLI 代码/固定样本确认绝对毫秒时间或其他契约。HA wrapper 不应重复实现一套未经核验的过期计算。本次保证没有新密码登录，并不表示 CLI 所有查询永远不会自动刷新 token。

## 5. 业务鉴权与加密链

查询不是简单 Bearer GET。内部公共参数包含 access_token、client_ver、device_id、regionx、language、platform、login_country 等；headers 包括 `Uid`（业务 uid）、`Device-Id,Request-Id,Service-Time,Business-Type,Client-Ver,Platform,Regionx,Need_decrypt,Ninebot-Version`。

```text
业务参数 + 公共参数
  → BuildInner / serviceTime、nonce、checkcode
  → BuildWrapper
  → AES-CBC + PKCS7 → d
  → 包装明文 MD5 → h
  → RSA PKCS#1 v1.5 包装请求 key → k
  → HTTP envelope {d,h,k,p,t}
  → 响应 envelope {v,s,r}
  → r 的 Base64/派生 key/AES 解密
  → 包装 data 与业务结果码检查
  → CLI --json 输出 data
```

`s` 保持不透明字段，不宣称已独立确认其全部数学作用。DeriveKey、ComputeCheckcode、字节转义与全部常量尚未做独立一致性验证。本次已成功查询证明发布客户端可运行，不能证明按这段概念图就能写出完整兼容客户端。

## 6. HA 命令与 HTTP 对应

| CLI | 当前已核验的请求 | 状态 |
|---|---|---|
| vehicles | ebike 与 steeldust 的 `/vehicle/binding/my-vehicle`，合并来源 | 实测查询成功 |
| status SN | ebike `/vehicle/vehicle/desktop-component`，车辆字段 sn_str | 两车实测成功 |
| battery SN | ebike `/v6/vehicle/battery-info`，wnumber | 两车实测成功 |
| travel SN --month YYYYMM | cn-cbu-gateway `/app-api/travel/v6/travel-list2`，wnumber/month 与分页/版本字段 | 两车实测成功 |
| travel --detail | `/app-api/travel/v6/travel-infostream` | 静态路径，未实测 |
| whoami / token refresh | Passport `/v5/user`、`/v3/user/refresh` | 命令/静态路径，未本次实测 |
| engine-start/engine-stop/bell/buck | 控制调用链与 help 可见 | 未执行，未审定独立 HTTP 控制契约 |

host override 是测试能力：先前将 CLI 指向本地观察器，再只读转发至官方 HTTPS。生产集成不暴露这些 overrides 为普通选项，以防会话被发送到错误 host；测试用假凭据和受限请求集合。

## 7. 发布平台与供应链

PyPI 0.1.7 wheel 范围：manylinux glibc ≥2.17 与 musllinux ≥1.2，各有 x86_64/aarch64；macOS x86_64/arm64；Windows amd64/arm64。本次仅 Linux musl arm64 执行和字节一致性得到实测，其他平台是发布可用性，未跑集成测试。没有 armv7/32位 wheel，没有 sdist 自动构建回退。

wheel 标签为 `py3-none-...`，发布 Python 要求≥3.8；这不表示 fork 的 HA 集成也兼容 Python3.8。fork 使用 Python3.12 之后的类型语法和当前 HA helper，最低 HA/Python 要通过 CI 决定。

PyPI/安装 METADATA 标记 MIT，但所检查 wheel 没有单独 LICENSE 文件。两份集成仓库基线也未见 LICENSE。重构复制上游实现前应取得/确认对应代码的授权和归属，并建立 LICENSE/NOTICE 与依赖清单；不要把“可以下载源码”写成“已确认所有复制许可”。这是项目发布审计待办，不宣称是 HACS 的某一条专门规则。

推荐依赖 `manifest.requirements=["ninecli==0.1.7"]` 作为初始候选，待兼容性和许可审计通过再发布；先不要自动升级到未测试版本、在运行时 pip install、下载可变二进制或要求用户复制可执行文件。保留 SHA/平台清单与测试结果作为版本升级审阅材料。若维护方不能提供完整源码与构建改动，可继续评估当前二进制后端，但必须明确依赖透明性限制；纯 Python 适配是后续完整协议验证项目，不作为本轮恢复查询的隐性范围。

## 8. wrapper 必须重写的边界

保留“异步 subprocess + CLI JSON”的思路，不照搬当前 api.py 的全部行为：

- subprocess_exec 参数列表，禁止 shell=True；不记录命令 argv、完整 stderr/stdout 或 token。
- 每个会话串行；跨会话受控并发；固定超时和输出上限；取消、超时、unload 都 terminate/限时等待/必要时kill并 reap。
- 只在明确认证码/认证证据时触发 reauth，禁止通用 `server code=`→AuthError。
- 解析 stdout 时检查退出码、JSON 类型与 schema；空输出不应默认当 `{}` 成功。
- 子进程出错时必须保留“控制是否可能已经执行”的不确定性，控制不能自动重试。
- 会话事务：临时目录验证 → 检查 unique_id/账户一致性 → 原子提交 → 更新 entry；失败保留旧会话，取消清理临时目录。

本轮离线复现5项补充行为、此前8项回归与全部实体回放均已通过。两项关键生命周期问题是“验证中提前替换正式会话”和“取消任务未 kill 子进程”；状态刷新缺失 mutex 则见配套实体文档。后续使用真实源码和模拟进程测试实现修复，不在用户的运行中 HA 上用控制动作试错。

## 9. 2.0重构的实际接入选择

以上章节保留固定审阅材料。重构已将原CLI逐命令stdout包装候选替换为固定版本的
受管理serve进程，详见[实施记录](2.0-实施记录.md)和[client实现](../custom_components/ninebot/client.py)。
原因是当前login命令只提供argv密码参数；serve允许从本地HTTP请求体提交密码，
不需要编造stdin支持或移植未完整取得源码的Go签名算法。

Python client启动`python -m ninecli --config <private-dir> serve --bind 127.0.0.1:<port> --quiet`，
随机Bearer通过NINEBOT_SERVE_TOKEN传入。它不是官方云端的Bearer鉴权：本地请求由
serve认证，云端仍由Go程序执行Passport/App token、签名与业务加密。两层不能混淆。
集成不会启用MCP，不提供生产host override或公开端口，不记录stdout/stderr及响应错误正文。

| 本地REST | 上游职责 | 验证范围 |
|---|---|---|
| POST /auth/login | Passport与业务登录两阶段；body包含account/password | 假凭据送至受控Passport模拟器；未真实新密码登录 |
| GET /vehicles | 合并车辆列表 | client envelope/认证测试；既有实车证据来自原CLI查询 |
| GET /vehicles/{SN}/status | App车辆状态 | 合成HA流程与client路径测试 |
| GET /vehicles/{SN}/battery | App BMS详情 | 同上；兼容data包装并严格解析 |
| GET /vehicles/{SN}/travel?month=YYYYMM | 月度行程 | 同上；当前月与上月last ride分离 |
| POST /vehicles/{SN}/engine/start、engine/stop、bell、buck | 实验控制 | 全部模拟；未向真实车辆发送 |

启动时先验证无认证/vehicles为401，再验证带Bearer的不存在路由为404，之后才发送
凭据。请求串行、队列上限8、输出上限1MiB；超时、取消、close均回收进程。代理
响应按ok/data/error envelope处理；仅明确unauthorized/invalid_auth/token_expired证据
触发认证错误，upstream_error不被泛化为密码错误。未确认的上游鉴权码保留服务错误，
不能从任意错误字符串猜测账号失效。

同权限用户仍可能观察进程环境或私有文件，loopback认证不消除该本机信任边界。
完整Go源码、发布构建改动和可重复构建依然未取得；上述runtime行为证据不替代
完整源码或供应链审计。平台与新版隔离实测结果需在最终发布验收记录中更新。
