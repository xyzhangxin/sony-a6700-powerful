# USB 功能刷写教程

本页从源码运行与桌面应用相同的 USB 核心，**只对清单管理的功能字段解锁／关闭**。不接 Pico，不刷入整份官方固件，不提供任意地址写入或整机恢复。普通用户优先使用[一键应用](Powerful6700使用说明.md)。

## 安装源码环境

以 macOS 为例，需要 Python 3.10+、libusb 和 Command Line Tools。源码设备层也支持 Linux，但本版公开验收以 macOS 为主；Windows 未适配。

```sh
git clone https://github.com/xyzhangxin/sony-a6700-powerful.git
cd sony-a6700-powerful/应用

# 已安装时无需重复执行；编译器用于临时构建 Mac USB 接管组件
xcode-select --install
brew install python@3.13 libusb

python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[usb]'
a6700-unlock doctor
a6700-unlock catalog
```

`brew` 需预先安装 Homebrew；不使用 Homebrew 时可自行提供上述依赖。桌面安装包已内置运行依赖，无需本节安装。`doctor`、`catalog` 都不访问相机，先确认检查通过。

## 准备相机

按[相机准备](Powerful6700使用说明.md#相机准备)设置 MSC、关闭自动关机、关闭 FTP（如有），再连接唯一相机。退出桌面应用的相机连接和其他工具。

```sh
# 只展示准备说明，不连接相机
a6700-unlock prepare --interactive

# 以下每次设备操作均重新确认准备、取得共享锁并检查当前相机
a6700-unlock device inspect
```

终端逐项输入 `y`；没有 FTP 菜单可确认该项已准备。macOS 接管可能通过终端请求管理员授权，密码只在系统／终端授权提示中输入。不要把密码发到问题反馈。

## 先预演，再写入

下面以 FTP 为例，**实际写入命令请按需要单独执行，不要整段复制后无人值守运行**：

```sh
# 仅预检和生成计划，不修改功能字段
a6700-unlock device unlock --feature ftp --dry-run

# 实际解锁 FTP，重新读取前态并在写后独立回读
a6700-unlock device unlock --feature ftp
```

其他可用 ID：`shutter`（关机快门帘）、`exposure`（灵活曝光）、`sound`（电子快门声音）。可重复 `--feature` 选择多项；声音可单独选择。用 `a6700-unlock device unlock --all-missing` 补齐本版开放但尚未解锁的功能。位移多帧合成与动态增强防抖被禁用，不能由命令强制开启。

关闭同样先预演，确认后再执行：

```sh
a6700-unlock device close --feature ftp --dry-run
a6700-unlock device close --feature ftp
```

关闭声音前先将相机“音频信号”设为“开”或“关”，避免菜单仍保存被移除的特殊选项。关闭只撤销所选功能管理的字段，不恢复整份固件；每项具体修改点见[功能清单](应用/docs/FEATURES.md)，选择和掩码规则见[功能选择](应用/docs/FEATURE-SELECTION.md)。

## 如何判断结果

- `inspected`／`dry-run`：读取／预演通过，未执行功能写入。
- `completed`：本次写入及独立回读通过，仍需关机 → 断开 USB／其他外接供电 → 拔出电池 → 至少等待 10 秒 → 插入电池 → 开机验收功能。
- `no-op`：已处于目标状态，无需修改。
- `blocked`／`rejected`：检查拒绝，不要删除守卫或扩大允许值来强行通过。
- `rolled-back`：本次失败后的回退已确认。
- `uncertain`／`verification-failed`／`recovery-required`：结果待核实，保留记录，不重复写入。

默认本机记录在 macOS `~/Library/Application Support/a6700-unlock/`、Linux `~/.local/state/a6700-unlock/`。窗口前镜像是私人操作记录，**不是完整备份**，不要上传 GitHub；更换目录不能绕过设备共享锁。

写入需保持供电和连接；任一未知字段、守卫或未管理字节不匹配都会阻断。本版没有“跳过校验”，也不保证所有相同固件号机身都兼容。结束服务未确认时，按提示拔 USB、正常关机重开。更多排查见[USB 排查](应用/docs/USB-TROUBLESHOOTING.md)，使用前阅读[声明与使用边界](声明与使用边界.md)。
