import XCTest
@testable import OrdituraCore

final class MailExtractorTests: XCTestCase {
    private var scratch: Scratch!
    private var url: URL!
    private var events: [Event]!

    override func setUpWithError() throws {
        scratch = makeScratch()
        url = scratch.file("Envelope Index")
        let seed = [
            """
            INSERT INTO addresses VALUES
              (1, 'me@studio.it', 'Me'),
              (2, 'anna@lanificio.it', 'Anna Rossi'),
              (3, 'marco@filatura.it', 'Marco Bianchi'),
              (4, 'no-reply@platform.com', 'Platform');
            """,
            """
            INSERT INTO mailboxes VALUES
              (1, 'imap://me%40studio.it@imap.example.com/INBOX'),
              (2, 'imap://me%40studio.it@imap.example.com/Sent%20Messages');
            """,
            """
            INSERT INTO messages VALUES
              (10, 1, NULL, \(unixSeconds(daysAgo(2))), \(unixSeconds(daysAgo(2))), 2, 0, 0),
              (11, 2, NULL, \(unixSeconds(daysAgo(2))), \(unixSeconds(daysAgo(2))), 1, 0, 0),
              (12, 1, NULL, \(unixSeconds(daysAgo(5))), \(unixSeconds(daysAgo(5))), 2, 0, 0),
              (13, 4, NULL, \(unixSeconds(daysAgo(1))), \(unixSeconds(daysAgo(1))), 1, 0, 0),
              (14, 2, NULL, \(unixSeconds(daysAgo(3))), \(unixSeconds(daysAgo(3))), 1, 1, 0),
              (15, 3, NULL, \(unixSeconds(daysAgo(3))), \(unixSeconds(daysAgo(3))), 1, 0, 1);
            """,
            """
            INSERT INTO recipients VALUES
              (1, 10, 0, 2, 0),
              (2, 11, 0, 1, 0),
              (3, 12, 0, 2, 0), (4, 12, 0, 3, 1), (5, 12, 1, 4, 2),
              (6, 13, 0, 1, 0),
              (7, 14, 0, 1, 0),
              (8, 15, 0, 1, 0);
            """,
        ]
        try Fixture.write(url, schema: Fixture.mail, seed: seed)
        events = try MailExtractor.extract(from: url, since: daysAgo(365))
    }


    func testOwnAddressIsDetectedFromTheMailboxURL() throws {
        let db = try SQLiteStore(snapshotting: url)
        XCTAssertTrue(MailExtractor.ownAddresses(in: db)
            .contains(try XCTUnwrap(Identity.email("me@studio.it"))))
    }

    func testDirections() {
        let out = Set(events.filter { $0.direction == .outgoing }.map(\.key.value))
        let incoming = Set(events.filter { $0.direction == .incoming }.map(\.key.value))
        XCTAssertEqual(out, ["anna@lanificio.it", "marco@filatura.it", "no-reply@platform.com"])
        XCTAssertTrue(incoming.contains("anna@lanificio.it"))
    }

    func testOwnAddressIsNeverACounterparty() {
        XCTAssertFalse(events.contains { $0.key.value == "me@studio.it" })
    }

    func testDeletedAndAutomatedMessagesAreExcluded() {
        let annaIn = events.filter { $0.key.value == "anna@lanificio.it" && $0.direction == .incoming }
        XCTAssertEqual(annaIn.count, 1, "message 14 is soft-deleted and must not count")
        XCTAssertTrue(events.filter {
            $0.key.value == "marco@filatura.it" && $0.direction == .incoming
        }.isEmpty, "message 15 is flagged automated by Mail")
    }

    func testAudienceSizeIsRecorded() throws {
        let broadcast = events.filter {
            $0.direction == .outgoing && $0.key.value == "marco@filatura.it"
        }
        XCTAssertEqual(broadcast.count, 1)
        XCTAssertEqual(try XCTUnwrap(broadcast.first).audience, 3)
        XCTAssertEqual(events.filter {
            $0.direction == .outgoing && $0.key.value == "anna@lanificio.it" && $0.audience == 1
        }.count, 1)
    }

    func testIncomingMessageYieldsExactlyOneEvent() {
        XCTAssertEqual(events.filter {
            $0.key.value == "no-reply@platform.com" && $0.direction == .incoming
        }.count, 1)
    }

    func testDisplayNamesAreCarried() {
        XCTAssertTrue(events.compactMap(\.display).contains("Anna Rossi"))
    }

    func testWindowIsRespected() throws {
        let recent = try MailExtractor.extract(from: url, since: daysAgo(3))
        XCTAssertTrue(recent.allSatisfy { $0.date >= daysAgo(3) })
        XCTAssertLessThan(recent.count, events.count)
    }
}

final class MessagesExtractorTests: XCTestCase {
    private var scratch: Scratch!
    private var events: [Event]!

    override func setUpWithError() throws {
        scratch = makeScratch()
        let url = scratch.file("chat.db")
        try Fixture.write(url, schema: Fixture.messages, seed: [
            "INSERT INTO handle VALUES (1, '+390551234567', 'iMessage'), "
                + "(2, 'marco@filatura.it', 'iMessage');",
            "INSERT INTO chat VALUES (1, '+390551234567', 45), (2, 'grp', 43);",
            "INSERT INTO chat_handle_join VALUES (1, 1), (2, 1), (2, 2);",
            """
            INSERT INTO message VALUES
              (100, 1, \(appleNanoseconds(daysAgo(1))), 0),
              (101, 0, \(appleNanoseconds(daysAgo(1))), 1),
              (102, 2, \(appleNanoseconds(daysAgo(4))), 0),
              (103, 0, \(appleNanoseconds(daysAgo(4))), 1),
              (104, 1, \(Int64(appleSeconds(daysAgo(900)))), 0);
            """,
            "INSERT INTO chat_message_join VALUES (1, 100), (1, 101), (2, 102), (2, 103), (1, 104);",
        ])
        events = try MessagesExtractor.extract(from: url, since: daysAgo(365))
    }


    func testNanosecondEpochIsDecoded() throws {
        let incoming = try XCTUnwrap(events.first { $0.direction == .incoming })
        XCTAssertEqual(incoming.date.timeIntervalSince1970,
                       daysAgo(1).timeIntervalSince1970, accuracy: 60)
    }

    func testLegacySecondsRowOutsideTheWindowIsDropped() {
        XCTAssertTrue(events.allSatisfy { $0.date >= daysAgo(365) })
    }

    func testOutgoingGroupMessageReachesEveryParticipant() {
        let group = Set(events.filter { $0.direction == .outgoing && $0.audience == 2 }
            .map(\.key.value))
        XCTAssertEqual(group, ["551234567", "marco@filatura.it"])
    }

    func testOneToOneAudienceIsOne() {
        XCTAssertTrue(events.contains { $0.key.value == "551234567" && $0.audience == 1 })
    }
}

final class CallExtractorTests: XCTestCase {
    func testBlobAddressesAndDirection() throws {
        let scratch = makeScratch()
        let url = scratch.file("CallHistory.storedata")
        try Fixture.write(url, schema: Fixture.calls, seed: [
            """
            INSERT INTO ZCALLRECORD VALUES
              (1, CAST('+390551234567' AS BLOB), \(appleSeconds(daysAgo(3))), 1, 240.0, 'Anna Rossi'),
              (2, '+390557654321', \(appleSeconds(daysAgo(9))), 0, 60.0, NULL),
              (3, CAST('+390551234567' AS BLOB), \(appleSeconds(daysAgo(900))), 1, 10.0, NULL);
            """,
        ])
        let events = try CallExtractor.extract(from: url, since: daysAgo(365))
        XCTAssertEqual(events.count, 2)
        let byKey = Dictionary(uniqueKeysWithValues: events.map { ($0.key.value, $0) })
        XCTAssertEqual(byKey["551234567"]?.direction, .outgoing)
        XCTAssertEqual(byKey["557654321"]?.direction, .incoming)
        XCTAssertEqual(byKey["551234567"]?.display, "Anna Rossi")
    }
}

final class CalendarExtractorTests: XCTestCase {
    func testSelfExcludedAndLargeMeetingsCapped() throws {
        let scratch = makeScratch()
        let url = scratch.file("Calendar.sqlitedb")
        var participants = [
            "(1, 1, 3, 'me@studio.it', NULL, 'Me', 1)",
            "(2, 1, 3, 'anna@lanificio.it', NULL, 'Anna Rossi', 0)",
            "(3, 1, 3, 'marco@filatura.it', NULL, 'Marco Bianchi', 0)",
        ]
        // A forty-person all-hands: above the attendee cap, must be ignored.
        participants += (0..<40).map { "(\(10 + $0), 2, 3, 'p\($0)@confer.it', NULL, 'P\($0)', 0)" }

        try Fixture.write(url, schema: Fixture.calendar, seed: [
            """
            INSERT INTO CalendarItem VALUES
              (1, 'Campionatura AW26', \(appleSeconds(daysAgo(6)))),
              (2, 'All hands', \(appleSeconds(daysAgo(7))));
            """,
            "INSERT INTO Participant VALUES " + participants.joined(separator: ", ") + ";",
        ])
        let events = try CalendarExtractor.extract(from: url, since: daysAgo(365))
        XCTAssertEqual(Set(events.map(\.key.value)),
                       ["anna@lanificio.it", "marco@filatura.it"])
        XCTAssertTrue(events.allSatisfy { $0.direction == .mutual && $0.audience == 2 })
    }
}

final class ContactsExtractorTests: XCTestCase {
    func testCardsEmailsPhonesAndLinkedIn() throws {
        let scratch = makeScratch()
        let url = scratch.file("AddressBook-v22.abcddb")
        try Fixture.write(url, schema: Fixture.addressBook, seed: [
            """
            INSERT INTO ZABCDRECORD VALUES
              (1, 'UID-1:ABPerson', 'Anna', 'Rossi', 'Lanificio Rossi', 'Direttrice creativa'),
              (2, 'UID-2:ABPerson', 'Marco', 'Bianchi', 'Filatura Bianchi', NULL);
            """,
            "INSERT INTO ZABCDEMAILADDRESS VALUES (1, 1, 'Anna@Lanificio.IT'), "
                + "(2, 2, 'marco@filatura.it');",
            "INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '+39 055 1234567');",
            "INSERT INTO ZABCDSOCIALPROFILE VALUES "
                + "(1, 1, 'LinkedIn', 'anna-rossi', 'https://www.linkedin.com/in/anna-rossi');",
            "INSERT INTO ZABCDURLADDRESS VALUES "
                + "(1, 2, 'https://it.linkedin.com/in/marco-bianchi');",
        ])
        // Anna already has a contact photo on disk; Marco does not.
        let images = url.deletingLastPathComponent().appendingPathComponent("Images")
        try FileManager.default.createDirectory(at: images, withIntermediateDirectories: true)
        try Data([0x89, 0x50, 0x4E, 0x47]).write(to: images.appendingPathComponent("UID-1"))

        let cards = Dictionary(uniqueKeysWithValues:
            ContactsExtractor.extract(from: url).map { ($0.uid, $0) })
        let anna = try XCTUnwrap(cards["UID-1:ABPerson"])
        XCTAssertEqual(anna.name, "Anna Rossi")
        XCTAssertEqual(anna.organisation, "Lanificio Rossi")
        XCTAssertEqual(anna.emails.map(\.value), ["anna@lanificio.it"])
        XCTAssertEqual(anna.phones.map(\.value), ["551234567"])
        XCTAssertEqual(anna.linkedIn, "https://www.linkedin.com/in/anna-rossi")
        XCTAssertTrue(anna.hasImage)

        let marco = try XCTUnwrap(cards["UID-2:ABPerson"])
        // A LinkedIn URL filed as a plain web address is picked up too.
        XCTAssertEqual(marco.linkedIn, "https://it.linkedin.com/in/marco-bianchi")
        XCTAssertFalse(marco.hasImage)
    }
}

final class CoreDuetExtractorTests: XCTestCase {
    func testJoinTableIsDiscovered() throws {
        let scratch = makeScratch()
        let url = scratch.file("interactionC.db")
        try Fixture.write(url, schema: Fixture.coreDuet, seed: [
            """
            INSERT INTO ZINTERACTIONS VALUES
              (1, 1, \(appleSeconds(daysAgo(2))), 'com.apple.mail'),
              (2, 0, \(appleSeconds(daysAgo(3))), 'com.apple.MobileSMS');
            """,
            "INSERT INTO ZCONTACTS VALUES (1, 'anna@lanificio.it', 'Anna Rossi');",
            "INSERT INTO Z_1INTERACTIONS VALUES (1, 1), (2, 1);",
        ])
        let events = try CoreDuetExtractor.extract(from: url, since: daysAgo(365))
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(Set(events.map(\.direction)), [.outgoing, .incoming])
        XCTAssertTrue(events.allSatisfy { $0.key.value == "anna@lanificio.it" })
    }
}

final class DegradationTests: XCTestCase {
    /// None of these schemas is documented and all of them move between macOS
    /// releases. One renamed column must cost a source, never the run.
    func testUnknownSchemaThrowsRatherThanCrashing() throws {
        let scratch = makeScratch()
        let url = scratch.file("Envelope Index")
        try Fixture.write(url, schema: "CREATE TABLE nonsense (a INTEGER);")
        XCTAssertThrowsError(try MailExtractor.extract(from: url, since: daysAgo(1)))
        XCTAssertThrowsError(try MessagesExtractor.extract(from: url, since: daysAgo(1)))
        XCTAssertThrowsError(try CalendarExtractor.extract(from: url, since: daysAgo(1)))
        XCTAssertThrowsError(try CallExtractor.extract(from: url, since: daysAgo(1)))
        XCTAssertThrowsError(try CoreDuetExtractor.extract(from: url, since: daysAgo(1)))
        XCTAssertTrue(ContactsExtractor.extract(from: url).isEmpty)
    }

    func testMissingFileThrowsNotFound() {
        let missing = URL(fileURLWithPath: "/nonexistent/CallHistory.storedata")
        XCTAssertThrowsError(try CallExtractor.extract(from: missing, since: daysAgo(1))) { error in
            XCTAssertEqual(error as? StoreError, .notFound(missing))
        }
    }

    /// The live stores are snapshotted, never opened in place.
    func testReadingDoesNotModifyTheSource() throws {
        let scratch = makeScratch()
        let url = scratch.file("Envelope Index")
        try Fixture.write(url, schema: Fixture.mail)
        let before = try FileManager.default.attributesOfItem(atPath: url.path)
        _ = try MailExtractor.extract(from: url, since: daysAgo(1))
        let after = try FileManager.default.attributesOfItem(atPath: url.path)
        XCTAssertEqual(before[.size] as? Int, after[.size] as? Int)
        XCTAssertEqual(before[.modificationDate] as? Date, after[.modificationDate] as? Date)
    }
}
