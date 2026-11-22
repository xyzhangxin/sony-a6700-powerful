import SwiftUI
import AppKit

struct DiagnosticsView: View {
    @ObservedObject var model: PreviewModel
    let physical: Bool
    @Environment(\.dismiss) private var dismiss
    private var guidedActive: Bool {
        ["starting", "restarting", "cancelling", "waiting_msc", "unplug_required", "waiting_user", "disconnected", "reconnected", "collecting", "awaiting_retrieval", "timeout"].contains(model.guidedState?.status ?? "")
    }
    private var recordingReady: Bool {
        physical && model.state?.compatible == true && model.state?.connected == true
    }
    private var awaitingRetrieval: Bool {
        model.guidedState?.status == "awaiting_retrieval"
    }
    private var pendingCameraLog: Bool {
        guidedActive
    }
    private var activelyRecording: Bool {
        ["starting", "waiting_msc", "unplug_required", "waiting_user", "disconnected", "reconnected"].contains(model.guidedState?.status ?? "")
    }

    private func close() {
        // Dismissing the sheet must not strand the on-camera worker. Collection
        // continues in the shared model and its result remains available when
        // the feedback sheet is reopened.
        dismiss()
    }

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "bubble.left.and.bubble.right").font(.system(size: 24)).foregroundColor(UI.accent)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("意见反馈").font(.system(size: 21, weight: .bold))
                        Text("填写问题，按需录制错误日志并附图，最后统一上传。")
                            .font(.system(size: 11)).foregroundColor(UI.muted)
                    }
                    Spacer()
                    Button("关闭") { close() }.buttonStyle(.plain).foregroundColor(UI.muted)
                }.padding(20)
                Divider()

                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("问题描述").font(.system(size: 12, weight: .semibold))
                    TextEditor(text: $model.feedbackDescription)
                        .font(.system(size: 12)).frame(height: 86)
                        .padding(7).background(Color.white).overlay(RoundedRectangle(cornerRadius: 8).stroke(UI.line))
                    Text("请写明现象、操作步骤和期望结果。")
                        .font(.system(size: 10)).foregroundColor(UI.muted)
                }

                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Label("可选图片", systemImage: "photo.on.rectangle.angled")
                            .font(.system(size: 12, weight: .semibold))
                        if !model.feedbackImages.isEmpty {
                            Text("\(model.feedbackImages.count)/12").font(.system(size: 10)).foregroundColor(UI.muted)
                        }
                    }
                    Text("点击上传框添加图片，单张不超过 200MiB。")
                        .font(.system(size: 10)).foregroundColor(UI.muted)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 88), spacing: 10)], spacing: 10) {
                        ForEach(model.feedbackImages) { image in
                            imageTile(image)
                        }
                        if model.feedbackImages.count < 12 {
                            ForEach(0..<(model.feedbackImages.isEmpty ? 3 : 1), id: \.self) { _ in
                                addImageTile
                            }
                        }
                    }
                    if !model.feedbackImageError.isEmpty {
                        Text(model.feedbackImageError).font(.system(size: 10)).foregroundColor(UI.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                logCard

                    }
                    .padding(20)
                }
                Divider()
                uploadFooter
            }.frame(width: 640, height: 680).background(UI.background)
        }
    }

    private var addImageTile: some View {
        Button {
            model.chooseFeedbackImages()
        } label: {
            VStack(spacing: 7) {
                Image(systemName: "plus").font(.system(size: 22, weight: .light)).foregroundColor(UI.accent)
                Text("添加图片").font(.system(size: 10)).foregroundColor(UI.accent)
            }.frame(width: 88, height: 88)
                .background(UI.accent.opacity(0.035))
                .overlay(RoundedRectangle(cornerRadius: 9)
                    .stroke(UI.accent.opacity(0.45), style: StrokeStyle(lineWidth: 1, dash: [5, 4])))
                .cornerRadius(9)
        }.buttonStyle(.plain).disabled(model.isBusy)
            .accessibilityLabel("添加图片")
    }

    private func imageTile(_ image: FeedbackImage) -> some View {
        ZStack(alignment: .topTrailing) {
            VStack(spacing: 4) {
                if let preview = NSImage(contentsOf: image.url) {
                    Image(nsImage: preview).resizable().aspectRatio(contentMode: .fill)
                        .frame(width: 88, height: 66).clipped().cornerRadius(8)
                } else {
                    VStack(spacing: 6) {
                        Image(systemName: "photo").font(.system(size: 23)).foregroundColor(UI.muted)
                        Text(image.url.pathExtension.uppercased()).font(.system(size: 10, weight: .medium)).foregroundColor(UI.muted)
                    }.frame(width: 88, height: 66).background(UI.muted.opacity(0.07)).cornerRadius(8)
                }
                Text(ByteCountFormatter.string(fromByteCount: image.bytes, countStyle: .file))
                    .font(.system(size: 9)).foregroundColor(UI.muted).lineLimit(1)
            }.frame(width: 88, height: 88)
            Button {
                model.removeFeedbackImage(image.id)
            } label: {
                Image(systemName: "xmark.circle.fill").font(.system(size: 16))
                    .foregroundColor(.white).background(Circle().fill(Color.black.opacity(0.55)))
            }.buttonStyle(.plain).offset(x: 5, y: -5).disabled(model.isBusy)
                .accessibilityLabel("移除 \(image.name)")
        }.accessibilityElement(children: .combine)
            .accessibilityLabel("图片 \(image.name)")
    }

    private var logCard: some View {
        Panel {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: pendingCameraLog ? "externaldrive.badge.clock" : "record.circle")
                    .font(.system(size: 25)).foregroundColor(pendingCameraLog ? UI.amber : .red)
                VStack(alignment: .leading, spacing: 8) {
                    Text(pendingCameraLog ? "相机内有未取回日志" : (model.diagnosticResult?.path == nil ? "错误日志采集" : "诊断报告已生成"))
                        .font(.system(size: 14, weight: .semibold))
                    Text(logCardMessage)
                        .font(.system(size: 11)).foregroundColor(UI.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    if model.diagnosticBusy {
                        progressContent
                    } else {
                        logCardActions
                    }
                }
            }
        }
    }

    private var logCardMessage: String {
        if let detail = model.guidedState?.error, !detail.isEmpty, !model.diagnosticBusy {
            return detail
        }
        if pendingCameraLog {
            return activelyRecording
                ? "正在持续记录。可直接操作相机；完成后点击“结束录制并上传”。"
                : "记录已保存在相机。可取回并上传；重新录制会先清空这份日志。"
        }
        if model.diagnosticResult?.path == nil {
            return recordingReady
                ? "开始后可直接操作相机；记录会持续写入并同步保存。"
                : "请先连接并检查相机，确认采集服务正常后再录制。"
        }
        return "日志已生成。填写问题和图片后，使用底部按钮统一上传。"
    }

    @ViewBuilder private var logCardActions: some View {
        if pendingCameraLog {
            HStack(spacing: 10) {
                Button(activelyRecording ? "结束录制" : "取回日志") {
                    model.collectGuidedDiagnostic()
                }.buttonStyle(PrimaryStyle())
                .disabled(model.isBusy)
                if !activelyRecording {
                    Button("重新录制（清空旧日志）") {
                        model.restartGuidedDiagnostic()
                    }.buttonStyle(PrimaryStyle(secondary: true))
                    .disabled(model.isBusy || !physical)
                }
            }
        } else if model.diagnosticResult?.path == nil {
            Button("开始录制") {
                model.startGuidedDiagnostic()
            }.buttonStyle(PrimaryStyle())
            .disabled(!recordingReady || model.isBusy)
        } else {
            HStack(spacing: 10) {
                Label("日志已就绪", systemImage: "checkmark.circle.fill")
                    .font(.system(size: 12, weight: .medium)).foregroundColor(UI.accent)
                Button("重新录制") {
                    model.restartGuidedDiagnostic()
                }.buttonStyle(PrimaryStyle(secondary: true))
                .disabled(!recordingReady || model.isBusy)
            }
        }
    }

    private var uploadFooter: some View {
        let descriptionReady = !model.feedbackDescription.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return VStack(alignment: .leading, spacing: 9) {
            if let upload = model.uploadResult {
                uploadResultPanel(upload)
            }
            Button {
                model.submitFeedback()
            } label: {
                HStack(spacing: 8) {
                    if model.uploadBusy {
                        ProgressView().controlSize(.small)
                        Text("正在上传意见反馈…")
                    } else {
                        Image(systemName: "paperplane.fill")
                        Text("上传意见反馈")
                    }
                }.frame(maxWidth: .infinity).frame(height: 24)
            }.buttonStyle(PrimaryStyle())
            .disabled(model.isBusy || !descriptionReady || pendingCameraLog)
            Text(pendingCameraLog
                 ? "请先结束录制并取回日志，再统一上传。"
                 : (model.diagnosticResult?.path == nil
                    ? "不录制日志也可以上传；将提交问题描述和所选图片。"
                    : "将提交问题描述、日志 ZIP 和所选图片。"))
                .font(.system(size: 10)).foregroundColor(UI.muted)
                .fixedSize(horizontal: false, vertical: true)
        }.padding(16).background(UI.background)
    }

    private var progressContent: some View {
        let status = model.guidedState?.status ?? ""
        let collecting = status == "collecting"
        let restarting = status == "restarting"
        let cancelling = status == "cancelling"
        let title = cancelling ? "正在取消操作" : (restarting ? "正在重新采集" : (collecting ? "正在结束录制并取回日志" : "正在启动错误日志采集"))
        let detail = cancelling
            ? "已收到取消请求。正在等待当前 USB 请求安全返回，不会强制断开相机。"
            : (restarting
                ? "正在连接相机、取回旧日志并启动新记录；各阶段进度会显示在下方。"
                : (collecting ? "正在停止机内记录、读取日志和整理诊断报告，请保持 USB 连接。" : "正在与相机通信，请保持连接。"))
        return VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(title)
                    .font(.system(size: 13, weight: .semibold)).foregroundColor(UI.accent)
            }
            Text(detail)
                .font(.system(size: 11)).foregroundColor(UI.muted)
                .fixedSize(horizontal: false, vertical: true).padding(.top, 5)
            if !model.diagnosticProgress.isEmpty {
                ForEach(Array(model.diagnosticProgress.suffix(5).enumerated()), id: \.offset) { _, line in
                    Text(line).font(.system(size: 11)).foregroundColor(UI.muted)
                        .fixedSize(horizontal: false, vertical: true).padding(.top, 5)
                }
            }
            if ["starting", "restarting"].contains(status) {
                Button("取消") {
                    model.cancelGuidedDiagnostic()
                }.buttonStyle(PrimaryStyle(secondary: true)).padding(.top, 6)
            } else if cancelling {
                Text("取消请求已发送，正在安全收尾…")
                    .font(.system(size: 11, weight: .medium)).foregroundColor(UI.amber).padding(.top, 5)
            }
        }
    }

    @ViewBuilder private func uploadResultPanel(_ result: FeedbackUploadResult) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            if result.status == "received" {
                Label("反馈和附件已上传", systemImage: "checkmark.circle.fill")
                    .font(.system(size: 12, weight: .semibold)).foregroundColor(UI.accent)
            } else if result.status == "partial" {
                Label("反馈已上传，部分附件未接收", systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12, weight: .semibold)).foregroundColor(UI.amber)
            } else {
                Label("上传未完成", systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12, weight: .semibold)).foregroundColor(UI.amber)
            }
            if let id = result.feedback_id {
                Text("feedback_id：\(id)").font(.system(size: 10, design: .monospaced)).textSelection(.enabled)
            }
            if let error = result.error {
                Text(error).font(.system(size: 11)).foregroundColor(UI.muted)
                    .fixedSize(horizontal: false, vertical: true)
            } else if result.status != "received" {
                Text("报告仍保留在本机，可稍后重试；不需要重新录制。")
                    .font(.system(size: 11)).foregroundColor(UI.muted)
            }
        }
    }
}
