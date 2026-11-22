import Foundation

enum DesktopWorkerMode {
    case camera, simulation, audioMarket

    func makeProcess(postUpdateStart: Bool = false) throws -> Process {
        guard let resources = Bundle.main.resourceURL else {
            throw error("应用资源缺失。")
        }
        let config = try JSONSerialization.jsonObject(
            with: Data(contentsOf: resources.appendingPathComponent("runtime.json"))
        ) as? [String: String]
        guard let configuredPython = config?["python"] else {
            throw error("应用运行配置缺失。")
        }
        let bundled = config?["kind"] == "bundled"
        let python = bundled
            ? resources.appendingPathComponent(configuredPython).standardizedFileURL.path
            : configuredPython
        guard (!bundled || python.hasPrefix(resources.standardizedFileURL.path + "/")),
              FileManager.default.isExecutableFile(atPath: python) else {
            throw error("本机开发运行环境已移动。请重新构建桌面预览。")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        if bundled {
            process.arguments = ["-I", "-S", "-B", "-u", resources.appendingPathComponent("worker.py").path]
            switch self {
            case .camera: process.arguments?.append("--physical")
            case .audioMarket: process.arguments?.append("--audio-market")
            case .simulation: break
            }
        } else {
            let module: String
            switch self {
            case .camera: module = "physical_bridge"
            case .simulation: module = "bridge"
            case .audioMarket: module = "audio_market"
            }
            process.arguments = ["-u", "-m", "a6700_unlock.desktop." + module]
        }
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = resources.appendingPathComponent("python").path
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if environment["A6700_AUDIO_IMPORT_PREVIEW"] == nil {
            environment["A6700_AUDIO_IMPORT_PREVIEW"] = "1"
        }
        if postUpdateStart {
            environment["POWERFUL6700_POST_UPDATE_START"] = "1"
        }
        process.environment = environment
        return process
    }

    private func error(_ description: String) -> NSError {
        NSError(domain: "A6700Preview", code: 1, userInfo: [NSLocalizedDescriptionKey: description])
    }
}
