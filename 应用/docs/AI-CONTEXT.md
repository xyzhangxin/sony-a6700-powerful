# 给 AI 与新贡献者的代码地图

先读本页再按问题进入具体模块，不需要扫描研究工程或从完整固件开始推断。`README.md` 描述当前能力，`VALIDATION.md` 区分证据与待验证范围，`CONTRIBUTING.md` 给出可复制的验证命令。

dev19 交互入口：`A6700Preview.swift` 管应用外壳；`WorkspaceView.swift` 管功能与备份恢复；`OperationViews.swift` 管进度及结果；`DesignSystem.swift` 管显示样式。准备声明通过固定 `confirm_preparation` 命令一次确认四个稳定 ID；`restart_preparation` 结束原会话并使准备／兼容失效，不跳过预检。

dev34：解锁／关闭的已核验结果进入独立结果页；`desktop/aftercare.py` 统一提供断电步骤及四项功能的检查路径／成功标准，SwiftUI 不复制这些元数据。物理事务及独立回读结束后关闭 USB 会话并释放锁；会话退出异常与写入结果分别呈现。模拟及物理的旧准备／兼容结论均失效，必须经 `restart_preparation`、重新声明及 inspect 才可继续。无需修改及失败／不确定结果不得伪装成已写入或机身验收成功。

dev35：`usb/legacy.py` 刷新／清空缓存不得直接调用 `libusb_exit` 或对仍被 Device 引用的 PyUSB backend 强行 finalize；交由 PyUSB 对象寿命管理，见 `test_usb.py` 的上下文终结器回归。`PreviewModel.serviceExited` 将服务死亡与活动事务分开：显示中断页并允许只重启本机服务，不能保留假 busy、自动重发请求或把未知写入结果改成失败／成功。进程 generation 和已退出标志共同拦截迟到状态。

dev36 补充：PyUSB 1.2.1 的底层 `_Device`／`_DevIterator` 不保留 backend，ResourceManager 销毁时 backend 仍可能先于 native device 释放。`_OwnedLibUSB` 在枚举和父设备返回的原生包装对象上保留 owner，确保 unref/free-list 在上下文退出前完成；不改第三方全局，不永久积累上下文。两项真实包装对象＋合成 C 函数回归覆盖该顺序。后续升级 PyUSB 必须重验这些私有接口。断电指引现为六步，`usb-out` 是独立稳定 ID；仍由 `aftercare.py` 统一提供。

## 稳定产品约束

- 最终交付可双击运行的桌面应用；当前提供 SwiftUI 桌面预览、源码／wheel 和可选真实 USB 桌面模式；已提供 Apple Silicon macOS 内置环境构建；正式签名、公证与真实写入闭环验收尚未完成。
- 桌面只展示解锁所选功能和关闭所选功能；备份与恢复入口暂时隐藏，待完整备份恢复实现并验证后再展示。现有稀疏备份恢复的核心服务、开发者命令和测试保留。dev14 支持按功能勾选与“全选缺失”，dev15 将电子快门声音并入；未选中功能保持不变；备份／恢复为稀疏的 260 个固定扇区（含 `/dev/nflasha8` CP 目标）；整盘／分区／固件镜像仍未实现，不能称为整机备份。
- 当前测试清单只开放原生 FTP、关机快门帘／静音联动、灵活曝光、电子快门声音（sound-after-exposure 验证基线；与灵活曝光无字段依赖）；位移多帧合成与动态增强防抖保留识别但暂禁用，不能修改；按实际字段状态、依赖和用户选择增量补齐，不按用户声称的版本放行。
- 配置向导必须在设备操作前运行；正常 USB 日常管理与 Pico 救砖为独立通路，不共用接线说明。
- 已验证的实机只读／dry-run 不等于新写入器完成实机闭环。事实来源是验证记录，不是包版本号或 AI 记忆。

## 从问题定位到代码

| 问题 | 从哪里读 | 如何验证 |
|---|---|---|
| 首屏适用范围、风险、免责声明与启动检查 | `notice.py`、`desktop/controller.py`、`desktop/macos/OperationNotice.swift`、`docs/OPERATION-NOTICE.md` | 桌面服务端阅读门槛与环境失败／重连测试；不能把阅读当作写入授权 |
| 环境缺失、相机模式／接线向导 | `doctor.py`、`preparation.py`、`desktop/macos/PreparationGuide.swift`、`docs/CAMERA-PREPARATION.md` | `test_preparation.py`；不访问设备，路径与示意共用元数据，FTP 无菜单也可确认 |
| 桌面界面、功能勾选、模拟流程与真实 USB 模式 | `desktop/macos/`（含关窗保护 `OperationWindowGuard.swift`）、`desktop/controller.py`、`desktop/simulation.py`、`desktop/physical.py`，说明见 `DESKTOP-PREVIEW.md` | `test_desktop.py`；SwiftUI 只呈现，Python 管理状态、模拟事务和真实 `operations.execute`；关窗／退出需实际窗口验收 |
| 物理 CLI | `cli.py`；调用 preparation 与 operations 服务 | 不在界面复制补丁实现 |
| 某功能到底改了什么 | `docs/FEATURES.md`、`data/a6700-2.00.json`、`catalog.py` | `test_planner.py` |
| 原始／已解锁／部分状态识别、功能选择 | `planner.py`、`device_profile.py`、`docs/FEATURE-SELECTION.md` | 合成字段、守卫、历史值与选择集测试 |
| 写前记录、失败分类、回读、选择执行 | `operations.py`、`device_protocol.py` | `test_operations.py` |
| 功能状态备份／恢复 | `backup.py`、`operations.py`（`execute_backup`／`execute_restore`）、`docs/BACKUP-RESTORE.md` | `test_operations.py` 的容器、篡改、守卫、选择与往返测试；不访问相机 |
| USB 认证、端点、分段、自动重枚举恢复与退出 | `usb/backend.py`、运行版 `usb/legacy.py`、`native/mac_capture.m` | `test_usb.py`，不需要相机 |
| 相机内读写与回退 | `native/transaction.c`、`scripts/build_native.py`、适配数据 | `test_native.py` 运行真实 ELF，须明确有无模拟器；`test_resources.py` 拒绝源码／产物失配 |
| 内置桌面环境、依赖校验与系统授权 | `scripts/build_macos_app.py`、`scripts/macos_runtime.py`、`bundled.py`、`desktop/worker.py` | `test_bundled.py`；`scripts/check_macos_app.py`只运行模拟和真实模式环境检查，不调用相机操作 |
| 许可、独立构建与源码分发 | `licensing.py`、`pyproject.toml`、`scripts/build_*` | 本地许可测试、构建和独立安装 |
| 历史研究与归集原件 | `verified/README.md`、`verified/SOURCES.json` | 按哈希核对，不将历史副本当成当前运行代码 |

## 调用边界

`物理 CLI → preparation → operations.execute（设备锁）→ USB backend → 固定范围 ARM64 载荷`。

`桌面预览（模拟）→ 本地 JSON bridge → DesktopController → preparation / SimulationService → 共用 _execute_session + 内存设备`。

`桌面预览（真实）→ physical_bridge → DesktopController → preparation / PhysicalService → operations.execute／execute_backup／execute_restore（设备锁）→ USB backend`。真实模式不是把模拟标签改成“真实”，而是复用物理 CLI 的执行、锁和事务边界；其实机写入闭环仍待验收。

规划器和准备状态可以在无相机环境单独调用。`preparation.assess` 返回机器可读状态；人工确认只表示准备完成，`compatibility_verified` 仍为 false。`operations.execute` 始终读取当前目标与结构守卫后再判断兼容性。

环境检查不枚举 USB；准备服务不持有 USB 或写入能力。实际设备检查也会上传临时读取器，不能宣称相机文件系统完全零写入。执行器的字段／扇区修改范围是清单和编译掩码共同决定的，不能只在界面增加一个按钮就扩大地址；“全选”也必须经过同一预检。备份容器只能由固定命令创建；恢复文件必须通过适配、容器、清单、逐扇区哈希与守卫校验，不能接受路径穿越或任意地址。

## 常见误判

`/dev/nflasha15`、`/dev/nflasha5` 是系统明文视图；Pico 原始镜像为密文。逻辑 WBI、完整加密 WBI 分区的长度与哈希不同。窗口前镜像只覆盖当前目标，不是整机备份；`.a6700backup` 只覆盖固定功能扇区，同样不是整机备份。USB 超时后不能自动重发写入器，必须保留不确定结果。

## 最小交接格式

说明修改的用户行为、触及模块与不变量、实际验证命令与结果、未验证范围及下一步所需证据。避免复制整段研究日志；不要在结论里把计划写成已完成。

动态增强防抖（dev23）：固定六字节和关闭前准备见[动态增强集成](动态增强防抖.md)，`test_dynamic_active.py` 使用合成扇区验证禁用选择、已安装状态保留、恢复范围及计数／高字节／未管理列阻断，不读取真实固件。

公开材料范围更新（2026-10-21）：三份历史固件数组载荷仅内部保留，不应恢复到发布目录。源码构建的已知块／路径检查与合成测试见 `scripts/build_source.py`、`test_public_distribution.py`；当前运行代码和 dev25 二进制未因此改变。维护者已选择保留 USB 临时 root 教程、受校验的功能修改与应用范围，定位为个人学习记录；不删除仍存在于二进制的对应源码，不把学习定位当法律免责，见[发布边界](公开发布边界.md)及[声明与使用边界](声明与使用边界.md)。

2026-10-22 dev33：MSC 发现与端点／固件适配分离；不要求磁盘卷或驱动、不先重置、不扫描卸载无关磁盘、不整套自动重试。共享锁覆盖整个已认证物理会话，结束会话才释放；实际执行复用既有锁内事务函数。物理普通快照不再枚举已隐藏的备份目录，避免文件系统阻塞被误判为 USB 错误；显式备份恢复仍可访问。参见 USB-TROUBLESHOOTING.md 与 VALIDATION.md。
