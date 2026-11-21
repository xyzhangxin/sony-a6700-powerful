import SwiftUI

struct ShutterSoundMarketView: View {
    @ObservedObject var session: AudioMarketSession
    @ObservedObject var model: PreviewModel
    let physical: Bool
    let onConnect: () -> Void
    let onGoToFeatures: () -> Void
    let onInstall: (MarketSound) -> Void

    @State private var renamingSound: MarketSound?
    @State private var deletingSound: MarketSound?
    @State private var renameText = ""
    @State private var renameError = ""

    private var sounds: [MarketSound] {
        let personalIDs = Set(session.personalSounds.map(\.id))
        return session.personalSounds + session.catalogSounds + session.demoSounds.filter {
            !personalIDs.contains($0.id)
        }
    }

    private var filteredSounds: [MarketSound] {
        let text = session.query.trimmingCharacters(in: .whitespacesAndNewlines)
        return sounds.filter { sound in
            let included = session.category == "全部声音" ||
                (session.category == "收藏" && session.favorites.contains(sound.id)) ||
                (session.category == "我的音频" && sound.personal)
            return included && (text.isEmpty ||
                ([sound.title, sound.subtitle] + sound.tags)
                    .contains { $0.localizedStandardContains(text) })
        }
    }

    private var connected: Bool { model.isCameraConnected }
    private var soundUnlocked: Bool { model.electronicSoundUnlocked }
    private var customUnlocked: Bool { model.customSoundUnlocked }
    private var canInstall: Bool { model.canInstallAudioOnCamera && !model.isBusy }

    private var installDisabledReason: String {
        if model.isBusy { return "请等待当前相机操作完成" }
        if !connected { return "请先连接相机并完成状态检查" }
        if !soundUnlocked { return "需先在「功能管理」解锁「电子快门声音」" }
        if !customUnlocked { return "当前相机未安装十槽自定义快门声固件" }
        return "选择目标槽位并写入相机"
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Text("快门声市场").font(.system(size: 21, weight: .bold))
                Spacer(minLength: 20)
                searchField.frame(maxWidth: 360)
                Button { session.upload() } label: {
                    Label("导入音频", systemImage: "square.and.arrow.down")
                }.buttonStyle(PrimaryStyle())
                    .help("支持 MP3、M4A、AAC、WAV、AIFF、FLAC 和 CAF。超过 350 毫秒时可裁剪后保存。")
            }.padding(.horizontal, 24).padding(.top, 18).padding(.bottom, 6)

            cameraStatusPanel
                .padding(.horizontal, 24)
                .padding(.vertical, 8)

            HStack(spacing: 7) {
                ForEach(["全部声音", "收藏", "我的音频"], id: \.self) { item in
                    Button { session.category = item } label: {
                        Text(item).font(.system(size: 12, weight: session.category == item ? .semibold : .regular))
                            .padding(.horizontal, 13).padding(.vertical, 8)
                            .foregroundColor(session.category == item ? UI.accent : UI.muted)
                            .background(session.category == item ? UI.accent.opacity(0.09) : .clear)
                            .cornerRadius(7)
                    }.buttonStyle(.plain)
                }
                Spacer()
                Text("\(filteredSounds.count) 个声音").font(.system(size: 11)).foregroundColor(UI.muted)
                Button("刷新目录") { Task { await session.loadCatalog(refresh: true) } }
                    .disabled(session.loadingCatalog)
            }.padding(.horizontal, 24).padding(.vertical, 6)

            ScrollView {
                if session.loadingCatalog && filteredSounds.isEmpty {
                    ProgressView("正在加载声音…").frame(maxWidth: .infinity).padding(50)
                } else if filteredSounds.isEmpty {
                    emptyState
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 268), spacing: 10)], spacing: 10) {
                        ForEach(filteredSounds) { sound in soundCard(sound) }
                    }.padding(.horizontal, 24).padding(.vertical, 6).padding(.bottom, 12)
                }
            }

            if !session.error.isEmpty {
                Label(session.error, systemImage: "exclamationmark.circle")
                    .font(.system(size: 12)).foregroundColor(UI.amber)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 24).padding(.vertical, 10)
            } else if !session.message.isEmpty {
                Label(session.message, systemImage: "checkmark.circle.fill")
                    .font(.system(size: 12)).foregroundColor(UI.accent)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 24).padding(.vertical, 10)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(UI.background).foregroundColor(UI.ink)
        .task { await session.loadCatalog() }
        .onDisappear { session.stopPreview() }
        .sheet(item: $renamingSound) { sound in
            audioRenameSheet(for: sound)
        }
        .sheet(item: $session.trimRequest) { request in
            AudioTrimSheet(session: session, request: request)
        }
        .alert("删除本地音频？", isPresented: Binding(
            get: { deletingSound != nil },
            set: { if !$0 { deletingSound = nil } }
        ), presenting: deletingSound) { sound in
            Button("移到废纸篓", role: .destructive) {
                do { try session.trashPersonalSound(sound) }
                catch { session.error = error.localizedDescription }
            }
            Button("取消", role: .cancel) {}
        } message: { sound in
            Text("「\(sound.title)」将从「我的音频」移到废纸篓。")
        }
    }

    private func audioRenameSheet(for sound: MarketSound) -> some View {
        let trimmedRename = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        let validationError = AudioMarketSession.cameraSlotNameError(trimmedRename)
        return VStack(alignment: .leading, spacing: 14) {
            Text("重命名音频").font(.system(size: 18, weight: .bold))
            Text("修改「我的音频」中的显示名称；写入相机时将直接使用该名称（支持中文与英文，无需 .wav 后缀）。")
                .font(.system(size: 12)).foregroundColor(UI.muted)
            TextField("输入音频名称（支持中文、英文、数字、空格及 ._-）", text: $renameText)
                .textFieldStyle(.roundedBorder)
            if let validationError, !trimmedRename.isEmpty {
                Label(validationError, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 11)).foregroundColor(UI.amber)
            } else {
                Text("写入相机槽位名称：\(trimmedRename.isEmpty ? "（未输入）" : trimmedRename)")
                    .font(.system(size: 11)).foregroundColor(UI.accent)
            }
            if !renameError.isEmpty {
                Label(renameError, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 11)).foregroundColor(UI.amber)
            }
            HStack {
                Button("取消") { renamingSound = nil }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("保存") {
                    do {
                        _ = try session.renamePersonalSound(sound, to: renameText)
                        renameError = ""
                        renamingSound = nil
                    } catch {
                        renameError = error.localizedDescription
                    }
                }
                .buttonStyle(PrimaryStyle())
                .disabled(trimmedRename.isEmpty || validationError != nil)
            }
        }
        .padding(22)
        .frame(width: 440)
        .foregroundColor(UI.ink)
    }

    private var cameraStatusPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                statusBadge(
                    icon: connected ? "checkmark.circle.fill" : "cable.connector",
                    text: connected ? "连接状态：已连接" : "连接状态：未连接",
                    tint: connected ? UI.accent : UI.muted
                )
                if connected {
                    statusBadge(
                        icon: soundUnlocked ? "speaker.wave.2.fill" : "speaker.slash",
                        text: "电子快门声音：" + featureLabel(model.electronicSoundState),
                        tint: soundUnlocked ? UI.accent : UI.amber
                    )
                    statusBadge(
                        icon: customUnlocked ? "waveform.circle.fill" : "waveform",
                        text: "自定义快门声：" + customLabel(model.customSoundState),
                        tint: customUnlocked ? UI.accent : (model.customSoundState == "disabled" ? UI.muted : UI.amber)
                    )
                } else {
                    Text("功能状态：连接后检查 · 未连接时仅支持试听、收藏和本地音频导入")
                        .font(.system(size: 11)).foregroundColor(UI.muted)
                }
                Spacer(minLength: 8)
                if physical && connected {
                    CameraDisconnectControl(model: model)
                }
                if !connected {
                    Button(physical ? "连接相机" : "演示连接", action: onConnect)
                        .buttonStyle(PrimaryStyle())
                        .disabled(model.cameraOperationActive)
                } else if !soundUnlocked || !customUnlocked {
                    Button("去功能管理", action: onGoToFeatures)
                        .buttonStyle(PrimaryStyle())
                        .disabled(model.isBusy)
                } else {
                    Button("功能管理", action: onGoToFeatures)
                        .buttonStyle(.bordered).controlSize(.small)
                        .disabled(model.isBusy)
                }
            }

            if physical && !connected && model.state?.camera_disconnect != nil {
                CameraDisconnectControl(model: model, showsAction: false)
            }

            if connected && (!soundUnlocked || !customUnlocked) {
                let reason: String = {
                    if !soundUnlocked {
                        return "当前相机尚未解锁「电子快门声音」；请先在「功能管理」解锁电子快门声音。"
                    }
                    return model.state?.custom_sound?.reason ?? "当前相机未检测到十槽自定义快门声固件，暂不可写入声音槽位。"
                }()
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Image(systemName: "info.circle").accessibilityHidden(true)
                    Text(reason).textSelection(.enabled)
                }
                .font(.system(size: 11))
                .foregroundColor(UI.amber)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .background(.white, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(UI.line))
    }

    private func statusBadge(icon: String, text: String, tint: Color) -> some View {
        Label(text, systemImage: icon)
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(tint)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(tint.opacity(0.08), in: Capsule())
    }

    private func featureLabel(_ state: String) -> String {
        switch state {
        case "enabled": return "已解锁"
        case "disabled": return "未解锁"
        case "partial": return "部分解锁"
        default: return "未读取"
        }
    }

    private func customLabel(_ state: String) -> String {
        switch state {
        case "enabled": return "已解锁"
        case "disabled": return "未安装"
        case "partial": return "部分改动"
        case "error": return "校验失败"
        default: return "未读取"
        }
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundColor(UI.muted).accessibilityHidden(true)
            TextField("搜索声音名称或关键词", text: $session.query).textFieldStyle(.plain)
                .accessibilityLabel("搜索音频市场")
            if !session.query.isEmpty {
                Button { session.query = "" } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundColor(UI.muted).accessibilityLabel("清空搜索")
            }
        }.font(.system(size: 13)).padding(.horizontal, 10).frame(height: 36)
            .background(.white, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(UI.line))
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: session.query.isEmpty ? "waveform" : "magnifyingglass")
                .font(.system(size: 30)).foregroundColor(UI.muted)
            Text(session.query.isEmpty ? (session.category == "收藏" ? "还没有收藏" : "这里还没有音频") : "没有找到匹配的声音")
                .font(.system(size: 16, weight: .semibold))
            Text(session.query.isEmpty ? "支持导入 MP3、M4A、WAV 等 2 秒以内音频，或到全部声音中试听。" : "试试其他名称，或清空搜索。")
                .font(.system(size: 12)).foregroundColor(UI.muted)
        }.frame(maxWidth: .infinity).padding(.vertical, 62)
    }

    private func soundCard(_ sound: MarketSound) -> some View {
        let active = session.playingID == sound.id || session.preparingID == sound.id
        let favorite = session.favorites.contains(sound.id)
        let selected = session.selectedSoundID == sound.id
        let summary = sound.subtitle.isEmpty ? sound.tags.joined(separator: " · ") : sound.subtitle
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 9) {
                Button { session.preview(sound) } label: {
                    Image(systemName: active ? "stop.fill" : "play.fill")
                        .font(.system(size: 13, weight: .semibold))
                        .frame(width: 34, height: 34)
                        .foregroundColor(active ? .white : UI.accent)
                        .background(active ? UI.accent : UI.accent.opacity(0.09),
                                    in: RoundedRectangle(cornerRadius: 9))
                        .contentShape(Rectangle())
                }.buttonStyle(.borderless)
                    .help(active ? "停止试听" : "试听")
                    .accessibilityLabel((active ? "停止试听：" : "试听：") + sound.title)
                VStack(alignment: .leading, spacing: 3) {
                    Text(sound.title).font(.system(size: 14, weight: .semibold))
                        .lineLimit(1).help(sound.title)
                    Text("\(sound.durationMS) 毫秒").font(.system(size: 11).monospacedDigit())
                        .foregroundColor(UI.muted)
                }.frame(maxWidth: .infinity, alignment: .leading)
                if sound.personal {
                    Button {
                        renameText = sound.title
                        renameError = ""
                        renamingSound = sound
                    } label: {
                        Image(systemName: "pencil")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(UI.muted)
                            .frame(width: 29, height: 29)
                            .background(Color.black.opacity(0.035), in: RoundedRectangle(cornerRadius: 8))
                            .contentShape(Rectangle())
                    }.buttonStyle(.borderless)
                        .help("重命名")
                        .accessibilityLabel("重命名：" + sound.title)
                    Button { deletingSound = sound } label: {
                        Image(systemName: "trash")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(UI.muted)
                            .frame(width: 29, height: 29)
                            .background(Color.black.opacity(0.035), in: RoundedRectangle(cornerRadius: 8))
                            .contentShape(Rectangle())
                    }.buttonStyle(.borderless)
                        .help("删除本地音频")
                        .accessibilityLabel("删除：" + sound.title)
                }
                Button { session.toggleFavorite(sound) } label: {
                    Image(systemName: favorite ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(favorite ? UI.accent : UI.muted)
                        .frame(width: 29, height: 29)
                        .background(favorite ? UI.accent.opacity(0.12) : Color.black.opacity(0.035),
                                    in: RoundedRectangle(cornerRadius: 8))
                        .contentShape(Rectangle())
                }.buttonStyle(.borderless)
                    .help(favorite ? "取消收藏" : "收藏")
                    .accessibilityLabel((favorite ? "取消收藏：" : "收藏：") + sound.title)
            }
            HStack(spacing: 6) {
                Text(summary).font(.system(size: 11)).foregroundColor(UI.muted)
                    .lineLimit(1).help(summary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("安装") { onInstall(sound) }
                    .buttonStyle(.bordered).controlSize(.small).tint(UI.accent)
                    .fixedSize().disabled(!canInstall)
                    .help(installDisabledReason)
                    .accessibilityLabel("安装到相机：" + sound.title)
            }
        }.padding(10)
            .background(.white, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(selected ? UI.accent : UI.line))
    }
}

private struct AudioTrimSheet: View {
    @ObservedObject var session: AudioMarketSession
    let request: AudioTrimRequest
    @State private var selection: AudioTrimSelection
    @State private var peaks: [Float] = []
    @State private var error = ""

    init(session: AudioMarketSession, request: AudioTrimRequest) {
        self.session = session
        self.request = request
        _selection = State(initialValue: AudioTrimSelection(duration: request.duration))
    }

    private var playing: Bool { session.playingID == request.id.uuidString }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("裁剪快门声").font(.system(size: 20, weight: .bold))
            Text(request.title).font(.system(size: 13, weight: .medium)).lineLimit(1)
            HStack(spacing: 12) {
                Button {
                    if playing { session.stopPreview() }
                    else { perform { try session.previewTrim(request, range: selection.range) } }
                } label: {
                    Image(systemName: playing ? "pause.fill" : "play.fill")
                        .font(.system(size: 15, weight: .semibold))
                        .frame(width: 38, height: 38)
                        .foregroundColor(UI.accent)
                        .background(UI.accent.opacity(0.09), in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .help(playing ? "停止试听" : "试听选区")
                .accessibilityLabel(playing ? "停止试听" : "试听选区")
                TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !playing)) { _ in
                    AudioTrimTimeline(selection: $selection, peaks: peaks,
                                      progress: session.trimPlaybackFraction(request))
                }
                .frame(height: 96)
            }
            HStack {
                Text("起点 \(time(selection.start))")
                Text("终点 \(time(selection.end))")
                Spacer()
                Text("已选 \(Int(((selection.end - selection.start) * 1000).rounded())) / 350 ms")
                    .foregroundColor(UI.accent)
            }
            .font(.system(size: 12)).monospacedDigit()
            Text("拖动两端调整起止点，拖动选区可整体移动。")
                .font(.system(size: 11)).foregroundColor(UI.muted)
            if !error.isEmpty {
                Text(error).font(.system(size: 12)).foregroundColor(UI.amber).textSelection(.enabled)
            }
            HStack(spacing: 8) {
                Spacer()
                Button { session.trimRequest = nil } label: { Text("取消").frame(width: 68) }
                    .buttonStyle(.bordered).keyboardShortcut(.cancelAction)
                Button {
                    perform { try session.saveTrim(request, range: selection.range) }
                } label: { Text("保存").frame(width: 68) }
                    .buttonStyle(.borderedProminent).tint(UI.accent).keyboardShortcut(.defaultAction)
            }
            .controlSize(.large)
        }
        .padding(24)
        .frame(width: 600)
        .task(id: request.id) {
            do { peaks = try await AudioMarketSession.trimWaveform(request.url) }
            catch is CancellationError {}
            catch { self.error = "读取波形失败：\(error.localizedDescription)" }
        }
        .onChange(of: selection) { _ in session.stopPreview(); error = "" }
        .onDisappear { session.stopPreview() }
    }

    private func time(_ seconds: Double) -> String { String(format: "%.3f 秒", seconds) }

    private func perform(_ action: () throws -> Void) {
        error = ""
        do { try action() }
        catch let failure { error = failure.localizedDescription }
    }
}

private struct AudioTrimTimeline: View {
    @Binding var selection: AudioTrimSelection
    let peaks: [Float]
    let progress: Double?
    @State private var dragOrigin: AudioTrimSelection?
    private let inset: CGFloat = 22

    private enum DragPart { case start, end, region }

    var body: some View {
        GeometryReader { proxy in
            let width = max(1, proxy.size.width - 2 * inset)
            let height = proxy.size.height
            let left = inset + width * selection.start / selection.duration
            let right = inset + width * selection.end / selection.duration
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 8).fill(UI.accent.opacity(0.055))
                Rectangle()
                    .fill(UI.accent.opacity(0.18))
                    .frame(width: right - left, height: height - 6)
                    .position(x: (left + right) / 2, y: height / 2)
                Canvas { context, size in
                    let middle = size.height / 2
                    var selected = Path()
                    var unselected = Path()
                    for (index, peak) in peaks.enumerated() {
                        let x = inset + width * (Double(index) + 0.5) / Double(peaks.count)
                        let amplitude = max(0.6, CGFloat(peak) * (middle - 10))
                        let top = CGPoint(x: x, y: middle - amplitude)
                        let bottom = CGPoint(x: x, y: middle + amplitude)
                        if x >= left && x <= right {
                            selected.move(to: top); selected.addLine(to: bottom)
                        } else {
                            unselected.move(to: top); unselected.addLine(to: bottom)
                        }
                    }
                    context.stroke(unselected, with: .color(UI.muted.opacity(0.48)), lineWidth: 1)
                    context.stroke(selected, with: .color(UI.accent), lineWidth: 1)
                }.allowsHitTesting(false)
                if let progress {
                    Rectangle().fill(UI.ink)
                        .frame(width: 1, height: height - 8)
                        .position(x: left + (right - left) * progress, y: height / 2)
                        .allowsHitTesting(false)
                }
                Rectangle().fill(.clear).contentShape(Rectangle())
                    .frame(width: right - left, height: height)
                    .position(x: (left + right) / 2, y: height / 2)
                    .gesture(drag(.region, width: width))
                    .accessibilityHidden(true)
                handle(.start, width: width).position(x: left - 10.5, y: height / 2)
                handle(.end, width: width).position(x: right + 10.5, y: height / 2)
            }
        }
    }

    private func handle(_ part: DragPart, width: CGFloat) -> some View {
        let isStart = part == .start
        return RoundedRectangle(cornerRadius: 3)
            .fill(UI.accent)
            .frame(width: 9, height: 84)
            .overlay { Capsule().fill(.white.opacity(0.75)).frame(width: 2, height: 15) }
            .frame(width: 21, height: 92, alignment: isStart ? .trailing : .leading)
            .contentShape(Rectangle())
            .gesture(drag(part, width: width))
            .accessibilityLabel(isStart ? "选区起点" : "选区终点")
            .accessibilityValue(String(format: "%.3f 秒", isStart ? selection.start : selection.end))
            .accessibilityAdjustableAction { direction in
                let offset = direction == .increment ? 0.005 : -0.005
                if isStart { selection.moveStart(to: selection.start + offset) }
                else { selection.moveEnd(to: selection.end + offset) }
            }
    }

    private func drag(_ part: DragPart, width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 1, coordinateSpace: .global)
            .onChanged { value in
                if dragOrigin == nil { dragOrigin = selection }
                guard var origin = dragOrigin else { return }
                let delta = value.translation.width / width * origin.duration
                switch part {
                case .start: origin.moveStart(to: origin.start + delta)
                case .end: origin.moveEnd(to: origin.end + delta)
                case .region: origin.shift(by: delta)
                }
                selection = origin
            }
            .onEnded { _ in dragOrigin = nil }
    }
}

struct ShutterSoundInstallView: View {
    @ObservedObject var session: AudioMarketSession
    @ObservedObject var model: PreviewModel
    let sound: MarketSound
    let physical: Bool
    let onClose: () -> Void

    @State private var selectedSlot: Int
    @State private var userSelectedSlot = false
    @State private var targetNameInput: String
    @State private var installPrepareError = ""
    @State private var demoInstalledSlot: Int?

    init(session: AudioMarketSession, model: PreviewModel, sound: MarketSound, physical: Bool,
         onClose: @escaping () -> Void) {
        self.session = session
        self.model = model
        self.sound = sound
        self.physical = physical
        self.onClose = onClose
        let suggestedPhysical = model.audioCatalog?.slots?.first(where: { $0.status == "empty" })?.slot
        let initialSlot = physical ? (suggestedPhysical ?? 1) : (session.suggestedSlot ?? 1)
        _selectedSlot = State(initialValue: initialSlot)
        _targetNameInput = State(initialValue: AudioMarketSession.defaultCameraTargetName(for: sound))
    }

    private var slotsKnownInPhysical: Bool {
        guard let slots = model.audioCatalog?.slots, !slots.isEmpty else { return false }
        return slots.contains { $0.status != "unknown" }
    }

    private var selectedSlotInfo: (label: String, occupied: Bool, unknown: Bool) {
        if physical {
            if let row = model.audioCatalog?.slots?.first(where: { $0.slot == selectedSlot }) {
                if row.status == "empty" { return ("[空]", false, false) }
                if row.status == "unknown" { return ("[未读取]", false, true) }
                return (row.name, true, false)
            }
            return ("[未读取]", false, true)
        } else if let existing = session.slots.first(where: { $0.id == selectedSlot })?.sound {
            return (existing.title, true, false)
        } else {
            return ("[空]", false, false)
        }
    }

    private var needsOverwriteConfirmation: Bool {
        selectedSlotInfo.occupied || (physical && selectedSlotInfo.unknown)
    }

    private var physicalSucceeded: Bool {
        physical && model.audioExecute?.status == "completed" && model.audioExecute?.independent_readback == true
    }

    private var succeeded: Bool {
        physical ? physicalSucceeded : (demoInstalledSlot != nil)
    }

    private var inFlight: Bool {
        model.audioImportFlowActive || model.audioPlanBusy || model.audioConfirmationBusy || model.audioExecuteBusy
    }

    private var canRenameTargetFile: Bool {
        if case .catalog = sound.source { return false }
        return true
    }

    private var trimmedTargetName: String {
        targetNameInput.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var targetNameError: String? {
        guard canRenameTargetFile else { return nil }
        return AudioMarketSession.cameraSlotNameError(trimmedTargetName)
    }

    private var previewCameraFilename: String {
        if case .catalog(_, let soundID) = sound.source {
            return soundID
        }
        return trimmedTargetName.isEmpty ? AudioMarketSession.defaultCameraTargetName(for: sound) : trimmedTargetName
    }

    var body: some View {
        let info = selectedSlotInfo
        let isPlaying = session.playingID == sound.id || session.preparingID == sound.id
        return VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("安装到相机槽位").font(.system(size: 20, weight: .bold))
                    Text("\(sound.title) · \(sound.durationMS) 毫秒 · \(sound.author)")
                        .font(.system(size: 12)).foregroundColor(UI.muted)
                }
                Spacer()
                Button { session.preview(sound) } label: {
                    Label(isPlaying ? "停止试听" : "试听", systemImage: isPlaying ? "stop.fill" : "play.fill")
                }.buttonStyle(.bordered).controlSize(.small)
            }

            if canRenameTargetFile {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 8) {
                        Text("写入名称").font(.system(size: 12, weight: .semibold))
                        TextField("输入槽位名称（支持中文、英文、数字，无需 .wav）", text: $targetNameInput)
                            .textFieldStyle(.roundedBorder)
                            .disabled(inFlight || succeeded)
                    }
                    if let targetNameError, !trimmedTargetName.isEmpty {
                        Label(targetNameError, systemImage: "exclamationmark.triangle.fill")
                            .font(.system(size: 10)).foregroundColor(UI.amber)
                    } else {
                        Text("相机槽位将显示：\(previewCameraFilename)（支持中文 UTF-8，无需 .wav 后缀）")
                            .font(.system(size: 10)).foregroundColor(UI.muted)
                    }
                }
            }

            Divider()

            HStack {
                Text("选择目标槽位（1～10）").font(.system(size: 13, weight: .semibold))
                Text("默认 · 原厂（不可写入）")
                    .font(.system(size: 10)).foregroundColor(UI.muted)
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(Color.black.opacity(0.045), in: Capsule())
                Spacer()
                if physical {
                    Button(model.audioBusy ? "读取中…" : "刷新槽位") {
                        model.send("refresh_audio_slots")
                    }.buttonStyle(.bordered).controlSize(.small)
                        .disabled(model.isBusy || inFlight || model.state?.camera_disconnect != nil)
                }
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 5), spacing: 8) {
                ForEach(1...10, id: \.self) { slot in
                    slotButton(slot)
                }
            }

            if physical && !model.audioSlotsError.isEmpty {
                importError("槽位读取失败：\(model.audioSlotsError)")
            } else if model.isCameraConnected && model.customSoundState == "error",
                      let reason = model.state?.custom_sound?.reason {
                importError(reason)
            } else if !model.canInstallAudioOnCamera {
                importError("相机未连接或自定义快门声未解锁，当前无法导入。")
            } else if physical && model.audioBusy && !slotsKnownInPhysical {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在读取相机 10 个槽位，请稍候…").font(.system(size: 12)).foregroundColor(UI.muted)
                }
            } else if physical && !slotsKnownInPhysical {
                Label("尚未读取相机槽位内容，点击下方按钮将直接写入槽位 \(selectedSlot)。", systemImage: "info.circle")
                    .font(.system(size: 12)).foregroundColor(UI.amber)
                    .fixedSize(horizontal: false, vertical: true)
            } else if info.occupied {
                Label("槽位 \(selectedSlot) 当前已有「\(info.label)」，点击确认将直接覆盖原声音。", systemImage: "arrow.triangle.2.circlepath")
                    .font(.system(size: 12)).foregroundColor(UI.amber)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Label("槽位 \(selectedSlot) 当前空闲，可直接导入。", systemImage: "checkmark.circle")
                    .font(.system(size: 12)).foregroundColor(UI.accent)
            }

            if inFlight {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(model.audioImportFlowMessage.isEmpty ? "正在执行声音导入…" : model.audioImportFlowMessage)
                        .font(.system(size: 12)).foregroundColor(UI.ink)
                }
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(UI.accent.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            } else if physicalSucceeded, let exec = model.audioExecute {
                VStack(alignment: .leading, spacing: 4) {
                    Label("槽位 \(exec.target_slot ?? selectedSlot) 已写入「\(model.audioPlan?.candidate.filename ?? previewCameraFilename)」并通过独立回读验证。", systemImage: "checkmark.circle.fill")
                        .font(.system(size: 12, weight: .semibold)).foregroundColor(UI.accent)
                    if let notice = exec.restart_notice {
                        Text(notice)
                            .font(.system(size: 11)).foregroundColor(UI.accent)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if exec.backup_cleanup_failed == true {
                        Text(exec.cleanup_warning ?? "注意：临时备份槽位清理未确认。")
                            .font(.system(size: 11)).foregroundColor(UI.amber)
                    }
                }
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(UI.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            } else if let slot = demoInstalledSlot, !physical {
                Label("已演示安装「\(trimmedTargetName.isEmpty ? sound.title : trimmedTargetName)」到槽位 \(slot)。", systemImage: "checkmark.circle.fill")
                    .font(.system(size: 12, weight: .semibold)).foregroundColor(UI.accent)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(UI.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            } else if !installPrepareError.isEmpty {
                importError(installPrepareError)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(UI.amber.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            } else if physical && (!model.audioExecuteError.isEmpty || !model.audioPlanError.isEmpty || !model.audioConfirmationError.isEmpty) {
                let errText = !model.audioExecuteError.isEmpty ? model.audioExecuteError
                    : (!model.audioConfirmationError.isEmpty ? model.audioConfirmationError : model.audioPlanError)
                importError(errText)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(UI.amber.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }

            HStack {
                if !succeeded {
                    Button("取消") { onClose() }.keyboardShortcut(.cancelAction).disabled(inFlight)
                    Spacer()
                    Button(needsOverwriteConfirmation ? "确认覆盖并导入" : "确认导入") {
                        performInstall()
                    }
                    .buttonStyle(PrimaryStyle(destructive: needsOverwriteConfirmation))
                    .disabled(!model.canInstallAudioOnCamera || model.isBusy || inFlight
                              || (canRenameTargetFile && (trimmedTargetName.isEmpty || targetNameError != nil)))
                } else {
                    Spacer()
                    Button("完成", action: onClose).buttonStyle(PrimaryStyle())
                }
            }.padding(.top, 4)
        }
        .padding(24)
        .frame(width: 580)
        .foregroundColor(UI.ink)
        .onAppear {
            model.ensureAudioSlotsLoaded()
            if physical, !userSelectedSlot,
               let empty = model.audioCatalog?.slots?.first(where: { $0.status == "empty" })?.slot {
                selectedSlot = empty
            }
        }
        .onChange(of: model.audioCatalog?.slots?.map(\.status)) { _ in
            guard physical, !userSelectedSlot else { return }
            if let empty = model.audioCatalog?.slots?.first(where: { $0.status == "empty" })?.slot {
                selectedSlot = empty
            }
        }
        .onDisappear { session.stopPreview() }
    }

    private func slotButton(_ slot: Int) -> some View {
        let isSelected = selectedSlot == slot
        let label: String = {
            if physical {
                if let row = model.audioCatalog?.slots?.first(where: { $0.slot == slot }) {
                    return row.status == "empty" ? "[空]" : row.name
                }
                return "[未读取]"
            }
            return session.slots.first(where: { $0.id == slot })?.sound?.title ?? "[空]"
        }()
        let filled = label != "[空]" && label != "[未读取]"
        return Button {
            guard !inFlight else { return }
            userSelectedSlot = true
            if selectedSlot != slot {
                selectedSlot = slot
                installPrepareError = ""
                demoInstalledSlot = nil
                model.resetAudioImportFlow()
            }
        } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text("槽位 \(slot)")
                    .font(.system(size: 11, weight: .semibold).monospacedDigit())
                    .foregroundColor(isSelected ? UI.accent : UI.ink)
                Text(label)
                    .font(.system(size: 10))
                    .foregroundColor(filled ? UI.ink : UI.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 8).padding(.vertical, 7)
            .background(isSelected ? UI.accent.opacity(0.11) : Color.black.opacity(0.025),
                        in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8)
                .strokeBorder(isSelected ? UI.accent : UI.line, lineWidth: isSelected ? 1.5 : 1))
        }
        .buttonStyle(.plain)
        .disabled(inFlight)
    }

    private func importError(_ message: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").accessibilityHidden(true)
            Text(message)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 12))
        .foregroundColor(UI.amber)
    }

    private func performInstall() {
        session.stopPreview()
        demoInstalledSlot = nil
        installPrepareError = ""
        if physical {
            do {
                let validatedName = canRenameTargetFile
                    ? try AudioMarketSession.validatedCameraTargetName(trimmedTargetName)
                    : nil
                model.startMarketSoundImport(sound, slot: selectedSlot, targetName: validatedName)
            } catch {
                installPrepareError = error.localizedDescription
            }
        } else {
            let expectedID = session.slots.first(where: { $0.id == selectedSlot })?.sound?.id
            let displayTitle = trimmedTargetName.isEmpty ? sound.title : trimmedTargetName
            let demoSound = MarketSound(
                id: sound.id,
                title: displayTitle,
                subtitle: sound.subtitle,
                author: sound.author,
                durationMS: sound.durationMS,
                tags: sound.tags,
                source: sound.source
            )
            if session.installDemo(demoSound, slot: selectedSlot, replacing: expectedID) {
                demoInstalledSlot = selectedSlot
            }
        }
    }
}
