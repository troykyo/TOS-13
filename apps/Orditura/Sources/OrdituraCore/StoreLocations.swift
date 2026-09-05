import Foundation

/// Where macOS keeps the six stores that describe who you are in touch with.
public enum Store: String, CaseIterable, Identifiable {
    case mail, imessage, call, calendar, contacts, coreduet

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .mail:     return "Mail"
        case .imessage: return "Messages"
        case .call:     return "Call history"
        case .calendar: return "Calendar"
        case .contacts: return "Contacts"
        case .coreduet: return "Siri interactions"
        }
    }

    public var explanation: String {
        switch self {
        case .mail:
            return "Direction, recipients and audience size. Empty if you use "
                 + "Spark, Outlook or a browser client."
        case .imessage:
            return "iMessage and SMS, including group membership."
        case .call:
            return "FaceTime and iPhone calls, synced through Continuity."
        case .calendar:
            return "Meeting co-attendance — the strongest professional signal."
        case .contacts:
            return "Names, organisations, titles, and any LinkedIn URLs you already hold."
        case .coreduet:
            return "Apple's own interaction ledger, behind Siri's people "
                 + "suggestions. Undocumented and version-dependent, so it is "
                 + "off by default and treated as corroboration."
        }
    }

    /// True for the stores gated by Full Disk Access. Contacts is reachable
    /// through the ordinary Contacts permission instead.
    public var needsFullDiskAccess: Bool { self != .contacts }

    /// Off unless explicitly enabled: see `explanation`.
    public var isOptIn: Bool { self == .coreduet }

    public func locations(home: URL = StoreLocations.home) -> [URL] {
        StoreLocations.urls(for: self, home: home)
    }
}

public enum StoreLocations {
    public static var home: URL {
        URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
    }

    public static func urls(for store: Store, home: URL = StoreLocations.home) -> [URL] {
        let library = home.appendingPathComponent("Library", isDirectory: true)
        let appSupport = library.appendingPathComponent("Application Support", isDirectory: true)
        let fm = FileManager.default

        func existing(_ url: URL) -> [URL] {
            fm.fileExists(atPath: url.path) ? [url] : []
        }

        switch store {
        case .mail:
            // The container version changes with macOS: V9, V10, V11…
            let mail = library.appendingPathComponent("Mail", isDirectory: true)
            guard let versions = try? fm.contentsOfDirectory(
                at: mail, includingPropertiesForKeys: nil) else { return [] }
            return versions
                .filter { $0.lastPathComponent.hasPrefix("V") }
                .sorted { $0.lastPathComponent < $1.lastPathComponent }
                .flatMap { existing($0.appendingPathComponent("MailData/Envelope Index")) }

        case .imessage:
            return existing(library.appendingPathComponent("Messages/chat.db"))

        case .call:
            return existing(appSupport.appendingPathComponent(
                "CallHistoryDB/CallHistory.storedata"))

        case .calendar:
            return existing(library.appendingPathComponent("Calendars/Calendar.sqlitedb"))

        case .contacts:
            // The top-level database plus one per configured account source.
            let base = appSupport.appendingPathComponent("AddressBook", isDirectory: true)
            var found = existing(base.appendingPathComponent("AddressBook-v22.abcddb"))
            let sources = base.appendingPathComponent("Sources", isDirectory: true)
            if let dirs = try? fm.contentsOfDirectory(at: sources,
                                                      includingPropertiesForKeys: nil) {
                for dir in dirs.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
                    found += existing(dir.appendingPathComponent("AddressBook-v22.abcddb"))
                }
            }
            return found

        case .coreduet:
            return existing(appSupport.appendingPathComponent(
                "CoreDuet/People/interactionC.db"))
        }
    }
}

/// What a probe of one store found. Drives the onboarding screen: the app
/// cannot request Full Disk Access programmatically, so the most it can do is
/// detect the failure precisely and explain the remedy.
public struct StoreStatus: Identifiable {
    public enum State: Equatable {
        case absent
        case readable(rows: Int)
        case denied
        case failed(String)
    }

    public let store: Store
    public let url: URL?
    public let state: State

    public var id: String { store.rawValue + (url?.path ?? "") }

    public var isUsable: Bool {
        if case .readable = state { return true }
        return false
    }

    public init(store: Store, url: URL?, state: State) {
        self.store = store
        self.url = url
        self.state = state
    }
}

public enum StoreProbe {
    /// The row count that best indicates whether a store carries anything
    /// useful, per store.
    private static func principalTable(_ store: Store, in db: SQLiteStore) -> String? {
        let present = db.tables()
        let candidates: [String]
        switch store {
        case .mail:     candidates = ["messages"]
        case .imessage: candidates = ["message"]
        case .call:     candidates = ["ZCALLRECORD"]
        case .calendar: candidates = ["CalendarItem"]
        case .contacts: candidates = ["ZABCDRECORD"]
        case .coreduet: candidates = ["ZINTERACTIONS"]
        }
        return candidates.first(where: present.contains)
    }

    public static func probe(_ store: Store, home: URL = StoreLocations.home) -> [StoreStatus] {
        let urls = store.locations(home: home)
        guard !urls.isEmpty else {
            return [StoreStatus(store: store, url: nil, state: .absent)]
        }
        return urls.map { url in
            do {
                let db = try SQLiteStore(snapshotting: url)
                guard let table = principalTable(store, in: db) else {
                    return StoreStatus(store: store, url: url,
                                       state: .failed("unrecognised schema"))
                }
                return StoreStatus(store: store, url: url,
                                   state: .readable(rows: db.count(table) ?? 0))
            } catch StoreError.accessDenied(_) {
                return StoreStatus(store: store, url: url, state: .denied)
            } catch {
                return StoreStatus(store: store, url: url,
                                   state: .failed(error.localizedDescription))
            }
        }
    }

    public static func probeAll(home: URL = StoreLocations.home) -> [StoreStatus] {
        Store.allCases.flatMap { probe($0, home: home) }
    }
}
