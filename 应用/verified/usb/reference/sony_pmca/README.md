# Sony-PMCA-RE 参考模块

本目录逐字节收录原研究代码实际使用的 `pmca/usb/constants.py` 与 `pmca/usb/crypto.py`，来源为 [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE)。原文件及归集校验见 `verified/SOURCES.json`。

`constants.py` 是公开上游协议常量（包含服务认证使用的固定常量），不包含本用户的密码、FTP 凭据或机身校准数据。`crypto.py` 保留 Sony 服务兼容 SHA-1 的原实现，归集代码不更换认证算法。

保留上游 `LICENSE.txt`。`crypto.py` 文件头另注明算法实现来自 [ajalt/python-sha1](https://github.com/ajalt/python-sha1)，因此同时收录其 `LICENSE-python-sha1.txt`，保留作者许可与归属。没有从本用户设备提取并分发私人密钥。
