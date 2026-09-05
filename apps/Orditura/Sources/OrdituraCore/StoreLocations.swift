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

    /// Sort V9 before V10. Lexicographic order puts V10 first, which would make
    /// "the newest version directory" select the oldest one.
    static func naturalLess(_ a: String, _ b: String) -> Bool {
        a.compare(b, options: .numeric) == .orderedAscending
    }

    private static func children(of directory: URL) -> [URL] {
        (try? FileManager.default.contentsOfDirectory(at: directory,
                                                      includingPropertiesForKeys: nil)) ?? []
    }

    /// Candidate locations for a store, newest convention first.
    ///
    /// Apple moves these between releases — Calendar migrated into a group
    /// container, Mail's version directory changes every few majors — so each
    /// store is a list tried in order, and the access probe reports what it
    /// searched when nothing matches. A wrong guess about an undocumented
    /// location should be visible, not silent.
    public static func candidates(for store: Store, home: URL = StoreLocations.home) -> [URL] {
        let library = home.appendingPathComponent("Library", isDirectory: true)
        let appSupport = library.appendingPathComponent("Application Support", isDirectory: true)
        let groups = library.appendingPathComponent("Group Containers", isDirectory: true)

        switch store {
        case .mail:
            return children(of: library.appendingPathComponent("Mail", isDirectory: true))
                .filter { $0.lastPathComponent.hasPrefix("V") }
                .sorted { naturalLess($0.lastPathComponent, $1.lastPathComponent) }
                .map { $0.appendingPathComponent("MailData/Envelope Index") }

        case .imessage:
            return [library.appendingPathComponent("Messages/chat.db")]

        case .call:
            return [appSupport.appendingPathComponent("CallHistoryDB/CallHistory.storedata")]

        case .calendar:
            return [
                // Sonoma and later; the earlier location follows for older systems.
                groups.appendingPathComponent("group.com.apple.calendar/Calendar.sqlitedb"),
                library.appendingPathComponent("Calendars/Calendar.sqlitedb"),
                library.appendingPathComponent(
                    "Containers/com.apple.CalendarAgent/Data/Library/Calendars/Calendar.sqlitedb"),
            ] + children(of: groups)
                .sorted { naturalLess($0.lastPathComponent, $1.lastPathComponent) }
                .map { $0.appendingPathComponent("Calendar.sqlitedb") }

        case .contacts:
            let base = appSupport.appendingPathComponent("AddressBook", isDirectory: true)
            return [base.appendingPathComponent("AddressBook-v22.abcddb")]
                + children(of: base.appendingPathComponent("Sources", isDirectory: true))
                    .sorted { naturalLess($0.lastPathComponent, $1.lastPathComponent) }
                    .map { $0.appendingPathComponent("AddressBook-v22.abcddb") }

        case .coreduet:
            return [appSupport.appendingPathComponent("CoreDuet/People/interactionC.db")]
                + children(of: groups)
                    .map { $0.appendingPathComponent("CoreDuet/People/interactionC.db") }
        }
    }

    /// The databases actually present.
    ///
    /// Contacts is the one store where several files are genuinely different
    /// accounts and all of them count. Everywhere else a second match means a
    /// copy left behind by a macOS upgrade — two Mail version directories, a
    /// Calendar store in both its old and new home — and reading both would
    /// count every interaction twice. So contacts takes everything; the rest
    /// take the newest match only.
    public static func urls(for store: Store, home: URL = StoreLocations.home) -> [URL] {
        let fm = FileManager.default
        let present = candidates(for: store, home: home)
            .filter { fm.fileExists(atPath: $0.path) }
        if store == .contacts { return present }
        return present.last.map { [$0] } ?? []
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
