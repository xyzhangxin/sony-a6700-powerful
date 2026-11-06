# macOS 桌面预览

当前版本提供原生 SwiftUI 桌面界面，默认相机模式启动时只做本机检查，可在页眉切换到演示模式。模拟模式演示完整流程和异常状态；真实模式复用 `operations.execute`、本机设备锁、稀疏预检、写前记录、失败回退和独立回读。当前验证边界见 [验证记录](VALIDATION.md)。

dev12 内置版已完成实机只读全流程：系统授权、USB 接管、认证、152 扇区读取、主页状态识别和返回 MSC 均通过。环境检查不创建 USB 上下文，接管后才初始化后端。仅此读取流程已验收，未发送功能写入器。

## 页面与行为

dev19 改为三步：操作前须知、准备并连接、功能管理；默认窗口为1060×770，最小980×720。详细规则见[应用流程](PRODUCT-FLOW.md)。

1. 首屏保留机型、风险、保修、免责声明与自动本机检查；详情按需展开，阅读与下一步固定在底部。
2. 准备项集中在一页，菜单路径常显、图解折叠。一次明确的人工声明对应四项准备 ID；未确认仍不能检查，相机兼容性另行预检。
3. 检查通过直接进入主页。左侧单一功能列表，右侧选择解锁／关闭并展示实际变化项目；“全选”只改选择，不自动写入。功能依赖来自服务端。
4. 备份恢复暂时隐藏，无页签、其他入口或占位按钮。保留底层稀疏备份实现，待完整备份恢复实现并验证后再设计展示。
5. 当前阶段、经过时间和取消等待清楚呈现，日志默认折叠。解锁／关闭进入独立结果页，含六步断电重启、菜单检查路径与继续操作入口，失败和不确定结果显示下一步及可展开诊断。

演示场景和模拟断连位于右上角“演示设置”；演示使用 FTP、快门帘、灵活曝光、电子快门声音、位移多帧合成和动态增强防抖六项合成数据，不访问相机。实际功能列表由读取结果提供。模式切换、场景重置、断连和重新连接都会使原有前置状态失效。忙碌时不能切换模式、重复操作、关窗或正常退出；强制结束进程和掉电仍不受窗口保护。

“重新连接”先结束原维护会话，清除准备和兼容性后回到准备页。复看须知用独立阅读面板，不丢失当前选择。取消只请求一次并等待结果，不强杀进程、不自动重发写入器。

模拟步骤短暂停留方便观察与取消，不代表相机实际性能；进度显示实际经过时间，不提供虚构百分比或预计剩余时间。

## 内置环境版与开发预览

内置环境版针对 Apple Silicon macOS 13+，接收者无需另装 Python、PyUSB、libusb 或编译器。构建机器仍需要 Swift／Clang Command Line Tools：

```sh
python3 scripts/build_macos_app.py --download-runtime
# 缓存已齐全后可离线构建；也可显式使用已核对的 Python 压缩包
python3 scripts/build_macos_app.py --runtime-archive /path/to/pinned-python.tar.zst --cache /path/to/cache --output 'dist/Powerful6700-copy.app'
```

固定依赖见 `desktop/runtime-macos-arm64.json` 和 `runtime-dependencies.json`。源码构建 libusb 的最低系统版本为13，避免把仅适用于构建机的 Homebrew 库打包。Python 的非必需 Tcl/Tk 组件不分发，界面只使用 SwiftUI。运行环境许可证、版本元数据以及所分发 libusb 的对应源码归档保留在应用资源中。

应用中的 Python 使用相对路径，以 `-I -S -B` 启动固定 worker；不读取外部 Python 路径或用户 site-packages。内置 libusb 和接口接管组件使用清单哈希校验，缺失或损坏时停止，不静默改用开发机组件。真正开始设备预检才会请求系统管理员授权，仅运行固定接口接管组件，不安装常驻 helper。

无相机的搬移检查：

```sh
python3 scripts/check_macos_app.py 'dist/Powerful6700.app'
```

该检查以精简 PATH、无效外部 Python 路径启动包内服务，运行模拟解锁；真实模式只做环境检查，不枚举相机、不请求接管授权、不发送载荷。

内置包目前使用临时开发签名，未完成开发者签名／Apple公证，本版继续扩大实机测试。面向用户的正式发布范围仍以验证记录为准。

开发预览仍可用 `python3 scripts/build_macos_preview.py` 构建，`--python`指定本机解释器；它保存解释器绝对路径，不能作为跨电脑安装包。同名输出均拒绝覆盖。

## 可分析的代码边界

| 文件 | 职责 |
|---|---|
| `desktop/macos/A6700Preview.swift` | 三步应用外壳、固定导航栏、模式与帮助 |
| `desktop/macos/WorkspaceView.swift` | 功能选择与操作预览；备份视图保留但无桌面入口 |
| `desktop/macos/OperationViews.swift` | 当前进度、一次取消、独立结果页与断电验收、失败引导；检查说明由 `desktop/aftercare.py` 提供 |
| `desktop/macos/DesignSystem.swift` | 共用样式与功能显示名称，不包含补丁或地址 |
| `desktop/macos/PreviewModel.swift` | 类型化快照、进程生命周期、异步 JSON 事件；不实现补丁逻辑 |
| `desktop/macos/OperationWindowGuard.swift` | 操作期间阻止关窗，保留原窗口委托；正常退出由应用委托检查同一繁忙状态 |
| `desktop/macos/PreparationGuide.swift` | 集中准备说明与折叠菜单示意；路径与选项来自准备服务，不抓取相机画面 |
| `desktop/macos/OperationNotice.swift`、`src/a6700_unlock/notice.py` | 须知呈现与统一产品文案，阅读确认由控制器管理，不代表设备操作授权 |
| `src/a6700_unlock/desktop/controller.py` | 与界面无关的状态机、前置阻断、取消和连接状态失效 |
| `src/a6700_unlock/desktop/simulation.py` | 合成六功能清单、内存设备、固定故障场景、模拟备份目录 |
| `src/a6700_unlock/backup.py` | 稀疏功能状态容器、清单／哈希校验、备份列表与恢复边界 |
| `src/a6700_unlock/desktop/bridge.py` | 固定命令的本地 JSON 行协议，不接受后端、设备路径或 shell 指令 |
| `src/a6700_unlock/desktop/physical.py`、`physical_bridge.py` | 真实 USB 服务适配与固定命令 JSON 行协议，复用 `operations.execute` |
| `operations.py` 的 `_execute_session` | 物理 CLI 和模拟器共用的事务正文 |

物理路径仍由 `operations.execute` 获取本机设备锁后调用事务。模拟器使用独立临时目录和内存设备，不触及物理设备锁或维护者操作目录；控制器只允许单个事务。模拟报告显式包含 `simulation=true`、`device_access=false`、`write_attempted=false`，另用 `simulated_write_attempted` 描述模拟动作。真实模式报告包含 `simulation=false`，并按事务结果返回 `device_access` 与 `write_attempted`。

界面使用 [SwiftUI](https://developer.apple.com/documentation/swiftui)；与 Python 的本地通信使用 Foundation [Process](https://developer.apple.com/documentation/foundation/process)。这轮不引入网络服务或额外 GUI 框架依赖。

## 验证

`python scripts/check.py --strict` 可在无相机、无桌面会话环境验证状态机和协议。新增测试覆盖准备阻断、模拟往返与重复执行、部分增量、各故障结果、取消发生在写前／写后、重复操作阻断及断连失效。

macOS CI 增加本地预览的编译步骤，不启动真实 USB 操作。实际窗口点击与视觉检查需要已解锁的桌面会话；编译成功与状态机通过不替代这一步。2026-10-19 已在维护者 Apple Silicon Mac 检查四页与进度布局、按钮阻断、增量／重复操作、场景切换、断连、通信故障、写后取消与许可证弹窗，范围及未覆盖项见 `VALIDATION.md`；远端 CI 尚未执行。
