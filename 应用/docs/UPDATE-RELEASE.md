# 更新签名与发布运维

本入口对应 `package_updates.py`、`online_updates.py`、Worker `scripts/sign-index.mjs` 和 `data/update-trust.json`。更新只改变本机适配集，不直接改变相机。 独立 macOS 应用的 `desktop/runtime-dependencies.json` 还固定 cryptography 46.0.3、cffi 2.1.1（cp313 arm64）和 pycparser 3.12；构建时校验 wheel 哈希并将原生库裁剪到 arm64，缺少这些组件的包不能称为支持签名更新。

## 0.5.1 双平台发布（2026-11-21）

经维护者验收，基于冻结测试源码发布 v0.5.1，标签指向 `8b20d7391dc489d92c58193ae7790bd10899c287`。双平台共用应用源码，Mac arm64 更新签名通过校验。六个 GitHub 附件下载回读与本地一致，0.5.0 更新检查在两个平台均识别到 0.5.1。[GitHub 发布](https://github.com/xyzhangxin/sony-a6700-powerful/releases/tag/v0.5.1) · [发行目录](../dist/v0.5.1/) · [发布验收](../../output/运行/快门声音/051发布/验收摘要.md)

## 当前信任根（2026-11-08）

- `key_id`：`a6700-update-2026-01`
- 公钥 SHA-256：`060c9e7657f0b34b44d685d5125dda6ab5b5da79702d4eb4ed016cf8ea0dc083`
- 公钥配置于应用 `data/update-trust.json` 和 Worker `updates/catalog.json`；`stable` sequence=1 已部署，`testing` 索引仍未配置。
- 私钥仅保存在仓库外 `~/Library/Application Support/sony-a6700-ftp/update-signing/ed25519-private.pem`，目录 0700、文件 0600。不得复制到仓库、Worker、GitHub、日志或 AgentMemory；维护者需另行做离线备份，丢失信任根不能靠同一根自证恢复，需通过应用更新更换。

## 本地候选

目录：`output/运行/应用/更新发布候选/`。`release-manifest.json` 记录公开指纹和全部校验值。

- `adapter-set-a6700-2.00.json`：当前精确适配集附件。
- `payload-stable.json`：stable sequence=1 的索引内容。
- `envelope-stable.json`：Worker 离线工具签出的完整封装。
- `public-key.json`：仅公钥及指纹，无私钥。

当前 URL 指向拟定的 GitHub Release 附件，但附件和索引均未上传／部署。不要把候选路径当成已发布地址。

## 签名与验收

使用 Worker 工具签名；私钥只在签名时读取，工具不联网、不生成密钥：

```sh
node release/services/cloud/scripts/sign-index.mjs   output/运行/应用/更新发布候选/payload-stable.json   "$HOME/Library/Application Support/sony-a6700-ftp/update-signing/ed25519-private.pem"   a6700-update-2026-01   output/运行/应用/更新发布候选/envelope-stable.json
```

应用使用 `load_trust_keys()` 取得固定公钥，再由 `verify_index_envelope` 和 `LocalUpdateCache` 完成验签、附件 SHA-256 校验与缓存激活。正式启用前必须重复这一步，并检查 `release-manifest.json` 的附件／payload／envelope 哈希。

## 用户审核后的上线顺序

1. 用户审核候选源码、附件、payload、公钥和签名，不在本轮自动执行。
2. 上传精确附件到对应 GitHub Release，回读附件长度和 SHA-256；不上传私钥。
3. 通过受控发布配置向 Worker 提供 `UPDATE_STABLE`（完整 envelope）、`UPDATE_PUBLIC_KEY` 和 `UPDATE_KEY_ID`；私钥不得进入 Worker 环境。
4. 检查 stable 索引可获取且签名有效，再用桌面“检查更新”做本机激活；相机写入仍需用户另行选择并执行。
5. 撤回时发布更高 sequence 的索引并列入撤回摘要；只停止新写入，不自动降级相机。过期索引只用于离线缓存，不能激活新包。

部署、GitHub 上传、密钥轮换和生产配置变更均须重新审核。若候选已过期，先更新时间与序号并重新签名，不能沿用旧索引。

## 独立 App 验证结果（2026-11-08）

独立 App 已完成 394 项全量严格测试、16 个原生资源签名检查、成品模拟／物理环境烟测，以及隔离内置 Python 的真实签名激活。cryptography、cffi 和 pycparser 均已随包提供，不需要用户安装 Python 依赖。该验证不替代用户发布审核，不上传或部署。

## 已部署记录（2026-11-08）

GitHub v0.4.1 附件已上传并回读一致。Worker版本`4889388c-4323-42ab-ac76-c382fd8ae80f`已部署 stable sequence=1；生产索引验签及独立Mac App在线激活通过。候选与内置适配集完全一致，不改变既有功能。Windows在线更新客户端尚未接入。

## FTP 修订 2 候选（2026-11-10）

0.4.2 的 sequence=2 附件 SHA-256 `0845deb5204dc6e63452c0eb6e5fc37fe0f8c6dc39f18746c632c3f3da5fa027` 基于旧 FT-101 两字节假设，保留为历史候选，不再作为后续发布输入。2026-11-10 研究结论为：241 字节基础解锁与 134 字节自定义键矩阵均已实机验证；研究机身随后按用户要求全部回退，矩阵候选尚未合入 release。线上 stable sequence=1 不变；生成和上传新的在线更新包前必须重新走用户审核。

## 0.4.2 发布记录（2026-11-11）

- 用户审核文档后直接推送 `main`（`64533ee`，随后 `cfe4fb7`、`146f6b2`、`cc0425a`、`adc9608` 修正源码包构建与 CI）；标签与 `main` 同步到 `adc9608`，CI 六个任务全部通过。
- GitHub 单个 Release `v0.4.2` 同时提供 macOS arm64 与 Windows x64 安装包，另含两平台共用源码包、`README-zh.md` 和合并的 `SHA256SUMS.txt`；附件回读长度与 SHA-256 一致。按用户意见不再拆分为 `v0.4.2-windows` 单独发布。
- 安装包 SHA-256：macOS `e0061e9be8aa84694eb3bae85bc358407152a9b7dfc7911c4ffbfb84a4d435cc`；Windows `2d8813a2436aea3ec6ac796f9c26951b66014d98fd30f91f5e91b5aff04c2528`；源码包 `9f4d527d4771c3cc59a67b361e06cec8670745fc9803737d57985d46fb0ec5c6`。
- 线上 stable 索引仍为 sequence=1，未部署 0.4.2 更新包；应用内“检查更新”当前不会拉到新包。
- 发布后修正：`build_wheel.py` 允许 FL23 `variants/{2,3}/carrier.bin`；桌面工作进程测试显式使用 UTF-8 管道；工作流为应用任务设置 `PYTHONIOENCODING=utf-8`，构建脚本输出改为可替换编码。两个 Release 的源码包已替换为修正版并重新回读校验；标签随修正提交移动。

- 2026-11-12 下载启动排查：Edge 下载的 macOS ZIP 与 GitHub 公布摘要一致，ZIP CRC 和解压 App 的 `codesign --verify --deep --strict` 通过。发布 ZIP 内为 `a6700-042-parity.app`，`spctl` 拒绝，用户启动日志记录了 Gatekeeper 拒绝和 `amfid` 的临时签名提示。安装说明应列出实际解压名称与 Apple 的“仍要打开”步骤。后续 Mac 发行件在浏览器下载状态下验收首次打开，核对解压名称并排除多余 AppleDouble 元数据。

## 0.4.3 双平台发布（2026-11-15）

基于 v0.4.2 发行源码，两端纳入首次连接 `04000010` 修复、合并准备页与失败日志上传。保留已发布功能，暂缓自定义快门声、音频市场和待部署的使用统计。

[成组交付](../dist/v0.4.3/)包含两个安装包、一份共用源码和发布说明。Mac 通过 335 项严格测试和成品回归；Windows 通过 326 项严格测试及成品界面回归。此前专项修复已获 Mac 实机通过，最终组合包待复测。维护者确认后推送 `d3a1ebb`，[GitHub CI](https://github.com/xyzhangxin/sony-a6700-powerful/actions/runs/43730303033) 六项通过。两个安装包及三份配套附件同发至 [v0.4.3](https://github.com/xyzhangxin/sony-a6700-powerful/releases/tag/v0.4.3)，下载回读的大小和 SHA-256 全部匹配。[验收摘要](../../output/运行/应用/043双平台/验收摘要.md)

### 在线更新复核（2026-11-15）

- 用匹配 GitHub 发行摘要的 macOS 0.4.2 与 0.4.3 成品，在独立临时缓存运行各自内置的更新器。两端均拉取签名索引和 `a6700-2.00-builtin` 附件（HTTP 200），完成验签与缓存激活（stable sequence=1）。再次检查返回 HTTP 304 并保留有效缓存。生产通道当前只有原有适配集，未测试上线新功能包后的激活。
- 0.4.3 Mac 的相机模式启动及菜单、两版 Windows 菜单均调用功能包检查。客户端版本提醒验收未通过：两版均未查询 GitHub 最新客户端版本或比较安装版本，0.4.2 因此不会发现已发布的 0.4.3。支持“启动时有新版就提醒”需要新增客户端版本检查，现有安装版需要先手动更新一次才能使用该入口。
- GitHub 0.4.2 Mac 成品内的 `online_updates.py`、`desktop/update_service.py` 与同版源码附件有差异，本次按发布二进制内的实际模块执行。0.4.3 Mac 和两版 Windows 的这两个模块与各自发行源码一致。Windows 本次做发布源码与包内模块核对，未在 Windows 实机复测在线请求。


### 0.4.4.dev1 客户端更新（2026-11-16，本地开发）

基于已发布的 `v0.4.3`，默认相机模式启动时后台查询 GitHub，发现新版可在标题栏内下载。菜单支持手动检查、切换启动检查，以及单独检查相机功能包。下载核对长度与 GitHub 公布的 SHA-256，结果可在 Finder 或资源管理器查看。自动安装和重启尚未接通。

0.4.3 正式包未纳入 PV、UV、刷机成功和报错埋点。用户可以在失败结果页主动上传脱敏异常报告，联系邮箱选填。

见[本地验收及交付](../../output/运行/应用/客户端版本检查/验收摘要.md)。

### 0.4.2 更新测试与 0.4.3 修订候选（2026-11-16）

更新检查及应用内下载已回移到本地 0.4.2 更新测试包。Apple Silicon Mac 已启动并发现线上 0.4.3；待维护者确认。0.4.3 修订候选本地接入可选统计，默认关闭。生产统计事件接口仍需配套的 0004 迁移和 Worker 发布，当前未部署。现有发行附件保持原样。


### Mac 应用内安装可行性（2026-11-16）

用 0.4.2／0.4.3 正式 Mac 应用包的临时副本，在同一卷上验证了系统文件替换、保留旧包、回退和替换前后的代码签名完整性。实际安装目录、本机已装 App 和相机均未改动。此验证只涵盖文件替换与回退，更新器中的退出、重启和启动确认尚未接入。

计划中的安装入口由用户确认，在相机会话正常结束后工作。发行附件先验项目签名并暂存，再替换且保留旧包，通过新版启动确认后再清理。macOS 的目录权限和 Gatekeeper 仍适用。项目更新签名独立于 Apple 的 Developer ID 及公证。现有版本需要先手动安装首个具备安装能力的版本。[Apple 文件替换接口](https://developer.apple.com/documentation/foundation/filemanager/replaceitemat%28_%3Awithitemat%3Abackupitemname%3Aoptions%3A%29?changes=l_8)

### Mac 应用内安装候选（2026-11-16）

当新版同时提供平台 ZIP 和分离式 `.ed25519` 附件时，Mac 显示「更新并重启」。应用下载并暂存 ZIP，原生助手先验证项目签名，再核对版本、应用标识和包内代码签名。本地服务须正常结束并确认连接关闭，然后旧应用退出，助手在同一卷完成替换。新版通过环境检查后报告本次启动，旧包随后清理；未通过时，助手正常结束新版并恢复及重开旧版。没有分离式附件的历史发行仍支持下载，Windows 沿用下载流程。

发行工具 `scripts/sign_app_update.py` 为完成审核的 Mac ZIP 生成同名 `.ed25519`。安装签名绑定类型、平台、包标识、版本、长度和摘要。只把 ZIP、签名和源码上传到发行页，私钥留在维护者本机，不放 Worker。已发布的 v0.4.3 仍是旧附件，启用安装能力和分发签名须走维护者审核。

[Mac 本地候选及验收](../../output/运行/应用/客户端版本检查/Mac应用内安装候选/README.md)覆盖成功重启、签名拒绝与故意启动失败后的回退。用独立测试包和测试签名，GitHub 线上发布未参与。

自动安装与可选统计已合入以正式 `d3a1ebb` 为基线的本地 `release/v0.4.3-revision` 工作树。合并后 344 项原生严格回归通过，候选源码与既有本地 Mac 签名包一致。生产健康检查仍报告 Cloud 0.1.0，统计服务端 0.2.0 的 37 项本地测试通过，事件接收和聚合待应用 `0004` 迁移并发布 Worker。

### 0.4.3 使用统计默认开启（2026-11-16）

本地修订候选对无保存设置的安装默认开启使用统计，在准备页和菜单提供关闭入口。已有关闭设置继续生效。正常桌面相机模式启用上报，演示和离线自检不启用；异常诊断日志仍需主动提交。服务端接受默认告知版本 `usage-default-on-v1` 和旧的 `usage-v1`，继续使用既有 `0004` 迁移。已上线的 GitHub 包和生产服务维持原状态。

### 0.4.3 修订版与生产统计上线（2026-11-16）

按维护者授权更新原 [GitHub v0.4.3](https://github.com/xyzhangxin/sony-a6700-powerful/releases/tag/v0.4.3)。原 Git 标签保留初版，修订版运行源码绑定 `5931304`，发布说明与 `main` 均链接对应源码。已回读校验 Mac、Windows、对应源码、项目签名及 README。初版在本地保留，首个修订版成组存放于 [历史发行目录](../dist/历史版本/v0.4.3-修订版/)。初版用户手动换装一次，修订版支持检查后续更高版本。Mac 验签后可更新并重启，Windows 下载后手动安装。

生产 Cloud 0.2.0 已备份 D1 并完成 `0004_usage_events.sql`，Worker `4c6a3bf4-e580-4c7a-a944-d988f1efa8f6` 正常服务。使用统计、去重、删除、管理鉴权均通过线上合成验证，已清理测试记录。现有相机功能包的签名索引与部署前一致。[发布验收](../../output/运行/应用/043修订发布/验收摘要.md)

### 0.4.3 诊断修订版（2026-11-16）

该次发行运行源码对应 `1bc6193`，六个附件下载回读一致。两端共用脱敏预检计划、执行结果和可选后台故障报告。该修订默认关闭后台报告。Windows 反馈记录与附件统一使用 UTF-8。[历史发行目录](../dist/历史版本/v0.4.3-诊断修订版-默认关闭/) · [发布验收](../../output/运行/应用/043日志发布/验收摘要.md)

### 0.4.3 后台诊断默认设置（2026-11-16）

按维护者确认，无保存设置时默认开启后台故障报告，保存过关闭的用户保持关闭。Mac 与 Windows 附件对应提交 `8665c4b`。原 GitHub 发行页已替换附件与说明，公开变更摘要聚焦首次连接 `04000010` 修复和异常日志上传。[发行目录](../dist/v0.4.3-诊断修订版/) · [发布验收](../../output/运行/应用/043默认诊断发布/验收摘要.md)
