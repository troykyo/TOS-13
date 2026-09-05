import SwiftUI
import AppKit
import OrdituraCore

/// The permission gate.
///
/// There is no API to request Full Disk Access — macOS grants it only by hand,
/// and only per application. So the most an app can honestly do is detect the
/// failure precisely, name the store it could not read, and take the user to
/// the right pane. Anything more confident would be a lie.
struct AccessView: View {
    @EnvironmentObject private var model: AppModel

    private var settingsURL: URL? {
        URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Orditura needs Full Disk Access")
                    .font(.title2).bold()
                Text("Your contact graph is derived from stores macOS keeps under "
                     + "~/Library. Everything is read-only and stays on this Mac — "
                     + "nothing is uploaded, and message content is never read.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(model.statuses) { status in
                        StoreStatusRow(status: status)
                    }
                }
                .padding(6)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("To grant it").font(.headline)
                Text("1.  Open System Settings › Privacy & Security › Full Disk Access\n"
                     + "2.  Add Orditura and switch it on\n"
                     + "3.  Quit and reopen Orditura — the grant is read at launch")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            HStack {
                if let settingsURL = settingsURL {
                    Button("Open Privacy & Security") {
                        NSWorkspace.shared.open(settingsURL)
                    }
                    .keyboardShortcut(.defaultAction)
                }
                Button("Check Again") { model.probe() }
                Spacer()
                Button("Continue Without It") { model.refresh() }
            }
        }
        .padding(28)
    }
}

struct StoreStatusRow: View {
    let status: StoreStatus

    private var symbol: (name: String, colour: Color) {
        switch status.state {
        case .readable:  return ("checkmark.circle.fill", .green)
        case .denied:    return ("lock.fill", .orange)
        case .absent:    return ("minus.circle", .secondary)
        case .failed:    return ("exclamationmark.triangle.fill", .yellow)
        }
    }

    private var detail: String {
        switch status.state {
        case .readable(let rows):
            return "\(rows) records"
        case .denied:
            return "Blocked — needs Full Disk Access"
        case .absent:
            return "Not present on this Mac"
        case .failed(let why):
            return why
        }
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: symbol.name)
                .foregroundStyle(symbol.colour)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(status.store.title).bold()
                    if status.store.isOptIn {
                        Text("opt-in")
                            .font(.caption2)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Color.secondary.opacity(0.15), in: Capsule())
                    }
                }
                Text(detail).font(.callout).foregroundStyle(.secondary)
                Text(status.store.explanation)
                    .font(.caption).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
        }
    }
}
