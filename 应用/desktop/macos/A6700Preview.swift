import SwiftUI
import AppKit

struct PreviewView: View {
    @StateObject var model = PreviewModel()
    @State private var physicalMode = true
    @State private var preparationAcknowledged = false
    @State private var license = false
    @State private var noticeSheet = false
    @State private var noticeLicense = false
    @State private var demoSettings = false
    @State private var usageSheet = false
    @State private var uploadToast: String?
    private var phase: String { model.serviceExited ? "service-stopped" : (model.state?.phase ?? "notice") }
    private var busy: Bool { model.isBusy || model.appUpdateBlocksOperations }
    private var hasAftercare: Bool {
        phase == "result" && model.state?.result?.success == true && model.state?.result?.aftercare != nil
    }
    private var stage: Int {
        if ["notice", "preparation"].contains(phase) || (["running", "result"].contains(phase) && model.state?.action == "inspect") { return 0 }
        return 1
    }
    var body: some View {
        VStack(spacing: 0) {
            appHeader
            if model.updateAvailable {
                HStack(spacing: 10) {
                    Image(systemName: "arrow.down.circle.fill").foregroundColor(.blue)
                    Text("相机功能包已更新")
                        .font(.system(size: 11)).foregroundColor(.blue)
                    Spacer()
                    Button("知道了") { model.updateAvailable = false }
                        .buttonStyle(.plain).font(.system(size: 11, weight: .medium)).foregroundColor(.blue)
                }.padding(.horizontal, 28).padding(.vertical, 6)
                    .background(Color.blue.opacity(0.06))
            }
            stepper
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if !model.error.isEmpty { errorBanner }
                    content
                }.padding(.horizontal, 28).padding(.top, 20).padding(.bottom, 20)
            }.id(phase) // Every new page starts at its title, including repeated results.
            if phase == "notice" || phase == "preparation" { navigationFooter }
            else if hasAftercare { resultFooter }
            else if phase != "service-stopped" { sessionFooter }
        }
        .frame(minWidth: 980, minHeight: 720)
        .foregroundColor(UI.ink).background(UI.background).preferredColorScheme(.light)
        .overlay(alignment: .topTrailing) {
            if let receipt = uploadToast {
                HStack(spacing: 10) {
                    Image(systemName: "checkmark.circle.fill").font(.system(size: 19)).foregroundColor(UI.accent)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("异常日志已上传").font(.system(size: 13, weight: .semibold))
                        if !receipt.isEmpty {
                            Text("反馈编号 \(receipt)").font(.system(size: 10)).foregroundColor(UI.muted)
                        }
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 10)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(UI.line))
                .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
                .padding(.trailing, 24).padding(.top, 70)
                .allowsHitTesting(false)
                .task(id: receipt) {
                    do { try await Task.sleep(for: .seconds(5)) }
                    catch { return }
                    uploadToast = nil
                }
            }
        }
        .alert("应用更新", isPresented: Binding(
            get: { model.appUpdateMessage != nil },
            set: { if !$0 { model.appUpdateMessage = nil } }
        )) {
            Button("知道了", role: .cancel) { model.appUpdateMessage = nil }
        } message: {
            Text(model.appUpdateMessage ?? "")
        }
        .background(OperationWindowGuard(model: model))
        .onAppear { AppDelegate.operationModel = model; model.start(physical: physicalMode) }
        .onChange(of: physicalMode) { preparationAcknowledged = false; model.setMode($0) }
        .onChange(of: phase) { value in
            if value == "notice" { preparationAcknowledged = false }
            if value != "result" { uploadToast = nil }
        }
        .onChange(of: model.state?.revision) { _ in
            // Hidden developer backup/restore commands retain their navigation.
            // Unlock/close keep their dedicated result until explicit reconnect.
            if model.state?.phase == "result", model.state?.compatible == true,
               model.state?.result?.success == true,
               model.state?.result?.aftercare == nil { model.send("home") }
        }
        .onDisappear { model.shutdown() }
        .sheet(isPresented: $model.showFailureFeedback, onDismiss: {
            uploadToast = model.failureUploadReceipt
            model.failureUploadReceipt = nil
        }) { FailureFeedbackView(model: model) }
        .onChange(of: model.showFailureFeedback) { showing in
            if showing { uploadToast = nil }
        }
        .sheet(isPresented: $license) { LicenseView { license = false } }
        .sheet(isPresented: $usageSheet) {
            VStack(alignment: .leading, spacing: 14) {
                PageTitle(title: "统计与诊断", subtitle: "可随时修改")
                Text("用于统计页面访问、活跃安装实例、写入成功和报错。每个安装生成随机标识，帮助去重。统计内容不包含序列号、邮箱、相机数据或日志。")
                    .font(.system(size: 12)).foregroundColor(UI.muted)
                Toggle("分享使用统计", isOn: Binding(
                    get: { model.state?.usage?.enabled == true },
                    set: { model.send("set_usage", extra: ["enabled": $0]) }
                )).toggleStyle(.checkbox)
                    .disabled(model.state?.usage == nil || model.isBusy)
                Divider()
                Toggle("自动上传脱敏故障报告", isOn: Binding(
                    get: { model.state?.automatic_failure_feedback == true },
                    set: { model.send("set_automatic_failure_feedback", extra: ["enabled": $0]) }
                )).toggleStyle(.checkbox)
                    .disabled(model.state?.automatic_failure_feedback == nil || model.isBusy)
                Text("开启后，失败时在后台发送本次的脱敏预检和操作结果。自动报告不含邮箱，保留 30 天。")
                    .font(.system(size: 12)).foregroundColor(UI.muted)
                HStack { Spacer(); Button("完成") { usageSheet = false }.buttonStyle(PrimaryStyle()) }
            }.padding(24).frame(width: 460)
        }
        .sheet(isPresented: $model.showDiagnostics) { DiagnosticsView(model: model, physical: physicalMode) }
        .sheet(isPresented: $noticeSheet) {
            VStack(alignment: .leading, spacing: 18) {
                ScrollView {
                    OperationNoticeView(state: model.state, onLicense: { noticeLicense = true })
                }
                HStack { Spacer(); Button("返回当前操作") { noticeSheet = false }.buttonStyle(PrimaryStyle()) }
            }.padding(24).frame(width: 780, height: 620)
                .sheet(isPresented: $noticeLicense) { LicenseView { noticeLicense = false } }
        }
    }
    @ViewBuilder private var content: some View {
        switch phase {
        case "service-stopped": OperationServiceStoppedView(model: model, physical: physicalMode)
        case "notice", "preparation":
            PageTitle(title: "准备并连接相机", subtitle: model.state?.notice.sections.first(where: { $0.id == "scope" })?.title ?? "仅适配 A6700 · 固件 2.00")
            if model.state?.checked == true && model.state?.environment.ready != true {
                StartupCheckView(state: model.state) { model.send("environment") }
            }
            PreparationGuideView(steps: model.state?.steps ?? [])
            if physicalMode {
                Text("连接时 macOS 可能要求管理员授权。相机的 MSC 提示消失后，请保持 USB 连接。")
                    .font(.system(size: 11)).foregroundColor(UI.muted)
            }
        case "home": WorkspaceView(model: model, physical: physicalMode)
        case "running": OperationProgressView(model: model, physical: physicalMode)
        case "result":
            if hasAftercare { OperationSuccessView(model: model, physical: physicalMode) }
            else { OperationFailureView(model: model, physical: physicalMode) }
        default: OperationNoticeView(state: model.state, onLicense: { license = true })
        }
    }
    private var appHeader: some View {
        HStack(spacing: 12) {
            if let icon = AppBrand.icon {
                Image(nsImage: icon).resizable().interpolation(.high).frame(width: 40, height: 40)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(AppBrand.name).font(.system(size: 14, weight: .bold))
                Text("固件 2.00 · 开发测试版").font(.system(size: 10)).foregroundColor(UI.muted)
            }
            Spacer()
            if !physicalMode {
                Button { demoSettings.toggle() } label: {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 13))
                        .frame(width: 28, height: 28)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).help("演示场景与连接设置").disabled(busy)
                .accessibilityLabel("演示设置")
                .popover(isPresented: $demoSettings, arrowEdge: .bottom) {
                    DemoSettingsPanel(model: model) { demoSettings = false }
                }
            }
            if model.showAppReleaseOffer, let version = model.appReleaseVersion {
                if let progress = model.appDownloadProgress {
                    Text("正在下载新版 · \(progress)%").font(.system(size: 11)).foregroundColor(UI.accent)
                } else if model.appInstallBusy {
                    Text("正在验证并准备安装…").font(.system(size: 11)).foregroundColor(UI.accent)
                } else {
                    let downloaded = model.appUpdateArchive != nil
                    Button(action: downloaded ? model.revealAppUpdate : model.downloadAppUpdate) {
                        Label(downloaded ? "查看已下载的新版" : model.appInstallAvailable ? "更新并重启" : "下载新版 \(version)", systemImage: "arrow.down.circle.fill")
                            .font(.system(size: 11, weight: .semibold))
                            .padding(.horizontal, 9).padding(.vertical, 5)
                            .background(UI.accent.opacity(0.10), in: Capsule())
                    }.buttonStyle(.plain).foregroundColor(UI.accent)
                        .disabled(!downloaded && (busy || !physicalMode || model.appUpdateChecking))
                    Button { model.showAppReleaseOffer = false } label: {
                        Image(systemName: "xmark").font(.system(size: 9, weight: .medium))
                    }.buttonStyle(.plain).foregroundColor(UI.muted).accessibilityLabel("稍后再更新")
                }
            }
            Button { model.showDiagnostics = true } label: {
                Label("意见反馈", systemImage: "bubble.left.and.bubble.right")
            }.buttonStyle(.plain).font(.system(size: 11, weight: .medium)).foregroundColor(UI.accent)
                .help("填写问题和图片，按需采集并上传相机日志")
            Label(physicalMode ? (model.state?.compatible == true ? "相机已检查" : "相机模式") : "演示模式 · 不连接相机",
                  systemImage: physicalMode ? "cable.connector" : "play.circle")
                .font(.system(size: 11, weight: .medium)).foregroundColor(UI.accent)
            Picker("设备模式", selection: $physicalMode) {
                Text("演示").tag(false); Text("相机").tag(true)
            }.labelsHidden().pickerStyle(.segmented).frame(width: 122).disabled(busy)
                .accessibilityLabel("设备模式")
            Menu {
                if physicalMode {
                    Button(model.appUpdateChecking ? "正在检查应用更新…" : "检查应用更新") { model.checkAppUpdate() }
                        .disabled(busy || model.appUpdateChecking || model.appDownloadProgress != nil)
                    Toggle("启动时检查应用更新", isOn: $model.checkAppAtStartup)
                    if model.appUpdateArchive != nil {
                        Button("查看已下载的新版") { model.revealAppUpdate() }
                    } else if let version = model.appReleaseVersion, model.appDownloadProgress == nil {
                        Button(model.appInstallAvailable ? "更新并重启" : "下载新版 \(version)") { model.downloadAppUpdate() }
                            .disabled(busy || model.appUpdateChecking)
                    }
                    Divider()
                    Button(model.updateBusy ? "正在检查相机功能包…" : "检查相机功能包") { model.send("check_updates") }
                        .disabled(model.isBusy)
                    if let updateStatus = model.updateStatus {
                        Text(updateStatus).font(.system(size: 10)).foregroundColor(UI.muted)
                    }
                    Divider()
                }
                if physicalMode { Button("统计与诊断…") { usageSheet = true } }
                Button("查看操作前须知") { noticeSheet = true }
                Button("开源许可") { license = true }
                Divider()
                Text(Bundle.main.object(forInfoDictionaryKey: "A6700DevelopmentVersion") as? String ?? "开发测试版")
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.system(size: 15))
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }.menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .accessibilityLabel("帮助与版本")
                .overlay(alignment: .topTrailing) {
                    if model.updateAvailable {
                        Circle().fill(Color.red).frame(width: 8, height: 8)
                            .offset(x: 4, y: -4)
                            .help("相机功能包已更新")
                    }
                }
        }.padding(.horizontal, 28).padding(.vertical, 16).background(.white)
    }
    private var stepper: some View {
        HStack(spacing: 20) {
            stageLabel(0, "准备并连接")
            Image(systemName: "chevron.right").font(.system(size: 9)).foregroundColor(UI.muted.opacity(0.5))
            stageLabel(1, "功能管理")
            Spacer()
            if stage > 0 {
                Button("查看须知") { noticeSheet = true }.buttonStyle(.plain)
                    .font(.system(size: 11)).foregroundColor(UI.muted)
            }
        }.padding(.horizontal, 28).padding(.vertical, 13).background(.white)
    }
    private func stageLabel(_ index: Int, _ title: String) -> some View {
        HStack(spacing: 7) {
            Image(systemName: stage > index ? "checkmark.circle.fill" : "\(index + 1).circle\(stage == index ? ".fill" : "")")
                .font(.system(size: 16))
            Text(title).font(.system(size: 12, weight: stage == index ? .semibold : .regular))
        }.foregroundColor(stage >= index ? UI.accent : UI.muted.opacity(0.65))
    }
    private var navigationFooter: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("非官方工具。修改固件有变砖风险，可能影响保修。")
                        .font(.system(size: 11)).foregroundColor(UI.muted)
                    HStack(spacing: 12) {
                        Button("查看完整须知") { noticeSheet = true }
                            .buttonStyle(.plain).foregroundColor(UI.accent)
                        if physicalMode, let usage = model.state?.usage {
                            Button(usage.enabled ? "使用统计已开启 · 设置" : "使用统计已关闭 · 设置") { usageSheet = true }
                                .buttonStyle(.plain).foregroundColor(UI.accent)
                        }
                        if model.state?.checked != true {
                            Text("正在检查本机组件…").foregroundColor(UI.muted)
                        }
                    }.font(.system(size: 11))
                }
                Spacer(minLength: 8)
                Toggle("我已阅读风险说明，并完成以上相机设置", isOn: $preparationAcknowledged)
                    .toggleStyle(.checkbox).font(.system(size: 12)).accessibilityLabel("阅读与相机准备确认")
                Button(physicalMode ? "连接并检查相机" : "检查演示设备") {
                    model.send("prepare_and_inspect", extra: ["acknowledged": true, "prepared": true])
                }.buttonStyle(PrimaryStyle())
                    .disabled(!preparationAcknowledged || model.state?.environment.ready != true || busy)
            }.padding(.horizontal, 28).padding(.vertical, 17)
        }.background(.white)
    }
    private var sessionFooter: some View {
        VStack(spacing: 0) {
            Divider()
            HStack {
                Label(physicalMode ? (model.state?.compatible == true
                      ? "相机检查已通过 · MSC 提示消失不等于断线，请保持 USB 连接"
                      : "请保持相机开机和 USB 连接，等待应用确认结果") : "合成数据演示 · 所有操作均不访问相机",
                      systemImage: physicalMode ? "cable.connector" : "shield")
                    .font(.system(size: 10)).foregroundColor(UI.muted)
                Spacer()
                if phase == "home" {
                    Button("重新连接") { model.send("restart_preparation") }
                        .buttonStyle(.plain).font(.system(size: 11)).foregroundColor(UI.accent).disabled(busy)
                }
            }.padding(.horizontal, 28).padding(.vertical, 12)
        }.background(.white)
    }
    private var resultFooter: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("还想管理其他功能？").font(.system(size: 12, weight: .semibold))
                    Text(physicalMode ? "完成机身检查后，重新连接 MSC；应用会重新检查当前状态。" : "返回准备页，重新检查演示设备后可继续体验。")
                        .font(.system(size: 11)).foregroundColor(UI.muted)
                }
                Spacer()
                Button("继续操作，重新连接") { model.send("restart_preparation") }
                    .buttonStyle(PrimaryStyle()).disabled(busy)
            }.padding(.horizontal, 28).padding(.vertical, 16)
        }.background(.white)
    }
    private var errorBanner: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text(model.error).fixedSize(horizontal: false, vertical: true)
        }.font(.system(size: 12)).foregroundColor(.red)
            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.red.opacity(0.05)).cornerRadius(9)
    }
}

struct DemoSettingsPanel: View {
    @ObservedObject var model: PreviewModel
    let close: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("演示设置").font(.headline)
            Text("只改变演示数据，不访问 USB。").font(.system(size: 11)).foregroundColor(UI.muted)
            Picker("场景", selection: Binding(get: { model.state?.scenario ?? "enabled" }, set: {
                close(); model.send("reset", extra: ["scenario": $0])
            })) { ForEach(model.state?.scenarios ?? []) { Text($0.name).tag($0.id) } }
            Button(model.state?.connected == true ? "模拟断开连接" : "重新连接演示设备") {
                close(); model.send(model.state?.connected == true ? "disconnect" : "reconnect")
            }.buttonStyle(PrimaryStyle(secondary: true))
        }.padding(22).frame(width: 310)
    }
}

struct LicenseView: View {
    let close: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("开源许可").font(.title2.bold())
            Text("GPL-3.0-only · 无担保").font(.system(size: 12)).foregroundColor(UI.muted)
            ScrollView {
                Text((try? String(contentsOf: Bundle.main.resourceURL!.appendingPathComponent("LICENSE"), encoding: .utf8)) ?? "请查看源码包 LICENSE。")
                    .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
            }
            HStack { Spacer(); Button("关闭", action: close).buttonStyle(PrimaryStyle()) }
        }.padding(24).frame(width: 720, height: 560)
    }
}
final class AppDelegate: NSObject, NSApplicationDelegate {
    static weak var operationModel: PreviewModel?
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationDidFinishLaunching(_ notification: Notification) { NSApp.activate(ignoringOtherApps: true) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard Self.operationModel?.allowWindowExit() != false else { return .terminateCancel }
        Self.operationModel?.shutdown(); return .terminateNow
    }
}
#if !UI_RENDER
@main
struct A6700PreviewApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        Window(AppBrand.name, id: "main") { PreviewView() }
            .windowStyle(.titleBar).defaultSize(width: 1060, height: 770)
    }
}
#endif
