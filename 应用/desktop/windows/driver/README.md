# libwdi 驱动准备组件

`wdi-simple.exe` 来自 Timocop/libwdi-wdi-releases 的 v1.0 `x64.zip`，仅用于在用户明确确认后，为限定的 Sony VID/PID 绑定 Windows 自带 WinUSB。应用不安装 libusb-win32 内核驱动，不关闭驱动签名或安全保护。

- 二进制发布：https://github.com/Timocop/libwdi-wdi-releases/releases/download/v1.0/x64.zip
- `x64.zip` SHA-256：`d5aace13a3c1d5980113a5938fbee2edaa788ed625e3c554ff47da24cde39cda`
- `wdi-simple.exe` SHA-256：`27b6d9847d874674b7c412af124acf64b0b1efa397e3bbea8eab9950a6a08ee0`
- 对应 libwdi 源码：https://github.com/pbatard/libwdi/tree/v1.5.0
- `libwdi-1.5.0.tar.gz` SHA-256：`c666fb3e93917c902c5a4d689595df535b25cc6a2bfd2a9c7ffcde8629419830`
- 许可：LGPL-3.0-or-later；完整文本沿用 `../licenses/LGPL-3.0-only.txt` 的第三版正文，同时保留“或更新版本”的上游声明。

成品在 `dependency-sources/` 中附带上述源码包，便于重建和审查。构建脚本核对二进制与源包摘要，禁止使用被替换的安装器。
