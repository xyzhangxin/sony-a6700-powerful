import SwiftUI
import AppKit

struct OperationServiceStoppedView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            PageTitle(title: model.serviceWriteUncertain ? "服务中断，写入结果待核实" : "本地设备服务已停止",
                      subtitle: "重新插拔 USB 不会重启已停止的本地服务。")
            Panel {
                Label(model.serviceWriteUncertain
                      ? "操作结果未获确认。不要直接重复解锁、关闭或恢复；先保留记录并核实机身状态。"
                      : "连接流程未完成。可以先重新启动本地服务，再重新准备和检查。",
                      systemImage: "exclamationmark.triangle")
                    .font(.system(size: 14, weight: .medium)).foregroundColor(UI.amber)
                    .fixedSize(horizontal: false, vertical: true)
                Text(physical ? "若相机停在 USB 服务模式，且确认没有写入仍在执行，请拔 USB、正常关机重开，再以 MSC 模式连接。" : "当前为演示模式，真实相机没有被访问。")
                    .font(.system(size: 12)).foregroundColor(UI.muted).lineSpacing(4).padding(.top, 15)
                Text("下方按钮只重启本机服务和检查环境，不会自动连接相机或重放上次操作。")
                    .font(.system(size: 12)).foregroundColor(UI.muted).padding(.top, 12)
            }
            Button("重新启动本地服务") { model.restartService() }.buttonStyle(PrimaryStyle())
        }
    }
}

struct OperationSuccessView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
    @State private var details = false
    private var result: OperationResult? { model.state?.result }
    private var guide: Aftercare? { result?.aftercare }
    private var title: String {
        if result?.status == "no-op" { return "已经是目标状态" }
        let verb = model.state?.action == "close" ? "关闭完成" : "解锁完成"
        return physical ? verb : "模拟" + verb
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top, spacing: 13) {
                Image(systemName: "checkmark.circle.fill").font(.system(size: 30)).foregroundColor(UI.accent)
                PageTitle(title: title, subtitle: subtitle)
                Spacer()
                StatusPill(text: physical ? "待机身验收" : "仅演示结果")
            }
            if let warning = result?.cleanup_warning, !warning.isEmpty {
                Label("USB 服务退出未确认。写入结果已保留；请按下方断电步骤重启，再重新连接。", systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12)).foregroundColor(UI.amber)
                    .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                    .background(UI.amber.opacity(0.06)).cornerRadius(10)
            }
            Panel {
                HStack {
                    Label("接下来：断电重启相机", systemImage: "power")
                        .font(.system(size: 16, weight: .semibold)).foregroundColor(UI.accent)
                    Spacer()
                    Text("关机 ≠ 完全断电").font(.system(size: 11, weight: .medium)).foregroundColor(UI.muted)
                }
                Text(!physical ? "以下为流程示意，无需操作真实相机。" :
                     (guide?.restart_required == true ? "请完整执行以下步骤，再检查菜单是否生效。" : "本次未重复写入。如尚未检查现有功能，可按以下步骤验收。"))
                    .font(.system(size: 12)).foregroundColor(UI.muted).padding(.top, 9)
                HStack(alignment: .top, spacing: 12) {
                    ForEach(Array((guide?.steps ?? []).enumerated()), id: \.element.id) { index, step in
                        VStack(alignment: .leading, spacing: 9) {
                            Text(String(index + 1)).font(.system(size: 11, weight: .bold))
                                .foregroundColor(UI.accent).frame(width: 23, height: 23)
                                .background(UI.accent.opacity(0.08)).clipShape(Circle())
                            Text(step.title).font(.system(size: step.id == "wait" ? 18 : 15, weight: .semibold))
                                .foregroundColor(step.id == "wait" ? UI.accent : UI.ink)
                            Text(step.detail).font(.system(size: 11)).foregroundColor(UI.muted)
                                .fixedSize(horizontal: false, vertical: true).lineSpacing(3)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.padding(.top, 20)
            }
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("开机后，检查本次功能").font(.system(size: 16, weight: .semibold))
                    Spacer()
                    Text("\(guide?.features.count ?? 0) 项 · 仅列出本次选择").font(.system(size: 11)).foregroundColor(UI.muted)
                }
                LazyVGrid(columns: [GridItem(.flexible(), alignment: .top), GridItem(.flexible(), alignment: .top)], alignment: .leading, spacing: 14) {
                    ForEach(guide?.features ?? []) { feature in
                        Panel {
                            HStack(alignment: .top) {
                                Text(feature.name).font(.system(size: 14, weight: .semibold))
                                Spacer(minLength: 8)
                                Text(feature.outcome).font(.system(size: 10)).foregroundColor(UI.accent)
                            }
                            Text(feature.path).font(.system(size: 12, weight: .medium))
                                .foregroundColor(UI.accent).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true).padding(.top, 14)
                            Text("成功标准").font(.system(size: 10, weight: .semibold)).foregroundColor(UI.muted).padding(.top, 14)
                            Text(feature.expected).font(.system(size: 12)).lineSpacing(4).padding(.top, 5)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(feature.note).font(.system(size: 11)).foregroundColor(UI.muted).lineSpacing(3).padding(.top, 10)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            DisclosureGroup("查看本次处理详情", isExpanded: $details) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(result?.detail ?? "")
                    if let warning = result?.cleanup_warning, !warning.isEmpty { Text(warning).textSelection(.enabled) }
                    ForEach(Array(model.progress.enumerated()), id: \.offset) { _, line in Text(line) }
                }.font(.system(size: 11)).foregroundColor(UI.muted).padding(.top, 10)
            }.font(.system(size: 11)).foregroundColor(UI.muted)
        }
    }
    private var subtitle: String {
        if !physical { return "合成数据演示已完成；真实相机没有被访问或修改。" }
        return result?.status == "no-op" ? "所选功能已符合目标，未重复写入。请在相机上确认实际表现。" : "写入与独立回读通过，实际功能还需在相机上确认。"
    }
}

struct ResultReceipt: View {
    let result: OperationResult
    let physical: Bool
    let dismiss: () -> Void
    @State private var details = false
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 11) {
                Image(systemName: "checkmark.circle.fill").font(.system(size: 19)).foregroundColor(UI.accent)
                VStack(alignment: .leading, spacing: 5) {
                    Text(result.title).font(.system(size: 12, weight: .semibold))
                    Text(result.detail).font(.system(size: 11)).foregroundColor(UI.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                if let path = result.backup_path, !path.isEmpty {
                    Button("在访达中显示") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
                        .buttonStyle(.plain).font(.system(size: 11)).foregroundColor(UI.accent)
                }
                Button { dismiss() } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                    .font(.system(size: 10)).foregroundColor(UI.muted).accessibilityLabel("收起操作结果")
            }
            if let digest = result.backup_sha256, !digest.isEmpty {
                DisclosureGroup("文件校验信息", isExpanded: $details) {
                    Text(digest).font(.system(size: 10, design: .monospaced)).textSelection(.enabled)
                }.font(.system(size: 10)).foregroundColor(UI.muted)
            }
        }.padding(15).frame(maxWidth: .infinity, alignment: .leading)
            .background(UI.accent.opacity(0.045)).cornerRadius(10)
    }
}

struct OperationProgressView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
    @State private var started = Date()
    @State private var cancelled = false
    @State private var details = false
    private var title: String {
        switch model.state?.action {
        case "inspect": return "正在连接并检查"
        case "backup": return "正在保存功能状态"
        case "restore": return "正在恢复所选功能"
        case "close": return "正在关闭所选功能"
        case "takeover": return "正在接管相机连接"
        default: return "正在解锁所选功能"
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            PageTitle(title: title, subtitle: physical
                      ? (model.state?.action == "takeover" ? "正在处理已确认的本机占用进程；此步骤不写入相机。" : model.state?.action == "inspect"
                         ? "授权后会从 MSC 切换到维护服务；MSC 提示可能消失，请保持连接，等待应用确认结果。"
                         : "请保持相机开机和 USB 连接，等待明确结果。")
                      : "正在运行演示流程，不会访问真实相机。")
            Panel {
                HStack(alignment: .center, spacing: 18) {
                    ProgressView().controlSize(.regular).frame(width: 32)
                    VStack(alignment: .leading, spacing: 8) {
                        Text(cancelled ? "正在请求结束，请稍候…" : currentStep)
                            .font(.system(size: 16, weight: .semibold))
                            .fixedSize(horizontal: false, vertical: true)
                        TimelineView(.periodic(from: started, by: 1)) { context in
                            Text("已用 \(max(0, Int(context.date.timeIntervalSince(started)))) 秒 · " + (model.state?.action == "takeover" ? "等待本机工具退出" : physical ? "请勿拔线或关机" : "演示步骤会短暂停留"))
                                .font(.system(size: 11)).foregroundColor(UI.muted)
                        }
                    }
                    Spacer()
                }.padding(.vertical, 18)
                if !model.progress.isEmpty {
                    Divider().padding(.vertical, 14)
                    DisclosureGroup("查看处理过程", isExpanded: $details) {
                        VStack(alignment: .leading, spacing: 9) {
                            ForEach(Array(model.progress.enumerated()), id: \.offset) { index, line in
                                Label(line, systemImage: index == model.progress.count - 1 ? "ellipsis.circle" : "checkmark.circle")
                                    .font(.system(size: 11)).foregroundColor(UI.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }.padding(.top, 12)
                    }.font(.system(size: 11)).foregroundColor(UI.muted)
                }
            }
            if model.state?.action != "takeover" { HStack(spacing: 14) {
                Button(cancelled ? "正在等待结果…" : "请求取消") {
                    cancelled = true; model.send("cancel")
                }.buttonStyle(PrimaryStyle(secondary: true)).disabled(cancelled)
                Text("取消不会强制中断已发送的写入，请等待核验结果。")
                    .font(.system(size: 11)).foregroundColor(UI.muted)
            } }
        }
    }
    private var currentStep: String {
        if let last = model.progress.last { return last }
        return physical ? "正在准备连接相机…" : "正在准备演示数据…"
    }
}

struct OperationFailureView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
    @State private var details = false
    @State private var confirmingTakeover = false
    private var status: String { model.state?.result?.status ?? "error" }
    private var uncertain: Bool { ["uncertain", "verification-failed", "recovery-required"].contains(status) }
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            PageTitle(title: model.state?.result?.title ?? "操作未完成",
                      subtitle: uncertain ? "先核实当前状态，再决定下一步。" : "处理下面的问题后，可以重新连接并检查。")
            Panel {
                HStack(alignment: .top, spacing: 16) {
                    Image(systemName: status == "connection-released" ? "checkmark.circle.fill" : uncertain ? "questionmark.circle.fill" : "exclamationmark.circle.fill")
                        .font(.system(size: 31)).foregroundColor(UI.amber)
                    VStack(alignment: .leading, spacing: 10) {
                        Text(model.state?.result?.detail ?? "当前状态无法确认，请重新检查。")
                            .font(.system(size: 14, weight: .medium)).fixedSize(horizontal: false, vertical: true)
                        Text(nextStep).font(.system(size: 12)).foregroundColor(UI.muted).lineSpacing(4)
                            .fixedSize(horizontal: false, vertical: true)
                        if !physical {
                            Text("这是演示场景，真实相机没有被访问或修改。")
                                .font(.system(size: 11)).foregroundColor(UI.accent)
                        }
                    }
                }.padding(.bottom, 18)
                Divider()
                DisclosureGroup("查看诊断详情", isExpanded: $details) {
                    VStack(alignment: .leading, spacing: 9) {
                        if let error = model.state?.result?.error, !error.isEmpty {
                            Text(error).textSelection(.enabled)
                        }
                        Text("状态：" + status)
                        Text(physical ? "功能写入器：" + (model.state?.write_attempted == true ? "已发送或尝试发送" : "未发送") : "真实设备访问：无")
                        ForEach(Array(model.progress.enumerated()), id: \.offset) { _, line in Text(line) }
                    }.font(.system(size: 11)).foregroundColor(UI.muted)
                        .fixedSize(horizontal: false, vertical: true).padding(.top, 12)
                }.font(.system(size: 11)).foregroundColor(UI.muted).padding(.top, 15)
            }
            HStack {
                Button("重新准备并检查") { model.send("restart_preparation") }.buttonStyle(PrimaryStyle())
                if physical, let takeover = model.state?.result?.takeover, takeover.available {
                    Button("强制接管…") { confirmingTakeover = true }
                        .buttonStyle(.bordered).tint(UI.amber)
                        .alert("强制接管相机连接？", isPresented: $confirmingTakeover) {
                            Button("取消", role: .cancel) {}
                            Button("确认无任务，强制接管", role: .destructive) {
                                model.send("takeover", extra: ["confirmation": takeover.confirmation, "confirmed": true])
                            }
                        } message: {
                            Text("将结束 \(takeover.owner)（PID \(takeover.pid)）。请先检查其他相机软件，确认没有正在拍摄、传输或写入固件。若仍有任务，强制接管可能中断任务或导致相机异常。接管后需要重新连接检查。")
                        }
                }
                if !physical { Text("也可在右上角“演示设置”切换场景。").font(.system(size: 11)).foregroundColor(UI.muted) }
                Spacer()
            }
        }
    }
    private var nextStep: String {
        if !physical {
            switch status {
            case "uncertain", "verification-failed", "recovery-required":
                return "保留本次演示结果，重新准备并检查以核实演示状态。"
            case "blocked":
                return "当前演示内容不兼容。可在演示设置中切换场景，再重新准备。"
            case "cancelled":
                return "本次演示已结束。重新准备后可以再次检查。"
            case "rolled-back":
                return "演示状态已回退。重新准备并检查后，可以继续体验。"
            default:
                return "在右上角“演示设置”重新连接演示设备，再返回准备页检查。"
            }
        }
        switch status {
        case "connection-busy": return "可先退出其他相机软件后重连。确认没有进行中的任务时，也可使用强制接管。"
        case "connection-released": return "点击“重新准备并检查”，重新确认连接后读取当前相机状态。"
        case "blocked": return "请核对机型、固件和已有修改。不要跳过兼容检查或重复尝试写入。"
        case "uncertain", "verification-failed", "recovery-required": return "请保留本次记录，检查相机状态，必要时拔线并重启相机。"
        case "cancelled": return "本次操作已结束。重新确认相机准备状态后，可以再次检查。"
        case "rolled-back": return "已确认回退结果。重新连接并检查后，再决定是否继续。"
        default: return "确认相机为 MSC 模式、保持唤醒；如有 FTP 菜单，请先关闭传输。"
        }
    }
}
