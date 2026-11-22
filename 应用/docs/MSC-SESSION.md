# 有界 USB MSC／Senser 脚本会话

2026-11-12 本地实现。MSC 是连接入口，内容读取与固定功能事务在 Senser `0x0336` 服务模式中进行。一次认证服务多个串行请求，保持现有固定 profile、设备锁、结构守卫、机内前态比对／回滚和独立回读。不是任意分区读写器，也不安装系统守护服务。

## 推荐入口

共享调试台已提供托管入口：`scripts/a6700-bench usb ensure` 复用就绪会话，`usb exec -- <SDK脚本>` 在客户端运行脚本，`usb inspect/read/plan/apply` 调用同一 IPC，批次结束用 `usb stop`。它适合跨多条命令复用；单个脚本自动开关仍可用下文的 `msc-tool run`。两种方式共用同一把锁和事务核心，不能同时占用。见 [调试台能力入口](../../a6700-testbench/validator/README.md#托管能力与脚本入口2026-11-13)。

研究工程根目录运行；包装器自动提交一个共享调试台任务，任务内 worker 取得原设备锁，直到排空与退出记录完成才归还。调试台未运行则不访问相机，不自动降级为直连。

```sh
scripts/msc-tool run -- python3 my_research_script.py
```

脚本使用同一核心 SDK（包装器已设置 PYTHONPATH 与配对端点）：

```python
from a6700_unlock.session import CameraMscSession

with CameraMscSession() as camera:
    for _ in range(10):
        sectors = camera.read_profile()  # 每次真实读取当前固定 profile
    state = camera.inspect()            # 同一连接内走原规划器
```

`CameraMscSession` 连接已有 worker；退出此客户端不会停止其他客户端共享的 worker。`run` 在所包裹脚本结束后负责退出 USB。没有配对端点时 SDK 报错，不自行认证或抢锁。worker 不接收 stdin；需交互确认的研究流程应先在普通终端准备计划，再向运行中的会话提交明确请求。

手动会话适用于多个终端：

```sh
scripts/msc-tool session start --timeout 300 --max-duration 900 --detach
# 上一命令打印 bench job ID，表示已入队；尚不表示连接就绪。
scripts/msc-tool session status
scripts/msc-tool inspect
scripts/msc-tool session stop
scripts/a6700-bench wait <job-id>
```

不带 `--detach` 时保持前台跟随，看到 `ready=true` 才表示已打开。状态返回 session ID、bench job、profile、证据目录和剩余期限。status／结果查询不延长空闲期限。Ctrl+C 仅停止调试台输出跟随，不停止 worker；用 wait 接回，或调用 session stop 请求排空，不能重复启动。

默认空闲300秒、总期限900秒；允许分别设置至900秒和3600秒。只在请求边界检查期限，写入／回滚不被计时器打断。达到期限会结束相机会话；仍在运行的用户脚本不会被强杀。接近 Senser 16位序号上限时排空，不猜测序号回绕、不自动认证续接。未加入未验证的 USB 心跳或看门狗修改。

## 固定范围读取

```sh
scripts/msc-tool read --dev /dev/nflasha15 --offset <profile内偏移> --size 512 --out dump.bin
```

设备、扇区对齐和范围均须在本次已加载 profile 中，跨越未登记扇区会在发 USB 前拒绝，输出文件存在时拒绝覆盖。当前每次底层仍读取完整固定 profile，再返回请求子集，不将其宣传为只扫描指定扇区。`read_profile()` 返回按 profile 顺序排列的扇区列表；`read(device=..., offset=..., size=...)` 返回 bytes。读探针仍会传临时载荷，不能称为相机文件系统零写入。

可复用只读延迟检查：

```sh
scripts/msc-tool run -- python3 scripts/msc-session-check.py --iterations 10 --out <未存在的报告路径>
```

报告区分认证耗时、底层完整 profile 读取耗时和含 IPC／审计的客户端延迟，记录每次不同 nonce、请求 ID、扇区数及内容哈希。无 `<50ms` 保证，不跳过落盘、fsync 或回读来达到指标。

## 计划与写入

写入仅支持既有普通 profile 功能；FL23、创意外观名称、自定义声音、任意文件导入和完整恢复尚未接入本协议。依赖和可用性由原能力解析器与规划器判断。

```sh
scripts/msc-tool plan unlock --feature sound
# 核对返回的计划及 run_directory；以下命令会实际写入。
scripts/msc-tool apply <plan_id> --confirm
```

SDK 对应 `camera.plan("unlock", ["sound"])` 与 `camera.apply(plan_id, confirmed=True)`。默认不确认；计划只在本会话有效，执行一次即消耗全部旧计划。每次 plan 使用独立 nonce；执行资源变化或最新完整前态与计划不符时拒绝写入。关闭计划同时绑定机身运行／保存设置快照；设置变化在调用任何复位接口前拒绝，不增加计划外准备动作。执行前重新读取、重新规划；原机内写入器仍逐字节比较 before 并限制固定掩码，写后独立读取。原生 ELF、掩码和事务协议没有改造，未添加原始 write 方法。

会话内成功事务之后可生成新计划，不能复用旧前态。前序设置修改、失败、回滚、未闭合事务和重启要求需按结果核对；写入回读通过与重启／机身功能验收分开。自动回滚仅覆盖执行路径可继续时的错误，不承诺断电原子性。实机写入演练需先审核具体计划，本次只读验证不替代该演练。

## 记录、异常与恢复

默认复用 `operations.default_state_dir()` 的持久化目录及同一共享设备锁；研究核验报告统一放主工程 `output/运行/USB会话/`。不要用新 state-dir 绕过待处理设置或未闭合事务。自定义 state-dir 用于隔离合成测试或已有明确部署配置。

- `msc-sessions/<session-id>/session.json`：会话、profile／catalog、期限及 bench job。
- `msc-sessions/<session-id>/requests/<request-id>/intent.json`、`result.json`：每次请求先记录意图，再落结果；读取数据和哈希仅保留本机。
- `msc-sessions/<session-id>/plan-<digest>.json`：一次性批准材料；`runs/` 继续保存核心原有完整前态、目标、写入 nonce、原生结果及独立回读。
- `msc-sessions/<session-id>/exit.json`：事务是否不确定、原生执行是否确认结束、STOP 回复与实际 MSC 观察分别记录。

POSIX 私有目录／socket 和配对文件权限为0700／0600；配对令牌不输出到常规日志。Windows 随机 loopback IPC 同样要求令牌，尚未实机验收。不提供任意宿主命令的 RPC；`run -- <command>` 是本机调用方自行指定的被包裹脚本。

客户端断开不能取消正在执行的事务。IPC 错误携带 request_id 与证据路径；`msc-tool result <request-id>` 只查询当前会话中已记录结果，不重发请求。原请求结果未持久化时明确返回 unknown。已知重复 request_id 只能取回同一结果，参数不同则拒绝。

worker 被 SIGINT／SIGTERM 请求退出时等待当前事务结束。SIGKILL、USB断开、系统崩溃不保证 finally 或 STOP；未知原生执行不发送 STOP，不自动重连或重试。新会话发现未闭合 apply 意图或原生执行未确认返回时，会在认证前拒绝；原生调用明确返回但独立回读失败时可以正常退出服务，事务仍为失败，后续会话仅允许读取／规划并阻断写入。退出命令不等于 MSC 已恢复，需核对 exit.json 和 bench 最终退出码；未观察到 MSC、清理警告或事务不确定返回70。退出记录的 `stop_requested` 与 `stop_acknowledged` 分别表示是否请求退出和是否取得成功回复，`msc_observed` 只由真实枚举观察决定；初版 `stop_sent` 为成功回复标志的兼容字段。

崩溃遗留的配对端点不会自动覆盖。核对 bench 记录后，可显式运行 `scripts/msc-tool session recover`：经调试台排队、取得共享锁并确认登记进程已退出，保存端点摘要后移除失效端点；不删锁、不操作 USB、不抹掉事务。未闭合 apply 记录继续阻断写入，必须根据原证据和新鲜实机状态处理，不以端点恢复冒充相机恢复。

## 验证边界

无设备测试覆盖合成扇区与实际本机 IPC，包括认证复用、锁期限、范围拒绝、计划冻结／失效、写入前持久化、独立回读、回滚、丢失回复不重发、信号排空、结果查询和端点恢复。实际机身结果以研究工程核验摘要为准；Windows IPC、长时间服务模式、断电中断及真实写入回滚演练尚待验收。未提交／发布 GitHub。
