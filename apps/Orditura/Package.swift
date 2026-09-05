// swift-tools-version:5.7
import PackageDescription

let package = Package(
    name: "Orditura",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "OrdituraCore", targets: ["OrdituraCore"]),
        .executable(name: "Orditura", targets: ["Orditura"]),
    ],
    targets: [
        // Pure Foundation + SQLite3. No AppKit, no SwiftUI, no Contacts.framework,
        // so the whole of Stage A is testable from the command line without a
        // running app and without any TCC prompt beyond file access.
        .target(name: "OrdituraCore"),
        .executableTarget(name: "Orditura", dependencies: ["OrdituraCore"]),
        .testTarget(name: "OrdituraCoreTests", dependencies: ["OrdituraCore"]),
    ]
)
