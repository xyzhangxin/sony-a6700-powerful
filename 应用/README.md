# Powerful6700

<img src="desktop/品牌/Powerful6700.png" width="128" alt="Powerful6700 图标">

应用名称为 **Powerful6700**；内部包名与命令仍使用 `a6700_unlock`／`a6700-unlock`，沿用已有配置目录；备份恢复仍要求适配与清单哈希完全匹配。

个人对 A6700 固件 2.00、USB 维护协议及功能行为的学习记录，附实验性功能解锁／关闭工具。当前源码为 **0.3.1 公开测试版**，上一下载版为 **0.2.0**；与 Sony 无关联，未获得官方授权、认证或支持。

- 原四项功能继续开放：FTP、关机快门帘与静音联动、灵活曝光、电子快门声音。
- 本次新增 FL2／FL3 实机安装／关闭测试入口，保留 ST、FL2、FL3 等简称、图标及完整说明。可与FTP等其他功能一起勾选，应用在同一连接内顺序执行；中文名称留待后续，不在本版界面展示。0.3.0应用已获维护者验收；完整卸载及重启后核验已通过，关闭会撤回CP代码／参数、启动入口及功能包；本版更新内容已由维护者审核，授权发布；见[安装范围](docs/FEATURE-SELECTION.md#创意外观独立功能包)。
- 暂不开放：位移多帧合成、动态增强防抖。两项开发与验证未完成，界面不可选择，CLI、恢复与原生写入器也不允许修改；已有实验状态不会自动撤销。
- 桌面备份与恢复入口继续隐藏，完整备份／固件恢复尚未实现。
- 连接被其他本机工具占用时，可在确认其他任务已结束后使用“强制接管”；普通用户不需要共享调试台。接管只处理占用进程，不跳过固件检查，见[连接占用处理](docs/PRODUCT-FLOW.md#连接占用与强制接管dev5)。

提供 Apple Silicon、macOS 13+ 的内置环境应用，包含 Python、PyUSB、libusb 和 USB 接管组件。默认相机模式启动仅检查本机环境，完成准备后才连接；演示模式保留。Windows 与 Intel Mac 暂不提供安装包。

**维护者已于2026-11-06确认当前FL23应用测试无问题，授权发布0.3.0；继续扩大测试样本。** 该结论不代表跨机身兼容性、故障注入或长期稳定性已全面验证。验收来源与后续测试边界见[验证记录](docs/VALIDATION.md)，本地配套交付位于 `dist/`（源码包不收录发行文件）。应用使用临时开发签名，尚未公证。

公开材料已排除三份非必要原厂固件扇区样例，保留 USB 临时 root 教程、受校验的功能修改与应用的发布范围。操作由使用者主动发起，仍会加载维护程序并实际修改固件字段。请先读[声明与使用边界](docs/声明与使用边界.md)；材料清理及学习定位不构成无侵权或免责保证，细节见[公开发布边界](docs/公开发布边界.md)。

自有代码采用 **GPL-3.0-only**，完整条款见 [LICENSE](LICENSE)，版权与无担保声明见 [NOTICE](NOTICE)。第三方文件保留原许可，见[归属说明](THIRD_PARTY_NOTICES.md)。分享安装包时，请在同一发布位置提供相同版本的源码包，包含原生载荷源码与构建脚本。

动态增强防抖的六个修改点、关闭前准备及验证边界见[集成说明](docs/动态增强防抖.md)。

## 桌面交互

使用“操作前须知 → 准备并连接 → 功能管理”三步流程。准备说明集中展示、菜单示意常驻展示；检查成功直接进入单一功能列表。右侧预览所选功能的解锁／关闭变化，桌面暂时隐藏备份与恢复入口，只展示功能解锁和关闭。成功后进入独立结果页，明确展示六步“关机 → 断开 USB → 拔电池 → 等待 10 秒 → 插入电池 → 开机检查”，并列出本次功能的菜单路径及成功标准。“继续操作，重新连接”返回准备页，重新检查后再管理其他功能。连接前说明 Mac 管理员授权和 MSC 提示变化的含义；失败显示对应下一步，演示设置收进右上角。交互规则见[应用流程](docs/PRODUCT-FLOW.md)。

## macOS 桌面应用

Apple Silicon macOS 13+ 内置环境版由 `scripts/build_macos_app.py` 构建，普通使用者无需安装 Python、Homebrew、libusb 或编译器。默认相机模式仅做本机检查，可切换演示；准备步骤和稀疏预检仍然执行，接管 USB 时由系统弹窗请求管理员授权，不安装常驻服务。

```sh
# 开发者构建；首次允许下载固定版本的运行环境和依赖
python3 scripts/build_macos_app.py --download-runtime
```

生成 `dist/Powerful6700.app`。构建器固定依赖哈希，从源码编译兼容 macOS 13 的 libusb，并检查所有本机二进制的架构、系统版本和库路径。应用使用临时开发签名，尚未公证；当前为维护者已验收、扩大样本测试的公测版，不称稳定版。模拟与物理模式切换在事务期间禁用，旧服务退出不会覆盖新服务状态。

仅开发界面、希望继续使用本机 Python 时，仍可运行 `python3 scripts/build_macos_preview.py` 构建预览。两种包复用同一份界面与业务代码。构建和检查方法见[桌面应用说明](docs/DESKTOP-PREVIEW.md)。

## 当前应用功能

| 操作 | 当前行为 |
|---|---|
| 解锁所选功能 | 勾选功能或“选择未解锁”，只补齐选中功能；已符合目标则不重复写入 |
| 关闭所选功能 | 撤销选中功能由本工具管理的修改，不依赖逆序运行历史脚本 |

桌面备份恢复入口暂时隐藏；现有稀疏功能状态备份／恢复保留在开发者命令中，不是整机或固件镜像。完整备份恢复仍未实现。

功能选择、统一 profile v2 和“全选缺失”规则见[功能选择与统一 profile v2](docs/FEATURE-SELECTION.md)。另有环境检查和开发者命令。备份默认保存到 `~/Documents/A6700 Backups/`，可用 `a6700-unlock backups` 查看。完整备份边界、格式和恢复规则见[功能状态备份与恢复](docs/BACKUP-RESTORE.md)。解锁不自动打开 FTP 传输等运行开关，实际是否使用仍由相机原生设置决定。

## 安装与启动

需要 Python 3.10+、PyUSB 1.2.1 和系统 libusb。设备框架当前支持 macOS／Linux；源码运行时 macOS 接口接管需要 Command Line Tools 的 clang；内置环境版已预编译，接管时仍请求系统授权。Windows 尚未适配。

```sh
python3 -m venv .venv
.venv/bin/python scripts/build_wheel.py --output-dir dist
.venv/bin/python -m pip install 'dist/a6700_unlock-0.3.1-py3-none-any.whl[usb]'
.venv/bin/a6700-unlock
```

源码目录也可运行 `sh start.sh`，macOS 可双击 `启动工具.command`；依赖仍按上面的步骤安装。

构建脚本过滤 macOS `._*` 元数据并检查资源完整性，同名 wheel 不覆盖。包内有预编译 ARM64 载荷及上游参考模块／许可；使用者不需要交叉编译器、研究目录或个人历史备份。

```sh
# 环境检查不访问相机
a6700-unlock doctor
a6700-unlock license
```

## 前置准备向导

启动菜单会先显示本机环境检查；解锁／关闭／设备只读检查前会进入准备向导：FTP 菜单仅在已有时处理，没有该菜单可直接确认；先设置 MSC 和关闭自动关机，最后用普通数据线连接唯一相机。日常功能管理不接 Pico 或测试板。

每项菜单操作提供完整路径，桌面版配有明确标为“非实机截图”的菜单示意；USB 与自动关机设置附 Sony 官方说明。实拍后续补充，详见[相机准备图解](docs/CAMERA-PREPARATION.md)。

自动检查与人工确认分别显示。准备未完成、取消或环境不通过时，不建立 USB 维护会话；准备通过后仍运行实际只读预检。详见[应用流程](docs/PRODUCT-FLOW.md)。

```sh
# 仅输出环境检查和相机准备说明，不访问相机
a6700-unlock prepare
# 交互式确认准备项，仍不访问相机
a6700-unlock prepare --interactive
```

## USB 操作

dev33 简化 MSC 识别：先发现 Sony MSC，再进行一次必要接管与协议认证；不要求存储卷已挂载或驱动仍在，不默认重置 USB，不做整套自动重试。已连接会话复用，断连后重新连接检查；固件机型与内容校验保持不变，仍只适配已验证的 A6700 2.00 布局。

若已有且开启 FTP，先停止任务、关闭 FTP 并重启释放介质占用；没有 FTP 菜单无需寻找或开启它。接线前设置 MSC 和关闭自动关机，仅连接一台目标相机。macOS 默认自动接管接口，已手动接管时可加 `--no-capture`。

```sh
a6700-unlock device inspect
a6700-unlock device unlock --dry-run
a6700-unlock device unlock --all-missing
a6700-unlock device unlock --feature ftp --feature exposure
a6700-unlock device close --feature exposure
a6700-unlock device backup
a6700-unlock device restore --dry-run
a6700-unlock device restore
a6700-unlock backups
```

`device backup` 把当前 260 个固定扇区保存为单个 `.a6700backup` 文件；`device restore` 默认选择备份目录中最新一份，也可用 `--backup` 指定。`--feature` 可重复，`--all-missing` 仅用于 unlock。备份／恢复都要求同机型、同固件、同适配版本；恢复只写回当前勾选（或本版全部开放）功能的清单字段。

预检窗口保留 **260 个扇区、133,120 字节**（149 个历史目标扇区、111 个结构守卫），覆盖 `/dev/nflasha15`、`/dev/nflasha5` 和 `/dev/nflasha8`。保留两项禁用功能的已知值识别与原守卫，不扩大兼容范围；只允许四项开放功能进入写入掩码。更新的研究候选或其他未知修改仍会阻止操作，不会猜测兼容。该窗口不是全盘备份。

未知字段、完整扇区或布局不匹配会阻止整个修改，没有跳过校验参数。适配针对已知 A6700 2.00 布局，不承诺所有同版本机身的实时 WBI 都能直接通过。

写入前会保存并同步本次窗口前镜像、目标和意图；相机内再次核对全部前值，再检查逐扇区写入、正确的 fsync 与回读。明确失败时回退已尝试的目标，主机再启动独立读取载荷确认；通信中断不自动重发写入器。写入后的本机执行记录保存失败不会跳过独立回读，仍会报告记录故障；写入前记录不能可靠保存时则拒绝发送写入器。

`completed` 表示写入及独立回读一致，仍需关机 → 断开 USB／其他外接供电 → 拔出电池 → 至少等待 10 秒 → 插入电池 → 开机验收实际功能；`no-op` 为无需修改；`blocked` 为预检拒绝；`rolled-back` 为回退已确认。出现 `uncertain`、`verification-failed` 或 `recovery-required` 时保留记录，先检查状态，不盲目重复写入。

默认私有记录目录：macOS 为 `~/Library/Application Support/a6700-unlock/`，Linux 为 `~/.local/state/a6700-unlock/`。可用 `--state-dir` 指定；设备锁固定在本机，与记录目录无关，并与既有研究向导共用。更换记录目录不会允许同时操作相机。`preimages.json` 等只覆盖本次窗口，不能称为整机备份，也不应公开上传。`result.json` 不包含原始镜像或 ProductInfo。

非交互设备命令需要逐项声明 `--confirm ftp_stopped --confirm camera_msc --confirm usb_connection --confirm power_awake`，不能靠上一次准备报告跨会话通行。人工声明不会跳过本机检查或设备内容预检。

设备命令支持 `--json`。成功、无需修改或预检成功退出码为 0，拒绝／失败为 2。详细流程见[运行框架](docs/RUNTIME.md)，连接失败见[USB 排查](docs/USB-TROUBLESHOOTING.md)。

## AI 分析与贡献

从 [AGENTS.md](AGENTS.md) 和 [AI 代码地图](docs/AI-CONTEXT.md) 进入，按模块定位改动；提交代码遵循 [CONTRIBUTING.md](CONTRIBUTING.md)。项目提供 PR／问题模板和 GitHub Actions 配置，无需私人备份或相机即可参与普通开发。每次发布均核对对应提交的远端 CI，不能将本地检查冒充远端结果。

```sh
python scripts/check.py --strict
# 安装 test 依赖且原生模拟器可工作时：
python scripts/check.py --native --strict
```

## 离线开发与资料

保留 `catalog`、`inspect --app ... --wbi ...` 和 `plan --action unlock/close --app ... --wbi ... --output ...` 命令，不访问 USB、不修改输入。离线 App 为 89,027,992 字节 ELF 明文，WBI 为 248,376,320 字节提取逻辑明文；离线文件偏移不能直接作为设备地址。

维护者可运行 `python3 scripts/build_source.py` 生成源码包；已有 setuptools 77+ 和 wheel 时，`python3 scripts/build_wheel.py --offline` 可禁止构建期间联网。两个打包命令都不覆盖同名产物。

功能逐项修改见[清单](docs/FEATURES.md)，代码结构见[架构](docs/ARCHITECTURE.md)，检查结果见[验证记录](docs/VALIDATION.md)，历史源码在 [verified/](verified/README.md)。
