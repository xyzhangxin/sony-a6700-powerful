import SwiftUI
import AppKit

struct WorkspaceView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
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
                                if !(feature.requires ?? []).isEmpty {
                                    Text("需要：" + features.filter { (feature.requires ?? []).contains($0.id) }.map(\.title).joined(separator: "、"))
                                        .font(.system(size: 10)).foregroundColor(UI.amber)
                                }
                            }
                            Spacer(minLength: 4)
                            Text(feature.isAvailable ? feature.stateText : "暂未开放").font(.system(size: 10, weight: .medium))
                                .foregroundColor(feature.state == "enabled" ? UI.accent : UI.muted)
                            if feature.id == "fl23" && feature.update_available == true {
                                Button("更新 FL2／FL3") { model.send("update_fl23") }
                                    .buttonStyle(.bordered).tint(UI.accent)
                                    .accessibilityHint("仅更新 FL2／FL3 修复包，保留中文名称和其他功能")
                            }
                        }.padding(.leading, 6)
                    }.toggleStyle(.checkbox).disabled(!feature.isAvailable).opacity(feature.isAvailable ? 1 : 0.55).padding(.horizontal, 16).padding(.vertical, 12)
                        .accessibilityLabel("选择功能：" + feature.title)
                        .accessibilityHint(feature.stateText + "。" + feature.explanation)
                    if feature.id != features.last?.id { Divider().padding(.leading, 50) }
                }
            }
        }.background(.white).cornerRadius(12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(UI.line))
            .disabled(model.isBusy)
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
                ForEach(selected) { feature in
                    if let notice = feature.unlock_notice, !notice.isEmpty {
                        Label(notice, systemImage: "clock")
                            .font(.system(size: 12)).foregroundColor(UI.amber)
                            .fixedSize(horizontal: false, vertical: true).padding(.bottom, 14)
                    }
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
