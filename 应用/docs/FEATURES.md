# 一期功能与具体修改点

由 `scripts/render_features.py` 根据安装包内同一份 JSON 清单生成。0.2 测试版只允许四项功能写入。未完成的两项保留历史字段供识别和审计，解锁／关闭／恢复均禁用；下表历史字节数不等于本版写入范围。功能历史实测不代表统一 USB 工具已完成实机验收。

## 范围

| 功能（本版状态） | 系统文件相对原始变化字节 | WBI 相对原始变化字节 | CP 分区相对原始变化字节 | 证据与边界 |
|---|---:|---:|---:|---|
| 原生 FTP 菜单、开关及设置解锁（可测试） | 89 | 151 | 0 | RAW+JPEG 上传及回家联网传输有正向实测；跨关机队列未全面验证；回放未传输筛选不在本期范围 |
| 关机快门帘及静音联动解锁（可测试） | 9 | 21 | 0 | 正常关机闭帘、开机释放、设置保存和静音联动有用户实测；长期可靠性及取电池保持未验证 |
| 灵活曝光（可测试） | 17 | 39 | 0 | 2026-10-19 实机写入 App 17＋WBI 39 字节，原生执行与独立回读一致；菜单已出现且可选；三项独立控制、实际曝光与关机保存已有用户正向反馈；与快门声音组合、长期稳定性及跨机身一致性未完全验证 |
| 电子快门声音（可测试） | 2 | 4 | 1 | 声音增量与灵活曝光字段不重叠；sound-after-exposure 是验证基线，不是运行时依赖；单独选择声音只改声音字段；2026-10-20 实机：菜单 4 项正常，电子快门有“克哒”声，“除快门以外”与“关”静音，重启保持由用户确认；CP 字节位于 /dev/nflasha8 扇区 11932（扇区内 256）；回退由同一字段写回 00，不影响菜单 |
| 位移多帧合成（Pixel Shift）（暂禁用） | 3 | 9 | 0 | 2026-10-20 实机写入 App 3＋WBI 9 字节，8 个扇区；菜单与合成流程、实际成像和关机保存尚待完整验收；仅支持当前 sound-after-exposure 基线 |
| 动态增强防抖（Dynamic Active）（暂禁用） | 2 | 4 | 0 | 历史实机写入及独立回读通过，菜单显示和可选择已由用户确认；实际功能仍未完成；普通录像适用；高帧率、S&Q、镜头开关等原有限制保留；关闭前请在相机中切回增强或标准；本工具不修改当前防抖设置值；当前整合版尚未实机安装／关闭验收；录像效果、重启保持的详细对照记录未补齐 |

共 355 个字段定义；全部功能相对原始内容改变 App 122 字节、WBI 228 字节、CP 分区 1 字节。这是六项历史定义的合计；下表固定清单仅前四项可写；0.3.0另通过独立FL23安装包提供创意外观扩展，可与原四项一起选择。字段可能为多字节计数，字段数与变化字节数不同。

FTP 参数包括开关 `0x5a5`，连接方式 `0x5a6`、服务器 `0x5a8`、自动传输 `0x5aa`，以及 `0x5ac～0x5b0` 的照片格式／尺寸、视频目标和节能设置。RAW+JPEG 双传使用原生选项，不新增传输程序。

快门功能包括主项 `0x6b2` 与静音联动 `0x4bb` 的原生菜单、机型支持和 WBI 对应状态；不强制设置为开启，不修改 CP、机械驱动或关机动作代码。

灵活曝光包括 `0x497`、`0x50c`、`0x50d`、`0x50e` 的机型支持、菜单权限和 WBI 状态；它已作为可选功能纳入统一清单，不再作为独立试用包。

电子快门声音使用 `sound-after-exposure` 变体，依赖灵活曝光已安装；菜单只保留 4 项（`0xabe` 的 gate 固定为 00，因为相机不保存第 5 项），并包含 `0xabf/0xac0` 菜单权限、声音门控、WBI 状态和 `/dev/nflasha8` 扇区 11932 的 CP 触发字节（`00→03`）。

位移多帧合成当前禁用。历史字段包含 `0x4b5/0x730/0x731` 的 App 静态机型可用性、WBI 运行时可用性和 `0x214/0x820` 菜单权限；不新增合成算法。

动态增强防抖当前禁用。历史清单仅合并已落地的 6 字节：App `0x4b7b93a/0x3ed654c`，WBI `0x2495b3a/0x3e2839c/0x743f53c/0x10c4701`。打开列 3 菜单及 ACTIVE_PLUS 值支持，移除一项不支持贡献；计数高三字节仍由未管理字节哈希守卫。没有开放列 6/7/8、绕过录像条件或移植 CP 算法；关闭前先在相机切回增强或标准。详见 [动态增强集成](动态增强防抖.md)。

回放筛选、回放文案和其他研究候选不纳入本期。关闭计划只撤销本清单列出的修改，不等于刷写整套官方固件或恢复整机设置。

## 历史差异的处理

最初 69 个权限字节是 WBI 中当前机型的父／子／叶权限。历史已验证 App 菜单候选相对原始内容改变 81 字节，多出的 12 字节位于 FTP 父菜单权限列。本清单明确保留这 12 处历史修改，以重现已验证 App；它们不是新增功能，也不能误计为 WBI 修改。关闭时这 12 处也恢复原值。

空白重复父项 `0x262` 的目标为隐藏：其未解锁值与最终解锁值均为 `00`，另接受早期 `01` 值并生成关闭该重复入口的差异。它不会被计算成一个新解锁功能。

WBI 引用计数仅移除已知样本中一份机型不支持贡献，保留其他贡献。本阶段只接受完整已知字段值和完整归一化镜像哈希；不在实时状态中盲目套用这些计数。

## 地址与数据视图

- `app` 是已解密 `/lib/appFw.so` 的 ELF **文件偏移**，不是 ELF 虚拟地址或分区偏移。
- `wbi` 是已解密且提取的 WBI2 逻辑内容偏移，保留原头。不是 Pico 裸 eMMC 密文地址。
- 表中解锁值是本期最终目标，已合并历史修改；已知值包含未解锁、最终值和明确支持的历史中间值。
- 所有记录均只修改固定长度字段；原型计划不包含将文件偏移映射到真实闪存的逻辑。

## 镜像身份

| 输入 | 长度 | 将已知字段还原后的 SHA-256 |
|---|---:|---|
| `app` | 89027992 | `e65fd595052be5d0e98ce8000dfcb94f90a2f8309de70ef140375052009b0ef3` |
| `cpapp` | 25008128 | `f12fa3699da841786b9d55314c7e05c8fa27c2d1b60a0274e40026b4399a91ed` |
| `wbi` | 248376320 | `bba227d90fb947642618017e109d544069be2687985b6be1f2b84f2766a85885` |

## 原生 FTP 菜单、开关及设置解锁

历史证据索引（研究工程内）：`docs/FTP解锁复盘与后续功能准备.md`。

| 字段 ID | 视图 | 文件偏移 | 未解锁值 | 解锁值 | 已知值 | 修改用途 |
|---|---|---|---|---|---|---|
| `edit-002` | `app` | `0x3f17524` | `00` | `01` | `00, 01` | FTP switch 0x5a5 static model support |
| `edit-003` | `app` | `0x3f1752c` | `00` | `01` | `00, 01` | 0x5a6: setting_ftp_connect_method |
| `edit-004` | `app` | `0x3f17534` | `00` | `01` | `00, 01` | 0x5a8: setting_set_server |
| `edit-005` | `app` | `0x3f1753c` | `00` | `01` | `00, 01` | 0x5aa: setting_auto_ftp_transfer |
| `edit-006` | `app` | `0x3f17544` | `00` | `01` | `00, 01` | 0x5ac: setting_raw_jh_trans_trgt |
| `edit-007` | `app` | `0x3f1754c` | `00` | `01` | `00, 01` | 0x5ad: setting_jpeg_jpeg_transfer_img |
| `edit-008` | `app` | `0x3f17554` | `00` | `01` | `00, 01` | 0x5ae: setting_heif_heif_transfer_img |
| `edit-009` | `app` | `0x3f1755c` | `00` | `01` | `00, 01` | 0x5af: setting_transfer_target |
| `edit-010` | `app` | `0x3f17564` | `00` | `01` | `00, 01` | 0x5b0: setting_ftp_power_save |
| `edit-012` | `app` | `0x4bd7522` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-013` | `app` | `0x4bd7524` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-014` | `app` | `0x4bd7526` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-015` | `app` | `0x4bd7528` | `00` | `01` | `00, 01` | FTP parent menu 0x226 current-model permission |
| `edit-016` | `app` | `0x4bd752a` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-017` | `app` | `0x4bd752c` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-018` | `app` | `0x4bde292` | `00` | `01` | `00, 01` | FTP child menu 0x22d current-model permission |
| `edit-020` | `app` | `0x4bee732` | `00` | `01` | `00, 01` | FTP child menu 0x227 current-model permission |
| `edit-021` | `app` | `0x4bee7e2` | `00` | `01` | `00, 01` | FTP child menu 0x22f current-model permission |
| `edit-022` | `app` | `0x4bff11a` | `00` | `01` | `00, 01` | FTP child menu 0x23c current-model permission |
| `edit-023` | `app` | `0x4c01f9a` | `00` | `01` | `00, 01` | FTP leaf menu 0x903 current-model permission |
| `edit-024` | `app` | `0x4c0203a` | `00` | `01` | `00, 01` | FTP leaf menu 0x904 current-model permission |
| `edit-025` | `app` | `0x4c09cd2` | `00` | `01` | `00, 01` | FTP leaf menu 0x924 current-model permission |
| `edit-026` | `app` | `0x4c09d72` | `00` | `01` | `00, 01` | FTP leaf menu 0x925 current-model permission |
| `edit-027` | `app` | `0x4c09e12` | `00` | `01` | `00, 01` | FTP leaf menu 0x90a current-model permission |
| `edit-028` | `app` | `0x4c09eb2` | `00` | `01` | `00, 01` | FTP leaf menu 0x90b current-model permission |
| `edit-029` | `app` | `0x4c09f52` | `00` | `01` | `00, 01` | FTP leaf menu 0x90c current-model permission |
| `edit-030` | `app` | `0x4c09ff2` | `00` | `01` | `00, 01` | FTP leaf menu 0x90d current-model permission |
| `edit-031` | `app` | `0x4c0a092` | `00` | `01` | `00, 01` | FTP leaf menu 0x90e current-model permission |
| `edit-032` | `app` | `0x4c0a132` | `00` | `01` | `00, 01` | FTP leaf menu 0x90f current-model permission |
| `edit-033` | `app` | `0x4c0a1d2` | `00` | `01` | `00, 01` | FTP leaf menu 0x910 current-model permission |
| `edit-034` | `app` | `0x4c0a272` | `00` | `01` | `00, 01` | FTP leaf menu 0x911 current-model permission |
| `edit-035` | `app` | `0x4c0a312` | `00` | `01` | `00, 01` | FTP leaf menu 0x912 current-model permission |
| `edit-036` | `app` | `0x4c0b1a2` | `00` | `01` | `00, 01` | FTP child menu 0x236 current-model permission |
| `edit-037` | `app` | `0x4c11232` | `00` | `01` | `00, 01` | FTP leaf menu 0x918 current-model permission |
| `edit-038` | `app` | `0x4c112d2` | `00` | `01` | `00, 01` | FTP leaf menu 0x919 current-model permission |
| `edit-039` | `app` | `0x4c11ea2` | `00` | `01` | `00, 01` | FTP child menu 0x235 current-model permission |
| `edit-040` | `app` | `0x4c11f52` | `00` | `01` | `00, 01` | FTP child menu 0x228 current-model permission |
| `edit-041` | `app` | `0x4c12002` | `00` | `01` | `00, 01` | FTP child menu 0x238 current-model permission |
| `edit-042` | `app` | `0x4c16bce` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-043` | `app` | `0x4c16bd0` | `00` | `00` | `00, 01` | FTP parent menu 0x262 current-model permission; parent:0x262: hide duplicate blank FTP parent |
| `edit-044` | `app` | `0x4c16bd2` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-045` | `app` | `0x4c16bd4` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-046` | `app` | `0x4c16bd6` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-047` | `app` | `0x4c16bd8` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-048` | `app` | `0x4c16bda` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-049` | `app` | `0x4c16bdc` | `00` | `01` | `00, 01` | historically validated FTP parent permission column (app-only) |
| `edit-050` | `app` | `0x4c17bd2` | `00` | `01` | `00, 01` | FTP leaf menu 0x916 current-model permission |
| `edit-051` | `app` | `0x4c17c72` | `00` | `01` | `00, 01` | FTP leaf menu 0x917 current-model permission |
| `edit-052` | `app` | `0x4c1b402` | `00` | `01` | `00, 01` | FTP leaf menu 0x906 current-model permission |
| `edit-053` | `app` | `0x4c1b4a2` | `00` | `01` | `00, 01` | FTP leaf menu 0x907 current-model permission |
| `edit-054` | `app` | `0x4c1b5e2` | `00` | `01` | `00, 01` | FTP leaf menu 0x909 current-model permission |
| `edit-055` | `app` | `0x4c1b682` | `00` | `01` | `00, 01` | FTP leaf menu 0x91f current-model permission |
| `edit-056` | `app` | `0x4c1b722` | `00` | `01` | `00, 01` | FTP leaf menu 0x920 current-model permission |
| `edit-057` | `app` | `0x4c1b7c2` | `00` | `01` | `00, 01` | FTP leaf menu 0x91d current-model permission |
| `edit-058` | `app` | `0x4c1b862` | `00` | `01` | `00, 01` | FTP leaf menu 0x91e current-model permission |
| `edit-059` | `app` | `0x4c1c082` | `00` | `01` | `00, 01` | FTP leaf menu 0x93e current-model permission |
| `edit-060` | `app` | `0x4c1c122` | `00` | `01` | `00, 01` | FTP leaf menu 0x93f current-model permission |
| `edit-061` | `app` | `0x4c1c1c2` | `00` | `01` | `00, 01` | FTP leaf menu 0x93c current-model permission |
| `edit-062` | `app` | `0x4c1c262` | `00` | `01` | `00, 01` | FTP leaf menu 0x93d current-model permission |
| `edit-063` | `app` | `0x4c1c302` | `00` | `01` | `00, 01` | FTP leaf menu 0x934 current-model permission |
| `edit-064` | `app` | `0x4c1c3a2` | `00` | `01` | `00, 01` | FTP leaf menu 0x935 current-model permission |
| `edit-065` | `app` | `0x4c1c442` | `00` | `01` | `00, 01` | FTP leaf menu 0x936 current-model permission |
| `edit-066` | `app` | `0x4c1c4e2` | `00` | `01` | `00, 01` | FTP leaf menu 0x937 current-model permission |
| `edit-068` | `app` | `0x4c1dbb2` | `00` | `01` | `00, 01` | FTP child menu 0x23b current-model permission |
| `edit-071` | `app` | `0x4c2215a` | `00` | `01` | `00, 01` | FTP leaf menu 0x932 current-model permission |
| `edit-072` | `app` | `0x4c221fa` | `00` | `01` | `00, 01` | FTP leaf menu 0x933 current-model permission |
| `edit-073` | `app` | `0x4c2502a` | `00` | `01` | `00, 01` | FTP child menu 0x234 current-model permission |
| `edit-074` | `app` | `0x4c2518a` | `00` | `01` | `00, 01` | FTP child menu 0x23a current-model permission |
| `edit-075` | `app` | `0x4c2523a` | `00` | `01` | `00, 01` | FTP child menu 0x239 current-model permission |
| `edit-077` | `app` | `0x4c2814a` | `00` | `01` | `00, 01` | FTP leaf menu 0x938 current-model permission |
| `edit-078` | `app` | `0x4c281ea` | `00` | `01` | `00, 01` | FTP leaf menu 0x939 current-model permission |
| `edit-079` | `app` | `0x4c2828a` | `00` | `01` | `00, 01` | FTP leaf menu 0x93a current-model permission |
| `edit-080` | `app` | `0x4c2832a` | `00` | `01` | `00, 01` | FTP leaf menu 0x93b current-model permission |
| `edit-081` | `app` | `0x4c283ca` | `00` | `01` | `00, 01` | FTP leaf menu 0x92f current-model permission |
| `edit-082` | `app` | `0x4c2846a` | `00` | `01` | `00, 01` | FTP leaf menu 0x930 current-model permission |
| `edit-083` | `app` | `0x4c2850a` | `00` | `01` | `00, 01` | FTP leaf menu 0x931 current-model permission |
| `edit-086` | `app` | `0x4c2ec3a` | `00` | `01` | `00, 01` | FTP child menu 0x237 current-model permission |
| `edit-087` | `app` | `0x4c30a5a` | `00` | `01` | `00, 01` | FTP leaf menu 0x913 current-model permission |
| `edit-088` | `app` | `0x4c30afa` | `00` | `01` | `00, 01` | FTP leaf menu 0x914 current-model permission |
| `edit-089` | `app` | `0x4c30b9a` | `00` | `01` | `00, 01` | FTP leaf menu 0x915 current-model permission |
| `edit-090` | `app` | `0x4c30c3a` | `00` | `01` | `00, 01` | FTP leaf menu 0x921 current-model permission |
| `edit-091` | `app` | `0x4c30cda` | `00` | `01` | `00, 01` | FTP leaf menu 0x922 current-model permission |
| `edit-092` | `app` | `0x4c30d7a` | `00` | `01` | `00, 01` | FTP leaf menu 0x923 current-model permission |
| `edit-093` | `app` | `0x4c3177a` | `00` | `01` | `00, 01` | FTP leaf menu 0x928 current-model permission |
| `edit-094` | `app` | `0x4c3195a` | `00` | `01` | `00, 01` | FTP leaf menu 0x92b current-model permission |
| `edit-095` | `app` | `0x4c319fa` | `00` | `01` | `00, 01` | FTP leaf menu 0x92c current-model permission |
| `edit-096` | `app` | `0x4c3598a` | `00` | `01` | `00, 01` | FTP leaf menu 0x91a current-model permission |
| `edit-097` | `app` | `0x4c35a2a` | `00` | `01` | `00, 01` | FTP leaf menu 0x91b current-model permission |
| `edit-098` | `app` | `0x4c35aca` | `00` | `01` | `00, 01` | FTP leaf menu 0x91c current-model permission |
| `edit-099` | `app` | `0x4c45f6a` | `00` | `01` | `00, 01` | FTP child menu 0x22e current-model permission |
| `edit-103` | `wbi` | `0x2521728` | `00` | `01` | `00, 01` | FTP parent menu 0x226 current-model permission in snapshot |
| `edit-104` | `wbi` | `0x2538492` | `00` | `01` | `00, 01` | FTP child menu 0x22d current-model permission in snapshot |
| `edit-106` | `wbi` | `0x2548932` | `00` | `01` | `00, 01` | FTP child menu 0x227 current-model permission in snapshot |
| `edit-107` | `wbi` | `0x25489e2` | `00` | `01` | `00, 01` | FTP child menu 0x22f current-model permission in snapshot |
| `edit-108` | `wbi` | `0x257523a` | `00` | `01` | `00, 01` | FTP leaf menu 0x904 current-model permission in snapshot |
| `edit-109` | `wbi` | `0x257719a` | `00` | `01` | `00, 01` | FTP leaf menu 0x903 current-model permission in snapshot |
| `edit-110` | `wbi` | `0x257831a` | `00` | `01` | `00, 01` | FTP child menu 0x23c current-model permission in snapshot |
| `edit-111` | `wbi` | `0x2584ed2` | `00` | `01` | `00, 01` | FTP leaf menu 0x924 current-model permission in snapshot |
| `edit-112` | `wbi` | `0x2584f72` | `00` | `01` | `00, 01` | FTP leaf menu 0x925 current-model permission in snapshot |
| `edit-113` | `wbi` | `0x2585012` | `00` | `01` | `00, 01` | FTP leaf menu 0x90a current-model permission in snapshot |
| `edit-114` | `wbi` | `0x25850b2` | `00` | `01` | `00, 01` | FTP leaf menu 0x90b current-model permission in snapshot |
| `edit-115` | `wbi` | `0x2585152` | `00` | `01` | `00, 01` | FTP leaf menu 0x90c current-model permission in snapshot |
| `edit-116` | `wbi` | `0x25851f2` | `00` | `01` | `00, 01` | FTP leaf menu 0x90d current-model permission in snapshot |
| `edit-117` | `wbi` | `0x2585292` | `00` | `01` | `00, 01` | FTP leaf menu 0x90e current-model permission in snapshot |
| `edit-118` | `wbi` | `0x2585332` | `00` | `01` | `00, 01` | FTP leaf menu 0x90f current-model permission in snapshot |
| `edit-119` | `wbi` | `0x25853d2` | `00` | `01` | `00, 01` | FTP leaf menu 0x910 current-model permission in snapshot |
| `edit-120` | `wbi` | `0x2585472` | `00` | `01` | `00, 01` | FTP leaf menu 0x911 current-model permission in snapshot |
| `edit-121` | `wbi` | `0x2585512` | `00` | `01` | `00, 01` | FTP leaf menu 0x912 current-model permission in snapshot |
| `edit-122` | `wbi` | `0x25863a2` | `00` | `01` | `00, 01` | FTP child menu 0x236 current-model permission in snapshot |
| `edit-123` | `wbi` | `0x258c432` | `00` | `01` | `00, 01` | FTP leaf menu 0x918 current-model permission in snapshot |
| `edit-124` | `wbi` | `0x258c4d2` | `00` | `01` | `00, 01` | FTP leaf menu 0x919 current-model permission in snapshot |
| `edit-125` | `wbi` | `0x258d0a2` | `00` | `01` | `00, 01` | FTP child menu 0x235 current-model permission in snapshot |
| `edit-126` | `wbi` | `0x258d152` | `00` | `01` | `00, 01` | FTP child menu 0x228 current-model permission in snapshot |
| `edit-127` | `wbi` | `0x258d202` | `00` | `01` | `00, 01` | FTP child menu 0x238 current-model permission in snapshot |
| `edit-128` | `wbi` | `0x2591dd0` | `00` | `00` | `00, 01` | FTP parent menu 0x262 current-model permission in snapshot; parent:0x262: hide duplicate blank FTP parent |
| `edit-129` | `wbi` | `0x2592dd2` | `00` | `01` | `00, 01` | FTP leaf menu 0x916 current-model permission in snapshot |
| `edit-130` | `wbi` | `0x2592e72` | `00` | `01` | `00, 01` | FTP leaf menu 0x917 current-model permission in snapshot |
| `edit-132` | `wbi` | `0x25a4db2` | `00` | `01` | `00, 01` | FTP child menu 0x23b current-model permission in snapshot |
| `edit-133` | `wbi` | `0x25a5282` | `00` | `01` | `00, 01` | FTP leaf menu 0x93e current-model permission in snapshot |
| `edit-134` | `wbi` | `0x25a5322` | `00` | `01` | `00, 01` | FTP leaf menu 0x93f current-model permission in snapshot |
| `edit-135` | `wbi` | `0x25a53c2` | `00` | `01` | `00, 01` | FTP leaf menu 0x93c current-model permission in snapshot |
| `edit-136` | `wbi` | `0x25a5462` | `00` | `01` | `00, 01` | FTP leaf menu 0x93d current-model permission in snapshot |
| `edit-137` | `wbi` | `0x25a5502` | `00` | `01` | `00, 01` | FTP leaf menu 0x934 current-model permission in snapshot |
| `edit-138` | `wbi` | `0x25a55a2` | `00` | `01` | `00, 01` | FTP leaf menu 0x935 current-model permission in snapshot |
| `edit-139` | `wbi` | `0x25a5642` | `00` | `01` | `00, 01` | FTP leaf menu 0x936 current-model permission in snapshot |
| `edit-140` | `wbi` | `0x25a56e2` | `00` | `01` | `00, 01` | FTP leaf menu 0x937 current-model permission in snapshot |
| `edit-141` | `wbi` | `0x25a6602` | `00` | `01` | `00, 01` | FTP leaf menu 0x906 current-model permission in snapshot |
| `edit-142` | `wbi` | `0x25a66a2` | `00` | `01` | `00, 01` | FTP leaf menu 0x907 current-model permission in snapshot |
| `edit-143` | `wbi` | `0x25a67e2` | `00` | `01` | `00, 01` | FTP leaf menu 0x909 current-model permission in snapshot |
| `edit-144` | `wbi` | `0x25a6882` | `00` | `01` | `00, 01` | FTP leaf menu 0x91f current-model permission in snapshot |
| `edit-145` | `wbi` | `0x25a6922` | `00` | `01` | `00, 01` | FTP leaf menu 0x920 current-model permission in snapshot |
| `edit-146` | `wbi` | `0x25a69c2` | `00` | `01` | `00, 01` | FTP leaf menu 0x91d current-model permission in snapshot |
| `edit-147` | `wbi` | `0x25a6a62` | `00` | `01` | `00, 01` | FTP leaf menu 0x91e current-model permission in snapshot |
| `edit-150` | `wbi` | `0x25ad35a` | `00` | `01` | `00, 01` | FTP leaf menu 0x932 current-model permission in snapshot |
| `edit-151` | `wbi` | `0x25ad3fa` | `00` | `01` | `00, 01` | FTP leaf menu 0x933 current-model permission in snapshot |
| `edit-152` | `wbi` | `0x25b022a` | `00` | `01` | `00, 01` | FTP child menu 0x234 current-model permission in snapshot |
| `edit-153` | `wbi` | `0x25b038a` | `00` | `01` | `00, 01` | FTP child menu 0x23a current-model permission in snapshot |
| `edit-154` | `wbi` | `0x25b043a` | `00` | `01` | `00, 01` | FTP child menu 0x239 current-model permission in snapshot |
| `edit-156` | `wbi` | `0x25b334a` | `00` | `01` | `00, 01` | FTP leaf menu 0x938 current-model permission in snapshot |
| `edit-157` | `wbi` | `0x25b33ea` | `00` | `01` | `00, 01` | FTP leaf menu 0x939 current-model permission in snapshot |
| `edit-158` | `wbi` | `0x25b348a` | `00` | `01` | `00, 01` | FTP leaf menu 0x93a current-model permission in snapshot |
| `edit-159` | `wbi` | `0x25b352a` | `00` | `01` | `00, 01` | FTP leaf menu 0x93b current-model permission in snapshot |
| `edit-160` | `wbi` | `0x25b35ca` | `00` | `01` | `00, 01` | FTP leaf menu 0x92f current-model permission in snapshot |
| `edit-161` | `wbi` | `0x25b366a` | `00` | `01` | `00, 01` | FTP leaf menu 0x930 current-model permission in snapshot |
| `edit-162` | `wbi` | `0x25b370a` | `00` | `01` | `00, 01` | FTP leaf menu 0x931 current-model permission in snapshot |
| `edit-165` | `wbi` | `0x25b9e3a` | `00` | `01` | `00, 01` | FTP child menu 0x237 current-model permission in snapshot |
| `edit-166` | `wbi` | `0x25bbc5a` | `00` | `01` | `00, 01` | FTP leaf menu 0x913 current-model permission in snapshot |
| `edit-167` | `wbi` | `0x25bbcfa` | `00` | `01` | `00, 01` | FTP leaf menu 0x914 current-model permission in snapshot |
| `edit-168` | `wbi` | `0x25bbd9a` | `00` | `01` | `00, 01` | FTP leaf menu 0x915 current-model permission in snapshot |
| `edit-169` | `wbi` | `0x25bbe3a` | `00` | `01` | `00, 01` | FTP leaf menu 0x921 current-model permission in snapshot |
| `edit-170` | `wbi` | `0x25bbeda` | `00` | `01` | `00, 01` | FTP leaf menu 0x922 current-model permission in snapshot |
| `edit-171` | `wbi` | `0x25bbf7a` | `00` | `01` | `00, 01` | FTP leaf menu 0x923 current-model permission in snapshot |
| `edit-172` | `wbi` | `0x25bc97a` | `00` | `01` | `00, 01` | FTP leaf menu 0x928 current-model permission in snapshot |
| `edit-173` | `wbi` | `0x25bcb5a` | `00` | `01` | `00, 01` | FTP leaf menu 0x92b current-model permission in snapshot |
| `edit-174` | `wbi` | `0x25bcbfa` | `00` | `01` | `00, 01` | FTP leaf menu 0x92c current-model permission in snapshot |
| `edit-175` | `wbi` | `0x25c0b8a` | `00` | `01` | `00, 01` | FTP leaf menu 0x91a current-model permission in snapshot |
| `edit-176` | `wbi` | `0x25c0c2a` | `00` | `01` | `00, 01` | FTP leaf menu 0x91b current-model permission in snapshot |
| `edit-177` | `wbi` | `0x25c0cca` | `00` | `01` | `00, 01` | FTP leaf menu 0x91c current-model permission in snapshot |
| `edit-178` | `wbi` | `0x25e116a` | `00` | `01` | `00, 01` | FTP child menu 0x22e current-model permission in snapshot |
| `edit-181` | `wbi` | `0x7494f34` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a6: remove one unsupported-model reference |
| `edit-182` | `wbi` | `0x7494f38` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-183` | `wbi` | `0x7494f3c` | `01000000` | `00000000` | `00000000, 01000000` | 0x5aa: remove one unsupported-model reference |
| `edit-184` | `wbi` | `0x7494f44` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ac: remove one unsupported-model reference |
| `edit-185` | `wbi` | `0x7494f48` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ad: remove one unsupported-model reference |
| `edit-186` | `wbi` | `0x7494f4c` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ae: remove one unsupported-model reference |
| `edit-187` | `wbi` | `0x7494f50` | `01000000` | `00000000` | `00000000, 01000000` | 0x5af: remove one unsupported-model reference |
| `edit-188` | `wbi` | `0x7494f54` | `01000000` | `00000000` | `00000000, 01000000` | 0x5b0: remove one unsupported-model reference |
| `edit-189` | `wbi` | `0x7494f60` | `02000000` | `01000000` | `01000000, 02000000` | FTP switch 0x5a5: remove one unsupported contribution at 0x728 |
| `edit-190` | `wbi` | `0x74a958c` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a6: remove one unsupported-model reference |
| `edit-191` | `wbi` | `0x74a9590` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a6: remove one unsupported-model reference |
| `edit-192` | `wbi` | `0x74a9594` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a6: remove one unsupported-model reference |
| `edit-193` | `wbi` | `0x74a9598` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a6: remove one unsupported-model reference |
| `edit-194` | `wbi` | `0x74a95a0` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-195` | `wbi` | `0x74a95a4` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-196` | `wbi` | `0x74a95a8` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-197` | `wbi` | `0x74a95ac` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-198` | `wbi` | `0x74a95b0` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-199` | `wbi` | `0x74a95b4` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-200` | `wbi` | `0x74a95b8` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-201` | `wbi` | `0x74a95bc` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-202` | `wbi` | `0x74a95c0` | `01000000` | `00000000` | `00000000, 01000000` | 0x5a8: remove one unsupported-model reference |
| `edit-203` | `wbi` | `0x74a95c8` | `01000000` | `00000000` | `00000000, 01000000` | 0x5aa: remove one unsupported-model reference |
| `edit-204` | `wbi` | `0x74a95cc` | `01000000` | `00000000` | `00000000, 01000000` | 0x5aa: remove one unsupported-model reference |
| `edit-205` | `wbi` | `0x74a95dc` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ac: remove one unsupported-model reference |
| `edit-206` | `wbi` | `0x74a95e0` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ac: remove one unsupported-model reference |
| `edit-207` | `wbi` | `0x74a95e4` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ac: remove one unsupported-model reference |
| `edit-208` | `wbi` | `0x74a95ec` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ad: remove one unsupported-model reference |
| `edit-209` | `wbi` | `0x74a95f0` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ad: remove one unsupported-model reference |
| `edit-210` | `wbi` | `0x74a95f8` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ae: remove one unsupported-model reference |
| `edit-211` | `wbi` | `0x74a95fc` | `01000000` | `00000000` | `00000000, 01000000` | 0x5ae: remove one unsupported-model reference |
| `edit-212` | `wbi` | `0x74a9604` | `01000000` | `00000000` | `00000000, 01000000` | 0x5af: remove one unsupported-model reference |
| `edit-213` | `wbi` | `0x74a9608` | `01000000` | `00000000` | `00000000, 01000000` | 0x5af: remove one unsupported-model reference |
| `edit-214` | `wbi` | `0x74a960c` | `01000000` | `00000000` | `00000000, 01000000` | 0x5af: remove one unsupported-model reference |
| `edit-215` | `wbi` | `0x74a9614` | `01000000` | `00000000` | `00000000, 01000000` | 0x5b0: remove one unsupported-model reference |
| `edit-216` | `wbi` | `0x74a9618` | `01000000` | `00000000` | `00000000, 01000000` | 0x5b0: remove one unsupported-model reference |
| `edit-217` | `wbi` | `0x74a963c` | `02000000` | `01000000` | `01000000, 02000000` | FTP switch 0x5a5: remove one unsupported contribution at 0x40df |
| `edit-218` | `wbi` | `0x74a9640` | `01000000` | `00000000` | `00000000, 01000000` | FTP switch 0x5a5: remove one unsupported contribution at 0x40e0 |
| `edit-226` | `wbi` | `0x852a79a` | `01` | `00` | `00, 01` | FTP switch 0x5a5: OFF option flag after its count reaches zero |
| `edit-227` | `wbi` | `0x852a7a8` | `01` | `00` | `00, 01` | 0x5a6: native flag at zero reference count |
| `edit-228` | `wbi` | `0x852a7a9` | `01` | `00` | `00, 01` | 0x5a6: native flag at zero reference count |
| `edit-229` | `wbi` | `0x852a7aa` | `01` | `00` | `00, 01` | 0x5a6: native flag at zero reference count |
| `edit-230` | `wbi` | `0x852a7ab` | `01` | `00` | `00, 01` | 0x5a6: native flag at zero reference count |
| `edit-231` | `wbi` | `0x852a7ac` | `01` | `00` | `00, 01` | 0x5a6: native flag at zero reference count |
| `edit-232` | `wbi` | `0x852a7cc` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-233` | `wbi` | `0x852a7cd` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-234` | `wbi` | `0x852a7ce` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-235` | `wbi` | `0x852a7cf` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-236` | `wbi` | `0x852a7d0` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-237` | `wbi` | `0x852a7d1` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-238` | `wbi` | `0x852a7d2` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-239` | `wbi` | `0x852a7d3` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-240` | `wbi` | `0x852a7d4` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-241` | `wbi` | `0x852a7d5` | `01` | `00` | `00, 01` | 0x5a8: native flag at zero reference count |
| `edit-242` | `wbi` | `0x852a7f4` | `01` | `00` | `00, 01` | 0x5aa: native flag at zero reference count |
| `edit-243` | `wbi` | `0x852a7f5` | `01` | `00` | `00, 01` | 0x5aa: native flag at zero reference count |
| `edit-244` | `wbi` | `0x852a7f6` | `01` | `00` | `00, 01` | 0x5aa: native flag at zero reference count |
| `edit-245` | `wbi` | `0x852a814` | `01` | `00` | `00, 01` | 0x5ac: native flag at zero reference count |
| `edit-246` | `wbi` | `0x852a815` | `01` | `00` | `00, 01` | 0x5ac: native flag at zero reference count |
| `edit-247` | `wbi` | `0x852a816` | `01` | `00` | `00, 01` | 0x5ac: native flag at zero reference count |
| `edit-248` | `wbi` | `0x852a817` | `01` | `00` | `00, 01` | 0x5ac: native flag at zero reference count |
| `edit-249` | `wbi` | `0x852a824` | `01` | `00` | `00, 01` | 0x5ad: native flag at zero reference count |
| `edit-250` | `wbi` | `0x852a825` | `01` | `00` | `00, 01` | 0x5ad: native flag at zero reference count |
| `edit-251` | `wbi` | `0x852a826` | `01` | `00` | `00, 01` | 0x5ad: native flag at zero reference count |
| `edit-252` | `wbi` | `0x852a834` | `01` | `00` | `00, 01` | 0x5ae: native flag at zero reference count |
| `edit-253` | `wbi` | `0x852a835` | `01` | `00` | `00, 01` | 0x5ae: native flag at zero reference count |
| `edit-254` | `wbi` | `0x852a836` | `01` | `00` | `00, 01` | 0x5ae: native flag at zero reference count |
| `edit-255` | `wbi` | `0x852a844` | `01` | `00` | `00, 01` | 0x5af: native flag at zero reference count |
| `edit-256` | `wbi` | `0x852a845` | `01` | `00` | `00, 01` | 0x5af: native flag at zero reference count |
| `edit-257` | `wbi` | `0x852a846` | `01` | `00` | `00, 01` | 0x5af: native flag at zero reference count |
| `edit-258` | `wbi` | `0x852a847` | `01` | `00` | `00, 01` | 0x5af: native flag at zero reference count |
| `edit-259` | `wbi` | `0x852a854` | `01` | `00` | `00, 01` | 0x5b0: native flag at zero reference count |
| `edit-260` | `wbi` | `0x852a855` | `01` | `00` | `00, 01` | 0x5b0: native flag at zero reference count |
| `edit-261` | `wbi` | `0x852a856` | `01` | `00` | `00, 01` | 0x5b0: native flag at zero reference count |
| `edit-264` | `wbi` | `0xe4a6e2c` | `00` | `01` | `00, 01` | FTP switch 0x5a5: runtime availability hash flag |
| `edit-265` | `wbi` | `0xe4a6e4c` | `00` | `01` | `00, 01` | 0x5a6: runtime model availability |
| `edit-266` | `wbi` | `0xe4a6e6c` | `00` | `01` | `00, 01` | 0x5a8: runtime model availability |
| `edit-267` | `wbi` | `0xe4a6e8c` | `00` | `01` | `00, 01` | 0x5aa: runtime model availability |
| `edit-268` | `wbi` | `0xe4a6eac` | `00` | `01` | `00, 01` | 0x5ac: runtime model availability |
| `edit-269` | `wbi` | `0xe4a6ecc` | `00` | `01` | `00, 01` | 0x5ad: runtime model availability |
| `edit-270` | `wbi` | `0xe4a6eec` | `00` | `01` | `00, 01` | 0x5ae: runtime model availability |
| `edit-271` | `wbi` | `0xe4a6f0c` | `00` | `01` | `00, 01` | 0x5af: runtime model availability |
| `edit-272` | `wbi` | `0xe4a6f2c` | `00` | `01` | `00, 01` | 0x5b0: runtime model availability |

## 关机快门帘及静音联动解锁

历史证据索引（研究工程内）：`research/shutter-poweroff-20261017/phase5/README.md`。

| 字段 ID | 视图 | 文件偏移 | 未解锁值 | 解锁值 | 已知值 | 修改用途 |
|---|---|---|---|---|---|---|
| `edit-001` | `app` | `0x3f17334` | `00` | `01` | `00, 01` | model availability initializer |
| `edit-011` | `app` | `0x3f1773c` | `00` | `01` | `00, 01` | model availability initializer |
| `edit-019` | `app` | `0x4bdeb90` | `0000` | `0100` | `0000, 0100` | current product menu permission |
| `edit-067` | `app` | `0x4c1d8f2` | `0000` | `0100` | `0000, 0100` | current product menu permission |
| `edit-069` | `app` | `0x4c1ff9a` | `0000` | `0100` | `0000, 0100` | current product menu permission |
| `edit-070` | `app` | `0x4c2003a` | `0000` | `0100` | `0000, 0100` | current product menu permission |
| `edit-076` | `app` | `0x4c262ba` | `0000` | `0100` | `0000, 0100` | current product menu permission |
| `edit-084` | `app` | `0x4c2d95a` | `0000` | `0100` | `0000, 0100` | current product menu permission |
| `edit-085` | `app` | `0x4c2d9fa` | `0000` | `0100` | `0000, 0100` | current product menu permission |
| `edit-100` | `wbi` | `0x10c4574` | `01` | `00` | `00, 01` | native availability flag after decrement |
| `edit-101` | `wbi` | `0x10c4575` | `01` | `00` | `00, 01` | native availability flag after decrement |
| `edit-102` | `wbi` | `0x10c4576` | `01` | `00` | `00, 01` | native availability flag after decrement |
| `edit-105` | `wbi` | `0x2538d90` | `0000` | `0100` | `0000, 0100` | resident static copy: current product menu permission |
| `edit-131` | `wbi` | `0x25a4af2` | `0000` | `0100` | `0000, 0100` | resident static copy: current product menu permission |
| `edit-148` | `wbi` | `0x25ab19a` | `0000` | `0100` | `0000, 0100` | resident static copy: current product menu permission |
| `edit-149` | `wbi` | `0x25ab23a` | `0000` | `0100` | `0000, 0100` | resident static copy: current product menu permission |
| `edit-155` | `wbi` | `0x25b14ba` | `0000` | `0100` | `0000, 0100` | resident static copy: current product menu permission |
| `edit-163` | `wbi` | `0x25b8b5a` | `0000` | `0100` | `0000, 0100` | resident static copy: current product menu permission |
| `edit-164` | `wbi` | `0x25b8bfa` | `0000` | `0100` | `0000, 0100` | resident static copy: current product menu permission |
| `edit-179` | `wbi` | `0x7464634` | `01000000` | `00000000` | `00000000, 01000000` | remove exactly one model-unavailable contribution |
| `edit-180` | `wbi` | `0x7464b3c` | `01000000` | `00000000` | `00000000, 01000000` | remove exactly one model-unavailable contribution |
| `edit-219` | `wbi` | `0x74abe54` | `01000000` | `00000000` | `00000000, 01000000` | remove exactly one model-unavailable contribution |
| `edit-220` | `wbi` | `0x74abe58` | `01000000` | `00000000` | `00000000, 01000000` | remove exactly one model-unavailable contribution |
| `edit-221` | `wbi` | `0x74cdc50` | `01000000` | `00000000` | `00000000, 01000000` | remove exactly one model-unavailable contribution |
| `edit-222` | `wbi` | `0x74cdc54` | `01000000` | `00000000` | `00000000, 01000000` | remove exactly one model-unavailable contribution |
| `edit-223` | `wbi` | `0x80e5790` | `01` | `00` | `00, 01` | native availability flag after decrement |
| `edit-224` | `wbi` | `0x80e5791` | `01` | `00` | `00, 01` | native availability flag after decrement |
| `edit-225` | `wbi` | `0x80e5792` | `01` | `00` | `00, 01` | native availability flag after decrement |
| `edit-262` | `wbi` | `0xe47568c` | `00` | `01` | `00, 01` | runtime model availability |
| `edit-263` | `wbi` | `0xe4a666c` | `00` | `01` | `00, 01` | runtime model availability |

## 灵活曝光

历史证据索引（研究工程内）：`docs/灵活曝光与快门声音实机验证-20261019.md`。

| 字段 ID | 视图 | 文件偏移 | 未解锁值 | 解锁值 | 已知值 | 修改用途 |
|---|---|---|---|---|---|---|
| `app-3f172bc` | `app` | `0x3f172bc` | `00` | `01` | `00, 01` | 0x497 model support |
| `app-3f173f4` | `app` | `0x3f173f4` | `00` | `01` | `00, 01` | 0x50c model support |
| `app-3f173fc` | `app` | `0x3f173fc` | `00` | `01` | `00, 01` | 0x50d model support |
| `app-3f17404` | `app` | `0x3f17404` | `00` | `01` | `00, 01` | 0x50e model support |
| `app-4bd5eda` | `app` | `0x4bd5eda` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4bdbf82` | `app` | `0x4bdbf82` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4bdc022` | `app` | `0x4bdc022` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c17010` | `app` | `0x4c17010` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c2021a` | `app` | `0x4c2021a` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c202ba` | `app` | `0x4c202ba` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c2b5ba` | `app` | `0x4c2b5ba` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c2b65a` | `app` | `0x4c2b65a` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c2b79a` | `app` | `0x4c2b79a` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c2b83a` | `app` | `0x4c2b83a` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c39ada` | `app` | `0x4c39ada` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c4910a` | `app` | `0x4c4910a` | `00` | `01` | `00, 01` | current product column menu permission |
| `app-4c51dda` | `app` | `0x4c51dda` | `00` | `01` | `00, 01` | current product column menu permission |
| `wbi-86b99c` | `wbi` | `0x86b99c` | `00` | `01` | `00, 01` | 0x497 runtime support |
| `wbi-cb26b8` | `wbi` | `0xcb26b8` | `02` | `01` | `02, 01` | remove one model-unavailable contribution |
| `wbi-10c42dd` | `wbi` | `0x10c42dd` | `01` | `00` | `01, 00` | native output after count update |
| `wbi-10c42de` | `wbi` | `0x10c42de` | `01` | `00` | `01, 00` | native output after count update |
| `wbi-10c42df` | `wbi` | `0x10c42df` | `01` | `00` | `01, 00` | native output after count update |
| `wbi-10c4c63` | `wbi` | `0x10c4c63` | `01` | `00` | `01, 00` | native output after count update |
| `wbi-10c4c73` | `wbi` | `0x10c4c73` | `01` | `00` | `01, 00` | native output after count update |
| `wbi-10c4c83` | `wbi` | `0x10c4c83` | `01` | `00` | `01, 00` | native output after count update |
| `wbi-25200da` | `wbi` | `0x25200da` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-2536182` | `wbi` | `0x2536182` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-2536222` | `wbi` | `0x2536222` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-2592210` | `wbi` | `0x2592210` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25ab41a` | `wbi` | `0x25ab41a` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25ab4ba` | `wbi` | `0x25ab4ba` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25b67ba` | `wbi` | `0x25b67ba` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25b685a` | `wbi` | `0x25b685a` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25b699a` | `wbi` | `0x25b699a` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25b6a3a` | `wbi` | `0x25b6a3a` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25c4cda` | `wbi` | `0x25c4cda` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-25f8fda` | `wbi` | `0x25f8fda` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-260030a` | `wbi` | `0x260030a` | `00` | `01` | `00, 01` | current product column menu permission saved static copy |
| `wbi-73fbb24` | `wbi` | `0x73fbb24` | `01` | `00` | `01, 00` | remove one model-unavailable contribution |
| `wbi-73fbb28` | `wbi` | `0x73fbb28` | `01` | `00` | `01, 00` | remove one model-unavailable contribution |
| `wbi-73fbb2c` | `wbi` | `0x73fbb2c` | `01` | `00` | `01, 00` | remove one model-unavailable contribution |
| `wbi-743ec30` | `wbi` | `0x743ec30` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-743ec34` | `wbi` | `0x743ec34` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-743ec38` | `wbi` | `0x743ec38` | `01` | `00` | `01, 00` | remove one model-unavailable contribution |
| `wbi-743ec6c` | `wbi` | `0x743ec6c` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-743ec70` | `wbi` | `0x743ec70` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-743ec74` | `wbi` | `0x743ec74` | `01` | `00` | `01, 00` | remove one model-unavailable contribution |
| `wbi-743ecc8` | `wbi` | `0x743ecc8` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-743eccc` | `wbi` | `0x743eccc` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-743ecd0` | `wbi` | `0x743ecd0` | `01` | `00` | `01, 00` | remove one model-unavailable contribution |
| `wbi-74944c8` | `wbi` | `0x74944c8` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-74944d8` | `wbi` | `0x74944d8` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-74944f0` | `wbi` | `0x74944f0` | `03` | `02` | `03, 02` | remove one model-unavailable contribution |
| `wbi-e4a696c` | `wbi` | `0xe4a696c` | `00` | `01` | `00, 01` | 0x50c runtime support |
| `wbi-e4a698c` | `wbi` | `0xe4a698c` | `00` | `01` | `00, 01` | 0x50d runtime support |
| `wbi-e4a69ac` | `wbi` | `0xe4a69ac` | `00` | `01` | `00, 01` | 0x50e runtime support |

## 电子快门声音

历史证据索引（研究工程内）：`research/electronic-shutter-sound-20261019/README.md`。

| 字段 ID | 视图 | 文件偏移 | 未解锁值 | 解锁值 | 已知值 | 修改用途 |
|---|---|---|---|---|---|---|
| `app-4b5922a` | `app` | `0x4b5922a` | `00` | `00` | `00, 01` | leaf 0xabe (AUDIO_SIGNALS_ON_ALL) gate pinned to 00: the camera does not persist the fifth option |
| `app-4b592ca` | `app` | `0x4b592ca` | `00` | `01` | `00, 01` | leaf 0xabf (AUDIO_SIGNALS_SHUTTER_OFF (locked)) current-product menu permission |
| `app-4b5936a` | `app` | `0x4b5936a` | `00` | `01` | `00, 01` | leaf 0xac0 (AUDIO_SIGNALS_ONLY_SHUTTER (locked)) current-product menu permission |
| `cp-5d3900` | `cpapp` | `0x5d3900` | `00` | `03` | `00, 03` | CP exposure-start sound mark: cpapp-b.bin 0x387900 movne r1,#0 -> movne r1,#3 (A7C II reference) |
| `wbi-245442a` | `wbi` | `0x245442a` | `00` | `00` | `00, 01` | leaf 0xabe (AUDIO_SIGNALS_ON_ALL) gate pinned to 00: the camera does not persist the fifth option |
| `wbi-24544ca` | `wbi` | `0x24544ca` | `00` | `01` | `00, 01` | leaf 0xabf (AUDIO_SIGNALS_SHUTTER_OFF (locked)) current-product menu permission |
| `wbi-245456a` | `wbi` | `0x245456a` | `00` | `01` | `00, 01` | leaf 0xac0 (AUDIO_SIGNALS_ONLY_SHUTTER (locked)) current-product menu permission |
| `wbi-80e55de` | `wbi` | `0x80e55de` | `01` | `00` | `01, 00` | runtime inhibit source for leaf 0xabf (model singleton byte) |
| `wbi-80e55df` | `wbi` | `0x80e55df` | `01` | `00` | `01, 00` | runtime inhibit source for leaf 0xac0 (model singleton byte) |

## 位移多帧合成（Pixel Shift）

历史证据索引（研究工程内）：`docs/PixelShift触发源与WBI联动-20261020.md`。

| 字段 ID | 视图 | 文件偏移 | 未解锁值 | 解锁值 | 已知值 | 修改用途 |
|---|---|---|---|---|---|---|
| `edit-ps-001` | `app` | `0x3f17324` | `00` | `01` | `00, 01` | 0x4b5 static model availability |
| `edit-ps-002` | `app` | `0x3f177f4` | `00` | `01` | `00, 01` | 0x730 static model availability |
| `edit-ps-003` | `app` | `0x3f177fc` | `00` | `01` | `00, 01` | 0x731 static model availability |
| `edit-ps-005` | `wbi` | `0xcb2b10` | `02000000` | `01000000` | `01000000, 02000000` | 0x214: remove one unavailable contribution at 0x214 |
| `edit-ps-009` | `wbi` | `0x10c4511` | `01` | `00` | `00, 01` | 0x214: OFF option flag after its count reaches zero |
| `edit-ps-010` | `wbi` | `0x24dffd4` | `0000` | `0100` | `0000, 0100` | 0x820: menu 0x820 current product-column permission |
| `edit-ps-006` | `wbi` | `0x73abd6c` | `01000000` | `00000000` | `00000000, 01000000` | 0x214: remove one unavailable contribution at 0x22ab |
| `edit-ps-007` | `wbi` | `0x73abd70` | `02000000` | `01000000` | `01000000, 02000000` | 0x214: remove one unavailable contribution at 0x22ac |
| `edit-ps-008` | `wbi` | `0x73abd74` | `02000000` | `01000000` | `01000000, 02000000` | 0x214: remove one unavailable contribution at 0x22ad |
| `edit-ps-011` | `wbi` | `0xe47596c` | `00` | `01` | `00, 01` | 0x730: 4-image submode runtime model availability |
| `edit-ps-012` | `wbi` | `0xe47598c` | `00` | `01` | `00, 01` | 0x731: 16-image submode runtime model availability |
| `edit-ps-004` | `wbi` | `0xe4a662c` | `00` | `01` | `00, 01` | 0x4b5: parent runtime model availability |

## 动态增强防抖（Dynamic Active）

历史证据索引（研究工程内）：`docs/动态增强防抖值支持修复-20261020.md`。

| 字段 ID | 视图 | 文件偏移 | 未解锁值 | 解锁值 | 已知值 | 修改用途 |
|---|---|---|---|---|---|---|
| `app-3ed654c` | `app` | `0x3ed654c` | `00` | `01` | `00, 01` | ACTIVE_PLUS initializer value-support flag |
| `app-4b7b93a` | `app` | `0x4b7b93a` | `00` | `01` | `00, 01` | leaf 0x3a3 gate: 00 locked, 01 open (ZV-E1 factory) |
| `wbi-10c4701` | `wbi` | `0x10c4701` | `01` | `00` | `01, 00` | inhibition output consistent with count zero |
| `wbi-2495b3a` | `wbi` | `0x2495b3a` | `00` | `01` | `00, 01` | WBI-resident leaf 0x3a3 gate: 00 locked, 01 open |
| `wbi-3e2839c` | `wbi` | `0x3e2839c` | `00` | `01` | `00, 01` | ACTIVE_PLUS runtime value-support flag |
| `wbi-743f53c` | `wbi` | `0x743f53c` | `01` | `00` | `01, 00` | remove exactly one unsupported-value contribution; upper 3 counter bytes pinned |

## 来源文件校验索引

下列路径是研究工程内的证据标识，不是本包运行依赖。

| 来源 | SHA-256 |
|---|---|
| `samples/A6700-v2.00.wbi-minimal-permission-manifest-20261016.json` | `07b2302668c5cd775506bc9bd7e17c64d80fd5c34667dc9caa8f5de609d181d1` |
| `samples/A6700-v2.00.ftp-parameter-availability-audit-20261016.json` | `89fd71b5d3127834411aa83be1d77157992020b02a2b3cf66de2819d548ba72c` |
| `samples/A6700-v2.00.ftp-settings-support-manifest-20261016.json` | `7b71604ef6f0a96bd413083bc732afc69f5683a6df8cc87a7d7a84f01156f5ea` |
| `research/shutter-poweroff-20261017/phase4/candidate/delta.json` | `f8c2193d5f0af6e68ad8451640794f113c49124dd8e9370176d23099c2024d3e` |
| `extracted/A6700-v2.00/appFw.so.sections.json` | `a4e29b3ba8de4c9afa745319e042f5b102878decc46315243801a6e557908e68` |
| `patches/live-a6700-2.00-ftp-leaves/plan.json` | `534c79b7a471fffc636378f6960abac45228e6c9c0c679e9536e5921a4e87e0c` |
| `patches/live-a6700-2.00-ftp-settings-support/plan.json` | `73d0eb56946a75cb123522b5e93b3efb7adf1d00a8fdcd0cadbcdbf64ab76dbf` |
| `patches/live-a6700-2.00-playback-withdraw/plan.json` | `ea7519f148d4172c2a612a8a82bd6ef5fece1bf9d38fc9d09674d888d72c6854` |
| `patches/feature-trials-20261019/exposure-current/catalog.json` | `3338cbebc9307a964d1b4ebbb8eec477ca519419d0dce341babbd5f65d41f4f1` |
| `patches/feature-trials-20261019/exposure-current/profile.json` | `747b7c7094aaabda4bac935e78f3e7081414d9551d1b77a4d02b8dabb0986492` |
| `patches/feature-trials-20261019/sound-after-exposure/catalog.json` | `641819d35eaafb58b63eddca9ab381dd73d530fdc5ab8eb7d79159053cee44e8` |
| `patches/feature-trials-20261019/sound-after-exposure/profile.json` | `187782aac9fd3758e115c0ff042cf90fb306bc962b278b7f0952a3ae0cdb862e` |
| `patches/feature-trials-20261020/pixel-shift-current/catalog.json` | `4c5590dc584d956f94aca2b7b068466b588e6704ff77059431bbdbcf0c456914` |
| `patches/feature-trials-20261020/pixel-shift-current/profile.json` | `e5ee2e2fef2b8eb291cd7f34823eba6069e8f7d3c8a8023aeb0d4f8082a1d262` |
| `patches/feature-trials-20261020/dynamic-active-gate/catalog.json` | `2e649623f8b1e1b6daf3ef1f9449adf06a922baf294db2bf1c23cd7fdd918a58` |
| `patches/feature-trials-20261020/dynamic-active-gate/profile.json` | `baa23c1a7eb74497310fb96d105dcd02a5e58cf3f7bd452ca473477d048c756f` |
| `patches/feature-trials-20261020/dynamic-active-wbi/catalog.json` | `a7845a2aad13668f65241487d75490bac74679c50c329910dedd71c77c5e06d4` |
| `patches/feature-trials-20261020/dynamic-active-wbi/profile.json` | `4b113084122ef44fe2f682a38399fd5e70e90767f93f0ac62ac6832610cce6ab` |
| `patches/feature-trials-20261020/dynamic-active-value-support/catalog.json` | `0bc5893a72f08592192b56f06aa1fd8366dda5e2fa8779abbdd9b23b1aeb4c2c` |
| `patches/feature-trials-20261020/dynamic-active-value-support/profile.json` | `93cabbc757e143e44597fdfab2d44587160e17cad9e001f9ac89649d5767bb81` |

## FL2／FL3 独立包（dev6 测试）

独立主列表、图标、完整帮助及成像支持，保留简称。采用固定文件载荷，不属于上表的固定清单字节数；安装／关闭与验证边界见[独立包](FEATURE-SELECTION.md#创意外观独立功能包)。中文名称本次延后。
