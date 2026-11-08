# Windows 预览

当前为开发预览，目标 Windows 11 x64、A6700 固件 2.00。本地候选 0.4.1 同步 Mac 0.4.1，开放 FTP、关机快门帘、灵活曝光、电子快门声音、FL2／FL3及创意外观中文名称；位移多帧与动态增强防抖禁用，完整备份恢复未提供。不得因不兼容而手工放宽守卫。本版尚待 Windows 实机验收。

Windows 界面遵循 Mac 版的品牌栏、步骤栏、固定底栏、双栏功能管理及六步断电结果页。仅驱动准备与系统授权说明按平台区分。

## 使用

完整解压后运行 `Powerful6700.exe`，保留 `_internal/`、`service/` 与 `licenses/`。无需另装 Python。默认相机模式只做本机检查，也可切换演示体验流程。

1. 阅读操作前须知，等待本机检查通过。
2. 点击“连接并自动准备驱动”。应用先通过SetupAPI检查现有绑定；已完成时不弹授权、不重复安装。
3. 首次需要准备时，只在系统UAC窗口授权一次专用驱动助手：先处理MSC `054C:0E76`，并提前准备服务身份`054C:0336`，避免短暂枚举窗口内来不及安装。两种身份共用Windows自带WinUSB，主应用与固件操作保持普通权限。
4. 安装器与对应源码随包附带并校验SHA-256，不需要联网或手动运行Zadig。自动准备失败时才使用[Zadig官方工具](https://zadig.akeo.ie/)，且只处理同一相机；不要更换其他设备的驱动。
5. 接线前关闭 FTP（如有），设置海量存储器(MSC)，关闭自动关机；每项菜单路径见应用准备页。只连接一台目标相机。
6. 点击“连接并检查”。MSC 提示消失可能只是切到维护模式，保持连接并等待结果。预检通过后才可选择功能。
7. 明确完成后：关机 → 断开 USB／其他外接供电 → 拔出电池 → 至少等待 10 秒 → 插入电池 → 开机按本次路径检查菜单。解锁FL23后每次取出电池再开机，需等待约40秒自动加载；该等待与断电至少10秒分开。
8. 继续操作须重新准备和检查。服务中断时结果可能待核实，不自动重放上次操作。

更换驱动可能影响资源管理器访问相机。需要恢复普通文件传输时，在设备管理器找到实际替换的相机设备，卸载该设备驱动后重新连接，使 Windows 重新选择系统驱动；不要删除其他设备或整个 USB 控制器。参考 [libusb Windows 文档](https://github.com/libusb/libusb/wiki/Windows)。

本程序尚未签名，不保证 SmartScreen 无提示。请校验来源和摘要；不需要关闭驱动签名或系统安全保护。非官方项目，固件修改可能影响保修，存在无法启动及数据丢失风险，见[声明与使用边界](声明与使用边界.md)。

旧版FL2／FL3会显示“已解锁 · 可更新”，可单独更新修复包，保留中文名称和其他功能；当前修复版不重复写入。关闭原四项时先自动恢复相关机身设置，再关闭固件并独立回读。

FL2／FL3 可与原四项一起勾选，依次执行并分别核验；中途失败明确列出已完成和未完成范围，不自动全部回滚或重发。关闭 FL23 是完整卸载专属依赖，并保留未选择的其他功能。中文名称可与FL23组合安装、分别关闭；仅中文不安装FL23的CP依赖，关闭FL23而保留中文时保留中文启动入口及载荷。未纳入FTP增量更新候选。连接占用时请在原工具结束任务；Windows 不提供强制结束进程的入口。

## 构建与离线检查

在 Windows x64 的独立虚拟环境安装 `desktop/windows/requirements.txt`。源码界面入口为 `python desktop/windows/main.py --demo`。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r desktop/windows/requirements.txt
.venv\Scripts\python scripts/check.py --strict
.venv\Scripts\python scripts/build_windows_app.py --libusb-source <libusb-1.0.30.tar.bz2路径> --libwdi-source <libwdi-1.5.0.tar.gz路径> --output dist\windows-preview\Powerful6700
$env:QT_QPA_PLATFORM='offscreen'
dist\windows-preview\Powerful6700\Powerful6700.exe --offline-self-test --report "$pwd\dist\windows-preview\offline-check.json"
```

`--native --strict` 还需要 `unicorn==2.1.4`、`pyelftools==0.33`，FL23 合成事务编译需要可交叉编译 AArch64 的 clang 与 ld.lld。没有 LLVM 的 Windows 验证主机可使用独立测试材料：

```sh
# 在具备 LLVM 和测试依赖的主机执行；目录必须尚不存在
PYTHONPATH=src python tests/test_fl23_package.py --export-native-fixture <合成测试目录>
```

将该目录完整传到 Windows，设置 `$env:POWERFUL6700_TEST_NATIVE_FIXTURE='<合成测试目录绝对路径>'`，再执行 `python scripts/check.py --native --strict`。测试会核对当前生成器、C 源码、测试源码、合成 recipe 及包文件摘要；过期或损坏材料直接失败。它只替代测试时的编译步骤，Windows 仍执行实际 ARM64 ELF 仿真；不会替换应用正式载荷、进入打包资源或提供设备写入入口。未设置时依然从源码现场编译。两种主机的平台检查数量不同：POSIX 接管测试与 Windows 手动释放测试分别按实际能力执行，不以跳过当作通过。

打包分为 GUI 与后台服务两个进程，避免窗口阻塞或把服务死亡当作正常繁忙。界面复用已有 JSON bridge、状态机、准备向导、事务和菜单验收元数据；没有复制固件地址或写入器。后台仅接受既有固定命令。离线自检只使用模拟设备，物理模式只检查环境，不枚举或访问相机。

Windows 关键记录使用独占、write-through 文件句柄并逐文件刷新；失败须在发送写入器之前停止。不能以 NTFS 日志作为已证明断电持久性的依据。需补原生 Windows 连接、跨重启和选定功能写入／独立回读验收后，才能称为 Windows 实机可用版。

基建源自贡献者 zhuorantiancheng 的 [PR #8](https://github.com/xyzhangxin/sony-a6700-powerful/pull/8)。本分支保留原提交并增加修正。参考独立 Windows 移植的分层方式，未引入其固件改动或守卫豁免。

libusb 源包的固定下载地址与 SHA-256 见 `desktop/runtime-dependencies.json`。构建会校验并随包保留源包。Windows 依赖许可与可替换动态库说明见 `desktop/windows/licenses/README.md`。

## Windows 实机验收顺序

1. **先看界面**：启动检查通过，演示能完成解锁、关闭、重启引导和继续操作；演示成功不代表已经改过相机。
2. **只读连接**：相机 USB 只接到本台 Windows，完成相机准备；先只点“连接并检查”，核对六项功能状态、禁用项与兼容性结果。不要为了通过而改 profile。
3. **单项闭环**：只在连接稳定、兼容性通过后，由维护者明确选择一项解锁／关闭；确认独立回读完成，按六步断电重启，按结果页检查菜单。
4. **重新连接**：再次检查相机状态，核对前次结果；验证正常退出和主动重连。出现未知结果时先保留记录，不自动重复操作。

使用虚拟机时，MSC 身份和切换后的 `054C:0336` 服务身份都需要进入同一台 Windows。PVE 推荐两个直通槽：`usb0: host=<物理总线-端口>` 负责 MSC，`usb2: host=054c:0336` 负责服务身份；仅绑定启动时的 PID 或只使用单个主机端口，可能在模式切换后保留旧描述符。当前P600S验证配置为`usb0: host=1-1`、`usb2: host=054c:0336`，另一个`usb1`保持不变。宿主机与虚拟机的文件锁无法互通，验收时先结束宿主机的相机工具／网络调试会话，不在两边同时操作。

预览不弹 Mac 管理员密码。Windows 需要管理员授权的主要是首次安装设备驱动；应用按普通用户运行，连接能力依赖实际驱动绑定。本机操作记录默认在 `%LOCALAPPDATA%\a6700-unlock\runs`，反馈前移除私人路径、设备标识，勿上传扇区前态或个人备份。


## 本次同步来源

自动驱动准备参考另一Windows项目的libwdi／SetupAPI方案，并合并为单次提权助手；其余核心源自Mac v0.4.1成组源码；FL23修复与旧版更新、关闭前设置恢复、中文组合、菜单检查及加载提示共用同一核心。Windows专用确认窗、品牌样式与平台IO保留；没有新增强制结束占用进程入口。UI及合成仿真通过不代表Windows机身验收；实际计数和构建结果见候选交付说明。
