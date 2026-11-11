# 已验证代码归集

本目录保持历史源码及其原验证边界。正式包现已另行接入基础运行框架，并修正历史同步调用问题，见[运行框架](../docs/RUNTIME.md)。不要把本目录固定单扇区试验源码当作当前整套工具入口。

本目录归集原项目已经实现并有实机记录的 USB 代码与固定范围载荷。整理日期：2026-10-19。本轮没有连接相机，没有增加全盘备份／恢复、通用刷写器或新功能研究。

| 内容 | 路径 | 已有证据与用途 |
|---|---|---|
| USB 枚举、双次 Senser 认证、ProductInfo、文件载荷发送、STOP_SENSER | `usb/usb_service_probe.py` | 原认证及传输实现；路径独立化、命令说明修正，增加同一已知 GET_FILE 操作的结果读取选项 |
| Jiritsu 报文构造 | `usb/packet_builder.py` | 原代码逐字节复制，12 字节头＋4 字节命令＋原始负载 |
| macOS MSC 接口接管 | `macos/mac_capture.m` | 原代码逐字节复制；使用 capture／surrender，已实机通过 |
| root 构造函数证明 | `payloads/proof_ctor.c` | 历史 2,560 字节共享库加载与构造函数执行成功 |
| 原生 FTP／快门功能的解锁和关闭定义 | 包内 `src/a6700_unlock/data/a6700-2.00.json` | 见[功能清单](../docs/FEATURES.md)；已有离线整套规划，功能本身有历史实测 |

2026-10-21 公开范围更新：`plain_readonly.c`、`plain_flasher.c`、`plain_restore.c` 包含从原厂固件取得的完整扇区数组，已移出本发布工程，仅在主工程内部保留原件与哈希证据；本公开源码不再携带这些历史试验。`SOURCES.json` 的 `excluded_history` 只保留排除原因与原哈希，不含原厂字节，也不是可运行依赖。

当前应用的实际读写器为 `src/a6700_unlock/native/transaction.c` 与配套固定 profile／ELF，不能用历史回放筛选补丁代替。原已归集的8份其他来源文件保持原哈希，第三方许可不变。移除历史片段不会改变当前应用、设备地址、校验守卫或解锁／关闭值。

## 使用位置与依赖

这是源码仓库中的维护代码，不进入只包含离线规划器的 Python wheel。下列命令均从 `release/` 源码目录运行；只有维护者明确执行 USB 命令时才访问设备。

```sh
python3 -m pip install -r verified/usb/requirements.txt
python3 verified/usb/usb_service_probe.py self-test
```

主机还需原流程要求的 libusb。认证参考模块已随本目录归集，不再读取研究工程的 `sources/`。第三方来源与许可见 `usb/reference/sony_pmca/README.md`。

## 已有 USB 操作流程

相机关闭 FTP 和待传任务，重启释放介质占用，USB 设置为 MSC 并保持唤醒。macOS 先手动运行接管脚本；Linux 沿用原先的 libusb 接口释放流程。

```sh
# macOS：手动执行时请求管理员权限；不安装常驻 helper 或 sudoers 规则
sh verified/macos/capture-usb.sh

# 以下命令才会与相机通信，本轮整理没有执行它们
python3 verified/usb/usb_service_probe.py list
python3 verified/usb/usb_service_probe.py auth --initial-pid 0x0e76 --service-pid 0x0336
```

macOS helper 沿用原实现，选择首个 Sony USB 设备；运行时仅连接目标相机。重新插拔后需重新接管，结束后拔插恢复普通存储驱动。

文件载荷发送保留原 `SET_FILE` 流程，归集版命令更名为 `send-file`。它发送**真实文件内容**，`--op set-file --file-id 2` 会写入并加载 `/tmp/jiritsu/libJit.so`，可执行构造函数；旧名 `phase1` 不再作为命令开放。`--read-result` 可读取载荷写入的固定 `file_id=0` 结果，不能替代目标扇区的独立只读回读。

```sh
# 仅为维护者展示原流程，payload.so 必须属于已核对的固定操作
python3 verified/usb/usb_service_probe.py send-file /path/to/payload.so \
  --initial-pid 0x0e76 --service-pid 0x0336 \
  --op set-file --file-id 2 --read-result
```

不附带一键编译并发送历史试验载荷的脚本。C 源码和原报文逻辑保留供复用；本轮不生成新载荷、不向相机发送文件。

## 来源与范围

`SOURCES.json` 列出原项目相对路径、原文件 SHA-256、归集文件 SHA-256 及少量适配差异。来源索引不要求用户下载个人备份或原研究目录。当前保留了小范围固定扇区样本对应的 C 数组，没有带入整份固件、用户配置或完整备份。

全盘备份与全盘恢复：**尚未验证，本轮不开发、不补测**。原四入口规划保留，这两项不影响已完成代码的整理和分享。
