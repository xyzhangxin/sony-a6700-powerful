import SwiftUI
import AppKit

enum AppBrand {
    static let name = "Powerful6700"
    static var icon: NSImage? {
        guard let url = Bundle.main.url(forResource: "Powerful6700", withExtension: "png") else { return nil }
        return NSImage(contentsOf: url)
    }
}

// Presentation metadata only. Feature IDs, dependencies and writes belong to the service.
enum UI {
    static let ink = Color(red: 0.12, green: 0.17, blue: 0.17)
    static let accent = Color(red: 0.08, green: 0.37, blue: 0.31)
    static let muted = Color(red: 0.43, green: 0.47, blue: 0.48)
    static let background = Color(red: 0.96, green: 0.97, blue: 0.97)
    static let line = Color.black.opacity(0.07)
    static let amber = Color(red: 0.65, green: 0.36, blue: 0.10)
}

struct PrimaryStyle: ButtonStyle {
    var secondary = false
    var destructive = false
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 13, weight: .semibold))
            .padding(.horizontal, 18).padding(.vertical, 11)
            .foregroundColor(secondary ? UI.ink : .white)
            .background(secondary ? Color.black.opacity(configuration.isPressed ? 0.09 : 0.045)
                        : (destructive ? Color(red: 0.68, green: 0.22, blue: 0.20) : UI.accent)
                            .opacity(configuration.isPressed ? 0.8 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .opacity(enabled ? 1 : 0.38)
    }
}
struct Panel<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .padding(20).frame(maxWidth: .infinity, alignment: .leading)
            .background(.white).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(UI.line))
    }
}
struct PageTitle: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.system(size: 23, weight: .bold))
            Text(subtitle).font(.system(size: 12)).foregroundColor(UI.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
struct StatusPill: View {
    let text: String
    var good = true
    var body: some View {
        Label(text, systemImage: good ? "checkmark.circle.fill" : "circle.lefthalf.filled")
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(good ? UI.accent : UI.amber)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background((good ? UI.accent : UI.amber).opacity(0.07))
            .clipShape(Capsule())
    }
}
extension FeatureRow {
    var title: String {
        switch id {
        case "ftp": return "FTP 传输"
        case "shutter": return "关机快门帘"
        case "exposure": return "灵活曝光"
        case "sound": return "电子快门声音"
        case "pixel_shift": return "位移多帧合成"
        case "dynamic_active": return "动态增强防抖"
        default: return name
        }
    }
    var explanation: String {
        switch id {
        case "ftp": return "解锁原生 FTP 菜单与传输设置"
        case "shutter": return "关机闭帘与静音模式联动"
        case "exposure": return "视频快门、光圈、ISO 独立控制"
        case "sound": return "解锁电子快门声音与音量选项"
        case "pixel_shift": return "解锁 Pixel Shift 菜单，成像效果待完整验证"
        case "dynamic_active": return "新增录像防抖「动态增强」，保留原有拍摄条件限制"
        case "fl23": return "新增独立 FL2／FL3、图标及完整说明；可与其他功能一起选择，保留简称"
        case "creative_look_names": return "主列表和编号 1～6 显示完整名称，可独立启用"
        default: return "按当前适配管理此功能"
        }
    }
    var icon: String {
        switch id {
        case "ftp": return "arrow.up.arrow.down"
        case "shutter": return "camera.aperture"
        case "exposure": return "slider.horizontal.3"
        case "sound": return "speaker.wave.2"
        case "dynamic_active": return "viewfinder"
        case "fl23": return "camera.filters"
        case "creative_look_names": return "character.bubble"
        default: return "square.3.layers.3d"
        }
    }
    var stateText: String { update_available == true ? "已解锁 · 可更新" : state == "unknown" ? "尚未读取" : state == "enabled" ? "已解锁" : state == "disabled" ? "未解锁" : "部分解锁" }
}
extension BackupRow {
    var dateText: String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = formatter.date(from: created_at) ?? ISO8601DateFormatter().date(from: created_at)
        guard let date = date else { return created_at }
        let output = DateFormatter(); output.dateFormat = "MM月dd日 HH:mm"
        return output.string(from: date)
    }
}
