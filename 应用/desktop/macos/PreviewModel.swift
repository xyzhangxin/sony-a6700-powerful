import SwiftUI
import AppKit
import Foundation
import AVFoundation

struct CheckRow: Decodable, Identifiable {
    let id: String
    let name: String
    let ok: Bool
    let detail: String
    let remediation: String
}
struct EnvironmentState: Decodable { let ready: Bool; let checks: [CheckRow] }
struct NoticeSection: Decodable, Identifiable {
    let id: String; let title: String; let icon: String; let level: String
    let body: String; let source_url: String
}
struct NoticeContent: Decodable {
    let title: String; let introduction: String; let acknowledgement: String
    let sections: [NoticeSection]
}
struct MenuGuideRow: Decodable, Identifiable {
    let id: String; let title: String; let path: [String]; let target: String
    let choices: [String]; let source_url: String; let source_note: String
}
struct PreparationRow: Decodable, Identifiable {
    let id: String; let title: String; let instruction: String; let confirmed: Bool
    let confirmation_label: String; let skip_label: String; let menu_guides: [MenuGuideRow]
}
struct FeatureRow: Decodable, Identifiable {
    let id: String; let name: String; let state: String; let selected: Bool?
    let unlock_notice: String?
    let update_available: Bool?
    let close_notice: String?
    let requires: [String]?
    let available: Bool?; let unavailable_reason: String?
    var isAvailable: Bool { available != false }
}
struct BackupRow: Decodable, Identifiable {
    let id: String; let name: String; let created_at: String; let size: Int
    let sector_count: Int; let total_bytes: Int; let bundle_state: String
    let features: [FeatureRow]
}
struct ScenarioRow: Decodable, Identifiable { let id: String; let name: String }
struct InspectSummary: Decodable {
    let compatible: Bool; let read_sectors: Int; let read_bytes: Int
    let features: [FeatureRow]
}
struct OperationResult: Decodable {
    let status: String; let title: String; let detail: String; let success: Bool; let error: String
    let backup_path: String?; let backup_sha256: String?; let backup_id: String?
    let aftercare: Aftercare?; let cleanup_warning: String?
    let close_preparation: ClosePreparation?
    let takeover: ConnectionTakeover?
}
struct ClosePreparationCheck: Decodable, Identifiable {
    let id: String; let name: String; let status: String
    let target_label: String; let reason: String
}
struct ClosePreparation: Decodable {
    let status: String; let completed: [String]; let checks: [ClosePreparationCheck]
    let settings_write_attempted: Bool
}
struct ConnectionTakeover: Decodable {
    let available: Bool; let confirmation: String; let owner: String; let pid: Int; let detail: String
}
struct RestartStep: Decodable, Identifiable {
    let id: String; let title: String; let detail: String
}
struct FeatureCheck: Decodable, Identifiable {
    let id: String; let name: String; let path: String
    let expected: String; let note: String; let outcome: String
}
struct Aftercare: Decodable {
    let simulation: Bool; let restart_required: Bool
    let startup_notice: String?
    let steps: [RestartStep]; let features: [FeatureCheck]
}
struct UsageState: Decodable { let enabled: Bool; let queued: Int }

struct CustomSoundRow: Decodable {
    let id: String
    let name: String
    let state: String
    let requires: [String]?
    let available: Bool?
    let installable: Bool?
    let recoverable: Bool?
    let recovery_mode: String?
    let mode: String?
    let reason: String?
}

struct CameraDisconnectResult: Decodable {
    let cleanup_status: String?
    let cleanup_warning: String?
}

struct Snapshot: Decodable {
    let usage: UsageState?
    let automatic_failure_feedback: Bool?
    let operation_id: String?
    let revision: Int; let phase: String; let scenario: String; let scenarios: [ScenarioRow]
    let environment: EnvironmentState; let checked: Bool; let steps: [PreparationRow]
    let notice: NoticeContent; let notice_acknowledged: Bool
    let ready: Bool; let compatible: Bool; let connected: Bool; let busy: Bool
    let features: [FeatureRow]; let custom_sound: CustomSoundRow?; let inspect_summary: InspectSummary?
    let camera_disconnect: CameraDisconnectResult?
    let backups: [BackupRow]; let backup_dir: String
    let action: String; let result: OperationResult?
    let simulation: Bool; let device_access: Bool; let write_attempted: Bool
}

struct FeedbackImage: Identifiable {
    let id = UUID()
    let url: URL
    let name: String
    let bytes: Int64
}

struct GuidedDiagnosticState: Decodable {
    let status: String
    let operation_id: String?
    let error: String?
}
struct FeedbackUploadResult: Decodable {
    let status: String
    let feedback_id: String?
    let expires_at: String?
    let error: String?
    let error_code: String?
}

struct DiagnosticEvent: Decodable {
    let step_id: String
    let phase: String
    let status: String
    let source: String
    let elapsed_ms: Int
}
struct DiagnosticResult: Decodable {
    let status: String
    let path: String?
    let camera: Bool?
    let service_recovery_required: Bool?
    let error: String?
    let error_type: String?
    let record_count: Int?
    let operation_id: String?
    let mode: String?
    let events: [DiagnosticEvent]?
}

struct AudioSoundRow: Decodable, Identifiable {
    let sound_id: String
    let title: String
    let description: String
    let duration_ms: Int
    let sample_rate: Int
    let channels: Int
    let bit_depth: Int
    let bytes: Int
    let tags: [String]
    var id: String { sound_id }
}

struct AudioPackageRow: Decodable, Identifiable {
    let package_id: String
    let version: String
    let title: String
    let description: String
    let author: String
    let license: String
    let digest: String
    let sounds: [AudioSoundRow]
    var id: String { package_id }
}

struct AudioSlotRow: Decodable, Identifiable {
    let slot: Int
    let name: String
    let label: String
    let status: String
    var id: Int { slot }
}

struct AudioCatalogResult: Decodable {
    let status: String
    let sequence: Int?
    let packages: [AudioPackageRow]
    var slots: [AudioSlotRow]? = nil
}

struct AudioPreviewResult: Decodable {
    let status: String
    let path: String?
    let title: String?
    let duration_ms: Int?
    let error: String?
}

struct AudioPlanCandidate: Decodable {
    let title: String
    let description: String?
    let filename: String
    let bytes: Int
    let sha256: String
    let duration_ms: Int
    let sample_rate: Int
    let channels: Int
    let bit_depth: Int
    let peak_amplitude: Double
    let rms_amplitude: Double
}

struct AudioPlanSource: Decodable {
    let type: String
    let package_id: String?
    let package_version: String?
    let sound_id: String?
    let author: String?
    let license: String?
}

struct AudioPlanReason: Decodable, Identifiable {
    let id: String
    let message: String
}

struct AudioPlanRequirement: Decodable, Identifiable {
    let id: String
    let status: String
    let evidence: String
}

struct AudioPlanStep: Decodable, Identifiable {
    let id: String
    let title: String
    let detail: String
    let status: String
}

struct AudioPlanAcceptance: Decodable, Identifiable {
    let id: String
    let path: String
    let expected: String
}

struct AudioPlanEvidence: Decodable {
    let local_validation: String
    let camera_access: String
    let write_attempted: Bool
    let user_confirmation: String
}

struct AudioImportPlan: Decodable {
    let status: String
    let can_execute: Bool
    let digest: String
    let candidate: AudioPlanCandidate
    let source: AudioPlanSource
    let target: AudioPlanTarget
    let reasons: [AudioPlanReason]
    let requirements: [AudioPlanRequirement]
    let steps: [AudioPlanStep]
    let acceptance: [AudioPlanAcceptance]
    let evidence: AudioPlanEvidence
}

struct AudioPlanTarget: Decodable {
    let slot_count: Int
    let slot: Int
    let label: String
    let factory_fallback: String
}

struct AudioImportConfirmation: Decodable {
    let status: String
    let plan_digest: String?
    let user_confirmation: String?
    let camera_access: String?
    let write_attempted: Bool?
    let error: String?
}

struct AudioImportGateResult: Decodable {
    let status: String
    let plan_digest: String?
    let candidate_sha256: String?
    let target_slot: Int?
    let stages: [String: String]?
    let reasons: [AudioPlanReason]?
    let camera_access: String?
    let write_attempted: Bool?
    let error: String?
}

struct AudioImportExecuteResult: Decodable {
    let status: String
    let target_slot: Int?
    let generation: Int?
    let independent_readback: Bool?
    let restart_notice: String?
    let backup_cleanup_failed: Bool?
    let cleanup_warning: String?
    let error: String?
}

/// The native view owns presentation only. The Python service owns preparation,
/// state transitions, planning and the in-memory transaction backend.
final class PreviewModel: ObservableObject {
    private static let operationInProgressMessage = "操作尚未结束，请保留窗口和 USB 连接。如需停止，请点击取消并等待明确结果。"
    private static let failureEmailKey = "lastFailureFeedbackEmail"
    private static let feedbackPreferences = UserDefaults(suiteName: "org.a6700unlock.feedback") ?? .standard
    @Published var state: Snapshot?
    @Published var progress: [String] = []
    @Published var error = ""
    @Published private(set) var pendingAction: String?
    @Published private(set) var serviceExited = false
    @Published private(set) var serviceExitAction: String?
    @Published var showFailureFeedback = false
    @Published var failureFeedbackJSON = ""
    private var failureDetails: [String: Any]?
    @Published var failureOperationID = ""
    @Published var failureUploadBusy = false
    @Published var failureUploadStatus = ""
    @Published var failureUploadReceipt: String?
    @Published var failureDescription = ""
    @Published var failureEmail = ""
    @Published var showDiagnostics = false
    @Published var diagnosticBusy = false
    @Published var diagnosticProgress: [String] = []
    @Published var feedbackDescription = "选择 FL2／FL3 后相机重启。"
    @Published var feedbackImages: [FeedbackImage] = []
    @Published var feedbackImageError = ""
    @Published var diagnosticResult: DiagnosticResult?
    @Published var guidedState: GuidedDiagnosticState?
    @Published var uploadBusy = false
    @Published var feedbackUploadPending = false
    @Published var uploadResult: FeedbackUploadResult?
    @Published var updateBusy = false
    @Published var updateStatus: String?
    @Published var updateAvailable = false
    @Published private(set) var appUpdateChecking = false
    @Published private(set) var appReleaseVersion: String?
    @Published private(set) var appDownloadProgress: Int?
    @Published private(set) var appUpdateArchive: URL?
    @Published var showAppReleaseOffer = false
    @Published var appUpdateMessage: String?
    @Published private(set) var appInstallAvailable = false
    @Published private(set) var appInstallBusy = false
    @Published private(set) var updateStartupToken = AppUpdateHealth.launchToken
    var appUpdateBlocksOperations: Bool { appInstallBusy || updateStartupToken != nil }
    private var updateStartupReported = false
    private var installRequested = false
    private var installHelper: Process?
    private var installControl: FileHandle?
    private var installShutdownID: String?
    private var installShutdownAcknowledged = false
    private var installWorkerExited = false
    @Published var checkAppAtStartup = UserDefaults.standard.object(forKey: "checkAppAtStartup") as? Bool ?? true {
        didSet { UserDefaults.standard.set(checkAppAtStartup, forKey: "checkAppAtStartup") }
    }
    private var appUpdateRequestID: String?
    private var manualAppUpdate = false
    private var checkedAppAtStartup = false
    private var autoUpdateCheck = false
    // Development capability enabled for 0.4.3 custom shutter sound workflow; honors explicit "0" opt-out.
    let audioImportPreviewEnabled = ProcessInfo.processInfo.environment["A6700_AUDIO_IMPORT_PREVIEW"] != "0"
    @Published var slotsLoadedOperationID: String?
    @Published var audioBusy = false
    @Published var audioCatalog: AudioCatalogResult?
    @Published var audioSlotsError = ""
    @Published var audioPreviewBusy = false
    @Published var audioPreviewError = ""
    @Published var audioPlanBusy = false
    @Published var audioPlan: AudioImportPlan?
    @Published var audioPlanError = ""
    @Published var audioImportSlot = 1
    func audioSlotLabel(_ slot: Int) -> String {
        audioCatalog?.slots?.first(where: { $0.slot == slot })?.label
            ?? "\(slot) [未读取]"
    }
    @Published var audioConfirmationBusy = false
    @Published var audioConfirmation: AudioImportConfirmation?
    @Published var audioConfirmationError = ""
    @Published var audioGateBusy = false
    @Published var audioGate: AudioImportGateResult?
    @Published var audioGateError = ""
    @Published var audioExecuteBusy = false
    @Published var audioExecute: AudioImportExecuteResult?
    @Published var audioExecuteError = ""
    @Published var audioImportFlowActive = false
    @Published var audioImportFlowSlot: Int?
    @Published var audioImportFlowMessage = ""
    private var audioPlayer: AVAudioPlayer?
    var isCameraConnected: Bool { state?.phase == "home" && state?.compatible == true && state?.connected == true }
    var electronicSoundState: String { state?.features.first(where: { $0.id == "sound" })?.state ?? "unknown" }
    var customSoundState: String { state?.custom_sound?.state ?? "unknown" }
    var electronicSoundUnlocked: Bool { electronicSoundState == "enabled" }
    var customSoundUnlocked: Bool { customSoundState == "enabled" }
    var customSoundRecoverable: Bool {
        physicalMode && customSoundState == "error"
            && (state?.custom_sound?.installable == true)
            && (state?.custom_sound?.recoverable == true)
    }
    var canReadSlotsOnCamera: Bool { isCameraConnected && customSoundUnlocked }
    var canInstallAudioOnCamera: Bool { isCameraConnected && electronicSoundUnlocked && customSoundUnlocked }
    var cameraOperationActive: Bool { !serviceExited && (pendingAction != nil || state?.busy == true) }
    private var workerBusy: Bool { !serviceExited && (cameraOperationActive || diagnosticBusy || uploadBusy || updateBusy || audioBusy || audioPreviewBusy || audioPlanBusy || audioConfirmationBusy || audioGateBusy || audioExecuteBusy) }
    var isBusy: Bool { workerBusy || audioImportFlowActive }
    var serviceWriteUncertain: Bool { physicalMode && ["unlock", "close", "restore", "recover_custom_sound"].contains(serviceExitAction ?? "") }
    private var process: Process?
    private var input: FileHandle?
    private var outputHandle: FileHandle?
    private var diagnosticsHandle: FileHandle?
    private var buffer = Data()
    private let reader = DispatchQueue(label: "a6700.preview.json")
    private var physicalMode = true
    private var canCollectCamera: Bool { physicalMode && state?.compatible == true && state?.connected == true }
    private var generation = UUID()

    func start(physical: Bool = true) {
        do {
            physicalMode = physical
            if ProcessInfo.processInfo.arguments.contains(AppUpdateHealth.rollbackOption) {
                appUpdateMessage = "新版未通过启动检查，已恢复上一版。"
            }
            let session = UUID()
            generation = session
            reader.sync { buffer = Data() }
            state = nil
            progress = []
            error = ""
            pendingAction = nil
            appUpdateRequestID = nil
            appUpdateChecking = false
            appDownloadProgress = nil
            if appUpdateArchive == nil {
                appReleaseVersion = nil
                showAppReleaseOffer = false
            }
            serviceExited = false
            serviceExitAction = nil
            diagnosticBusy = false
            diagnosticProgress = []
            diagnosticResult = nil
            guidedState = nil
            feedbackImages = []
            feedbackImageError = ""
            uploadBusy = false
            feedbackUploadPending = false
            uploadResult = nil
            slotsLoadedOperationID = nil
            audioBusy = false
            audioCatalog = nil
            audioSlotsError = ""
            audioPreviewBusy = false
            audioPreviewError = ""
            audioPlanBusy = false
            audioPlanError = ""
            audioPlan = nil
            audioImportSlot = 1
            audioConfirmationBusy = false
            audioConfirmation = nil
            audioConfirmationError = ""
            audioGateBusy = false
            audioGate = nil
            audioGateError = ""
            audioExecuteBusy = false
            audioExecute = nil
            audioExecuteError = ""
            audioImportFlowActive = false
            audioImportFlowSlot = nil
            audioImportFlowMessage = ""
            audioGateError = ""
            audioExecuteBusy = false
            audioExecute = nil
            audioExecuteError = ""
            audioPlayer?.stop()
            audioPlayer = nil
            let worker = try (physical ? DesktopWorkerMode.camera : .simulation).makeProcess(
                postUpdateStart: updateStartupToken != nil)
            let source = Pipe(), output = Pipe(), diagnostics = Pipe()
            worker.standardInput = source; worker.standardOutput = output; worker.standardError = diagnostics
            input = source.fileHandleForWriting
            let outHandle = output.fileHandleForReading
            let diagHandle = diagnostics.fileHandleForReading
            outputHandle = outHandle
            diagnosticsHandle = diagHandle
            outHandle.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                guard !data.isEmpty else {
                    handle.readabilityHandler = nil
                    return
                }
                self?.reader.async { [weak self] in
                    guard self?.generation == session else { return }
                    self?.read(data, session: session)
                }
            }
            // Drain stderr so a failed dependency cannot fill a pipe and freeze
            // the interface. Raw diagnostics/paths are not shown in user pages.
            diagHandle.readabilityHandler = { handle in
                if handle.availableData.isEmpty {
                    handle.readabilityHandler = nil
                }
            }
            worker.terminationHandler = { [weak self] job in
                DispatchQueue.main.async {
                    guard let self = self, self.generation == session else { return }
                    if self.installShutdownID != nil {
                        self.installWorkerExited = job.terminationStatus == 0
                        if self.installWorkerExited { self.commitAppInstallWhenReady() }
                        else { self.abortAppInstall("本地服务未能正常退出，应用更新已停止。") }
                        return
                    }
                    self.serviceExitAction = self.pendingAction ?? (self.state?.busy == true ? self.state?.action : nil)
                    self.serviceExited = true
                    if self.appUpdateChecking && self.manualAppUpdate {
                        self.appUpdateMessage = "本地服务已停止，请重新打开应用后再检查。"
                    }
                    self.appUpdateChecking = false
                    if self.appDownloadProgress != nil {
                        self.appUpdateMessage = "下载已中断，请重新检查应用更新。"
                        self.appDownloadProgress = nil
                        self.appInstallBusy = false
                        self.installRequested = false
                    }
                    self.appUpdateRequestID = nil
                    self.failureUploadBusy = false
                    self.audioBusy = false
                    self.audioPreviewBusy = false
                    self.audioPlanBusy = false
                    self.audioConfirmationBusy = false
                    self.audioGateBusy = false
                    self.audioExecuteBusy = false
                    if self.audioImportFlowActive {
                        self.audioImportFlowActive = false
                        self.audioImportFlowMessage = ""
                        if self.audioExecuteError.isEmpty {
                            self.audioExecuteError = "本地设备服务已退出，请关闭弹窗后重试。"
                        }
                    }
                    if self.showFailureFeedback { self.failureUploadStatus = "本地服务已退出，上传结果未确认。可先导出报告。" }
                    self.pendingAction = nil
                    self.error = ""
                }
            }
            try worker.run()
            process = worker
            updateBusy = false
            updateStatus = nil
            updateAvailable = false
            if physical {
                send("environment")
                if updateStartupToken == nil && checkAppAtStartup && !checkedAppAtStartup {
                    checkedAppAtStartup = true
                    checkAppUpdate(automatic: true)
                }
                if updateStartupToken == nil {
                    autoUpdateCheck = true
                    send("check_updates")
                    send("start_usage")
                }
            } else {
                send("reset", extra: ["scenario": "enabled"])
            }
        } catch { self.error = error.localizedDescription }
    }
    func setMode(_ physical: Bool) {
        guard physical != physicalMode, !isBusy, !appUpdateBlocksOperations else { return }
        shutdown()
        start(physical: physical)
    }
    func restartService() {
        guard serviceExited else { return }
        shutdown()
        start(physical: physicalMode) // Environment only; never replay an operation.
    }
    private func bridgeError(_ text: String) -> NSError {
        NSError(domain: "A6700Preview", code: 1, userInfo: [NSLocalizedDescriptionKey: text])
    }
    private func read(_ data: Data, session: UUID) {
        buffer.append(data)
        while let end = buffer.firstIndex(of: 10) {
            let line = buffer.prefix(upTo: end)
            buffer.removeSubrange(...end)
            guard let message = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let kind = message["type"] as? String else { continue }
            if kind == "failure-feedback-preview", let result = message["result"] as? [String: Any],
               let payload = result["payload"], let encoded = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]) {
                DispatchQueue.main.async { [weak self] in
                    guard self?.generation == session else { return }
                    self?.failureFeedbackJSON = String(data: encoded, encoding: .utf8) ?? ""
                    self?.failureOperationID = result["operation_id"] as? String ?? ""
                    self?.failureDetails = result["details"] as? [String: Any]
                }
            } else if kind == "failure-feedback-result", let result = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.generation == session, self.showFailureFeedback else { return }
                    self.failureUploadBusy = false
                    if result["status"] as? String == "received" {
                        self.failureUploadReceipt = result["feedback_id"] as? String ?? ""
                        self.showFailureFeedback = false
                    } else if result["status"] as? String == "partial", let id = result["feedback_id"] as? String {
                        self.failureUploadStatus = "已收到反馈 \(id)。诊断附件待接收，可再次确认上传或导出报告。"
                    } else {
                        self.failureUploadStatus = result["error"] as? String ?? "上传未完成，可稍后使用相同报告重试。"
                    }
                }
            } else if kind == "state" {
                guard let raw = message["state"],
                      let encoded = try? JSONSerialization.data(withJSONObject: raw),
                      let snapshot = try? JSONDecoder().decode(Snapshot.self, from: encoded) else {
                    DispatchQueue.main.async { [weak self] in
                        guard self?.generation == session else { return }
                        self?.error = "本地服务状态格式不兼容，请重新获取同一版本的完整应用。"
                    }
                    continue
                }
                DispatchQueue.main.async { [weak self] in
                    guard let self = self else { return }
                    guard self.generation == session, !self.serviceExited else { return }
                    if let current = self.state, snapshot.revision < current.revision { return }
                    let valid = self.physicalMode
                        ? !snapshot.simulation
                        : snapshot.simulation && !snapshot.device_access && !snapshot.write_attempted
                    guard valid else {
                        self.error = self.physicalMode
                            ? "真实设备服务返回了模拟状态，已停止更新。"
                            : "预览收到不支持的设备模式，已停止更新。"
                        return
                    }
                    self.state = snapshot
                    self.reportUpdatedStartup(snapshot)
                    if let shutdownID = self.installShutdownID,
                       message["request_id"] as? String == shutdownID, !snapshot.connected, !snapshot.busy {
                        self.installShutdownAcknowledged = true
                        self.commitAppInstallWhenReady()
                    }
                    if (!snapshot.connected || (snapshot.phase != "home" && snapshot.phase != "running")) {
                        if self.slotsLoadedOperationID != nil || self.audioCatalog?.slots != nil {
                            self.slotsLoadedOperationID = nil
                            self.audioCatalog?.slots = nil
                            self.audioSlotsError = ""
                        }
                    }
                    if !snapshot.busy && self.error == Self.operationInProgressMessage {
                        self.error = ""
                    }
                    if !snapshot.busy || snapshot.action == self.pendingAction {
                        self.pendingAction = nil
                    }
                }
            } else if kind == "progress", let text = message["message"] as? String {
                DispatchQueue.main.async { [weak self] in
                    guard self?.generation == session else { return }
                    self?.progress.append(text)
                }
            } else if kind == "diagnostic-progress", let text = message["message"] as? String {
                DispatchQueue.main.async { [weak self] in
                    guard self?.generation == session else { return }
                    self?.diagnosticProgress.append(text)
                }
            } else if kind == "guided-state" {
                let raw = message
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.guidedState = try? JSONDecoder().decode(GuidedDiagnosticState.self, from: encoded)
                    }
                    let status = self.guidedState?.status ?? ""
                    // Recording is an idle, user-driven state, not a blocked
                    // bridge operation. Keep End/Retrieve available while the
                    // on-camera recorder is running.
                    self.diagnosticBusy = ["starting", "restarting", "collecting", "cancelling"].contains(status)
                    if !self.diagnosticBusy,
                       ["awaiting_retrieval", "timeout", "error", "cancelled"].contains(status),
                       self.feedbackUploadPending {
                        // A reconnect wait can end without a diagnostic-result.
                        // Do not leave a unified upload pending forever.
                        self.feedbackUploadPending = false
                        self.uploadBusy = false
                        self.uploadResult = FeedbackUploadResult(status: "error", feedback_id: nil,
                            expires_at: nil, error: "日志未完整取回，本次未上传；请保留现场并先完成取回。", error_code: nil)
                    }
                }
            } else if kind == "feedback-result", let raw = message["result"] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.uploadBusy = false
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.uploadResult = try? JSONDecoder().decode(FeedbackUploadResult.self, from: encoded)
                    }
                }
            } else if kind == "diagnostic-result", let raw = message["result"] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.diagnosticBusy = false
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.diagnosticResult = try? JSONDecoder().decode(DiagnosticResult.self, from: encoded)
                        if self.feedbackUploadPending {
                            self.feedbackUploadPending = false
                            if self.diagnosticResult?.status == "collected", self.diagnosticResult?.path != nil {
                                self.uploadDiagnostic(description: self.feedbackDescription)
                            } else {
                                self.uploadBusy = false
                                self.uploadResult = FeedbackUploadResult(status: "error", feedback_id: nil,
                                    expires_at: nil, error: "日志未完整取回，本次未上传；请保留现场并先完成取回。", error_code: nil)
                            }
                        }
                    } else {
                        self.diagnosticResult = DiagnosticResult(status: "error", path: nil, camera: nil,
                            service_recovery_required: nil, error: "诊断结果格式不兼容。", error_type: nil, record_count: nil,
                            operation_id: nil, mode: nil, events: nil)
                    }
                }
            } else if kind == "app-update-result", let raw = message["result"] as? [String: Any],
                      let requestID = message["request_id"] as? String {
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.generation == session, self.appUpdateRequestID == requestID else { return }
                    self.finishAppUpdate(raw)
                }
            } else if kind == "app-update-download-progress", let percent = message["percent"] as? Int,
                      let requestID = message["request_id"] as? String {
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.generation == session, self.appUpdateRequestID == requestID else { return }
                    self.appDownloadProgress = percent
                }
            } else if kind == "app-update-download-result", let raw = message["result"] as? [String: Any],
                      let requestID = message["request_id"] as? String {
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.generation == session, self.appUpdateRequestID == requestID else { return }
                    self.finishAppDownload(raw)
                }
            } else if kind == "update-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.updateBusy = false
                    let status = raw["status"] as? String ?? "error"
                    let sequence = raw["sequence"] as? Int
                    if status == "activated" {
                        self.updateAvailable = true
                    }
                    switch status {
                    case "activated":
                        self.updateStatus = "相机功能包已更新"
                    case "unchanged", "not-modified":
                        self.updateStatus = "相机功能包已是最新"
                    case "unconfigured":
                        self.updateStatus = "更新暂不可用"
                    case "offline":
                        self.updateStatus = "网络不可用"
                    case "unavailable", "artifact-unavailable":
                        self.updateStatus = "更新暂不可用"
                    case "rejected":
                        self.updateStatus = "更新失败"
                    default:
                        self.updateStatus = nil
                    }
                }
            } else if kind == "audio-slots-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.audioBusy = false
                    self.audioSlotsError = raw["error"] as? String ?? ""
                    if self.audioCatalog == nil {
                        self.audioCatalog = AudioCatalogResult(status: "unavailable", sequence: nil, packages: [])
                    }
                    self.audioCatalog?.slots = nil
                    if let rows = raw["slots"],
                       let data = try? JSONSerialization.data(withJSONObject: rows),
                       let decoded = try? JSONDecoder().decode([AudioSlotRow].self, from: data) {
                        self.audioCatalog?.slots = decoded
                        if self.audioSlotsError.isEmpty {
                            self.slotsLoadedOperationID = self.state?.operation_id ?? "connected"
                        }
                    }
                }
            } else if kind == "audio-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.audioBusy = false
                    let existingSlots = self.audioCatalog?.slots
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.audioCatalog = try? JSONDecoder().decode(AudioCatalogResult.self, from: encoded)
                    } else {
                        self.audioCatalog = AudioCatalogResult(status: "error", sequence: nil, packages: [])
                    }
                    if let existingSlots = existingSlots,
                       existingSlots.contains(where: { $0.status != "unknown" }) {
                        self.audioCatalog?.slots = existingSlots
                    }
                }
            } else if kind == "audio-preview-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.audioPreviewBusy = false
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw),
                       let preview = try? JSONDecoder().decode(AudioPreviewResult.self, from: encoded),
                       preview.status == "ready", let path = preview.path {
                        do {
                            self.audioPlayer = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
                            self.audioPlayer?.prepareToPlay()
                            self.audioPlayer?.play()
                            self.audioPreviewError = ""
                        } catch {
                            self.audioPreviewError = "本机试听播放失败：\(error.localizedDescription)"
                        }
                    } else if let encoded = try? JSONSerialization.data(withJSONObject: raw),
                              let preview = try? JSONDecoder().decode(AudioPreviewResult.self, from: encoded),
                              let detail = preview.error, !detail.isEmpty {
                        self.audioPreviewError = detail
                    } else {
                        self.audioPreviewError = "音频试听准备失败。"
                    }
                }
            } else if kind == "audio-import-plan-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.audioPlanBusy = false
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.audioPlan = try? JSONDecoder().decode(AudioImportPlan.self, from: encoded)
                    }
                    if self.audioPlan == nil {
                        if let detail = raw["error"] as? String, !detail.isEmpty {
                            self.audioPlanError = detail
                        } else {
                            self.audioPlanError = "音频导入计划格式不兼容。"
                        }
                    } else {
                        self.audioPlanError = ""
                    }
                    if self.audioImportFlowActive {
                        if let plan = self.audioPlan, plan.status == "ready" && plan.can_execute {
                            self.audioImportFlowMessage = "音频检查通过，正在确认导入槽位 \(plan.target.slot)…"
                            self.send("confirm_audio_import", extra: [
                                "plan_digest": plan.digest,
                                "confirmed": true,
                            ])
                        } else {
                            self.audioImportFlowActive = false
                            self.audioImportFlowMessage = ""
                            if self.audioExecuteError.isEmpty {
                                let fallback = self.audioPlan?.reasons.first?.message ?? "音频校验或导入计划生成未通过。"
                                self.audioExecuteError = self.audioPlanError.isEmpty ? fallback : self.audioPlanError
                            }
                        }
                    }
                }
            } else if kind == "audio-import-confirmation-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.audioConfirmationBusy = false
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.audioConfirmation = try? JSONDecoder().decode(AudioImportConfirmation.self, from: encoded)
                    }
                    if self.audioConfirmation == nil {
                        if let detail = raw["error"] as? String, !detail.isEmpty {
                            self.audioConfirmationError = detail
                        } else {
                            self.audioConfirmationError = "音频导入确认结果格式不兼容。"
                        }
                    } else {
                        self.audioConfirmationError = ""
                    }
                    if self.audioImportFlowActive {
                        if let confirmation = self.audioConfirmation,
                           confirmation.user_confirmation == "confirmed",
                           let digest = confirmation.plan_digest,
                           digest == self.audioPlan?.digest {
                            let targetSlot = self.audioPlan?.target.slot ?? (self.audioImportFlowSlot ?? self.audioImportSlot)
                            self.audioImportFlowMessage = "正在写入相机槽位 \(targetSlot) 并执行独立回读…"
                            self.send("execute_audio_import", extra: ["plan_digest": digest])
                        } else {
                            self.audioImportFlowActive = false
                            self.audioImportFlowMessage = ""
                            if self.audioExecuteError.isEmpty {
                                let fallback = self.audioConfirmation?.error ?? "未能确认导入计划。"
                                self.audioExecuteError = self.audioConfirmationError.isEmpty ? fallback : self.audioConfirmationError
                            }
                        }
                    }
                }
            } else if kind == "audio-import-gate-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.audioGateBusy = false
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.audioGate = try? JSONDecoder().decode(AudioImportGateResult.self, from: encoded)
                    }
                    if self.audioGate == nil {
                        if let detail = raw["error"] as? String, !detail.isEmpty {
                            self.audioGateError = detail
                        } else {
                            self.audioGateError = "音频导入执行门禁结果格式不兼容。"
                        }
                    } else {
                        self.audioGateError = ""
                    }
                }
            } else if kind == "audio-import-execute-result", let raw = message["result"] as? [String: Any] {
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.generation == session else { return }
                    self.audioExecuteBusy = false
                    self.audioImportFlowActive = false
                    self.audioImportFlowMessage = ""
                    if let encoded = try? JSONSerialization.data(withJSONObject: raw) {
                        self.audioExecute = try? JSONDecoder().decode(AudioImportExecuteResult.self, from: encoded)
                    }
                    if self.audioExecute == nil {
                        self.audioCatalog?.slots = nil
                        self.slotsLoadedOperationID = nil
                        if let detail = raw["error"] as? String, !detail.isEmpty {
                            self.audioExecuteError = detail
                        } else {
                            self.audioExecuteError = "音频导入执行结果格式不兼容。"
                        }
                    } else {
                        if let detail = self.audioExecute?.error, !detail.isEmpty,
                           self.audioExecute?.status != "completed" {
                            self.audioExecuteError = detail
                        } else {
                            self.audioExecuteError = ""
                        }
                        if self.audioExecute?.status == "completed",
                           self.audioExecute?.independent_readback == true,
                           let plan = self.audioPlan {
                            let targetSlot = plan.target.slot
                            let filename = plan.candidate.filename
                            var updated = self.audioCatalog?.slots ?? (1...10).map {
                                AudioSlotRow(slot: $0, name: "[未读取]", label: "\($0) [未读取]", status: "unknown")
                            }
                            if let idx = updated.firstIndex(where: { $0.slot == targetSlot }) {
                                updated[idx] = AudioSlotRow(
                                    slot: targetSlot,
                                    name: filename,
                                    label: "\(targetSlot) \(filename)",
                                    status: "imported"
                                )
                            }
                            if self.audioCatalog == nil {
                                self.audioCatalog = AudioCatalogResult(status: "unavailable", sequence: nil, packages: [])
                            }
                            self.audioCatalog?.slots = updated
                        } else {
                            self.audioCatalog?.slots = nil
                            self.slotsLoadedOperationID = nil
                        }
                    }
                }
            } else if kind == "error" {
                DispatchQueue.main.async { [weak self] in
                    guard self?.generation == session else { return }
                    let messageText = message["message"] as? String ?? "预览操作失败。"
                    self?.error = messageText
                    if self?.showFailureFeedback == true { self?.failureUploadStatus = message["message"] as? String ?? "日志操作未完成。" }
                    self?.pendingAction = nil
                    self?.failureUploadBusy = false
                    self?.diagnosticBusy = false
                    self?.uploadBusy = false
                    self?.updateBusy = false
                    self?.audioBusy = false
                    self?.audioPreviewBusy = false
                    self?.audioPlanBusy = false
                    self?.audioConfirmationBusy = false
                    self?.audioGateBusy = false
                    self?.audioExecuteBusy = false
                    if self?.audioImportFlowActive == true {
                        self?.audioImportFlowActive = false
                        self?.audioImportFlowMessage = ""
                        if self?.audioExecuteError.isEmpty == true {
                            self?.audioExecuteError = messageText
                        }
                    }
                }
            }
        }
    }
    func previewFailureFeedback() {
        guard !isBusy else { return }
        failureFeedbackJSON = ""
        failureDetails = nil
        failureOperationID = ""
        failureUploadStatus = ""
        failureUploadReceipt = nil
        failureDescription = ""
        failureEmail = Self.feedbackPreferences.string(forKey: Self.failureEmailKey) ?? ""
        showFailureFeedback = true
        send("preview_failure_feedback")
    }

    func uploadFailureFeedback() {
        guard !isBusy, !serviceExited, !failureUploadBusy, !failureOperationID.isEmpty else { return }
        let email = failureEmail.trimmingCharacters(in: .whitespacesAndNewlines)
        if !email.isEmpty && (email.unicodeScalars.count > 254 || email.range(of: #"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$"#, options: .regularExpression) == nil || email.rangeOfCharacter(from: .controlCharacters) != nil) {
            failureUploadStatus = "请输入有效的联系邮箱，或留空。"
            return
        }
        let description = failureDescription.trimmingCharacters(in: .whitespacesAndNewlines)
        guard description.unicodeScalars.count <= 1700 else {
            failureUploadStatus = "问题说明最多填写 1700 字。"
            return
        }
        failureUploadBusy = true
        failureUploadStatus = ""
        send("upload_failure_feedback", extra: ["operation_id": failureOperationID,
            "description": description, "email": email, "confirmed": true])
        if error.isEmpty {
            Self.feedbackPreferences.set(email, forKey: Self.failureEmailKey)
        } else {
            failureUploadBusy = false
            failureUploadStatus = error
        }
    }

    func exportFailureFeedback() {
        guard !failureUploadBusy, let data = failureFeedbackJSON.data(using: .utf8),
              var payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
        let description = failureDescription.trimmingCharacters(in: .whitespacesAndNewlines)
        if !description.isEmpty { payload["description"] = description }
        if let failureDetails { payload["diagnostic_details"] = failureDetails }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "Powerful6700-异常报告.json"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
            failureUploadStatus = "脱敏报告已导出。"
        } catch { failureUploadStatus = "报告未能保存，请选择可写入的位置。" }
    }

    private func requestAppUpdate(_ command: String) throws {
        guard let input, !serviceExited else { throw bridgeError("本地服务未就绪。") }
        let requestID = UUID().uuidString
        let request: [String: Any] = ["command": command, "api_version": 1, "request_id": requestID]
        appUpdateRequestID = requestID
        let bytes = try JSONSerialization.data(withJSONObject: request)
        try input.write(contentsOf: bytes + Data([10]))
    }

    func checkAppUpdate(automatic: Bool = false) {
        guard physicalMode, !isBusy, !appUpdateBlocksOperations, appUpdateRequestID == nil else { return }
        manualAppUpdate = !automatic
        appUpdateChecking = true
        do { try requestAppUpdate("check_app_update") }
        catch { finishAppUpdate(["status": "unavailable"]) }
    }

    func downloadAppUpdate() {
        guard physicalMode, !isBusy, !appUpdateBlocksOperations,
              appUpdateRequestID == nil, appReleaseVersion != nil else { return }
        installRequested = appInstallAvailable
        if installRequested { appInstallBusy = true }
        appDownloadProgress = 0
        showAppReleaseOffer = true
        do { try requestAppUpdate("download_app_update") }
        catch { finishAppDownload(["status": "unavailable"]) }
    }

    func revealAppUpdate() {
        guard let archive = appUpdateArchive, FileManager.default.fileExists(atPath: archive.path) else {
            appUpdateArchive = nil
            appUpdateMessage = "找不到已下载的安装包，请重新下载。"
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([archive])
    }

    private func finishAppDownload(_ result: [String: Any]) {
        appUpdateRequestID = nil
        appDownloadProgress = nil
        if result["status"] as? String == "downloaded", let path = result["path"] as? String {
            let archive = URL(fileURLWithPath: path)
            appUpdateArchive = archive
            if installRequested { prepareAppInstall(archive) }
            return
        }
        installRequested = false
        appInstallBusy = false
        if result["status"] as? String == "verification-failed" {
            appUpdateMessage = "安装包校验失败，请重新下载。"
        } else {
            appUpdateMessage = "下载未完成，请重新检查应用更新或稍后重试。"
        }
    }

    private func finishAppUpdate(_ result: [String: Any]) {
        appUpdateRequestID = nil
        appUpdateChecking = false
        let status = result["status"] as? String
        if status == "available", let version = result["version"] as? String {
            if appReleaseVersion != version { appUpdateArchive = nil }
            appReleaseVersion = version
            appInstallAvailable = result["installable"] as? Bool == true && appInstallerExecutable != nil
            showAppReleaseOffer = true
            return
        }
        if status == "up-to-date" {
            appReleaseVersion = nil
            appInstallAvailable = false
            appUpdateArchive = nil
            showAppReleaseOffer = false
        }
        guard manualAppUpdate else { return }
        switch status {
        case "up-to-date": appUpdateMessage = "当前已是最新应用版本。"
        case "offline": appUpdateMessage = "无法连接 GitHub，请检查网络后重试。"
        case "rate-limited": appUpdateMessage = "GitHub 暂时限制了查询，请稍后重试。"
        case "checking": appUpdateMessage = "正在检查应用更新，请稍候。"
        default: appUpdateMessage = "暂时无法检查应用更新，请稍后重试。"
        }
    }

    private var appInstallerExecutable: URL? {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("helpers/app-update-installer"),
              FileManager.default.isExecutableFile(atPath: url.path) else { return nil }
        return url
    }

    private func prepareAppInstall(_ archive: URL) {
        guard let executable = appInstallerExecutable, let version = appReleaseVersion,
              allowWindowExit() else {
            abortAppInstall("无法开始安装。你可以查看已经下载的安装包。")
            return
        }
        let helper = Process()
        let control = Pipe(), output = Pipe(), diagnostics = Pipe()
        helper.executableURL = executable
        helper.arguments = [String(ProcessInfo.processInfo.processIdentifier), archive.path,
                            archive.path + ".ed25519", version]
        helper.standardInput = control
        helper.standardOutput = output
        helper.standardError = diagnostics
        installHelper = helper
        installControl = control.fileHandleForWriting
        helper.terminationHandler = { [weak self] _ in
            let detail = String(data: diagnostics.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            DispatchQueue.main.async {
                guard let self, self.installHelper === helper else { return }
                self.abortAppInstall((detail?.isEmpty == false ? detail : nil) ?? "应用安装未完成。")
            }
        }
        do { try helper.run() }
        catch {
            abortAppInstall("无法启动安装助手。你可以查看已经下载的安装包。")
            return
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let response = output.fileHandleForReading.readDataToEndOfFile()
            guard response == Data("prepared\n".utf8) else { return }
            DispatchQueue.main.async {
                guard let self, self.installHelper === helper else { return }
                self.closeWorkerForAppInstall()
            }
        }
    }

    private func closeWorkerForAppInstall() {
        guard allowWindowExit(), let input, process?.isRunning == true else {
            abortAppInstall("请等相机会话结束后重新尝试安装。")
            return
        }
        let identifier = UUID().uuidString
        installShutdownID = identifier
        installShutdownAcknowledged = false
        installWorkerExited = false
        do {
            let bytes = try JSONSerialization.data(withJSONObject: ["command": "shutdown", "request_id": identifier])
            try input.write(contentsOf: bytes + Data([10]))
        } catch {
            abortAppInstall("无法正常结束本地服务，应用更新已停止。")
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { [weak self] in
            guard let self, self.installShutdownID == identifier else { return }
            self.abortAppInstall("相机会话未确认结束，应用更新已停止。")
        }
    }

    private func commitAppInstallWhenReady() {
        guard installShutdownAcknowledged, installWorkerExited, let control = installControl else { return }
        do {
            try control.write(contentsOf: Data("install\n".utf8))
            try control.close()
        } catch {
            abortAppInstall("无法交接安装助手，应用更新已停止。")
            return
        }
        installShutdownID = nil
        installControl = nil
        installHelper = nil
        appInstallBusy = false
        NSApp.terminate(nil)
    }

    private func abortAppInstall(_ message: String) {
        try? installControl?.close()
        installControl = nil
        installHelper = nil
        if installShutdownID != nil && process?.isRunning != true { serviceExited = true }
        installShutdownID = nil
        appInstallBusy = false
        installRequested = false
        appUpdateMessage = message
    }

    private func reportUpdatedStartup(_ snapshot: Snapshot) {
        guard let token = updateStartupToken, !updateStartupReported, snapshot.checked,
              snapshot.environment.ready, !snapshot.device_access, !snapshot.write_attempted else { return }
        do {
            try AppUpdateHealth.ready(token, pid: ProcessInfo.processInfo.processIdentifier)
            updateStartupReported = true
            waitForUpdateApproval(token, deadline: ProcessInfo.processInfo.systemUptime + 30)
        } catch { /* The helper retains the old application and handles rollback. */ }
    }

    private func waitForUpdateApproval(_ token: UUID, deadline: TimeInterval) {
        guard updateStartupToken == token, ProcessInfo.processInfo.systemUptime < deadline,
              let directory = try? AppUpdateHealth.directory(for: token) else { return }
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("approved").path) {
            updateStartupToken = nil
            if physicalMode { send("start_usage") }
            try? FileManager.default.removeItem(at: directory)
            appUpdateMessage = "应用已更新，启动检查通过。"
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
            self?.waitForUpdateApproval(token, deadline: deadline)
        }
    }

    func send(_ command: String, extra: [String: Any] = [:]) {
        if appUpdateBlocksOperations && !["environment", "shutdown"].contains(command) { return }
        guard !serviceExited else {
            error = "本地设备服务已停止，请先重新启动服务。"
            return
        }
        if command == "restart_preparation" && physicalMode && !isBusy {
            shutdown()
            start(physical: true)
            send("acknowledge", extra: ["value": true])
            send("prepare")
            return
        }
        if ["prepare_and_inspect", "inspect", "unlock", "close", "backup", "restore", "takeover", "recover_custom_sound"].contains(command) {
            guard !isBusy else { return }
            pendingAction = command == "prepare_and_inspect" ? "inspect" : command
        }
        if command == "disconnect_camera" {
            guard physicalMode, !isBusy else { return }
            pendingAction = command
        }
        if command == "check_updates" {
            if autoUpdateCheck {
                autoUpdateCheck = false
            } else {
                guard !isBusy else { return }
                updateBusy = true
                updateStatus = nil
                updateAvailable = false
            }
        }
        if command == "list_audio" || command == "refresh_audio_slots" {
            guard !isBusy else { return }
            audioBusy = true
            audioSlotsError = ""
            if command == "refresh_audio_slots" {
                audioCatalog?.slots = nil
                invalidateAudioImportPlan()
            }
        }
        if command == "preview_audio" {
            guard !isBusy else { return }
            audioPreviewBusy = true
            audioPreviewError = ""
        }
        if command == "plan_audio_import" {
            let blocked = audioImportFlowActive ? workerBusy : isBusy
            guard !blocked else {
                if audioImportFlowActive {
                    audioImportFlowActive = false
                    audioImportFlowMessage = ""
                    if audioExecuteError.isEmpty { audioExecuteError = "请等待当前相机或音频操作完成后再试。" }
                }
                return
            }
            audioPlanBusy = true
            audioPlanError = ""
        }
        if command == "confirm_audio_import" {
            let blocked = audioImportFlowActive ? workerBusy : isBusy
            guard !blocked else {
                if audioImportFlowActive {
                    audioImportFlowActive = false
                    audioImportFlowMessage = ""
                    if audioExecuteError.isEmpty { audioExecuteError = "请等待当前相机或音频操作完成后再试。" }
                }
                return
            }
            audioConfirmationBusy = true
            audioConfirmationError = ""
        }
        if command == "gate_audio_import" {
            guard !isBusy else { return }
            audioGateBusy = true
            audioGateError = ""
        }
        if command == "execute_audio_import" {
            let blocked = audioImportFlowActive ? workerBusy : isBusy
            guard !blocked else {
                if audioImportFlowActive {
                    audioImportFlowActive = false
                    audioImportFlowMessage = ""
                    if audioExecuteError.isEmpty { audioExecuteError = "请等待当前相机或音频操作完成后再试。" }
                }
                return
            }
            audioExecuteBusy = true
            audioExecuteError = ""
        }
        error = ""
        if ["inspect", "unlock", "close", "backup", "restore", "takeover", "recover_custom_sound", "reset", "environment"].contains(command) { progress = [] }
        var request = extra; request["command"] = command
        request["api_version"] = 1
        request["request_id"] = UUID().uuidString
        guard let bytes = try? JSONSerialization.data(withJSONObject: request), let input = input else {
            pendingAction = nil
            audioBusy = false
            audioPreviewBusy = false
            audioPlanBusy = false
            audioConfirmationBusy = false
            audioGateBusy = false
            audioExecuteBusy = false
            audioImportFlowActive = false
            audioImportFlowMessage = ""
            error = "本地服务尚未就绪，请重新打开应用。"
            return
        }
        do { try input.write(contentsOf: bytes + Data([10])) }
        catch {
            pendingAction = nil
            audioBusy = false
            audioPreviewBusy = false
            audioPlanBusy = false
            audioConfirmationBusy = false
            audioGateBusy = false
            audioExecuteBusy = false
            audioImportFlowActive = false
            audioImportFlowMessage = ""
            self.error = "无法连接本地服务。请重新打开应用；如操作已开始，先核实相机状态。"
        }
    }
    func exportDiagnostic(camera: Bool) {
        if camera && !canCollectCamera {
            error = "请先连接并检查相机，再采集相机日志。"
            return
        }
        guard !isBusy else { return }
        diagnosticBusy = true
        diagnosticProgress = []
        diagnosticResult = nil
        guidedState = nil
        uploadResult = nil
        send("diagnostic", extra: ["camera": camera])
    }

    func startGuidedDiagnostic() {
        if !canCollectCamera {
            error = "请先连接并检查相机，再开始录制。"
            return
        }
        guard !isBusy else { return }
        diagnosticBusy = true
        diagnosticProgress = []
        diagnosticResult = nil
        uploadResult = nil
        guidedState = nil
        send("guided_diagnostic", extra: ["action": "start"])
    }

    func collectGuidedDiagnostic() {
        NSApp.activate(ignoringOtherApps: true)
        diagnosticBusy = true
        diagnosticProgress = ["正在结束录制并取回日志…"]
        send("guided_diagnostic", extra: ["action": "collect"])
    }

    func restartGuidedDiagnostic() {
        if !canCollectCamera {
            error = "请先连接并检查相机，再重新录制。"
            return
        }
        guard !isBusy else { return }
        diagnosticBusy = true
        diagnosticProgress = ["正在清理相机内旧日志并重新录制…"]
        diagnosticResult = nil
        uploadResult = nil
        send("guided_diagnostic", extra: ["action": "restart"])
    }

    func cancelGuidedDiagnostic() {
        send("guided_diagnostic", extra: ["action": "cancel"])
    }

    func submitFeedback() {
        guard !isBusy, !feedbackDescription.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        if let status = guidedState?.status,
           ["starting", "restarting", "cancelling", "waiting_msc", "unplug_required", "waiting_user", "disconnected", "reconnected", "collecting", "awaiting_retrieval", "timeout"].contains(status) {
            error = "请先结束录制并取回日志，再统一上传。"
            NSSound.beep()
            return
        }
        NSApp.activate(ignoringOtherApps: true)
        uploadResult = nil
        feedbackUploadPending = false
        uploadDiagnostic(description: feedbackDescription)
    }

    func uploadDiagnostic(description: String) {
        guard !uploadBusy else { return }
        feedbackUploadPending = false
        uploadBusy = true
        uploadResult = nil
        send("upload_feedback", extra: ["description": description,
            "images": feedbackImages.map { $0.url.path }])
    }

    func chooseFeedbackImages() {
        let panel = NSOpenPanel()
        panel.title = "选择要上传的图片"
        panel.prompt = "选择"
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowedFileTypes = ["jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "gif", "bmp", "webp", "arw", "dng", "cr2", "cr3", "nef", "raf", "orf", "rw2"]
        guard panel.runModal() == .OK else { return }
        feedbackImageError = ""
        for url in panel.urls {
            if feedbackImages.contains(where: { $0.url == url }) { continue }
            if feedbackImages.count >= 12 {
                feedbackImageError = "一次最多上传 12 张图片。"
                break
            }
            do {
                let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
                guard values.isRegularFile == true, let size = values.fileSize else {
                    feedbackImageError = "只能选择普通图片文件。"
                    continue
                }
                if size > 200 * 1024 * 1024 {
                    feedbackImageError = "\(url.lastPathComponent) 超过 200MiB，未加入。"
                    continue
                }
                feedbackImages.append(FeedbackImage(url: url, name: url.lastPathComponent, bytes: Int64(size)))
            } catch {
                feedbackImageError = "无法读取图片大小：\(url.lastPathComponent)"
            }
        }
    }

    func removeFeedbackImage(_ id: UUID) {
        feedbackImages.removeAll { $0.id == id }
    }

    func ensureAudioSlotsLoaded() {
        guard physicalMode, canReadSlotsOnCamera, !isBusy else { return }
        let currentOp = state?.operation_id ?? "connected"
        guard slotsLoadedOperationID != currentOp else { return }
        send("refresh_audio_slots")
    }

    func resetAudioImportFlow() {
        audioImportFlowActive = false
        audioImportFlowSlot = nil
        audioImportFlowMessage = ""
        invalidateAudioImportPlan()
    }

    func startMarketSoundImport(_ sound: MarketSound, slot: Int, targetName: String? = nil) {
        guard !isBusy, (1...10).contains(slot) else { return }
        invalidateAudioImportPlan()
        audioImportSlot = slot
        audioImportFlowSlot = slot
        audioImportFlowActive = true
        audioImportFlowMessage = "正在校验音频并生成槽位 \(slot) 导入计划…"
        let customName = targetName?.trimmingCharacters(in: .whitespacesAndNewlines)
        switch sound.source {
        case .sample(let url), .personal(let url):
            var extra: [String: Any] = [
                "source": "local",
                "slot": slot,
                "path": url.path,
            ]
            if let customName, !customName.isEmpty {
                extra["filename"] = customName
            }
            send("plan_audio_import", extra: extra)
        case .catalog(let packageID, let soundID):
            var extra: [String: Any] = [
                "source": "market",
                "slot": slot,
                "package_id": packageID,
                "sound_id": soundID,
            ]
            if let customName, !customName.isEmpty {
                extra["filename"] = customName
            }
            send("plan_audio_import", extra: extra)
        }
    }

    func invalidateAudioImportPlan() {
        audioPlan = nil
        audioPlanError = ""
        audioConfirmation = nil
        audioConfirmationError = ""
        audioGate = nil
        audioGateError = ""
        audioExecute = nil
        audioExecuteError = ""
    }


    func shutdown() {
        generation = UUID()
        outputHandle?.readabilityHandler = nil
        outputHandle = nil
        diagnosticsHandle?.readabilityHandler = nil
        diagnosticsHandle = nil
        if let input = input, let process = process, process.isRunning {
            if let bytes = try? JSONSerialization.data(withJSONObject: ["command": "shutdown"]) {
                try? input.write(contentsOf: bytes + Data([10]))
            }
            try? input.close()
            let deadline = Date().addingTimeInterval(1.5)
            while process.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.05)
            }
            if process.isRunning { process.terminate() }
        } else {
            try? input?.close()
            if process?.isRunning == true { process?.terminate() }
        }
        process = nil
        input = nil
    }

    func allowWindowExit() -> Bool {
        let guidedActive = ["starting", "restarting", "cancelling", "waiting_msc", "unplug_required",
                            "waiting_user", "disconnected", "reconnected", "collecting"].contains(guidedState?.status ?? "")
        guard isBusy || guidedActive else { return true }
        error = Self.operationInProgressMessage
        NSSound.beep()
        return false
    }
}
