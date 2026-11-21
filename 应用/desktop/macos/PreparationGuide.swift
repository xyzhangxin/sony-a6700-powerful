import SwiftUI

private let guideGreen = UI.accent
private let guideInk = UI.ink
private let guideOrange = Color(red: 1.0, green: 0.63, blue: 0.24)
private let guidePanel = Color(red: 0.10, green: 0.15, blue: 0.20)

extension PreparationRow {
    static let defaultSteps: [PreparationRow] = [
        PreparationRow(
            id: "ftp_stopped",
            title: "FTP：仅已有菜单时处理",
            instruction: "首次解锁通常没有 FTP 菜单，这是正常情况，可直接继续。仅当机身已有此菜单且 FTP 开启时，停止传输并将 FTP 功能设为关；此前运行过 FTP 时，关闭后重启相机以释放介质占用。",
            confirmed: false,
            confirmation_label: "已有 FTP 菜单，已关闭并完成准备",
            skip_label: "没有 FTP 菜单，直接继续",
            menu_guides: [
                MenuGuideRow(
                    id: "ftp-off",
                    title: "FTP功能",
                    path: ["MENU", "网络", "传输/远程", "FTP传输功能", "FTP功能"],
                    target: "关",
                    choices: ["关", "开"],
                    source_url: "",
                    source_note: "仅已解锁机身；依据项目菜单与实机记录，不属于 A6700 原厂公开菜单。位置以机身为准。"
                )
            ]
        ),
        PreparationRow(
            id: "camera_msc",
            title: "设置 USB 海量存储模式",
            instruction: "先断开 USB，在相机菜单中选择海量存储器(MSC)。若保留连接时选择，则接线后选择影像传输(MSC)。不要选择 MTP、电脑遥控或 USB 流式传输。",
            confirmed: false,
            confirmation_label: "已设置 MSC 模式",
            skip_label: "",
            menu_guides: [
                MenuGuideRow(
                    id: "usb-msc",
                    title: "USB连接模式",
                    path: ["MENU", "设置", "USB", "USB连接模式"],
                    target: "海量存储器(MSC)",
                    choices: ["连接时选择", "USB流式传输", "海量存储器(MSC)", "MTP", "电脑遥控"],
                    source_url: "https://helpguide.sony.net/ilc/2320/v1/zh-cn/contents/0601_usb_connection.html",
                    source_note: "菜单名称及选项依据 Sony A6700 简体中文帮助指南。"
                )
            ]
        ),
        PreparationRow(
            id: "power_awake",
            title: "关闭自动关机，确认电量",
            instruction: "在接线前将自动关机开始时间设为关，并确认电量充足或供电可靠。不要把自动关机温度当作这个项目；操作完成后可恢复原来的省电设置。",
            confirmed: false,
            confirmation_label: "已关闭自动关机，电量或供电可靠",
            skip_label: "",
            menu_guides: [
                MenuGuideRow(
                    id: "power-save-off",
                    title: "自动关机开始时间",
                    path: ["MENU", "设置", "电源设置选项", "自动关机开始时间"],
                    target: "关",
                    choices: ["关", "30分钟", "5分钟", "2分钟", "1分钟", "10秒"],
                    source_url: "https://helpguide.sony.net/ilc/2320/v1/zh-cn/contents/0601B_power_save_start_time.html",
                    source_note: "Sony 帮助指南说明 USB 供电或连接电脑时会停用自动关机；仍提前设为关，以覆盖连接切换阶段。"
                )
            ]
        ),
        PreparationRow(
            id: "usb_connection",
            title: "最后连接 USB 数据线",
            instruction: "完成上面的菜单设置后，使用可传数据的 USB 线连接相机与电脑，只连接一台目标相机。若弹出连接方式选择，选择影像传输(MSC)。日常功能管理不接 Pico 或测试板；操作期间保持相机开机。",
            confirmed: false,
            confirmation_label: "已连接唯一相机，保持开机与唤醒",
            skip_label: "",
            menu_guides: []
        )
    ]
}

/// Four preparation declarations with an always-visible, cropped preview of
/// the relevant camera-screen corner. The preview is an owned illustration,
/// not a firmware screenshot; it never changes device or preparation state.
struct PreparationGuideView: View {
    let steps: [PreparationRow]

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.element.id) { index, row in
                HStack(alignment: .top, spacing: 16) {
                    Text(String(index + 1)).font(.system(size: 12, weight: .semibold))
                        .foregroundColor(UI.accent).frame(width: 27, height: 27)
                        .background(UI.accent.opacity(0.07)).cornerRadius(8)
                    VStack(alignment: .leading, spacing: 7) {
                        Text(row.title).font(.system(size: 13, weight: .semibold))
                        Text(summary(row)).font(.system(size: 11)).foregroundColor(UI.muted)
                            .fixedSize(horizontal: false, vertical: true)
                        if let guide = row.menu_guides.first {
                            Text(guide.path.joined(separator: " → ") + " → " + guide.target)
                                .font(.system(size: 10)).foregroundColor(UI.ink.opacity(0.72))
                                .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 12)
                    PreparationPreview(row: row)
                }.padding(.horizontal, 16).padding(.vertical, 10)
                if index + 1 < steps.count { Divider().padding(.leading, 59) }
            }
        }.background(.white).cornerRadius(12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(UI.line))
    }

    private func summary(_ row: PreparationRow) -> String {
        switch row.id {
        case "ftp_stopped": return "没有 FTP 菜单可直接跳过；如已有且开启，关闭传输后重启相机。"
        case "camera_msc": return "选择海量存储器（MSC），不要选择 MTP、电脑遥控或 USB 流式传输。"
        case "power_awake": return "关闭自动关机，确认电量充足或供电可靠。"
        case "usb_connection": return "使用可传数据的 USB 线，只连接一台相机；普通使用不接 Pico。"
        default: return row.instruction
        }
    }
}

private struct PreparationPreview: View {
    let row: PreparationRow

    var body: some View {
        if let guide = row.menu_guides.first {
            CompactMenuPreview(guide: guide)
        } else {
            CompactUSBPreview()
        }
    }
}

/// A single highlighted setting from the top-right area of the camera screen.
private struct CompactMenuPreview: View {
    let guide: MenuGuideRow

    private var symbol: String {
        switch guide.id {
        case "ftp-off": return "network.slash"
        case "usb-msc": return "externaldrive.connected.to.line.below"
        case "power-save-off": return "battery.75percent"
        default: return "list.bullet.rectangle"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 5) {
                Image(systemName: "camera").font(.system(size: 8))
                Text("相机界面 · 右上角").font(.system(size: 8, weight: .semibold))
                Spacer(minLength: 0)
                Text("示意").font(.system(size: 8)).foregroundColor(.white.opacity(0.42))
            }.foregroundColor(.white.opacity(0.58))

            HStack(spacing: 8) {
                Image(systemName: symbol).font(.system(size: 15, weight: .medium))
                    .foregroundColor(guideOrange).frame(width: 25, height: 25)
                    .background(guideOrange.opacity(0.13)).cornerRadius(6)
                VStack(alignment: .leading, spacing: 2) {
                    Text(guide.title).font(.system(size: 9)).foregroundColor(.white.opacity(0.58))
                        .lineLimit(1)
                    Text(guide.target).font(.system(size: 13, weight: .bold))
                        .foregroundColor(guideOrange).lineLimit(2)
                        .minimumScaleFactor(0.8).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                Image(systemName: "checkmark.circle.fill").font(.system(size: 13))
                    .foregroundColor(guideOrange)
            }
        }.padding(10).frame(width: 214, alignment: .leading)
            .background(guidePanel).cornerRadius(10)
            .overlay(alignment: .topTrailing) {
                Image(systemName: "arrow.up.right").font(.system(size: 7, weight: .bold))
                    .foregroundColor(.white.opacity(0.22)).padding(5)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("右上角菜单示意，不是实机截图。" + guide.title + "，请选择" + guide.target)
    }
}

/// A compact Mac—cable—camera connection preview, with MSC called out.
private struct CompactUSBPreview: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 5) {
                Image(systemName: "cable.connector").font(.system(size: 8))
                Text("连接方式 · 关键项").font(.system(size: 8, weight: .semibold))
                Spacer(minLength: 0)
                Text("示意").font(.system(size: 8)).foregroundColor(.secondary)
            }.foregroundColor(.secondary)
            HStack(spacing: 7) {
                Image(systemName: "laptopcomputer").font(.system(size: 17))
                Rectangle().fill(UI.accent.opacity(0.4)).frame(width: 26, height: 2)
                Image(systemName: "camera").font(.system(size: 17))
                Text("MSC").font(.system(size: 9, weight: .bold)).foregroundColor(.white)
                    .padding(.horizontal, 6).padding(.vertical, 3)
                    .background(guideOrange).cornerRadius(4)
            }.foregroundColor(UI.accent)
            Text("只连一台 · 不接 Pico").font(.system(size: 9)).foregroundColor(UI.muted)
        }.padding(10).frame(width: 214, alignment: .leading)
            .background(UI.accent.opacity(0.045)).cornerRadius(10)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(UI.accent.opacity(0.12)))
            .accessibilityElement(children: .combine)
            .accessibilityLabel("USB 连接示意：Mac 通过数据线连接 A6700，选择 MSC，只连一台，不接 Pico")
    }
}
