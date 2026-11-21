# 架构与扩展方式

服务端新增设计入口：[轻量服务端设计](轻量服务端设计.md)，覆盖自愿实例统计、私有日志与签名更新分发；仍处设计阶段。签名密钥和候选发布顺序见[更新签名与发布运维](UPDATE-RELEASE.md)。

工程包含独立的功能清单、离线规划器、USB 运行框架和历史归档，运行时不依赖研究工程、个人备份或会话路径。核心使用 Python 标准库；设备连接使用可选 PyUSB 和系统 libusb。

下一步跨平台归一、签名功能包、在线更新及反馈的已确认结构基线见[跨平台与插件化方案](跨平台与插件化方案.md)。后续开发遵守其第0节和第4.1节。

2026-11-08 已实施 A 阶段 Mac 切片：`host_io.py` 归一路径、持久化与共享锁；桌面协议 v1 增加请求／操作关联 ID、能力和固定字段校验；`operation_workflow.py` 将既有写后指引编译为版本化流程及独立证据；`device_access.py` 将准备、打开认证传输和释放访问从桌面物理服务归一到固定生命周期适配器；`capability_packages.py` 将内置功能编译为版本化能力包和精确适配集，并在操作快照及执行前解析中固定其摘要；`package_updates.py` 提供与 Worker 同格式的 Ed25519 索引验签、内容寻址缓存及离线／撤回策略；`online_updates.py` 在合成传输边界上实现带超时、ETag 和审查重定向的只读网络检查。SwiftUI 与原生执行器未替换，签名能力包、通用计划合成器和统一界面尚未实施。

## 模块职责

| 模块 | 职责 |
|---|---|
| `catalog.py`、`data/a6700-2.00.json` | 功能归属、已知字段值、历史兼容值、边界及冲突校验 |
| `feature_updates.py` | 基础能力与增量修订识别；FTP 修订 2 的 Playback 自定义键单字节增量及独立更新计划 |
| `planner.py` | 本地明文文件的只读访问、完整基线校验、整套修改计划 |
| `device_profile.py`、`data/device-profile.json` | 逻辑字段与三台设备扇区映射、254 个目标／结构守卫识别、profile v2 未管理字节哈希与选择集 |
| `device_protocol.py` | 请求与结果结构、nonce、长度及可变字节范围校验 |
| `operations.py` | 预检、持久写前记录、执行与独立回读、结果分类，以及稀疏功能状态备份／恢复事务；锁和路径委托平台适配层 |
| `host_io.py` | macOS／Windows／Linux 的状态路径、独占记录、目录持久化与共享锁语义；不访问相机 |
| `device_access.py`、`usb/backend.py` | `prepare_access → open_transport → release_access` 生命周期；Mac 接管、libusb 与双次认证保持原顺序，失败打开不冒充已建立会话 |
| `session.py`、`msc_tool.py` | 调试台托管的有界脚本会话与配对 IPC；复用固定读取器和原事务核心，计划摘要执行、新鲜前态、排空退出与未闭合请求门禁，见 [MSC会话](MSC-SESSION.md) |
| `operation_workflow.py` | 版本化重启／就绪／验收步骤及五维证据；计时不等于就绪，人工验收保持 pending |
| `capability_packages.py` | 内置能力包、适配集摘要、依赖／同字节冲突校验与选择解析；只声明元数据，不生成第二套字节计划 |
| `package_updates.py` | 签名索引、严格 JSON／URL／时间／序号校验、附件 SHA-256 缓存、过期离线与撤回；`updates` extra 提供固定版本 cryptography |
| `online_updates.py` | 有界索引／附件请求、ETag、HTTPS 与审查重定向；200／304／503／断网状态映射，不自动重试或写相机 |
| `desktop/update_service.py`、`data/update-trust.json` | 桌面更新缓存目录、固定信任配置和主动检查；信任为空时返回 unconfigured 不联网，正式公钥已配置，检查与相机操作互斥 |
| `backup.py` | `.a6700backup` 容器、清单与逐扇区哈希校验、备份列表、恢复掩码边界 |
| `usb/backend.py`、`usb/legacy.py` | 双次认证、接口接管、自动 USB 重枚举恢复、固定文件载荷发送、分段读取和退出观察 |
| `native/transaction.c`、`data/native/` | 固定地址与字节掩码的 ARM64 读写载荷、产物哈希 |
| `desktop/macos/`、`desktop/controller.py`、`desktop/simulation.py` | 原生桌面模拟预览、状态机、合成内存设备与固定命令协议 |
| `desktop/protocol.py`、`desktop/bridge.py`、`desktop/physical_bridge.py` | 协议 v1、固定命令字段、request_id／operation_id 关联及能力声明；拒绝任意后端、路径与 USB 请求 |
| `diagnostics.py`、`diagnostic_records.py`、`data/diagnostics/` | FL23 本机记录与相机只读诊断、固定载荷校验、脱敏 ZIP 导出；不改变解锁／关闭事务范围 |
| `feedback.py`、`guided_diagnostics.py` | 引导复现的只读 USB 断开／MSC 恢复观察，以及白名单结构化反馈、独立删除密钥、同字节待发送与有限手动重试 |
| `preparation.py` | 无设备的准备状态、人工确认与前置阻断；CLI／桌面共用 |
| `cli.py`、`doctor.py`、`start.sh` | 四入口菜单、环境诊断、开发命令和源码启动 |
| `licensing.py`、`data/legal/` | 不访问相机的本地许可证与第三方归属 |
| `scripts/` | 原生构建、源码／wheel 打包、功能文档生成 |
| `tests/` | 合成数据、事务故障、模拟 USB、实际 ELF 仿真及许可资源检查 |
| `verified/` | 历史源码与原验证来源，保留归集哈希，独立于运行版本维护 |

## 两种预检

离线预检同时读取已解密的 App 和 WBI 常规文件，核对精确长度、完整字段值及归一化的整文件哈希；读取期间检查文件被修改或替换的情况。它只生成逻辑文件计划，不直接生成设备写入请求。

USB 预检使用固定映射读取 254 个扇区，覆盖 `/dev/nflasha15`（appFw）、`/dev/nflasha5`（WBI）与 `/dev/nflasha8`（CP 分区）。App 映射包含 ext2 结构守卫，WBI 包含头部与段表守卫，CP 使用目标扇区未管理字节哈希与前后邻扇区守卫；profile v2 对目标扇区校验“所有已知字段置零后的未管理字节哈希”，因此同一扇区内的不同功能字段可以独立处于已知状态。它不等同于完整固件身份校验或整机备份，也不承诺任意实时 WBI 状态都兼容。

其中 9 个 App 守卫扇区落在 /usr 分区的 ext2 inode 表内：相机开机自行刷新这些 inode 的 atime/ctime/mtime（每条 256 字节记录的偏移 8–20），全字节钉死会把未修改的相机误判为已修改。`data/guard-volatile.json` 按适配摘要绑定登记这些守卫，比对前清零时间字段、期望哈希使用原厂归一化值；扇区其余字节仍逐字节校验，预检结果的 `guard_volatile` 字段列出本次生效的豁免。

`data/guard-compatibility.json` 另按精确 profile、分区、物理偏移和原厂守卫哈希绑定 SonyCameraTool 1.0.0 的六处 FTP 回放筛选修改。三个守卫的完整扇区哈希逐一匹配已登记状态，允许六处修改的部分安装组合。识别结果保存在 `guard_compatibility`，识别规则摘要同时进入适配集和检查报告。原始扇区始终保留在前态、目标和独立回读中，守卫的原生写掩码仍为零。任何其他内容差异继续阻断，并记录预期与实际 SHA-256。离线生成方法为 `scripts/build_guard_compatibility.py --app <原始App常规文件> --output <新JSON路径>`。

独立迁移入口 `ftp_migration.py` 使用 `data/ftp-migration/` 成组资源及单独冻结的 manifest 摘要。迁移 profile 将上述三个守卫中的 7 个已知差异字节登记为恢复字段，全部其他字节继续做 unmanaged hash 校验；另纳入 134 字节自定义键矩阵，重建相同 `native/transaction.c` 的读写载荷。非 FTP 功能在该适配集中不开放写入。profile 内的 `guard_volatile_overlay` 仅复用原 inode 时间字段，随 profile digest 一起编入原生载荷，不通过进程全局状态切换适配。`inspect_sectors(..., allow_guard_compatibility=False)` 可显式要求通用版原始规则；普通操作默认行为不变。

迁移仍由共用 `_execute_session` 执行，沿用共享锁、新鲜读前、不可覆盖前态／意图、原生全窗口复核、失败回滚、独立回读和 USB 退出。固定计划在发送前再次核对，另以原通用 profile 检查目标及回读，输出 `public-042-inspection.json` 和 `migration-result.json`。检查与执行之间不保留兼容结论，不自动重发。相机菜单、重启保持和实际上传始终单独验收；普通 0.4.2 不管理其检查窗口外的新增矩阵。

USB 路径按选择集补齐字段：未选中功能保持当前值，选中功能按实际状态补到目标值，支持原始、已解锁和已记录的历史中间值。一个未知字段、守卫或未管理字节不匹配就停止整个修改；不提供强制放行参数。

## 写入事务

本机设备锁独立于报告目录，并与研究向导共用。预检通过后，先同步窗口前镜像、目标与写入意图，再发送一次写入器。相机内核对全部写前内容和固定掩码，逐扇区写入、fsync、回读，明确失败时逆序回退已尝试的目标。

主机随后用新 nonce 执行独立读取。写入后本机报告保存失败也继续回读；回读通过和记录故障分别报告。写入结果丢失不自动重发；原生报告拒绝也要确认前值仍一致，才能报告普通拒绝。

窗口前镜像不是完整备份，明文块设备视图也不能直接交给 Pico 密文写入器。dev13 增加稀疏功能状态备份／恢复；dev15 将窗口扩展到 251 个扇区并支持恢复所选功能，dev19 扩展到 254 个扇区（含 CP 目标）。备份保存固定功能扇区与清单哈希；恢复先检查守卫和非管理字节，只写回当前选中功能的清单掩码内字段，复用同一写入器、回退与独立回读。整盘／分区／固件镜像仍未提供，不能把 `.a6700backup` 称为整机备份。

## 界面与前置流程

界面通过 `preparation.assess` 获取环境与人工准备状态，通过 `preparation.guide` 接入当前 CLI。准备完成只允许进入只读预检，不能替代 `operations.execute` 的实际字段／守卫检查。未来桌面 UI 使用相同服务与稳定状态 ID，断连后使旧准备和兼容性失效。

AI 分析入口见 `AGENTS.md` 与 `docs/AI-CONTEXT.md`；贡献流程和无设备 CI 见 `CONTRIBUTING.md`。这些文件作为独立源码包的一部分分发，不需要研究工程或私人记忆服务。

## 新增或修订功能

先把已验证功能的具体修改点、原始值、解锁值、历史迁移值与证据写入清单，并更新功能文档。USB 执行器据字段状态补齐选中功能的目标，不能仅凭用户声称的版本判断已安装内容。新增功能需同时更新 profile 条目、未管理字节哈希、掩码和原生载荷。

设备映射或可变字节范围改变时，同时更新适配数据与固定掩码，重建两个原生 ELF 及哈希清单；映射或原生代码的变化需重新验收。不要只修改 Python 清单就对旧原生写入器放开新地址。备份格式记录适配与清单哈希；适配变化后旧备份会被拒绝，而不是按新偏移猜测恢复。

## 当前验证边界

dev3 已完成实机读取、解锁／关闭 dry-run 和退出后返回 MSC。新写入器的真实关闭→重新解锁、失败回退和独立回读闭环仍待验收；新自动 macOS helper 的接管也尚未实测。dev6 增加桌面模拟预览；共用事务正文移入 `_execute_session`，物理 `execute` 始终先获取设备锁，模拟服务只使用独立临时目录与内存设备。预览不接入真实 USB，不增加解锁功能或扩大设备范围。
