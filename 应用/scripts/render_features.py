"""Render the public per-field inventory from the runtime catalog."""
from __future__ import annotations

from pathlib import Path

from a6700_unlock.catalog import load_catalog


def main():
    catalog = load_catalog()
    summary = catalog.summary()
    lines = [
        "# 一期功能与具体修改点", "",
        "由 `scripts/render_features.py` 根据安装包内同一份 JSON 清单生成。0.2 测试版只允许四项功能写入。未完成的两项保留历史字段供识别和审计，解锁／关闭／恢复均禁用；下表历史字节数不等于本版写入范围。功能历史实测不代表统一 USB 工具已完成实机验收。", "",
        "## 范围", "",
        "| 功能（本版状态） | 系统文件相对原始变化字节 | WBI 相对原始变化字节 | CP 分区相对原始变化字节 | 证据与边界 |",
        "|---|---:|---:|---:|---|",
    ]
    for f in catalog.data["features"]:
        counts = {key: sum(sum(a != b for a, b in zip(e.disabled, e.enabled))
                           for e in catalog.edits if e.feature == f["id"] and e.image == key)
                  for key in catalog.data["images"]}
        lines.append(f"| {f['name']}（{'可测试' if f.get('available', True) else '暂禁用'}） | {counts['app']} | {counts['wbi']} | {counts['cpapp']} | "
                     + "；".join(f["limits"]) + " |")
    lines.extend([
        "", f"共 {len(catalog.edits)} 个字段定义；全部功能相对原始内容改变 App {summary['images']['app']['changed_bytes']} 字节、WBI {summary['images']['wbi']['changed_bytes']} 字节、CP 分区 {summary['images']['cpapp']['changed_bytes']} 字节。这是六项历史定义的合计；本版仅前四项可写。字段可能为多字节计数，字段数与变化字节数不同。",
        "",
        "FTP 参数包括开关 `0x5a5`，连接方式 `0x5a6`、服务器 `0x5a8`、自动传输 `0x5aa`，以及 `0x5ac～0x5b0` 的照片格式／尺寸、视频目标和节能设置。RAW+JPEG 双传使用原生选项，不新增传输程序。",
        "",
        "快门功能包括主项 `0x6b2` 与静音联动 `0x4bb` 的原生菜单、机型支持和 WBI 对应状态；不强制设置为开启，不修改 CP、机械驱动或关机动作代码。",
        "",
        "灵活曝光包括 `0x497`、`0x50c`、`0x50d`、`0x50e` 的机型支持、菜单权限和 WBI 状态；它已作为可选功能纳入统一清单，不再作为独立试用包。",
        "",
        "电子快门声音使用 `sound-after-exposure` 历史验证基线，与灵活曝光无运行时依赖；菜单只保留 4 项（`0xabe` 的 gate 固定为 00，因为相机不保存第 5 项），并包含 `0xabf/0xac0` 菜单权限、声音门控、WBI 状态和 `/dev/nflasha8` 扇区 11932 的 CP 触发字节（`00→03`）。",
        "",
        "位移多帧合成实机稳定性失败，已撤回并禁用；下表中的旧实验描述不代表当前可用状态。历史字段包含 `0x4b5/0x730/0x731` 的 App 静态机型可用性、WBI 运行时可用性和 `0x214/0x820` 菜单权限；不新增合成算法。",
        "",
        "动态增强防抖当前禁用。历史清单仅合并已落地的 6 字节：App `0x4b7b93a/0x3ed654c`，WBI `0x2495b3a/0x3e2839c/0x743f53c/0x10c4701`。打开列 3 菜单及 ACTIVE_PLUS 值支持，移除一项不支持贡献；计数高三字节仍由未管理字节哈希守卫。没有开放列 6/7/8、绕过录像条件或移植 CP 算法；关闭前先在相机切回增强或标准。详见 [动态增强集成](动态增强防抖.md)。",
        "",
        "回放筛选、回放文案和其他研究候选不纳入本期。关闭计划只撤销本清单列出的修改，不等于刷写整套官方固件或恢复整机设置。",
        "",
        "## 历史差异的处理", "",
        "最初 69 个权限字节是 WBI 中当前机型的父／子／叶权限。历史已验证 App 菜单候选相对原始内容改变 81 字节，多出的 12 字节位于 FTP 父菜单权限列。本清单明确保留这 12 处历史修改，以重现已验证 App；它们不是新增功能，也不能误计为 WBI 修改。关闭时这 12 处也恢复原值。",
        "",
        "空白重复父项 `0x262` 的目标为隐藏：其未解锁值与最终解锁值均为 `00`，另接受早期 `01` 值并生成关闭该重复入口的差异。它不会被计算成一个新解锁功能。",
        "",
        "WBI 引用计数仅移除已知样本中一份机型不支持贡献，保留其他贡献。本阶段只接受完整已知字段值和完整归一化镜像哈希；不在实时状态中盲目套用这些计数。",
        "",
        "## 地址与数据视图", "",
        "- `app` 是已解密 `/lib/appFw.so` 的 ELF **文件偏移**，不是 ELF 虚拟地址或分区偏移。",
        "- `wbi` 是已解密且提取的 WBI2 逻辑内容偏移，保留原头。不是 Pico 裸 eMMC 密文地址。",
        "- 表中解锁值是本期最终目标，已合并历史修改；已知值包含未解锁、最终值和明确支持的历史中间值。",
        "- 所有记录均只修改固定长度字段；原型计划不包含将文件偏移映射到真实闪存的逻辑。",
        "",
        "## 镜像身份", "",
        "| 输入 | 长度 | 将已知字段还原后的 SHA-256 |",
        "|---|---:|---|",
    ])
    for key, spec in catalog.data["images"].items():
        lines.append(f"| `{key}` | {spec['size']} | `{spec['normalized_sha256']}` |")
    for f in catalog.data["features"]:
        lines.extend(["", f"## {f['name']}", "",
                      f"历史证据索引（研究工程内）：`{f['evidence']}`。", "",
                      "| 字段 ID | 视图 | 文件偏移 | 未解锁值 | 解锁值 | 已知值 | 修改用途 |",
                      "|---|---|---|---|---|---|---|"])
        for e in catalog.edits:
            if e.feature == f["id"]:
                known = ", ".join(v.hex() for v in e.known_values)
                purpose = e.purpose.replace("|", "\\|")
                lines.append(f"| `{e.id}` | `{e.image}` | `{e.offset:#x}` | `{e.disabled.hex()}` | "
                             f"`{e.enabled.hex()}` | `{known}` | {purpose} |")
    lines.extend(["", "## 来源文件校验索引", "",
                  "下列路径是研究工程内的证据标识，不是本包运行依赖。", "",
                  "| 来源 | SHA-256 |", "|---|---|"])
    for source in catalog.data["sources"]:
        lines.append(f"| `{source['path']}` | `{source['sha256']}` |")
    target = Path(__file__).resolve().parents[1] / "docs/FEATURES.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Rendered {len(catalog.edits)} field definitions")


if __name__ == "__main__":
    main()
