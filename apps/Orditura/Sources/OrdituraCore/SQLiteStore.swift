import Foundation
import SQLite3

/// Thrown when a store is absent, unreadable or shaped in a way this build does
/// not recognise. Every extractor throws rather than crashing, so one moved
/// column costs a source, not the run.
public enum StoreError: Error, LocalizedError, Equatable {
    case notFound(URL)
    case accessDenied(URL)
    case unreadable(URL, String)
    case unexpectedSchema(URL, String)

    public var errorDescription: String? {
        switch self {
        case .notFound(let url):
            return "Not present: \(url.path)"
        case .accessDenied(let url):
            return "Permission denied reading \(url.lastPathComponent). "
                 + "Grant Full Disk Access to Orditura in System Settings › "
                 + "Privacy & Security, then try again."
        case .unreadable(let url, let why):
            return "Cannot read \(url.lastPathComponent): \(why)"
        case .unexpectedSchema(let url, let why):
            return "Unrecognised schema in \(url.lastPathComponent): \(why). "
                 + "This macOS version may have changed the store layout."
        }
    }
}

/// A read-only view of one of the live macOS SQLite stores.
///
/// These databases are written continuously by system daemons and run in WAL
/// mode. Opening them in place, even read-only, can fail on a stale lock, and
/// `immutable=1` silently hides everything still sitting in the write-ahead
/// log. So the database is snapshotted together with its `-wal` and `-shm`
/// sidecars into a temporary directory and the copy is read instead: safe for
/// the live store, and complete.
public final class SQLiteStore {
    private var db: OpaquePointer?
    private let scratch: URL
    public let origin: URL

    public init(snapshotting url: URL) throws {
        self.origin = url
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) else { throw StoreError.notFound(url) }

        scratch = fm.temporaryDirectory
            .appendingPathComponent("orditura-\(UUID().uuidString)", isDirectory: true)
        do {
            try fm.createDirectory(at: scratch, withIntermediateDirectories: true)
            let target = scratch.appendingPathComponent(url.lastPathComponent)
            try fm.copyItem(at: url, to: target)
            for suffix in ["-wal", "-shm"] {
                let side = URL(fileURLWithPath: url.path + suffix)
                if fm.fileExists(atPath: side.path) {
                    try? fm.copyItem(at: side,
                                     to: URL(fileURLWithPath: target.path + suffix))
                }
            }
            var handle: OpaquePointer?
            guard sqlite3_open_v2(target.path, &handle, SQLITE_OPEN_READWRITE, nil) == SQLITE_OK,
                  handle != nil else {
                let message = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "open failed"
                sqlite3_close(handle)
                try? fm.removeItem(at: scratch)
                throw StoreError.unreadable(url, message)
            }
            db = handle
        } catch let error as StoreError {
            throw error
        } catch let error as NSError {
            try? fm.removeItem(at: scratch)
            if error.domain == NSCocoaErrorDomain,
               error.code == NSFileReadNoPermissionError || error.code == NSFileReadUnknownError {
                throw StoreError.accessDenied(url)
            }
            throw StoreError.unreadable(url, error.localizedDescription)
        }
    }

    deinit {
        if db != nil { sqlite3_close(db) }
        try? FileManager.default.removeItem(at: scratch)
    }

    // MARK: - Introspection
    //
    // None of these schemas is documented and all of them move between macOS
    // releases, so every query is built after asking what is actually there.

    public func tables() -> Set<String> {
        var names = Set<String>()
        try? query("SELECT name FROM sqlite_master WHERE type='table'") { row in
            if let n = row.string(0) { names.insert(n) }
        }
        return names
    }

    public func hasTables(_ required: String...) -> Bool {
        let present = tables()
        return required.allSatisfy { present.contains($0) }
    }

    public func columns(of table: String) -> Set<String> {
        var names = Set<String>()
        try? query("PRAGMA table_info(\(Self.quote(table)))") { row in
            if let n = row.string(1) { names.insert(n) }
        }
        return names
    }

    public func count(_ table: String) -> Int? {
        var n: Int?
        try? query("SELECT count(*) FROM \(Self.quote(table))") { row in n = row.int(0) }
        return n
    }

    /// Quote an identifier that came from the database itself. Table names are
    /// never user input here, but Core Data join tables have names like
    /// `Z_1INTERACTIONS` that must survive interpolation intact.
    public static func quote(_ identifier: String) -> String {
        "\"" + identifier.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }

    // MARK: - Query

    public struct Row {
        fileprivate let stmt: OpaquePointer

        public func isNull(_ i: Int32) -> Bool {
            sqlite3_column_type(stmt, i) == SQLITE_NULL
        }
        public func int(_ i: Int32) -> Int? {
            isNull(i) ? nil : Int(sqlite3_column_int64(stmt, i))
        }
        public func double(_ i: Int32) -> Double? {
            isNull(i) ? nil : sqlite3_column_double(stmt, i)
        }
        public func string(_ i: Int32) -> String? {
            switch sqlite3_column_type(stmt, i) {
            case SQLITE_NULL:
                return nil
            case SQLITE_BLOB:
                // Call history stores phone numbers as raw bytes.
                guard let bytes = sqlite3_column_blob(stmt, i) else { return nil }
                let count = Int(sqlite3_column_bytes(stmt, i))
                return String(data: Data(bytes: bytes, count: count), encoding: .utf8)
            default:
                guard let c = sqlite3_column_text(stmt, i) else { return nil }
                return String(cString: c)
            }
        }
    }

    public func query(_ sql: String,
                      _ bind: [Double] = [],
                      _ each: (Row) throws -> Void) throws {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let stmt = stmt else {
            let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "prepare failed"
            throw StoreError.unexpectedSchema(origin, message)
        }
        defer { sqlite3_finalize(stmt) }
        for (i, value) in bind.enumerated() {
            sqlite3_bind_double(stmt, Int32(i + 1), value)
        }
        while true {
            let rc = sqlite3_step(stmt)
            if rc == SQLITE_ROW {
                try each(Row(stmt: stmt))
            } else if rc == SQLITE_DONE {
                return
            } else {
                let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "step failed"
                throw StoreError.unreadable(origin, message)
            }
        }
    }
}

// MARK: - Timestamps

public enum AppleTime {
    /// Core Data and CFAbsoluteTime count from 2001-01-01, which is exactly
    /// Foundation's own reference date — so no magic constant is needed. The
    /// magnitude check disambiguates the seconds, microseconds and nanoseconds
    /// encodings that appear across stores and macOS versions (Messages moved
    /// to nanoseconds in 10.13).
    public static func fromReference(_ raw: Double?) -> Date? {
        guard var v = raw, v > 0 else { return nil }
        if v > 1e17 { v /= 1e9 } else if v > 1e14 { v /= 1e6 }
        return Date(timeIntervalSinceReferenceDate: v)
    }

    /// Mail's Envelope Index stores plain Unix seconds. Tolerate an
    /// Apple-epoch value anyway: anything before 1990 cannot be a real
    /// message date and is therefore the other encoding.
    public static func fromUnixOrReference(_ raw: Double?) -> Date? {
        guard let v = raw, v > 0 else { return nil }
        if v < 631_152_000 { return Date(timeIntervalSinceReferenceDate: v) }
        return Date(timeIntervalSince1970: v)
    }

    public static func referenceValue(of date: Date) -> Double {
        date.timeIntervalSinceReferenceDate
    }
}
