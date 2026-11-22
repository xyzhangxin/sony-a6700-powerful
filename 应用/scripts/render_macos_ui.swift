import AppKit
import Foundation
import SwiftUI
import Vision

@main
struct MacOSUIRenderer {
    struct Page {
        let name: String
        let expected: [String]
        let width: CGFloat
        let height: CGFloat
        let makeView: (PreviewModel, Snapshot) -> AnyView
    }

    @MainActor static func main() {
        let arguments = CommandLine.arguments
        guard arguments.count == 3 else {
            FileHandle.standardError.write("usage: render_macos_ui <state-dir> <output-dir>\n".data(using: .utf8)!)
            exit(2)
        }
        let stateDirectory = URL(fileURLWithPath: arguments[1])
        let outputDirectory = URL(fileURLWithPath: arguments[2])
        try? FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
        NSApplication.shared.appearance = NSAppearance(named: .aqua)

        let pages = [
            Page(name: "notice", expected: ["A6700", "固件2.00", "设备与数据风险", "查看开源许可"],
                 width: 1004, height: 714) { _, snapshot in
                AnyView(OperationNoticeView(state: snapshot, onLicense: {}))
            },
            Page(name: "preparation", expected: ["FTP", "海量存储器", "自动关机", "USB"],
                 width: 1004, height: 714) { _, snapshot in
                AnyView(VStack(alignment: .leading, spacing: 18) {
                    PageTitle(title: "准备相机", subtitle: "按下面四项完成设置，最后连接数据线。已经准备好的话，确认后即可检查。")
                    PreparationGuideView(steps: snapshot.steps)
                })
            },
            Page(name: "home-enabled", expected: ["FTP", "快门", "光圈", "电子快门声音", "自定义快门声", "去快门声市场", "FL2", "主列表和编号", "未开放"],
                 width: 1004, height: 760) { model, _ in
                AnyView(WorkspaceView(model: model, physical: false) {})
            },
            Page(name: "home-original", expected: ["FTP", "快门", "曝光", "电子快门声音", "自定义快门声", "未解锁", "去快门声市场", "FL2", "创意外观", "未开放"],
                 width: 1004, height: 760) { model, _ in
                AnyView(WorkspaceView(model: model, physical: false) {})
            },
            Page(name: "home-single-selection", expected: ["FTP", "快门", "演示"],
                 width: 1004, height: 760) { model, _ in
                AnyView(WorkspaceView(model: model, physical: false) {})
            },
            Page(name: "result-success", expected: ["原已解锁", "MENU", "成功标准"],
                 width: 1004, height: 714) { model, _ in
                AnyView(OperationSuccessView(model: model, physical: false))
            },
            Page(name: "result-close", expected: ["已关闭", "自定义快门声", "csnd", "MENU"],
                 width: 1004, height: 780) { model, _ in
                AnyView(OperationSuccessView(model: model, physical: false))
            },
            Page(name: "result-uncertain", expected: ["演示"],
                 width: 1004, height: 714) { model, _ in
                AnyView(OperationFailureView(model: model, physical: false))
            },
            Page(name: "result-verification-failed", expected: ["演示"],
                 width: 1004, height: 714) { model, _ in
                AnyView(OperationFailureView(model: model, physical: false))
            },
            Page(name: "result-rollback", expected: ["演示"],
                 width: 1004, height: 714) { model, _ in
                AnyView(OperationFailureView(model: model, physical: false))
            },
            Page(name: "demo-settings", expected: ["模拟断开连接"],
                 width: 350, height: 240) { model, _ in
                AnyView(DemoSettingsPanel(model: model, close: {}))
            },
            Page(name: "feedback", expected: ["意见反馈", "图片"],
                 width: 780, height: 620) { model, _ in
                AnyView(DiagnosticsView(model: model, physical: false))
            },
            Page(name: "audio-market-disconnected", expected: ["快门声市场", "未连接", "导入音频", "连接后检查"],
                 width: 1004, height: 720) { model, _ in
                AnyView(ShutterSoundMarketView(
                    session: AudioMarketSession(),
                    model: model,
                    physical: false,
                    onConnect: {},
                    onGoToFeatures: {},
                    onInstall: { _ in }
                ))
            },
            Page(name: "audio-market", expected: ["快门声市场", "已连接", "电子快门声音", "自定义快门声", "已解锁", "导入音频"],
                 width: 1004, height: 720) { model, _ in
                AnyView(ShutterSoundMarketView(
                    session: AudioMarketSession(),
                    model: model,
                    physical: false,
                    onConnect: {},
                    onGoToFeatures: {},
                    onInstall: { _ in }
                ))
            },
            Page(name: "audio-install", expected: ["安装到相机槽位", "写入名称", "槽位 1", "原厂", "确认导入"],
                 width: 580, height: 540) { model, _ in
                let session = AudioMarketSession()
                let sound = session.demoSounds.first ?? MarketSound(
                    id: "sample", title: "清脆单音", subtitle: "测试", author: "示例",
                    durationMS: 160, tags: [], source: .catalog(package: "demo", sound: "sample")
                )
                return AnyView(ShutterSoundInstallView(
                    session: session,
                    model: model,
                    sound: sound,
                    physical: false,
                    onClose: {}
                ))
            },
            Page(name: "license", expected: ["GPL-3.0-only", "关闭"],
                 width: 720, height: 560, makeView: { _, _ in AnyView(LicenseView(close: {})) }),
        ]

        var results: [[String: Any]] = []
        for page in pages {
            let stateURL = stateDirectory.appendingPathComponent(page.name + ".json")
            guard let stateData = try? Data(contentsOf: stateURL),
                  let snapshot = try? JSONDecoder().decode(Snapshot.self, from: stateData) else {
                FileHandle.standardError.write("state missing or invalid: \(page.name)\n".data(using: .utf8)!)
                exit(3)
            }
            let model = PreviewModel()
            model.state = snapshot
            model.feedbackDescription = "选择 FL2／FL3 后相机重启。"
            let rootView = page.makeView(model, snapshot)
                .frame(width: page.width, height: page.height)
                .foregroundColor(UI.ink)
                .background(UI.background)
                .preferredColorScheme(.light)
            let host = NSHostingView(rootView: rootView)
            host.appearance = NSAppearance(named: .aqua)
            host.frame = NSRect(x: 0, y: 0, width: page.width, height: page.height)
            host.layoutSubtreeIfNeeded()
            guard let representation = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
                FileHandle.standardError.write("bitmap creation failed: \(page.name)\n".data(using: .utf8)!)
                exit(4)
            }
            host.cacheDisplay(in: host.bounds, to: representation)
            guard let png = representation.representation(using: .png, properties: [:]) else {
                FileHandle.standardError.write("PNG encoding failed: \(page.name)\n".data(using: .utf8)!)
                exit(5)
            }
            let pngURL = outputDirectory.appendingPathComponent(page.name + ".png")
            try! png.write(to: pngURL)

            let recognized = recognizeText(imageURL: pngURL)
            try! recognized.data(using: .utf8)!.write(to: outputDirectory.appendingPathComponent(page.name + ".ocr.txt"))
            let normalizedRecognized = normalized(recognized)
            let missing = page.expected.filter { !normalizedRecognized.contains(normalized($0)) }
            results.append([
                "page": page.name,
                "expected": page.expected,
                "recognized": recognized,
                "missing": missing,
                "passed": missing.isEmpty,
                "png_bytes": png.count,
            ])
            print("\(page.name): \(missing.isEmpty ? "PASS" : "FAIL")")
        }

        let summary: [String: Any] = [
            "generated_at": ISO8601DateFormatter().string(from: Date()),
            "renderer": "NSHostingView + Vision OCR",
            "device_access": false,
            "write_attempted": false,
            "pages": results,
        ]
        let json = try! JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys])
        try! json.write(to: outputDirectory.appendingPathComponent("visual-validation.json"))
        if results.contains(where: { ($0["passed"] as? Bool) != true }) { exit(6) }
    }

    static func recognizeText(imageURL: URL) -> String {
        guard let image = NSImage(contentsOf: imageURL),
              let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return "" }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
            return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
        } catch {
            return ""
        }
    }

    static func normalized(_ text: String) -> String {
        var result = ""
        for character in text.lowercased() where character.isLetter || character.isNumber {
            result.append(character)
        }
        return result
    }
}
