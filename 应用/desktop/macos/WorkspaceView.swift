import SwiftUI
import AppKit

struct WorkspaceView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
    var onOpenMarket: (() -> Void)? = nil
    @State private var action = "unlock"
    @State private var hideReceipt = false
    private var features: [FeatureRow] { model.state?.features ?? [] }
    private var available: [FeatureRow] { features.filter { $0.isAvailable } }
    private var selected: [FeatureRow] { available.filter { $0.selected == true } }
    private var affected: [FeatureRow] { selected.filter { $0.state != (action == "unlock" ? "enabled" : "disabled") || (action == "unlock" && $0.update_available == true) } }
    private var hasUpdates: Bool { action == "unlock" && affected.contains { $0.update_available == true } }
    private var dependencyIssue: String? {
        if action == "unlock" {
            for feature in affected {
                for id in feature.requires ?? [] {
                    if let dependency = features.first(where: { $0.id == id }), dependency.state != "enabled", dependency.selected != true {
                        return "\(feature.title)需要\(dependency.title)，请同时选中。"
                    }
                }
            }
        } else {
            for feature in features where feature.state != "disabled" {
                for id in feature.requires ?? [] {
                    if affected.contains(where: { $0.id == id }), feature.selected != true {
                        return "\(feature.title)依赖此功能，请同时选中关闭。"
                    }
                }
            }
        }
        return nil
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                Text("功能管理").font(.system(size: 22, weight: .bold))
                Spacer()
                StatusPill(text: physical ? "A6700 · 检查通过" : "演示设备 · 检查通过")
            }
            if let result = model.state?.result, result.success && result.aftercare == nil && !hideReceipt {
                ResultReceipt(result: result, physical: physical) { hideReceipt = true }
            }
            Text("已解锁 \(available.filter { $0.state == "enabled" }.count) / \(available.count) 项")
                .font(.system(size: 11)).foregroundColor(UI.muted)
            HStack(alignment: .top, spacing: 18) {
                featureList
                actionPanel.frame(width: 270)
            }
        }
    }
    private var featureList: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Text("选择功能").font(.system(size: 12, weight: .semibold))
                Spacer()
                Button(action == "unlock" ? "选择未解锁或可更新" : "选择可关闭") {
                    model.send("select", extra: ["mode": action == "unlock" ? "missing" : "enabled"])
                }.buttonStyle(.plain).foregroundColor(UI.accent)
                Button(selected.count == available.count ? "清空" : "全选") {
                    model.send("select", extra: ["mode": selected.count == available.count ? "none" : "all"])
                }.buttonStyle(.plain).foregroundColor(UI.muted)
            }.font(.system(size: 11)).padding(14)
            Divider()
            ForEach(features) { feature in
                let soundFailure = feature.id == "custom_shutter_sound" && model.customSoundState == "error"
                let statusText = soundFailure ? "校验失败" : feature.stateText
                VStack(spacing: 0) {
                    Toggle(isOn: Binding(get: { feature.selected == true }, set: {
                        model.send("select_feature", extra: ["id": feature.id, "value": $0])
                    })) {
                        HStack(spacing: 12) {
                            Image(systemName: feature.icon).font(.system(size: 17)).foregroundColor(UI.accent)
                                .frame(width: 32, height: 34).background(UI.accent.opacity(0.045)).cornerRadius(8)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(feature.title).font(.system(size: 13, weight: .semibold))
                                Text(feature.isAvailable ? feature.explanation : (feature.unavailable_reason ?? "开发中，暂不开放")).font(.system(size: 10)).foregroundColor(UI.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                                if soundFailure, let reason = model.state?.custom_sound?.reason {
                                    Text(reason).font(.system(size: 10)).foregroundColor(UI.amber)
                                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                                }
                                if !(feature.requires ?? []).isEmpty {
                                    Text("需要：" + features.filter { (feature.requires ?? []).contains($0.id) }.map(\.title).joined(separator: "、"))
                                        .font(.system(size: 10)).foregroundColor(UI.amber)
                                }
                            }
                            Spacer(minLength: 8)
                            if feature.update_available == true {
                                Button(feature.id == "ftp" ? "更新 FTP" : "更新 FL2／FL3") {
                                    model.send(feature.id == "ftp" ? "update_ftp" : "update_fl23")
                                }
                                .buttonStyle(.bordered).tint(UI.accent).controlSize(.small)
                                .accessibilityHint("仅更新当前功能，保留其他功能和设置")
                            }
                            if feature.id == "custom_shutter_sound" && model.customSoundRecoverable {
                                recoverCustomSoundButton(disabled: model.isBusy)
                            }
                            if feature.id == "custom_shutter_sound" && onOpenMarket != nil {
                                marketShortcutButton(disabled: model.isBusy)
                            }
                            featureStatusLabel(
                                feature.isAvailable ? statusText : "暂未开放",
                                color: soundFailure ? UI.amber : feature.state == "enabled" ? UI.accent : UI.muted
                            )
                        }.padding(.leading, 6)
                    }.toggleStyle(.checkbox).disabled(!feature.isAvailable).opacity(feature.isAvailable ? 1 : 0.55).padding(.horizontal, 16).padding(.vertical, 10)
                        .accessibilityLabel("选择功能：" + feature.title)
                        .accessibilityHint(statusText + "。" + feature.explanation)
                    if feature.id == "sound" && !features.contains(where: { $0.id == "custom_shutter_sound" }) {
                        Divider().padding(.leading, 50)
                        customSoundFeatureRow
                    }
                    if feature.id != features.last?.id { Divider().padding(.leading, 50) }
                }
            }
        }.background(.white).cornerRadius(12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(UI.line))
            .disabled(model.isBusy)
    }

    private var customSoundFeatureRow: some View {
        let custom = model.state?.custom_sound
        let state = custom?.state ?? "disabled"
        let stateLabel: String = {
            switch state {
            case "enabled": return "已解锁"
            case "partial": return "部分改动"
            case "error": return "校验失败"
            default: return "未安装"
            }
        }()
        let explanation = (custom?.reason?.isEmpty == false)
            ? (custom?.reason ?? "")
            : "十槽自定义快门声功能与「电子快门声音」独立；App 内固件安装／卸载将在后续版本开放。"
        return HStack(spacing: 12) {
            Toggle(isOn: .constant(false)) { EmptyView() }
                .toggleStyle(.checkbox).labelsHidden().disabled(true).opacity(0.45)
                .help("App 内自定义快门声固件安装／卸载将在后续版本开放")
                .accessibilityLabel("自定义快门声（当前仅支持检查状态与市场导入）")
            Image(systemName: "waveform.path.ecg").font(.system(size: 17)).foregroundColor(UI.accent)
                .frame(width: 32, height: 34).background(UI.accent.opacity(0.045)).cornerRadius(8)
                .opacity(state == "enabled" ? 1 : 0.65)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(custom?.name ?? "自定义快门声").font(.system(size: 13, weight: .semibold))
                    Text("仅检查状态")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(UI.muted)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Color.black.opacity(0.045)).cornerRadius(4)
                }
                Text(explanation).font(.system(size: 10)).foregroundColor(UI.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Text("需要：电子快门声音").font(.system(size: 10)).foregroundColor(UI.amber)
            }
            Spacer(minLength: 8)
            if model.customSoundRecoverable {
                recoverCustomSoundButton(disabled: model.isBusy)
            }
            marketShortcutButton(disabled: model.isBusy || onOpenMarket == nil)
            featureStatusLabel(
                stateLabel,
                color: state == "enabled" ? UI.accent : (state == "partial" || state == "error") ? UI.amber : UI.muted
            )
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
    }

    private func recoverCustomSoundButton(disabled: Bool) -> some View {
        Button {
            model.send("recover_custom_sound")
        } label: {
            Label("无损恢复（保留音频）", systemImage: "arrow.triangle.2.circlepath")
        }
        .buttonStyle(.bordered).tint(UI.accent).controlSize(.small)
        .disabled(disabled)
        .accessibilityHint("清除开机保护锁标记并按需升级常驻，完整保留全部 10 槽音频")
    }

    private func marketShortcutButton(disabled: Bool) -> some View {
        Button {
            onOpenMarket?()
        } label: {
            Label("去快门声市场", systemImage: "waveform")
        }
        .buttonStyle(.bordered).tint(UI.accent).controlSize(.small)
        .disabled(disabled)
        .accessibilityHint("前往快门声市场查看状态、读取相机槽位或导入音频")
    }

    private func featureStatusLabel(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .medium))
            .foregroundColor(color)
            .fixedSize(horizontal: true, vertical: false)
            .frame(minWidth: 48, alignment: .trailing)
    }
    private var actionPanel: some View {
        Panel {
            Text("调整所选功能").font(.system(size: 14, weight: .semibold))
            Picker("操作方式", selection: $action) {
                Text("解锁").tag("unlock"); Text("关闭").tag("close")
            }.labelsHidden().pickerStyle(.segmented).padding(.top, 14).accessibilityLabel("操作方式")
            Text(action == "unlock" ? "将\(hasUpdates ? "解锁或更新" : "解锁") \(affected.count) 项" : "检查并关闭 \(selected.count) 项")
                .font(.system(size: 21, weight: .bold)).padding(.top, 23)
            Text("已选 \(selected.count) 项 · " + (action == "close" ? "固件已关闭的项仍检查机身设置" : "状态已符合的功能会跳过"))
                .font(.system(size: 10)).foregroundColor(UI.muted).padding(.top, 6)
            VStack(alignment: .leading, spacing: 10) {
                if affected.isEmpty {
                    Text(action == "close" && !selected.isEmpty ? "固件已关闭，仍会检查所选功能的机身设置。" : available.allSatisfy { $0.state == "enabled" } && action == "unlock"
                         ? "本版可用功能都已解锁。如需撤销，切换到“关闭”并选择功能。"
                         : "在左侧选择需要调整的功能。")
                        .font(.system(size: 12)).foregroundColor(UI.muted).lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(affected) { feature in
                    Label(feature.title + (action == "unlock" && feature.update_available == true ? " · 更新" : ""), systemImage: action == "unlock" ? "plus.circle" : "minus.circle")
                        .font(.system(size: 12)).foregroundColor(UI.ink)
                }
            }.padding(.vertical, 20).frame(maxWidth: .infinity, minHeight: 94, alignment: .topLeading)
            if let issue = dependencyIssue {
                Text(issue).font(.system(size: 11)).foregroundColor(UI.amber)
                    .fixedSize(horizontal: false, vertical: true).padding(.bottom, 14)
            }
            if action == "close" && affected.contains(where: { $0.id == "dynamic_active" }) {
                Text("关闭前，请先在相机中将录像防抖切回「增强」或「标准」。")
                    .font(.system(size: 11)).foregroundColor(UI.amber)
                    .fixedSize(horizontal: false, vertical: true).padding(.bottom, 14)
            }
            if action == "close" {
                ForEach(selected) { feature in
                    if let notice = feature.close_notice, !notice.isEmpty {
                        Text(notice).font(.system(size: 11)).foregroundColor(UI.amber)
                            .fixedSize(horizontal: false, vertical: true).padding(.bottom, 14)
                    }
                }
            }
            if action == "unlock" {
                let customActive = (available + selected).contains {
                    $0.id == "custom_shutter_sound" && ($0.state == "enabled" || $0.state == "error")
                } || (model.state?.custom_sound?.state == "enabled" || model.state?.custom_sound?.state == "error")
                let customNotice = (available + selected).first { $0.id == "custom_shutter_sound" }?.unlock_notice
                let notices: [String] = {
                    if (selected.contains(where: { $0.id == "custom_shutter_sound" })
                        || (customActive && selected.contains(where: { !($0.unlock_notice ?? "").isEmpty }))),
                       let notice = customNotice, !notice.isEmpty {
                        return [notice]
                    }
                    var seen: [String] = []
                    for feature in selected {
                        if let notice = feature.unlock_notice, !notice.isEmpty, !seen.contains(notice) {
                            seen.append(notice)
                        }
                    }
                    return seen
                }()
                ForEach(notices, id: \.self) { notice in
                    Label(notice, systemImage: "clock")
                        .font(.system(size: 12)).foregroundColor(UI.amber)
                        .fixedSize(horizontal: false, vertical: true).padding(.bottom, 14)
                }
            }
            Button { model.send(action) } label: {
                Text((physical ? "" : "演示：") + (action == "unlock" ? "\(hasUpdates ? "解锁或更新" : "解锁") \(affected.count) 项" : "恢复设置并关闭 \(selected.count) 项"))
                    .frame(maxWidth: .infinity)
            }.buttonStyle(PrimaryStyle(destructive: action == "close"))
                .disabled((action == "close" ? selected.isEmpty : affected.isEmpty) || dependencyIssue != nil || model.isBusy)
            Text(physical
                 ? (action == "close" ? "一次完成：先恢复默认设置，再关闭固件功能并独立回读。" : "只修改所选功能，每次执行前会重新检查。")
                 : "演示不会连接或修改真实相机。")
                .font(.system(size: 10)).foregroundColor(UI.muted).lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true).padding(.top, 12)
        }
    }
}

// Retained for development; no desktop entry until full backup/restore is delivered.
struct BackupWorkspaceView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
    @State private var selectedBackup = ""
    private var backups: [BackupRow] { model.state?.backups ?? [] }
    private var current: BackupRow? { backups.first(where: { $0.id == selectedBackup }) ?? backups.first }
    private var features: [FeatureRow] { model.state?.features ?? [] }
    private var available: [FeatureRow] { features.filter { $0.isAvailable } }
    private var selected: [FeatureRow] { available.filter { $0.selected == true } }
    var body: some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 16) {
                Panel {
                    Text("保存当前功能状态").font(.system(size: 14, weight: .semibold))
                    Text("保存工具管理的功能字段，用于回到这次保存的状态。这不是整机或固件镜像。")
                        .font(.system(size: 11)).foregroundColor(UI.muted).lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true).padding(.vertical, 12)
                    HStack {
                        Button("创建功能状态备份") { model.send("backup") }.buttonStyle(PrimaryStyle(secondary: true))
                        Spacer()
                        Button("打开备份文件夹") {
                            if let path = model.state?.backup_dir { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                        }.buttonStyle(.plain).font(.system(size: 10)).foregroundColor(UI.accent)
                    }
                }
                Text("本机备份 · \(backups.count) 份").font(.system(size: 12, weight: .semibold))
                if backups.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "archivebox").font(.system(size: 27)).foregroundColor(UI.muted.opacity(0.5))
                        Text("还没有备份").font(.system(size: 13, weight: .medium))
                        Text("先保存一份当前状态，之后可在这里选择恢复。")
                            .font(.system(size: 11)).foregroundColor(UI.muted)
                    }.frame(maxWidth: .infinity).padding(.vertical, 38)
                        .background(.white).cornerRadius(12)
                } else {
                    VStack(spacing: 0) {
                        ForEach(backups) { backup in
                            Button { selectedBackup = backup.id } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: current?.id == backup.id ? "largecircle.fill.circle" : "circle")
                                        .foregroundColor(UI.accent)
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text(backup.dateText).font(.system(size: 12, weight: .semibold))
                                        Text("\(backup.features.filter { $0.state == "enabled" }.count) 项已解锁 · \(max(1, backup.size / 1024)) KB")
                                            .font(.system(size: 10)).foregroundColor(UI.muted)
                                    }
                                    Spacer()
                                    if backup.id == backups.first?.id { Text("最新").font(.system(size: 10)).foregroundColor(UI.accent) }
                                }.padding(16).contentShape(Rectangle())
                            }.buttonStyle(.plain).accessibilityLabel("选择备份：" + backup.dateText)
                            if backup.id != backups.last?.id { Divider() }
                        }
                    }.background(.white).cornerRadius(12)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            restorePanel.frame(width: 310)
        }.disabled(model.isBusy)
    }
    private var restorePanel: some View {
        Panel {
            Text("恢复到备份状态").font(.system(size: 14, weight: .semibold))
            if let backup = current {
                Text(backup.dateText).font(.system(size: 11)).foregroundColor(UI.muted).padding(.top, 7)
                Text("选择要恢复的功能").font(.system(size: 11, weight: .medium)).padding(.top, 18)
                HStack {
                    Spacer()
                    Button(selected.count == available.count ? "清空" : "全选") {
                        model.send("select", extra: ["mode": selected.count == available.count ? "none" : "all"])
                    }.buttonStyle(.plain).font(.system(size: 10)).foregroundColor(UI.accent)
                }.padding(.top, 4)
                VStack(alignment: .leading, spacing: 13) {
                    ForEach(available) { feature in
                        Toggle(isOn: Binding(get: { feature.selected == true }, set: {
                            model.send("select_feature", extra: ["id": feature.id, "value": $0])
                        })) {
                            HStack {
                                Text(feature.title).font(.system(size: 11))
                                Spacer()
                                Text(backup.features.first(where: { $0.id == feature.id })?.stateText ?? "未知")
                                    .font(.system(size: 10)).foregroundColor(UI.muted)
                            }
                        }.toggleStyle(.checkbox).accessibilityLabel("恢复功能：" + feature.title)
                    }
                }.padding(.vertical, 16)
                Button {
                    model.send("restore", extra: ["backup_id": backup.id])
                } label: { Text("恢复所选 \(selected.count) 项").frame(maxWidth: .infinity) }
                    .buttonStyle(PrimaryStyle()).disabled(selected.isEmpty)
                Text("仅恢复所选功能，其他字段保持现状。备份与当前机身内容不匹配时会停止。")
                    .font(.system(size: 10)).foregroundColor(UI.muted).lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true).padding(.top, 12)
            } else {
                Text("左侧创建或选择一份备份后，再选择需要恢复的功能。")
                    .font(.system(size: 12)).foregroundColor(UI.muted).lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true).padding(.top, 16)
            }
        }
    }
}
