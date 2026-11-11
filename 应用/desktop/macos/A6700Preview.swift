import SwiftUI
import AppKit

struct PreviewView: View {
    @StateObject var model = PreviewModel()
    @State private var physicalMode = true
    @State private var license = false
    @State private var noticeSheet = false
    @State private var noticeLicense = false
    @State private var demoSettings = false
    private var phase: String { model.serviceExited ? "service-stopped" : (model.state?.phase ?? "notice") }
    private var busy: Bool { model.isBusy }
    private var hasAftercare: Bool {
        phase == "result" && model.state?.result?.success == true && model.state?.result?.aftercare != nil
    }
    private var stage: Int {
        if phase == "notice" { return 0 }
        if phase == "preparation" || (["running", "result"].contains(phase) && model.state?.action == "inspect") { return 1 }
        return 2
    }
    private var canPrepare: Bool {
        model.state?.checked == true && model.state?.environment.ready == true && model.state?.notice_acknowledged == true
    }
    var body: some View {
        VStack(spacing: 0) {
            appHeader
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
        .background(OperationWindowGuard(model: model))
        .onAppear { AppDelegate.operationModel = model; model.start(physical: physicalMode) }
        .onChange(of: physicalMode) { model.setMode($0) }
        .onChange(of: model.state?.revision) { _ in
            // Hidden developer backup/restore commands retain their navigation.
            // Unlock/close keep their dedicated result until explicit reconnect.
            if model.state?.phase == "result", model.state?.compatible == true,
               model.state?.result?.success == true,
               model.state?.result?.aftercare == nil { model.send("home") }
        }
        .onDisappear { model.shutdown() }
        .sheet(isPresented: $license) { LicenseView { license = false } }
        .sheet(isPresented: $noticeSheet) {
            VStack(alignment: .leading, spacing: 18) {
                ScrollView { OperationNoticeView(state: model.state, onRecheck: {}, onLicense: { noticeLicense = true }, reviewing: true) }
                HStack { Spacer(); Button("返回当前操作") { noticeSheet = false }.buttonStyle(PrimaryStyle()) }
            }.padding(24).frame(width: 780, height: 620)
                .sheet(isPresented: $noticeLicense) { LicenseView { noticeLicense = false } }
        }
    }
    @ViewBuilder private var content: some View {
        switch phase {
        case "service-stopped": OperationServiceStoppedView(model: model, physical: physicalMode)
        case "preparation":
            PageTitle(title: "准备相机", subtitle: "按下面四项完成设置，最后连接数据线。已经准备好的话，确认后即可检查。")
            PreparationGuideView(steps: model.state?.steps ?? [])
        case "home": WorkspaceView(model: model, physical: physicalMode)
        case "running": OperationProgressView(model: model, physical: physicalMode)
        case "result":
            if hasAftercare { OperationSuccessView(model: model, physical: physicalMode) }
            else { OperationFailureView(model: model, physical: physicalMode) }
        default: OperationNoticeView(state: model.state, onRecheck: { model.send("environment") }, onLicense: { license = true })
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
                .popover(isPresented: $demoSettings, arrowEdge: .bottom) { demoPanel }
            }
            Label(physicalMode ? (model.state?.compatible == true ? "相机已检查" : "相机模式") : "演示模式 · 不连接相机",
                  systemImage: physicalMode ? "cable.connector" : "play.circle")
                .font(.system(size: 11, weight: .medium)).foregroundColor(UI.accent)
            Picker("设备模式", selection: $physicalMode) {
                Text("演示").tag(false); Text("相机").tag(true)
            }.labelsHidden().pickerStyle(.segmented).frame(width: 122).disabled(busy)
                .accessibilityLabel("设备模式")
            Menu {
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
        }.padding(.horizontal, 28).padding(.vertical, 16).background(.white)
    }
    private var stepper: some View {
        HStack(spacing: 20) {
            stageLabel(0, "操作前须知")
            Image(systemName: "chevron.right").font(.system(size: 9)).foregroundColor(UI.muted.opacity(0.5))
            stageLabel(1, "准备并连接")
            Image(systemName: "chevron.right").font(.system(size: 9)).foregroundColor(UI.muted.opacity(0.5))
            stageLabel(2, "功能管理")
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
                if phase == "notice" {
                    Spacer()
                    Toggle(isOn: Binding(get: { model.state?.notice_acknowledged == true },
                                         set: { model.send("acknowledge", extra: ["value": $0]) })) {
                        Text("我已阅读并了解适用范围与操作风险").font(.system(size: 12))
                    }.toggleStyle(.checkbox).disabled(model.state == nil)
                        .accessibilityLabel("阅读确认")
                    Button("开始准备相机") { model.send("prepare") }.buttonStyle(PrimaryStyle()).disabled(!canPrepare)
                } else {
                    Button("返回") { model.send("cancel") }.buttonStyle(.plain).foregroundColor(UI.muted)
                    Spacer()
                    Toggle(isOn: Binding(get: { model.state?.ready == true },
                                         set: { model.send("confirm_preparation", extra: ["value": $0]) })) {
                        Text("以上设置已完成，相机已连接并保持开机").font(.system(size: 12))
                    }.toggleStyle(.checkbox).accessibilityLabel("相机准备确认")
                    Button(physicalMode ? "连接并检查相机" : "检查演示设备") { model.send("inspect") }
                        .buttonStyle(PrimaryStyle()).disabled(model.state?.ready != true || busy)
                }
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
    private var demoPanel: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("演示设置").font(.headline)
            Text("只改变演示数据，不访问 USB。").font(.system(size: 11)).foregroundColor(UI.muted)
            Picker("场景", selection: Binding(get: { model.state?.scenario ?? "enabled" }, set: {
                demoSettings = false; model.send("reset", extra: ["scenario": $0])
            })) { ForEach(model.state?.scenarios ?? []) { Text($0.name).tag($0.id) } }
            Button(model.state?.connected == true ? "模拟断开连接" : "重新连接演示设备") {
                demoSettings = false; model.send(model.state?.connected == true ? "disconnect" : "reconnect")
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
@main
struct A6700PreviewApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        Window(AppBrand.name, id: "main") { PreviewView() }
            .windowStyle(.titleBar).defaultSize(width: 1060, height: 770)
    }
}
