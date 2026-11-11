# Windows 运行依赖

本应用使用可替换的动态库；修改或替换依赖后可从对应源码重新打包，不限制为调试这些修改所需的逆向工程。发布包保留许可证、依赖版本及 libusb 源包。源码包必须与程序版本对应。

| 组件 | 许可 | 对应源码／构建入口 |
|---|---|---|
| CPython 3.13／3.14 | PSF 与随附第三方许可 | [Python 源码](https://www.python.org/downloads/source/)；实际补丁版本见构建 Python |
| PyUSB 1.2.1 | BSD-3-Clause | [v1.2.1](https://github.com/pyusb/pyusb/tree/v1.2.1) |
| libusb-package 1.0.30.0 | Apache-2.0 | [源码发布包](https://pypi.org/project/libusb-package/1.0.30.0/#files) |
| libusb 1.0.30 | LGPL-2.1-or-later | `dependency-sources/libusb-1.0.30.tar.bz2`；构建说明在源包中 |
| Qt 6.10.3 / PySide6 / shiboken6 | 本预览使用 LGPL-3.0 | [Qt 对应源码与许可证](https://download.qt.io/archive/qt/6.10/6.10.3/submodules/)、[PySide/shiboken 对应源码](https://code.qt.io/cgit/pyside/pyside-setup.git/snapshot/pyside-setup-6.10.3.tar.gz)、[官方许可说明](https://doc.qt.io/qtforpython-6/licenses.html) |
| PyInstaller 6.22.3（构建） | GPL-2.0-or-later 及引导程序分发例外 | [对应源码](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3)；完整许可随包保留 |

QtCore、QtGui、QtWidgets 及其所需插件由 PyInstaller 的 Qt hooks 收集；不打包未用的整个 Qt 模块集合。Qt 和 libusb DLL 分别保留在 `_internal/`、`service/_internal/` 内，可用兼容版本替换。libusb 有启动摘要校验，重新构建时会为替代 DLL 生成对应清单；构建脚本见 `scripts/build_windows_app.py`。
