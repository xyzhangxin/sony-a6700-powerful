import AppKit
import CryptoKit
import Foundation

private enum InstallError: LocalizedError {
    case invalid, signature, target, cancelled, launch, recovery
    var errorDescription: String? {
        switch self {
        case .invalid: "新版安装包格式或版本不符合要求。"
        case .signature: "新版安装包的项目签名未通过验证。"
        case .target: "无法在应用所在位置安装更新。请确认目录可写。"
        case .cancelled: "已取消自动安装，当前版本可继续使用。"
        case .launch: "新版未通过启动检查，已恢复上一版。"
        case .recovery: "更新被中断，上一版安装包已保留在应用所在目录。"
        }
    }
}

private let fm = FileManager.default
private let projectID = "org.a6700unlock.desktop"
private let maximumArchiveSize: UInt64 = 512 * 1024 * 1024

private func versionParts(_ value: String) -> [Int]? {
    let parts = value.split(separator: ".", omittingEmptySubsequences: false)
    guard parts.count == 3 else { return nil }
    let numbers = parts.compactMap { part -> Int? in
        guard let number = Int(part), 0...999_999 ~= number, String(number) == part else { return nil }
        return number
    }
    return numbers.count == 3 ? numbers : nil
}

private func packageInfo(_ url: URL) throws -> (String, String) {
    let file = url.appendingPathComponent("Contents/Info.plist")
    guard let info = try PropertyListSerialization.propertyList(from: Data(contentsOf: file), format: nil)
            as? [String: Any], let identifier = info["CFBundleIdentifier"] as? String,
        let version = info["CFBundleShortVersionString"] as? String else { throw InstallError.invalid }
    return (identifier, version)
}

private func verifyArchive(_ archive: URL, signature: URL, key: URL,
                           identifier: String, version: String) throws {
    let attributes = try archive.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
    guard attributes.isRegularFile == true, attributes.isSymbolicLink != true,
          let length = attributes.fileSize, length > 0, UInt64(length) <= maximumArchiveSize else { throw InstallError.invalid }
    let source = try FileHandle(forReadingFrom: archive)
    defer { try? source.close() }
    var hash = SHA256()
    while let chunk = try source.read(upToCount: 1024 * 1024), !chunk.isEmpty { hash.update(data: chunk) }
    let digest = hash.finalize().map { String(format: "%02x", $0) }.joined()
    let publicText = try String(contentsOf: key, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
    guard let publicKey = Data(base64Encoded: publicText), try signature.resourceValues(forKeys: [.fileSizeKey]).fileSize == 64 else { throw InstallError.signature }
    let message = Data("Powerful6700.AppUpdate/v1\nmacOS-arm64\n\(identifier)\n\(version)\n\(length)\n\(digest)\n".utf8)
    let signer = try Curve25519.Signing.PublicKey(rawRepresentation: publicKey)
    guard signer.isValidSignature(try Data(contentsOf: signature), for: message) else { throw InstallError.signature }
}

private func tool(_ executable: String, _ args: [String]) throws {
    let command = Process()
    command.executableURL = URL(fileURLWithPath: executable)
    command.arguments = args
    command.standardOutput = FileHandle.nullDevice
    command.standardError = FileHandle.nullDevice
    try command.run()
    command.waitUntilExit()
    if command.terminationStatus != 0 { throw InstallError.invalid }
}

private func wait(_ seconds: TimeInterval, until condition: () -> Bool) -> Bool {
    let deadline = ProcessInfo.processInfo.systemUptime + seconds
    repeat {
        if condition() { return true }
        if !RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1)) {
            Thread.sleep(forTimeInterval: 0.05)
        }
    } while ProcessInfo.processInfo.systemUptime < deadline
    return condition()
}

private func alive(_ app: NSRunningApplication) -> Bool {
    kill(app.processIdentifier, 0) == 0 || errno == EPERM
}

private func launch(_ url: URL, arguments: [String]) throws -> NSRunningApplication? {
    let config = NSWorkspace.OpenConfiguration()
    config.createsNewApplicationInstance = true
    config.arguments = arguments
    var result: NSRunningApplication?
    var returned = false
    NSWorkspace.shared.openApplication(at: url, configuration: config) { app, _ in
        DispatchQueue.main.async { result = app; returned = true }
    }
    guard wait(15, until: { returned }) else { throw InstallError.recovery }
    return result
}

private func install() throws {
    let args = CommandLine.arguments
    guard args.count == 5, let pid = Int32(args[1]), pid == getppid(),
          let parent = NSRunningApplication(processIdentifier: pid) else { throw InstallError.target }
    let helper = URL(fileURLWithPath: args[0]).resolvingSymlinksInPath()
    let installed = helper.deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    guard installed.pathExtension == "app", installed.path == parent.bundleURL?.resolvingSymlinksInPath().path,
          fm.isWritableFile(atPath: installed.deletingLastPathComponent().path) else { throw InstallError.target }
    let (identifier, current) = try packageInfo(installed)
    guard identifier == projectID || identifier.hasPrefix(projectID + "."),
          let oldVersion = versionParts(current), let offered = versionParts(args[4]),
          oldVersion.lexicographicallyPrecedes(offered) else { throw InstallError.invalid }

    let token = UUID()
    let health = try AppUpdateHealth.directory(for: token)
    let staging = try fm.url(for: .itemReplacementDirectory, in: .userDomainMask,
                             appropriateFor: installed, create: true)
    var keepHealth = false
    defer {
        try? fm.removeItem(at: staging)
        if !keepHealth { try? fm.removeItem(at: health) }
    }
    let originalArchive = URL(fileURLWithPath: args[2]); let signature = URL(fileURLWithPath: args[3])
    guard let archiveSize = try originalArchive.resourceValues(forKeys: [.fileSizeKey]).fileSize,
          archiveSize > 0, UInt64(archiveSize) <= maximumArchiveSize else { throw InstallError.invalid }
    let archive = staging.appendingPathComponent("verified.zip")
    try fm.copyItem(at: originalArchive, to: archive)
    let key = helper.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("app-update-public-key.txt")
    try verifyArchive(archive, signature: signature, key: key, identifier: identifier, version: args[4])
    let expanded = staging.appendingPathComponent("expanded", isDirectory: true)
    try fm.createDirectory(at: expanded, withIntermediateDirectories: false)
    try tool("/usr/bin/ditto", ["-x", "-k", archive.path, expanded.path])
    let files = try fm.contentsOfDirectory(at: expanded, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey], options: [.skipsHiddenFiles])
    guard files.count == 1, let next = files.first, next.pathExtension == "app" else { throw InstallError.invalid }
    let nextValues = try next.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
    let (nextID, nextVersion) = try packageInfo(next)
    guard nextValues.isDirectory == true, nextValues.isSymbolicLink != true,
          nextID == identifier, nextVersion == args[4] else { throw InstallError.invalid }
    try tool("/usr/bin/codesign", ["--verify", "--deep", "--strict", next.path])
    try fm.createDirectory(at: health, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    FileHandle.standardOutput.write(Data("prepared\n".utf8))
    try FileHandle.standardOutput.close()
    guard readLine() == "install", wait(15, until: { getppid() != pid }) else { throw InstallError.cancelled }

    let backupName = ".Powerful6700-rollback-\(token.uuidString).app"
    let backup = installed.deletingLastPathComponent().appendingPathComponent(backupName, isDirectory: true)
    _ = try fm.replaceItemAt(installed, withItemAt: next, backupItemName: backupName,
                             options: [.usingNewMetadataOnly, .withoutDeletingBackupItem])
    let child = try launch(installed, arguments: [AppUpdateHealth.option, token.uuidString])
    let healthReceipt = health.appendingPathComponent("ready")
    if let child, wait(25, until: {
        alive(child) && (try? String(contentsOf: healthReceipt, encoding: .utf8)) == String(child.processIdentifier)
    }) {
        try Data().write(to: health.appendingPathComponent("approved"), options: .atomic)
        keepHealth = true
        try? fm.removeItem(at: backup)
        return
    }

    if let child, alive(child) {
        _ = child.terminate()
        guard wait(8, until: { !alive(child) }) else { throw InstallError.recovery }
    }
    _ = try fm.replaceItemAt(installed, withItemAt: backup, backupItemName: nil, options: .usingNewMetadataOnly)
    _ = try launch(installed, arguments: [AppUpdateHealth.rollbackOption])
    throw InstallError.launch
}

do { try install() }
catch {
    let message = (error as? InstallError)?.localizedDescription ?? "应用更新未完成，请检查安装目录或重新下载。"
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}
