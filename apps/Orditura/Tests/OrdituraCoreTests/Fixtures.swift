import Foundation
import SQLite3
import XCTest
@testable import OrdituraCore

/// The macOS stores cannot exist on a build machine, so every extractor is
/// exercised against a synthetic SQLite file built to the same column shape as
/// the real store. That verifies the SQL, the epoch handling, the direction
/// logic and the scoring. It cannot verify that Apple has not renamed a column
/// in the next release — which is what schema introspection and `StoreProbe`
/// are for.
enum Fixture {
    enum Failure: Error { case sqlite(String) }

    static func write(_ url: URL, schema: String, seed: [String] = []) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var handle: OpaquePointer?
        guard sqlite3_open(url.path, &handle) == SQLITE_OK, let db = handle else {
            sqlite3_close(handle)
            throw Failure.sqlite("cannot create \(url.path)")
        }
        defer { sqlite3_close(db) }
        for statement in [schema] + seed {
            var error: UnsafeMutablePointer<CChar>?
            if sqlite3_exec(db, statement, nil, nil, &error) != SQLITE_OK {
                let message = error.map { String(cString: $0) } ?? "exec failed"
                sqlite3_free(error)
                throw Failure.sqlite(message)
            }
        }
    }

    // MARK: Schemas, matching the real stores column for column

    static let mail = """
        CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
        CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, sender INTEGER, subject INTEGER,
                               date_sent INTEGER, date_received INTEGER, mailbox INTEGER,
                               deleted INTEGER DEFAULT 0, automated_type INTEGER DEFAULT 0);
        CREATE TABLE recipients (ROWID INTEGER PRIMARY KEY, message INTEGER, type INTEGER,
                                 address INTEGER, position INTEGER);
        """

    static let messages = """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, style INTEGER);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, handle_id INTEGER, date INTEGER,
                              is_from_me INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """

    static let calls = """
        CREATE TABLE ZCALLRECORD (Z_PK INTEGER PRIMARY KEY, ZADDRESS BLOB, ZDATE REAL,
                                  ZORIGINATED INTEGER, ZDURATION REAL, ZNAME TEXT);
        """

    static let calendar = """
        CREATE TABLE CalendarItem (ROWID INTEGER PRIMARY KEY, summary TEXT, start_date REAL);
        CREATE TABLE Participant (ROWID INTEGER PRIMARY KEY, owner_id INTEGER,
                                  entity_type INTEGER, email TEXT, phone_number TEXT,
                                  display_name TEXT, is_self INTEGER);
        """

    static let addressBook = """
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZUNIQUEID TEXT, ZFIRSTNAME TEXT,
                                  ZLASTNAME TEXT, ZORGANIZATION TEXT, ZJOBTITLE TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER,
                                       ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDSOCIALPROFILE (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER,
                                         ZSERVICE TEXT, ZUSERNAME TEXT, ZURL TEXT);
        CREATE TABLE ZABCDURLADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZURL TEXT);
        """

    static let coreDuet = """
        CREATE TABLE ZINTERACTIONS (Z_PK INTEGER PRIMARY KEY, ZDIRECTION INTEGER,
                                    ZSTARTDATE REAL, ZBUNDLEID TEXT);
        CREATE TABLE ZCONTACTS (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZDISPLAYNAME TEXT);
        CREATE TABLE Z_1INTERACTIONS (Z_1INTERACTIONS INTEGER, Z_3CONTACTS INTEGER);
        """
}

/// A scratch directory for fixture databases.
final class Scratch {
    let url: URL

    init() {
        url = FileManager.default.temporaryDirectory
            .appendingPathComponent("orditura-test-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    func file(_ path: String) -> URL { url.appendingPathComponent(path) }

    func remove() { try? FileManager.default.removeItem(at: url) }

    deinit { remove() }
}

extension XCTestCase {
    /// A scratch directory whose lifetime is tied to the test rather than to
    /// ARC. A plain `let scratch = Scratch()` is not safe here: Swift may
    /// release a local as soon as its last reference is used, so the directory
    /// could be deleted while an extractor is still reading from it. The
    /// teardown block captures it strongly and runs when the test ends.
    func makeScratch() -> Scratch {
        let scratch = Scratch()
        addTeardownBlock { scratch.remove() }
        return scratch
    }
}

// MARK: - Time helpers

/// A fixed clock, so that decay assertions are exact rather than approximate.
let testNow = Date(timeIntervalSince1970: 1_788_609_600)   // 2026-09-05T12:00:00Z

func daysAgo(_ n: Double) -> Date { testNow.addingTimeInterval(-n * 86_400) }

/// Core Data / CFAbsoluteTime seconds.
func appleSeconds(_ date: Date) -> Double { date.timeIntervalSinceReferenceDate }

/// Messages' post-10.13 nanosecond encoding.
func appleNanoseconds(_ date: Date) -> Int64 {
    Int64(date.timeIntervalSinceReferenceDate * 1e9)
}

func unixSeconds(_ date: Date) -> Int64 { Int64(date.timeIntervalSince1970) }

func event(_ address: String,
           _ channel: Event.Channel,
           _ direction: Event.Direction,
           _ date: Date,
           audience: Int = 1) -> Event {
    Event(key: Identity.email(address) ?? Identity.phone(address)!,
          channel: channel, direction: direction, date: date, audience: audience)
}
