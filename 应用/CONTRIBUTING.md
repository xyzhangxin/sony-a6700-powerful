# 参与开发

应用变更须遵守已确认的[跨平台与插件化方案](docs/跨平台与插件化方案.md)第0节与第4.1节。提交说明列出涉及层、接口／包版本、准备与验收流程变化、平台验证及未验证项；结构变化先修订同一设计和迁移说明。设计尚未实施，不为接入新功能复制平台业务逻辑或绕过既有守卫。

欢迎人工和 AI 辅助贡献。项目自有代码采用 GPL-3.0-only，提交内容需保留兼容的许可和来源。AI 可以分析和生成修改，提交者仍需理解改动、核对来源并报告实际执行的验证。

## 独立开始

将本工程作为仓库根目录，无需研究目录、私人固件或真实相机。阅读 [AI 上下文](docs/AI-CONTEXT.md) 找到需要修改的模块，然后创建自己的开发分支。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[usb,test]'
.venv/bin/python scripts/check.py --strict
# 原生模拟器可工作时：
.venv/bin/python scripts/check.py --native --strict
```

基础验证排除原生仿真但覆盖模拟 USB。没有测试依赖时，可先运行不带 `--strict` 的命令查看跳过原因；不能把跳过称为通过。源码运行的相机准备检查可用 `python -m a6700_unlock prepare`，不会枚举或认证相机。

Windows 贡献者可使用 `.venv\Scripts\python`；界面依赖、WinUSB 准备与原生打包见 [Windows 预览](docs/WINDOWS.md)。Windows 工作流在 GitHub 托管 runner 构建目录包并运行不访问相机的界面验收；硬件验收仍单独安排。

## 提交与评审

1. Fork 仓库并创建分支；一个 PR 解决一个明确问题，避免同时重排大段历史代码。
2. 根据下表补齐代码、文档和相应验证；修复失败场景时增加能够复现问题的测试。
3. 提交 PR，填写问题、行为变化、验证命令／结果与未覆盖范围。AI 辅助修改也使用相同流程，可简述使用范围，不提交私人对话。
4. PR 的无设备 CI 在 Linux／macOS／Windows 检查 Python 兼容性、模拟测试和打包。CI 配置随工程提供，远端仓库启用后才会真实运行；目前不宣称已在 GitHub 跑过。
5. 维护者评审后合并。涉及原生执行或设备布局的改动由维护者另行安排实机验收；提交者不必为普通文档、界面或事务模拟修复自行刷相机。

| 改动 | 需要同步的信息 |
|---|---|
| 向导／界面 | `docs/PRODUCT-FLOW.md`、准备状态／取消测试；保持人工与自动校验区分 |
| 功能新增或迁移 | 功能清单、修改点与证据、原始／已有／部分／未知状态测试；遵循清单 available 发布开关；未完成项不可进入自动选择或原生写入范围 |
| 设备映射／原生载荷 | 适配与固定掩码、构建产物及 manifest、模拟故障和回退测试，明确实机状态；源码未重建会被资源检查拒绝 |
| USB 连接与事务 | 模拟端点、写前记录、无重发及独立回读测试，必要时更新排查文档 |
| 许可或分发 | 根目录及包内法律资源一致；源码包能重建原生载荷，保留第三方许可 |
| 普通文档 | 确认描述和当前行为一致、链接有效；不要求为文案访问相机 |

验证证据请区分：代码检查、合成测试、模拟原生执行、实机只读、实机写入、重启后的实际功能观察。未执行项直接注明，不用推测补齐。

## 发布与分发

源码和 wheel 可分别构建；同名产物不会覆盖：

```sh
python scripts/build_source.py
python -m pip install 'setuptools>=77' wheel
python scripts/build_wheel.py --offline
```

将相同版本源码包和二进制放在同一发布位置，保留许可证与第三方归属。当前 wheel 属于开发交付；SwiftUI 桌面模拟预览可用 `python scripts/build_macos_preview.py` 在 macOS 构建，真实 USB 模式已接入；内置环境版使用 `scripts/build_macos_app.py`，维护者已验收当前macOS应用；更多机身与故障边界继续验证。

工作流 Action 用法参考官方 [checkout](https://github.com/actions/checkout) 和 [setup-python](https://github.com/actions/setup-python) 文档。CI 使用 `pull_request`，只授予代码读取权限，不使用真实相机、私人备份或设备凭据。不要把 PR 验证改成直接访问维护者电脑或插着相机的自托管 runner。

反馈时只提供问题描述、环境与脱敏后的结果。不要上传窗口前镜像、完整固件、校准数据、密钥或原始 ProductInfo；文件中的本机路径也需先移除。

## 工作区整洁

优先修改已有功能模块与测试，不创建同用途的 final／final2 副本。临时 Swift／Python 构建使用系统临时目录；报告在主工程 `output/运行/主题/任务/`，独立克隆可放 `reports/主题/任务/`。不要留下根目录 `main`、`.swift-module-cache/` 或 `.local-validation/`。`verified/` 和上游归属属于有意保留的证据，不作普通重复文件删除。

交付目录保存一套应用、对应源码与校验值；旧版成组放 `dist/历史版本/`，不同版本不覆盖。不要为仅文档或目录清理重新发布无代码变化的应用包。
