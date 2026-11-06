# 功能选择与统一 profile v2

dev14 将“整套解锁”扩展为**按功能选择**，并把灵活曝光并入正式功能清单。这里不是把每个功能做成独立应用，而是用一份统一的功能清单、统一预检窗口和一次事务管理所有选中功能。

## 功能模型

当前登记六项，0.2 只开放前四项；后两项开发未完成，暂禁用：

| 功能 ID | 名称 | 证据边界 |
|---|---|---|
| `ftp` | 原生 FTP 菜单、开关及设置解锁 | 历史实机验证；RAW+JPEG 上传有正向实测 |
| `shutter` | 关机快门帘及静音联动解锁 | 历史实机验证；关机闭帘、开机释放有用户实测 |
| `exposure` | 灵活曝光 | 2026-10-19 实机写入 App 17＋WBI 39 字节；菜单可选，三项独立控制和保存仍有待完整验收 |
| `sound` | 电子快门声音 | sound-after-exposure 是验证基线，不是运行时依赖；菜单 4 项，App 2＋WBI 4 字节＋CP 1 字节，4 个扇区；2026-10-20 实机出声与 4 项菜单已由用户验收 |
| `pixel_shift` | 位移多帧合成（Pixel Shift，暂禁用） | 2026-10-20 实机写入 App 3＋WBI 9 字节，8 个扇区；合成流程与成像待完整验收 |
| `dynamic_active` | 动态增强防抖（暂禁用） | App 2＋WBI 4 字节；历史写入及回读通过、用户确认可选，新统一包待实机验收；见[集成说明](动态增强防抖.md) |

关闭电子快门声音前，先把相机的「音频信号」设为「开」或「关」。若相机仍保存着「开：仅电子快门」或「开：除快门以外」，关闭会移除这两个菜单项但不会改写相机自己的设置值，再次进入菜单会停在无效项（上下切换无效、出现异常高亮），触摸选择「开」或「关」即可恢复。该值不属于本工具管理的清单字段，关闭时不会改写；桌面关闭确认页会显示这条提示。

每个字段在清单中保留 `feature` 归属。选择执行时：

- 只把**选中功能**的字段改到目标值；
- 未选中功能的字段保持相机当前值；
- 所有功能字段仍然逐项校验已知值；
- 所有结构守卫和每扇区未管理字节仍然完整校验；
- 选择集为空时拒绝写入。

## 统一 profile v2

旧 profile 用“整扇区规范化哈希”判断兼容性。FTP／快门帘与灵活曝光恰好落在同一批 App 扇区，且字段不重叠，但整扇区哈希会把另一个功能的修改当成不匹配。

v2 改为：

- 每个目标扇区记录 `unmanaged_sha256`：把该扇区所有已知字段字节置零后计算哈希；
- 字段值由清单逐项校验，允许同扇区不同功能分别处于已知状态；
- 每个扇区由 `edit_ids` 生成识别掩码，保持历史状态及未管理字节校验；写入掩码额外排除 `available=false` 的功能，主机请求与原生写入器都拒绝这些字节；
- 守卫扇区继续使用完整 `sha256`。

统一窗口为 **260 个扇区**：149 个目标扇区、111 个结构守卫。它覆盖 FTP、快门帘、灵活曝光、电子快门声音（含 `/dev/nflasha8` CP 触发）、位移多帧合成和动态增强防抖的全部字段。

## 适配数据生成

`data/device-profile.json` 由维护者脚本生成，不依赖相机：

```sh
python3 scripts/build_profile_v2.py \
  --catalog src/a6700_unlock/data/a6700-2.00.json \
  --trial-profile ../patches/feature-trials-20261019/exposure-current/profile.json \
  --trial-preimages ../hardware/feature-trial-sessions/exposure-current/runs/20261019T153114Z-f141873dc1/preimages.json \
  --trial-profile ../patches/feature-trials-20261019/sound-after-exposure/profile.json \
  --trial-preimages ../hardware/feature-trial-sessions/sound-after-exposure/runs/20261019T155304Z-aba9ecc8a5/preimages.json \
  --trial-profile ../patches/feature-trials-20261020/pixel-shift-current/profile.json \
  --trial-preimages ../hardware/feature-trial-sessions/pixel-shift-current/runs/20261019T180630Z-d801327259/preimages.json \
  --trial-profile ../patches/feature-trials-20261020/sound-cp-trigger/profile.json \
  --trial-preimages ../hardware/feature-trial-sessions/sound-cp-trigger/runs/20261019T183844Z-507a2e7d81/preimages.json \
  --release-profile src/a6700_unlock/data/device-profile.json \
  --output src/a6700_unlock/data/device-profile.json \
  --profile-id a6700-2.00-unified-v5-cp-sound-20261020
```

脚本会合并 trial 与上一版 profile 的扇区、校验每个字段恰好映射到一个目标扇区、用 trial 写前镜像计算未管理字节哈希，并调用 `DeviceProfile.from_dict` 校验结果。改完 profile 后必须运行 `python3 scripts/build_native.py` 重建 reader/writer 和哈希清单，再运行测试。dev19 profile 的 SHA-256 为 `555ce496611bcabd0d6dea4b5f05d9f99947bd3e2f09c921f068f6408c96dc40`；历史 dev23 profile v6 为 `fb1c352843ff16c18c6970a5960f3014aca0b0171f84de69b8a5b2258eb15d6f`。像素位移的 App／WBI 字节已作为正式字段纳入目标扇区掩码，不能当作“易变字节”排除；本轮撤销了该临时处理。

## 选择模式

- `--feature ftp --feature exposure`：显式选择；
- `--all-missing` / 桌面“全选缺失”：只补齐本版开放且当前未解锁的功能；
- 桌面“全选”：选择本版全部开放功能；
- 桌面“清除”：空选择，解锁／关闭／恢复按钮禁用。

“全选”不是强制放行：未知字段、守卫变化、未管理字节变化仍会阻止整个操作。功能可以声明 `requires`，但当前开放功能之间没有字段依赖；电子快门声音与灵活曝光字段无重叠，可以只选声音。`sound-after-exposure` 只是验证基线，不是运行时前置。

## 桌面流程

1. 连接相机、完成准备、只读检查。
2. 主页显示每个功能的当前状态和勾选框。
3. 选择功能，或点“全选缺失”。
4. 点“解锁所选功能”或“关闭所选功能”。
5. 结果页显示本次事务与回读状态；未选中功能不应发生变化。

备份仍保存完整统一窗口；恢复只写回当前勾选的功能字段。因此可以用同一份备份只恢复 FTP，而保留灵活曝光的当前状态。

## 命令行

```sh
a6700-unlock device inspect
a6700-unlock device unlock --feature ftp --feature exposure
a6700-unlock device unlock --all-missing
a6700-unlock device close --feature exposure
a6700-unlock device restore --feature ftp
```

`--all-missing` 仅用于 `unlock`，不能与 `--feature` 同时使用。恢复时省略 `--feature` 表示恢复本版全部开放功能。

## 边界

- 只读预检与写入仍使用同一固定 260 扇区窗口，没有任意地址或整盘 API。
- 功能选择不改变原生写入器的掩码检查、写前校验、失败回退或独立回读。
- 不同功能共享扇区时，掩码取并集；写入前仍逐字节检查未管理内容。
- 跨适配版本的新旧备份会被拒绝，不会按新偏移猜测恢复。
- 2026-10-20 已完成实机只读预检、声音 close dry-run、声音关闭→独立回读→重新解锁→回读；关机重开后的声音菜单与实际播放仍需用户确认，其他功能未执行写入。

## dev23 动态增强增量

在上面的历史四包输入之后，再依次加入 `dynamic-active-gate`、`dynamic-active-wbi`、`dynamic-active-value-support` 的 `profile.json` 与 `offline-preimages.json`，上一版 profile 作为 `--release-profile`，输出 ID 为 `a6700-2.00-unified-v6-dynamic-active-20261020`。六个新扇区均不与旧窗口重叠；后一个值支持包的列 3 守卫与新门控字段重合，由合并后的字段掩码及未管理字节哈希共同约束。只复制最终数据与载荷，不分发原始扇区或私人样本。

## 0.1 测试候选范围（dev25）

`Catalog.available_features` 是统一发布开关；缺省为开放，显式 `available=false` 必须附原因。默认、全选与 missing 均排除禁用功能；显式选择禁用功能立即拒绝，解锁／关闭／恢复共用此规则。离线镜像计划也跳过禁用字段。

`DeviceProfile.masks()` 继续用于识别和未管理字节校验；`masks(writable_only=True)` 用于请求校验及编译原生 writer。两者不可混用。禁用不是回滚，已有实验字节原样保留；未知值和未登记的新版研究改动仍阻断，不放宽守卫。旧备份按原哈希绑定保留，不跨 profile 恢复。

当前 profile v7 SHA-256：`37d80afc7ecdf2d5d943952745ff5db3fe4a4fd5317547271d6bf002afd10a71`，地址与识别守卫不变，reader/writer 已按新清单绑定重建。

## 创意外观独立功能包

0.3.0 本次只提供 FL2／FL3 实机安装／关闭测试。中文名称从界面隐藏，逻辑研究保留供后续版本使用。菜单名称采用原简称，新增两项保留图标、完整说明与成像支持。

FL23 不混入旧四功能的 260 扇区 profile；`fl23_package.py` 与固定 C 事务读取 40 个程序目标及 1416 个只读结构／缓存守卫，两份 CP 参数单位只按已验证差分更新。完整参数必须匹配已知摘要，其他内容不覆盖；SAVE 前后核对 38 个原有外观／MR 值。保留读取、持久写前记录、相机内二次前检、失败逆向、独立回读、共享锁及服务文件恢复。

外部载荷写入 `/setting/fl23.new`，完整 SHA 校验后原子提交为 `/setting/fl23`。启动器只读取该固定文件；未知旧文件、遗留 staging、代码或参数状态均拒绝，不自动覆盖、不重发未知执行。新包不使用研究机身的分区尾部、个人身份或原始备份。

首次安装依次提交载荷、所需 CP 参数与代码、AP／WBI 启动入口。已存在的入口先暂时停用，正文完成后最后启用；失败按记录恢复原值。关闭时要求先切回 ST，停用载荷并恢复启动入口，CP 支持保留供再次启用。关闭不是整机原厂恢复。

应用允许FL23与原四项一起选择，不自动取消其他勾选。混选时在同一设备锁和认证连接内，先执行原四项中的所选项目，再执行FL23；两套既有安装器各自重新读前、记录和独立回读。最后用原四项reader再次核对保持状态，全部通过才报告完成。中途失败不继续、不重发，明确报告已确认和未完成项目；不会将先前成功的修改称为已回滚。仅选一种时沿用原入口。

研究版旧分区载荷／启动器不属于公开安装前态，需按内部已绑定流程撤回后再测试本应用；不能以强制接管跳过内容检查。测试相机已撤回旧名称测试包的 13 个启动扇区并回读，CP 未重刷。

本版已通过原生构造器、固定范围正反向与故障检查，以及独立源码重建的逐字节核对。原研究载荷的 FL23 成像验收不能代替本文件载荷版本的应用安装、冷启动、关闭／再开启验收；dev6应用文件安装及原四项随后解锁已写入并回读通过，用户确认已全部开启；dev7之后维护者确认测试无问题并授权发布。关闭／重装、多机身等继续扩大样本。

对应源文件随包保留在 `data/fl23/runtime/`；`scripts/rebuild_fl23_runtime.py --output <新目录>` 可在不读取研究工程／固件／个人备份的条件下重建相同载荷；`scripts/build_fl23.py` 重建固定读写事务。此开发版不自动上传 GitHub 或替换旧发行文件。
