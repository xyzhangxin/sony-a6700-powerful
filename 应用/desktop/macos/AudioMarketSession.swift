import SwiftUI
import AppKit
@preconcurrency import AVFoundation
import UniformTypeIdentifiers

struct MarketSound: Identifiable {
    let id: String
    let title: String
    let subtitle: String
    let author: String
    let durationMS: Int
    let tags: [String]
    let source: Source

    enum Source {
        case sample(URL)
        case personal(URL)
        case catalog(package: String, sound: String)
    }

    var personal: Bool {
        if case .personal = source { return true }
        return false
    }

    var fileURL: URL? {
        switch source {
        case .sample(let url), .personal(let url): return url
        case .catalog: return nil
        }
    }
}

struct AudioTrimRequest: Identifiable {
    let id = UUID()
    let url: URL
    let duration: Double

    var title: String { url.deletingPathExtension().lastPathComponent }
}

struct AudioTrimSelection: Equatable {
    let duration: Double
    private(set) var start = 0.0
    private(set) var end: Double

    init(duration: Double) {
        self.duration = duration
        end = min(duration, AudioMarketSession.cameraClipDuration)
    }

    var range: Range<Double> { start..<end }
    private var minimumLength: Double { min(0.001, duration) }

    mutating func moveStart(to time: Double) {
        start = min(end - minimumLength, max(0, end - AudioMarketSession.cameraClipDuration, time))
    }

    mutating func moveEnd(to time: Double) {
        end = max(start + minimumLength, min(duration, start + AudioMarketSession.cameraClipDuration, time))
    }

    mutating func shift(by delta: Double) {
        let length = end - start
        start = min(max(0, duration - length), max(0, start + delta))
        end = min(duration, start + length)
    }
}

struct MarketSlot: Identifiable {
    let id: Int
    var sound: MarketSound?
    var label: String { "槽位 \(id)" }
}

@MainActor
final class AudioMarketSession: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var personalSounds: [MarketSound] = []
    @Published private(set) var demoSounds: [MarketSound] = []
    @Published private(set) var catalogSounds: [MarketSound] = []
    @Published private(set) var favorites: Set<String> = []
    @Published private(set) var slots = (1...10).map { MarketSlot(id: $0) }
    @Published private(set) var playingID: String?
    @Published private(set) var preparingID: String?
    @Published private(set) var loadingCatalog = false
    @Published var trimRequest: AudioTrimRequest?
    nonisolated static let cameraClipDuration = 0.350
    private static let cameraSampleRate = 24_000
    private static let cameraClipFrames = 8_400

    @Published var query = ""
    @Published var category = "全部声音"
    @Published var selectedSoundID: String?
    @Published var message = ""
    @Published var error = ""

    private struct ImportRecord: Codable {
        let title: String
        let subtitle: String
        let durationMS: Int
        let wavFilename: String?
    }

    private struct Preview: Decodable {
        let status: String
        let wav: Data?
        let error: String?
    }

    private let library: URL
    private let defaults: UserDefaults
    private var catalogLoaded = false
    private var player: AVAudioPlayer?
    private var previewTask: Task<Void, Never>?
    private var samplesDirectory: URL?

    init(library: URL? = nil, defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.library = library ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("a6700-unlock/audio-library", isDirectory: true)
        super.init()
        favorites = Set(defaults.stringArray(forKey: "audioMarketFavorites") ?? [])
        prepareSamples()
        loadImports()
    }

    deinit {
        if let samplesDirectory = samplesDirectory { try? FileManager.default.removeItem(at: samplesDirectory) }
    }

    var suggestedSlot: Int? { slots.first(where: { $0.sound == nil })?.id }

    func installedSlots(for sound: MarketSound) -> [Int] {
        slots.filter { $0.sound?.id == sound.id }.map(\.id)
    }

    func toggleFavorite(_ sound: MarketSound) {
        if !favorites.insert(sound.id).inserted { favorites.remove(sound.id) }
        defaults.set(favorites.sorted(), forKey: "audioMarketFavorites")
    }

    func installDemo(_ sound: MarketSound, slot: Int, replacing expectedID: String?) -> Bool {
        guard let index = slots.firstIndex(where: { $0.id == slot }) else { return false }
        guard slots[index].sound?.id == expectedID else {
            error = "槽位内容已变化，请重新确认安装。"
            return false
        }
        slots[index].sound = sound
        message = "已演示安装「\(sound.title)」到槽位 \(slot)"
        error = ""
        return true
    }

    func loadCatalog(refresh: Bool = false) async {
        prepareSamples()
        guard !loadingCatalog, refresh || !catalogLoaded else { return }
        loadingCatalog = true
        defer { loadingCatalog = false }
        do {
            let catalog: AudioCatalogResult = try await Self.request(["command": "list_audio"])
            guard catalog.status != "error" else { throw audioError("读取本机市场目录失败。") }
            catalogSounds = catalog.packages.flatMap { package in
                package.sounds.map { sound in
                    MarketSound(id: package.package_id + "/" + sound.sound_id, title: sound.title,
                                subtitle: sound.description, author: package.author,
                                durationMS: sound.duration_ms, tags: sound.tags,
                                source: .catalog(package: package.package_id, sound: sound.sound_id))
                }
            }
            catalogLoaded = true
        } catch { self.error = "市场目录读取失败，可刷新重试。" }
    }

    func upload() {
        let panel = NSOpenPanel()
        panel.title = "导入自定义快门声"
        panel.message = "支持 MP3、M4A、AAC、WAV、AIFF、FLAC 和 CAF。超过 350 毫秒时请选择要保留的片段。"
        panel.prompt = "选择音频"
        var allowed: [UTType] = [.wav, .mp3, .mpeg4Audio, .aiff, .audio]
        for identifier in ["public.aac-audio", "org.xiph.flac", "com.apple.coreaudio-format"] {
            if let type = UTType(identifier) { allowed.append(type) }
        }
        panel.allowedContentTypes = allowed
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try importLocal(url)
        } catch { self.error = error.localizedDescription }
    }

    func importLocal(_ url: URL) throws {
        let audio = try openImportAudio(url)
        let duration = Double(audio.length) / audio.processingFormat.sampleRate
        guard duration.isFinite, duration > 0 else {
            throw audioError("音频时长无效，无法导入。")
        }
        stopPreview()
        error = ""
        message = ""
        let request = AudioTrimRequest(url: url, duration: duration)
        if duration > Self.cameraClipDuration {
            trimRequest = request
        } else {
            try saveImport(request, range: 0..<duration)
        }
    }

    private func openImportAudio(_ url: URL) throws -> AVAudioFile {
        let ext = url.pathExtension.lowercased()
        let supportedExts: Set<String> = ["wav", "mp3", "m4a", "aac", "aiff", "aif", "flac", "caf"]
        guard supportedExts.contains(ext) else {
            throw audioError("不支持的音频格式「.\(ext.isEmpty ? "未知" : ext)」；请选择 MP3、M4A、AAC、WAV、AIFF、FLAC 或 CAF。")
        }
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard values.isRegularFile == true, let size = values.fileSize, size > 0, size <= 20 * 1024 * 1024 else {
            throw audioError("请选择有效的音频文件（大小不超过 20 MB）。")
        }
        let audio: AVAudioFile
        do {
            audio = try AVAudioFile(forReading: url)
        } catch {
            throw audioError("无法读取或解码该音频文件，请确认文件未损坏且为标准音频。")
        }
        let rate = audio.processingFormat.sampleRate
        guard rate.isFinite, rate > 0, audio.length > 0 else {
            throw audioError("音频文件为空或采样率无效，无法导入。")
        }
        return audio
    }

    private func cameraClip(_ request: AudioTrimRequest, range: Range<Double>, pad: Bool) throws -> Data {
        let audio = try openImportAudio(request.url)
        let rate = audio.processingFormat.sampleRate
        let first = min(audio.length, max(0, AVAudioFramePosition((range.lowerBound * rate).rounded())))
        let last = min(audio.length, AVAudioFramePosition((range.upperBound * rate).rounded()))
        guard last > first else { throw audioError("请先选择一段音频。") }
        audio.framePosition = first
        var remaining = last - first
        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16, sampleRate: Double(Self.cameraSampleRate),
            channels: 1, interleaved: true
        ), let converter = AVAudioConverter(from: audio.processingFormat, to: targetFormat),
            let input = AVAudioPCMBuffer(pcmFormat: audio.processingFormat, frameCapacity: 4096),
            let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: AVAudioFrameCount(Self.cameraClipFrames))
        else {
            throw audioError("无法准备相机音频转换。")
        }
        converter.primeMethod = .none
        converter.downmix = true

        var pcm = Data()
        var hasSignal = false
        var readError: Error?
        while pcm.count < Self.cameraClipFrames * 2 {
            output.frameLength = 0
            var convertError: NSError?
            let status = converter.convert(to: output, error: &convertError) { requested, inputStatus in
                guard remaining > 0 else {
                    inputStatus.pointee = .endOfStream
                    return nil
                }
                let count = AVAudioFrameCount(min(remaining, AVAudioFramePosition(min(requested, input.frameCapacity))))
                do {
                    try audio.read(into: input, frameCount: count)
                } catch {
                    readError = error
                    remaining = 0
                    inputStatus.pointee = .endOfStream
                    return nil
                }
                guard input.frameLength > 0 else {
                    remaining = 0
                    inputStatus.pointee = .endOfStream
                    return nil
                }
                remaining -= AVAudioFramePosition(input.frameLength)
                inputStatus.pointee = .haveData
                return input
            }
            if let error = readError ?? convertError {
                throw audioError("音频转换失败：\(error.localizedDescription)")
            }
            if status == .error {
                throw audioError("音频转换失败，请检查源文件编码。")
            }
            if let samples = output.int16ChannelData?[0] {
                let count = min(Int(output.frameLength), Self.cameraClipFrames - pcm.count / 2)
                for index in 0..<count {
                    let sample = samples[index]
                    hasSignal = hasSignal || sample != 0
                    var littleEndian = sample.littleEndian
                    withUnsafeBytes(of: &littleEndian) { pcm.append(contentsOf: $0) }
                }
            }
            if status == .endOfStream { break }
            if output.frameLength == 0 {
                if remaining == 0 { break }
                throw audioError("音频转换未生成采样，请尝试另一个片段。")
            }
        }
        guard hasSignal else {
            throw audioError("所选片段没有声音，请调整起点后重试。")
        }
        if pad { pcm.append(Data(count: Self.cameraClipFrames * 2 - pcm.count)) }
        return buildWavData(pcmData: pcm, sampleRate: Self.cameraSampleRate, channels: 1)
    }

    nonisolated static func trimWaveform(_ url: URL) async throws -> [Float] {
        let audio = try AVAudioFile(forReading: url, commonFormat: .pcmFormatFloat32, interleaved: false)
        let binCount = 320
        let capacity: AVAudioFrameCount = 8192
        guard audio.length > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: audio.processingFormat, frameCapacity: capacity)
        else { return [] }
        var peaks = [Float](repeating: 0, count: binCount)
        let framesPerBin = Double(audio.length) / Double(binCount)
        while audio.framePosition < audio.length {
            try Task.checkCancellation()
            let first = audio.framePosition
            try audio.read(into: buffer, frameCount: capacity)
            guard buffer.frameLength > 0, let channels = buffer.floatChannelData else { break }
            for frame in 0..<Int(buffer.frameLength) {
                let bin = min(binCount - 1, Int(Double(first + Int64(frame)) / framesPerBin))
                for channel in 0..<Int(buffer.format.channelCount) {
                    peaks[bin] = max(peaks[bin], abs(channels[channel][frame]))
                }
            }
        }
        let peak = max(0.01, peaks.max() ?? 0)
        return peaks.map { $0 / peak }
    }

    func previewTrim(_ request: AudioTrimRequest, range: Range<Double>) throws {
        stopPreview()
        try play(AVAudioPlayer(data: cameraClip(request, range: range, pad: false)), id: request.id.uuidString)
    }

    func trimPlaybackFraction(_ request: AudioTrimRequest) -> Double? {
        guard playingID == request.id.uuidString, let player, player.duration > 0 else { return nil }
        return min(1, max(0, player.currentTime / player.duration))
    }

    func saveTrim(_ request: AudioTrimRequest, range: Range<Double>) throws {
        try saveImport(request, range: range)
        trimRequest = nil
    }

    private func saveImport(_ request: AudioTrimRequest, range: Range<Double>) throws {
        let wavData = try cameraClip(request, range: range, pad: true)
        let id = UUID()
        let title = request.title
        let record = ImportRecord(title: title, subtitle: "24000 Hz · 16 位单声道",
                                  durationMS: 350, wavFilename: "audio.wav")
        let folder = library.appendingPathComponent(id.uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        do {
            try wavData.write(to: folder.appendingPathComponent("audio.wav"), options: .atomic)
            try JSONEncoder().encode(record).write(to: folder.appendingPathComponent("audio.json"), options: .atomic)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            throw error
        }
        personalSounds.insert(importedSound(id: id, record: record, folder: folder), at: 0)
        query = ""
        category = "我的音频"
        message = "「\(title)」已加入我的音频"
        error = ""
    }

    private func loadImports() {
        guard let folders = try? FileManager.default.contentsOfDirectory(
            at: library, includingPropertiesForKeys: [.creationDateKey], options: [.skipsHiddenFiles]
        ) else { return }
        let entries = folders.compactMap { folder -> (Date, MarketSound)? in
            guard let id = UUID(uuidString: folder.lastPathComponent),
                  FileManager.default.isReadableFile(atPath: folder.appendingPathComponent("audio.wav").path),
                  let data = try? Data(contentsOf: folder.appendingPathComponent("audio.json")),
                  let record = try? JSONDecoder().decode(ImportRecord.self, from: data) else { return nil }
            let date = (try? folder.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
            return (date, importedSound(id: id, record: record, folder: folder))
        }
        personalSounds = entries.sorted { $0.0 > $1.0 }.map { $0.1 }
    }

    private func importedSound(id: UUID, record: ImportRecord, folder: URL) -> MarketSound {
        let wavURL: URL
        if let filename = record.wavFilename, Self.isSafeWavFilename(filename) {
            let preferred = folder.appendingPathComponent(filename)
            wavURL = FileManager.default.isReadableFile(atPath: preferred.path)
                ? preferred
                : folder.appendingPathComponent("audio.wav")
        } else {
            wavURL = folder.appendingPathComponent("audio.wav")
        }
        return MarketSound(id: id.uuidString, title: record.title, subtitle: record.subtitle, author: "我的音频",
                           durationMS: record.durationMS, tags: [], source: .personal(wavURL))
    }

    static let maxCameraSlotNameBytes = 255

    static func isSafeWavFilename(_ name: String) -> Bool {
        guard !name.contains("..") else { return false }
        let pattern = #"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}\.wav$"#
        return name.range(of: pattern, options: .regularExpression) != nil
    }

    static func isValidCameraSlotScalar(_ scalar: UnicodeScalar) -> Bool {
        let value = scalar.value
        if value < 0x80 {
            return (value >= 48 && value <= 57)
                || (value >= 65 && value <= 90)
                || (value >= 97 && value <= 122)
                || value == 46 || value == 95 || value == 45 || value == 32
        }
        return !(
            (value >= 0x0080 && value <= 0x009F)
            || (value >= 0x200B && value <= 0x200F)
            || (value >= 0x202A && value <= 0x202E)
            || (value >= 0x2066 && value <= 0x2069)
            || (value >= 0xD800 && value <= 0xDFFF)
            || value == 0xFEFF
            || (value >= 0xFFFE && value <= 0xFFFF)
        )
    }

    static func cameraSlotNameError(_ raw: String) -> String? {
        if raw.isEmpty {
            return "请输入写入相机的槽位名称。"
        }
        if raw.hasPrefix(".") || raw.hasPrefix(" ") || raw.contains("..") {
            return "名称不能以 . 或空格开头，也不能包含 ..。"
        }
        for scalar in raw.unicodeScalars where !isValidCameraSlotScalar(scalar) {
            return "名称包含不支持的符号（支持中英文、数字、空格及 ._-）。"
        }
        if raw.utf8.count > maxCameraSlotNameBytes {
            return "名称 UTF-8 长度超过 255 字节，请缩短名称。"
        }
        return nil
    }

    static func sanitizeCameraSlotName(_ raw: String) -> String {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.lowercased().hasSuffix(".wav") {
            text = String(text.dropLast(4)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        var cleaned = String(text.unicodeScalars.filter { isValidCameraSlotScalar($0) })
        while cleaned.contains("..") {
            cleaned = cleaned.replacingOccurrences(of: "..", with: ".")
        }
        while let first = cleaned.first, first == "." || first == " " {
            cleaned.removeFirst()
        }
        cleaned = cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
        var byteCount = 0
        var kept = String.UnicodeScalarView()
        for scalar in cleaned.unicodeScalars {
            let nextBytes = String(scalar).utf8.count
            if byteCount + nextBytes > maxCameraSlotNameBytes {
                break
            }
            byteCount += nextBytes
            kept.append(scalar)
        }
        let clipped = String(kept).trimmingCharacters(in: .whitespacesAndNewlines)
        return clipped.isEmpty ? "自定义快门音" : clipped
    }

    static func defaultCameraTargetName(for sound: MarketSound) -> String {
        sanitizeCameraSlotName(sound.title)
    }

    static func validatedCameraTargetName(_ raw: String) throws -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if let message = cameraSlotNameError(trimmed) {
            throw NSError(domain: "PowerfulAudioMarket", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
        }
        return trimmed
    }

    func renamePersonalSound(_ sound: MarketSound, to newTitle: String) throws -> MarketSound {
        let trimmed = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        if let nameErr = Self.cameraSlotNameError(trimmed) {
            throw audioError(nameErr)
        }
        guard sound.personal, let id = UUID(uuidString: sound.id) else {
            throw audioError("仅支持重命名「我的音频」中的本地音频。")
        }
        let folder = library.appendingPathComponent(id.uuidString)
        let metaURL = folder.appendingPathComponent("audio.json")
        let metaData = try Data(contentsOf: metaURL)
        let oldRecord = try JSONDecoder().decode(ImportRecord.self, from: metaData)
        let targetURL = folder.appendingPathComponent("audio.wav")
        let sourceURL = sound.fileURL ?? targetURL
        if !FileManager.default.isReadableFile(atPath: targetURL.path), sourceURL != targetURL {
            let wavBytes = try Data(contentsOf: sourceURL)
            try wavBytes.write(to: targetURL, options: .atomic)
        }
        if let oldName = oldRecord.wavFilename, oldName != "audio.wav" {
            try? FileManager.default.removeItem(at: folder.appendingPathComponent(oldName))
        }
        let newRecord = ImportRecord(
            title: trimmed,
            subtitle: oldRecord.subtitle,
            durationMS: oldRecord.durationMS,
            wavFilename: "audio.wav"
        )
        try JSONEncoder().encode(newRecord).write(to: metaURL, options: .atomic)
        let updated = MarketSound(
            id: sound.id,
            title: trimmed,
            subtitle: newRecord.subtitle,
            author: sound.author,
            durationMS: newRecord.durationMS,
            tags: sound.tags,
            source: .personal(targetURL)
        )
        if let idx = personalSounds.firstIndex(where: { $0.id == sound.id }) {
            personalSounds[idx] = updated
        }
        for slotIdx in slots.indices where slots[slotIdx].sound?.id == sound.id {
            slots[slotIdx].sound = updated
        }
        message = "已将「\(sound.title)」重命名为「\(trimmed)」"
        error = ""
        return updated
    }

    func trashPersonalSound(_ sound: MarketSound) throws {
        guard sound.personal, let id = UUID(uuidString: sound.id),
              personalSounds.contains(where: { $0.id == sound.id })
        else { throw audioError("仅可删除「我的音频」中的本地音频。") }
        let folder = library.appendingPathComponent(id.uuidString, isDirectory: true)
        try FileManager.default.trashItem(at: folder, resultingItemURL: nil)
        if playingID == sound.id { stopPreview() }
        personalSounds.removeAll { $0.id == sound.id }
        if selectedSoundID == sound.id { selectedSoundID = nil }
        favorites.remove(sound.id)
        defaults.set(favorites.sorted(), forKey: "audioMarketFavorites")
        for index in slots.indices where slots[index].sound?.id == sound.id {
            slots[index].sound = nil
        }
        message = "已将「\(sound.title)」移到废纸篓"
        error = ""
    }

    func preview(_ sound: MarketSound) {
        let wasActive = playingID == sound.id || preparingID == sound.id
        stopPreview()
        guard !wasActive else { return }
        switch sound.source {
        case .sample(let url), .personal(let url):
            do { try play(AVAudioPlayer(contentsOf: url), id: sound.id) }
            catch { self.error = "试听失败，\(error.localizedDescription)" }
        case .catalog(let package, let id):
            preparingID = sound.id
            previewTask = Task {
                do {
                    let preview: Preview = try await Self.request([
                        "command": "preview_audio", "package_id": package, "sound_id": id
                    ])
                    guard !Task.isCancelled else { return }
                    preparingID = nil
                    guard preview.status == "ready", let wav = preview.wav else {
                        throw audioError(preview.error ?? "本机缓存中的试听音频暂不可用。")
                    }
                    try play(AVAudioPlayer(data: wav), id: sound.id)
                } catch {
                    guard !Task.isCancelled else { return }
                    preparingID = nil
                    self.error = "试听失败，\(error.localizedDescription)"
                }
            }
        }
    }

    private func play(_ audio: AVAudioPlayer, id: String) throws {
        audio.delegate = self
        guard audio.prepareToPlay(), audio.play() else { throw audioError("无法启动音频播放。") }
        player = audio
        playingID = id
        error = ""
    }

    func stopPreview() {
        previewTask?.cancel()
        previewTask = nil
        preparingID = nil
        player?.stop()
        player = nil
        playingID = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            guard player === self.player else { return }
            stopPreview()
        }
    }

    private nonisolated static func request<Result: Decodable>(_ payload: [String: String]) async throws -> Result {
        try await Task.detached(priority: .userInitiated) {
            let job = try DesktopWorkerMode.audioMarket.makeProcess()
            let request = Pipe()
            let response = Pipe()
            job.standardInput = request
            job.standardOutput = response
            job.standardError = FileHandle.nullDevice
            let data = try JSONEncoder().encode(payload) + Data([10])
            try job.run()
            defer {
                try? request.fileHandleForWriting.close()
                job.waitUntilExit()
            }
            try request.fileHandleForWriting.write(contentsOf: data)
            try request.fileHandleForWriting.close()
            let result = response.fileHandleForReading.readDataToEndOfFile()
            return try JSONDecoder().decode(Result.self, from: result)
        }.value
    }

    private func audioError(_ message: String) -> NSError {
        NSError(domain: "PowerfulAudioMarket", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    private func prepareSamples() {
        guard samplesDirectory == nil else { return }
        do {
            let folder = FileManager.default.temporaryDirectory
                .appendingPathComponent("powerful-audio-market-" + UUID().uuidString)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            samplesDirectory = folder
            let bundledSounds = [
                ("coin", "起跳吃金币", 350, "336BFC4B-BBDB-4285-ADDF-353B4D2223F9"),
                ("slap", "扇巴掌", 350, "9A2DB6E8-7A5F-4D5B-B57E-71323E52122A"),
                ("duck", "鸭叫", 350, "D0C80002-0000-4000-8000-000000000002"),
                ("beep", "嘟嘟", 200, "1C5A4886-53D4-4B3A-BEB2-31025EC74BB0"),
            ]
            for (filename, title, duration, id) in bundledSounds {
                guard let url = Bundle.main.url(forResource: filename, withExtension: "wav",
                                                subdirectory: "默认音频") else { continue }
                demoSounds.append(MarketSound(
                    id: id, title: title, subtitle: "24000 Hz · 16 位单声道", author: "Powerful6700",
                    durationMS: duration, tags: ["内置"], source: .sample(url)
                ))
            }
            let examples = [
                ("清脆单音", "短促、清晰的拍摄提示", "提示音", 880.0, 0.16),
                ("柔和双音", "两段柔和的确认声", "提示音", 520.0, 0.32),
                ("轻触快门", "低沉短音与快速衰减", "机械感", 160.0, 0.12),
            ]
            for (index, item) in examples.enumerated() {
                let url = folder.appendingPathComponent("demo-\(index).wav")
                try makeSample(url: url, frequency: item.3, duration: item.4, doubleTone: index == 1)
                demoSounds.append(MarketSound(
                    id: "demo-\(index)", title: item.0, subtitle: item.1, author: "Powerful6700 示例音频",
                    durationMS: Int(item.4 * 1000), tags: [item.2], source: .sample(url)
                ))
            }
        } catch { self.error = "示例音频准备失败，请尝试导入本地 WAV。" }
    }

    private func makeSample(url: URL, frequency: Double, duration: Double, doubleTone: Bool) throws {
        let rate = 24000
        let totalSamples = 8400
        let activeSamples = min(Int(Double(rate) * duration), totalSamples)
        var pcm = Data(capacity: totalSamples * 2)
        for index in 0..<totalSamples {
            let sampleValue: Int16
            if index < activeSamples {
                let time = Double(index) / Double(rate)
                let segment = doubleTone ? time.truncatingRemainder(dividingBy: duration / 2) : time
                let pitch = doubleTone && time > duration / 2 ? frequency * 1.25 : frequency
                let attack = min(segment * 800, 1)
                let val = sin(2 * Double.pi * pitch * time) * exp(-segment * 32) * attack * 0.3
                sampleValue = Int16(clamping: Int((val * 32767.0).rounded()))
            } else {
                sampleValue = 0
            }
            var le = sampleValue.littleEndian
            withUnsafeBytes(of: &le) { pcm.append(contentsOf: $0) }
        }
        let wav = buildWavData(pcmData: pcm, sampleRate: rate, channels: 1)
        try wav.write(to: url, options: .atomic)
    }

    private func buildWavData(pcmData: Data, sampleRate: Int, channels: Int) -> Data {
        let dataBytes = pcmData.count
        let blockAlign = channels * 2
        let byteRate = sampleRate * blockAlign
        var wav = Data(capacity: 44 + dataBytes)
        func appendASCII(_ s: String) { wav.append(contentsOf: s.utf8) }
        func appendU32(_ v: UInt32) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { wav.append(contentsOf: $0) }
        }
        func appendU16(_ v: UInt16) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { wav.append(contentsOf: $0) }
        }
        appendASCII("RIFF")
        appendU32(UInt32(36 + dataBytes))
        appendASCII("WAVE")
        appendASCII("fmt ")
        appendU32(16)
        appendU16(1)
        appendU16(UInt16(channels))
        appendU32(UInt32(sampleRate))
        appendU32(UInt32(byteRate))
        appendU16(UInt16(blockAlign))
        appendU16(16)
        appendASCII("data")
        appendU32(UInt32(dataBytes))
        wav.append(pcmData)
        return wav
    }
}
