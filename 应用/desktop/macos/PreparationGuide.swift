import SwiftUI

private let guideGreen = UI.accent
private let guideInk = UI.ink
private let guideOrange = Color(red: 1.0, green: 0.63, blue: 0.24)
private let guidePanel = Color(red: 0.10, green: 0.15, blue: 0.20)

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
                }.padding(18)
                if index + 1 < steps.count { Divider().padding(.leading, 59) }
            }
        }.background(.white).cornerRadius(12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(UI.line))
        Text("右侧只展示相机界面右上角的关键项，不是实机截图。下方确认只代表人工声明，连接后仍会检查固件与功能状态。")
            .font(.system(size: 11)).foregroundColor(UI.muted)
            .fixedSize(horizontal: false, vertical: true)
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: "info.circle").foregroundColor(UI.accent)
            VStack(alignment: .leading, spacing: 6) {
                Text("为什么要授权，MSC 提示为什么会消失？").font(.system(size: 12, weight: .semibold))
                Text("相机模式会请求 Mac 管理员密码或 Touch ID，临时接管 USB 并进入维护服务。它不是相机密码；普通导照片无需此授权。相机退出 MSC 提示本身不等于断线，请保持数据线连接，以应用的操作结果为准。若应用提示会话中断或需要重新检查，请按错误指引处理。")
                    .font(.system(size: 11)).foregroundColor(UI.muted)
                    .fixedSize(horizontal: false, vertical: true).lineSpacing(3)
            }
        }.padding(15).frame(maxWidth: .infinity, alignment: .leading)
            .background(UI.accent.opacity(0.045)).cornerRadius(10)
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
