import Foundation

// Shared by the update helper and the newly launched app. The argument is an
// opaque UUID. All filesystem paths are determined locally.
enum AppUpdateHealth {
    static let option = "--app-update-health"
    static let rollbackOption = "--app-update-rolled-back"

    static var launchToken: UUID? {
        let args = ProcessInfo.processInfo.arguments
        guard let index = args.firstIndex(of: option), args.indices.contains(index + 1) else { return nil }
        return UUID(uuidString: args[index + 1])
    }

    static func directory(for token: UUID) throws -> URL {
        let caches = try FileManager.default.url(for: .cachesDirectory, in: .userDomainMask,
                                                 appropriateFor: nil, create: true)
        return caches.appendingPathComponent("org.a6700unlock.app-update", isDirectory: true)
            .appendingPathComponent(token.uuidString, isDirectory: true)
    }

    static func ready(_ token: UUID, pid: Int32) throws {
        let job = try directory(for: token)
        let values = try job.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else { throw CocoaError(.fileNoSuchFile) }
        try Data(String(pid).utf8).write(to: job.appendingPathComponent("ready"), options: .atomic)
    }
}
