# USB 运行框架

工具版本 `0.1.0.dev9`；功能包仍为 `0.1.0.dev1`，本版完善基础框架，不增加解锁功能。

## 流程

0. CLI 入口先完成本机环境检查与相机准备确认；取消或未准备好时不构造 USB 后端。向导不访问相机，准备完成不等于已兼容。
1. 菜单或 `device inspect/unlock/close` 获取本机共享设备锁，创建本机私有操作目录。锁与 `--state-dir` 无关，且兼容既有研究向导；为兼容 dev2，还保留原记录目录锁。
2. 校验包内载荷与适配数据哈希、加载可选 USB 依赖；macOS 必要时接管接口，先仅卸载该 USB 设备下的存储卷；卷繁忙则停止，不强制卸载。无控制终端且未获得 sudo 授权时立即报错。新 helper 只匹配唯一的 `054c:0e76`，不会选择其他 Sony 设备。
3. 沿用双次 Senser 认证和 ProductInfo 读取；普通输出和报告不保存其原始设备标识。
4. 上传带新 nonce 的固定范围读取请求及只读载荷，获取目标与守卫。
5. 识别完整字段状态，将已知字段还原成原值后核对完整扇区哈希；检查 ext2 映射、WBI 头与段表守卫。
6. 生成选择集目标。未知内容全部停止；未选中功能保持不变，已符合目标不写入。
7. 实际修改前保存本次窗口前镜像、目标、写入意图并同步；只读取和 dry-run 不发送写入器。
8. 相机内核对全部写前内容；仅允许修改清单拥有的字节，守卫完全只读。
9. 检查短 I/O、每次写入、同步及回读；失败时逆序回退本次已尝试目标，并验证回退。
10. 主机用新 nonce 执行另一轮只读载荷，核对所有目标与守卫，最后请求退出服务模式并释放接口。控制状态丢失时独立观察是否回到唯一 MSC；观察失败保留拔插提示，释放异常不会覆盖主要操作结果。

写入前记录失败会阻止发送写入器；写入后若本机执行结果记录失败，仍继续独立回读，并把文件系统故障单独保存为 `record_warning`。原生拒绝且报告未触及扇区，也必须回读确认前值一致，才能标记 `rejected`；前值已漂移则标记 `verification-failed`。

相机必须能正常启动。Pico 冷启动救砖仍为独立专题；本次前镜像是明文小窗口，不会直接作为 Pico 密文镜像使用。

## 固定适配

`data/device-profile.json` 从已知历史布局导出，不包含完整固件或密钥：143 个功能字段所在扇区（含 1 个 `/dev/nflasha8` CP 目标）、111 个 ext2／WBI／CP 结构守卫，共 254 个扇区、130,048 字节。profile v2 对目标扇区校验未管理字节哈希；全部功能相对原始内容改变 142 个扇区。

App 偏移使用真实文件块到系统明文分区的映射；WBI 使用已核对逻辑偏移到明文分区的映射。新读取器只读固定表；写入器编译固定地址与每个可变字节掩码，不能通过请求增加任意路径或地址。

这不是全固件身份校验或整机备份。合法实时 WBI 变化也可能触发严格拒绝，当前没有推测放行或强制参数。

## 调试与可靠性

请求绑定适配哈希及随机 nonce，结果带同一 nonce，防止读取旧结果。服务接口保留第一组 bulk IN/OUT（`0x81/0x02`），避免被第二组端点覆盖导致认证超时。接管后枚举有界等待，结束时释放 libusb 句柄。认证显示分阶段进度且不输出原始 ProductInfo；服务身份出现多个匹配设备时拒绝继续。USB 通道拒绝短写、收齐分段响应并限制结果长度；写入器从不自动重发。用户中断或结果丢失保留意图记录并报告不确定状态。

历史样例将 raw syscall `74` 用作同步，但 Linux AArch64 通用 ABI 中 `74` 是 `signalfd4`、`82` 才是 `fsync`。新执行器使用 `SYS_FSYNC=82` 并检查返回值；历史归档保留原样。依据：[Linux v6.6 asm-generic/unistd.h](https://github.com/torvalds/linux/blob/v6.6/include/uapi/asm-generic/unistd.h)。不能把旧编号调用解释为可靠同步证据。

`native/transaction.c` 编译成无 libc 依赖的 ARM64 ELF，经既有 `SET_FILE/file_id=2` 运行，配置与结果经 `file_id=0`。使用者使用包内预编译文件；维护者重建：

```sh
python3 scripts/build_native.py
python3 scripts/build_wheel.py --output-dir dist
```

原生构建需要 clang 与 `aarch64-elf-ld` 或 `ld.lld`；只在本机编译，不访问相机。源码、适配和产物通过 `data/native/manifest.json` 关联。

基础测试使用标准库；可选 `unicorn/pyelftools` 测试真实编译后的 ARM64 指令，可选 `pyusb` 测试模拟通信端点：

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 验证边界

旧版 152 扇区窗口的实机读取、解锁／关闭 dry-run 和返回 MSC 已通过；dev15 的 251 扇区 profile 已完成实机只读预检和声音 dry-run。实际写入闭环尚未完成，元数据保留 `hardware_validation=pending-unified-selection`；模拟原生代码测试不算实机写入验证。稀疏功能状态备份／恢复已在 dev13 接入同一设备锁与事务边界，dev15 扩展为 251 扇区并支持恢复所选功能；整盘／分区／固件镜像备份与恢复、其他机型适配仍未提供。

桌面预览有两种模式。模拟模式复用事务正文 `_execute_session`、`_backup_session`、`_restore_session`，使用内存设备和独立临时目录，不经过物理 USB 或物理设备锁。真实模式通过 `physical_bridge.py` 与 `PhysicalService` 调用物理 `execute`／`execute_backup`／`execute_restore`，因此仍获取本机设备锁并进入同一套 USB 事务正文；详见 [桌面预览](DESKTOP-PREVIEW.md) 与[功能状态备份与恢复](BACKUP-RESTORE.md)。


## 会话掉线恢复

真实模式在操作异常时清除缓存的 USB backend；helper 重新枚举时按 `locationID` 去重同端口残留节点。若相机停在已接管状态，helper 会先 reset 恢复 mass-storage 驱动，再重新接管。


## 本机免密接管

macOS 若已一次性安装 `/usr/local/sbin/a6700-capture` 并配置 `sudo -n`，真实模式优先使用它，避免每次弹出管理员密码；helper 不存在或未配置免密时回退到单次 `osascript` 授权。该 helper 为 root 所有且非组／其他可写。
