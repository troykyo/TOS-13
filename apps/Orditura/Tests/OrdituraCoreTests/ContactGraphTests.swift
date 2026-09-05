import XCTest
@testable import OrdituraCore

/// End to end against a synthetic home directory laid out exactly like a Mac's,
/// so that discovery, extraction, identity fusion, ranking and export are all
/// exercised through the paths a real run takes.
final class ContactGraphTests: XCTestCase {
    private var scratch: Scratch!
    private var home: URL!
    private var result: RankingResult!

    override func setUpWithError() throws {
        scratch = makeScratch()
        home = scratch.url
        let library = home.appendingPathComponent("Library")
        let appSupport = library.appendingPathComponent("Application Support")

        var messages: [String] = []
        var recipients: [String] = []
        for i in 0..<8 {
            let t = unixSeconds(daysAgo(Double(i) * 4))
            messages.append("(\(100 + i), 1, NULL, \(t), \(t), 2, 0, 0)")
            recipients.append("(\(1000 + i), \(100 + i), 0, 2, 0)")
        }
        for i in 0..<8 {
            let t = unixSeconds(daysAgo(Double(i) * 4 + 1))
            messages.append("(\(200 + i), 2, NULL, \(t), \(t), 1, 0, 0)")
            recipients.append("(\(2000 + i), \(200 + i), 0, 1, 0)")
        }
        for i in 0..<30 {
            let t = unixSeconds(daysAgo(Double(i)))
            messages.append("(\(300 + i), 4, NULL, \(t), \(t), 1, 0, 0)")
            recipients.append("(\(3000 + i), \(300 + i), 0, 1, 0)")
        }

        try Fixture.write(library.appendingPathComponent("Mail/V10/MailData/Envelope Index"),
                          schema: Fixture.mail, seed: [
            """
            INSERT INTO addresses VALUES
              (1, 'me@studio.it', 'Me'),
              (2, 'anna@lanificio.it', 'Anna Rossi'),
              (3, 'marco@filatura.it', 'Marco Bianchi'),
              (4, 'newsletter@textilenews.com', 'Textile News');
            """,
            """
            INSERT INTO mailboxes VALUES
              (1, 'imap://me%40studio.it@imap.example.com/INBOX'),
              (2, 'imap://me%40studio.it@imap.example.com/Sent%20Messages');
            """,
            "INSERT INTO messages VALUES " + messages.joined(separator: ", ") + ";",
            "INSERT INTO recipients VALUES " + recipients.joined(separator: ", ") + ";",
        ])

        let chat = (0..<10).map { i in
            "(\(500 + i), 1, \(appleNanoseconds(daysAgo(Double(i) * 2))), \(i % 2))"
        }
        try Fixture.write(library.appendingPathComponent("Messages/chat.db"),
                          schema: Fixture.messages, seed: [
            "INSERT INTO handle VALUES (1, '+390557654321', 'iMessage');",
            "INSERT INTO chat VALUES (1, '+390557654321', 45);",
            "INSERT INTO chat_handle_join VALUES (1, 1);",
            "INSERT INTO message VALUES " + chat.joined(separator: ", ") + ";",
            "INSERT INTO chat_message_join VALUES "
                + (0..<10).map { "(1, \(500 + $0))" }.joined(separator: ", ") + ";",
        ])

        try Fixture.write(
            appSupport.appendingPathComponent("CallHistoryDB/CallHistory.storedata"),
            schema: Fixture.calls, seed: [
                "INSERT INTO ZCALLRECORD VALUES (1, CAST('+390557654321' AS BLOB), "
                    + "\(appleSeconds(daysAgo(3))), 1, 320.0, 'Marco Bianchi');",
            ])

        try Fixture.write(library.appendingPathComponent("Calendars/Calendar.sqlitedb"),
                          schema: Fixture.calendar, seed: [
            "INSERT INTO CalendarItem VALUES (1, 'Campionatura AW26', "
                + "\(appleSeconds(daysAgo(11))));",
            """
            INSERT INTO Participant VALUES
              (1, 1, 3, 'me@studio.it', NULL, 'Me', 1),
              (2, 1, 3, 'anna@lanificio.it', NULL, 'Anna Rossi', 0);
            """,
        ])

        let addressBook = appSupport.appendingPathComponent(
            "AddressBook/Sources/ABCD-1234/AddressBook-v22.abcddb")
        try Fixture.write(addressBook, schema: Fixture.addressBook, seed: [
            """
            INSERT INTO ZABCDRECORD VALUES
              (1, 'UID-1:ABPerson', 'Anna', 'Rossi', 'Lanificio Rossi', 'Direttrice creativa'),
              (2, 'UID-2:ABPerson', 'Marco', 'Bianchi', 'Filatura Bianchi', 'Titolare');
            """,
            "INSERT INTO ZABCDEMAILADDRESS VALUES (1, 1, 'anna@lanificio.it');",
            "INSERT INTO ZABCDPHONENUMBER VALUES (1, 2, '+39 055 765 4321');",
            "INSERT INTO ZABCDSOCIALPROFILE VALUES "
                + "(1, 1, 'LinkedIn', 'anna-rossi', 'https://www.linkedin.com/in/anna-rossi');",
        ])
        let images = addressBook.deletingLastPathComponent().appendingPathComponent("Images")
        try FileManager.default.createDirectory(at: images, withIntermediateDirectories: true)
        try Data([0x89, 0x50, 0x4E, 0x47]).write(to: images.appendingPathComponent("UID-1"))

        result = ContactGraph.build(now: testNow, home: home)
    }


    func testEveryStoreIsFoundAndReadable() {
        let byStore = Dictionary(grouping: result.reports, by: \.store)
        for store in [Store.mail, .imessage, .call, .calendar, .contacts] {
            let reports = byStore[store] ?? []
            XCTAssertFalse(reports.isEmpty, "\(store) produced no report")
            XCTAssertTrue(reports.allSatisfy(\.succeeded),
                          "\(store): \(reports.compactMap(\.error).joined(separator: "; "))")
        }
        // CoreDuet is opt-in and absent here; it must not have been attempted.
        XCTAssertNil(byStore[.coreduet])
    }

    func testProbeReportsRowCountsForTheSyntheticHome() {
        let statuses = StoreProbe.probeAll(home: home)
        let usable = statuses.filter(\.isUsable).map(\.store)
        XCTAssertTrue(usable.contains(.mail))
        XCTAssertTrue(usable.contains(.imessage))
        XCTAssertTrue(usable.contains(.calendar))
        XCTAssertTrue(usable.contains(.contacts))
        XCTAssertTrue(statuses.contains { $0.store == .coreduet && $0.state == .absent })
    }

    func testRankingOrderAndIdentityFusion() throws {
        let names = result.people.map(\.name)
        XCTAssertEqual(names.first, "Anna Rossi")
        XCTAssertTrue(names.contains("Marco Bianchi"))
        XCTAssertFalse(result.people.contains { $0.name.contains("Textile News") })
        XCTAssertFalse(result.people.contains { $0.keys.contains { $0.value == "me@studio.it" } })

        let anna = try XCTUnwrap(result.people.first { $0.name == "Anna Rossi" })
        XCTAssertEqual(anna.organisation, "Lanificio Rossi")
        XCTAssertEqual(anna.linkedIn, "https://www.linkedin.com/in/anna-rossi")
        XCTAssertTrue(anna.hasPhoto)
        XCTAssertTrue(anna.channels.contains(.calendar))
        XCTAssertTrue(anna.channels.contains(.mail))
        XCTAssertEqual(anna.meetings, 1)

        // Marco is reachable only by phone; the address book fuses his iMessage
        // handle and his call record onto one card, and he still needs a photo.
        let marco = try XCTUnwrap(result.people.first { $0.name == "Marco Bianchi" })
        XCTAssertFalse(marco.hasPhoto)
        XCTAssertEqual(marco.linkedIn, "")
        XCTAssertTrue(marco.channels.contains(.imessage))
        XCTAssertTrue(marco.channels.contains(.call))
        XCTAssertEqual(marco.keys.map(\.display), ["557654321"])
    }

    func testNarrowWindowYieldsNothing() {
        let empty = ContactGraph.build(options: RankingOptions(windowDays: 0),
                                       now: testNow, home: home)
        XCTAssertTrue(empty.people.isEmpty)
    }

    func testAbsentHomeReportsPerStoreRatherThanFailing() {
        let nowhere = URL(fileURLWithPath: "/nonexistent-home")
        let empty = ContactGraph.build(now: testNow, home: nowhere)
        XCTAssertTrue(empty.people.isEmpty)
        XCTAssertEqual(empty.reports.filter { $0.error == "not present" }.count,
                       empty.reports.count)
    }
}

final class ExportTests: XCTestCase {
    private func sample() -> [Person] {
        var card = Card(uid: "UID-1:ABPerson")
        card.first = "Anna"
        card.last = "Rossi, Lanificio"          // a comma, to exercise CSV quoting
        card.organisation = "Lanificio \"Rossi\""
        card.emails = [Identity.email("anna@lanificio.it")!]
        card.linkedIn = "https://www.linkedin.com/in/anna-rossi"
        return Ranker.rank(events: [event("anna@lanificio.it", .mail, .outgoing, daysAgo(1)),
                                    event("anna@lanificio.it", .mail, .incoming, daysAgo(2))],
                           cards: [card], now: testNow,
                           options: RankingOptions(minimumActiveDays: 1))
    }

    func testCSVHeaderMatchesTheColumnList() throws {
        let csv = Export.csv(sample())
        let header = try XCTUnwrap(csv.split(separator: "\n").first)
        XCTAssertEqual(String(header), PersonRow.columns.joined(separator: ","))
    }

    /// The two implementations must produce byte-identical CSV for the same
    /// input, so they can be diffed against each other on one machine.
    func testBooleansAreLowercaseAndFloatsFixedPrecision() {
        let csv = Export.csv(sample())
        XCTAssertTrue(csv.contains("true"), csv)
        XCTAssertFalse(csv.contains("True"), "Booleans must match the Python export")
        let cells = Export.rows(sample())[0].cells
        XCTAssertEqual(cells[4].split(separator: ".").last?.count, 3, "score is 3dp")
    }

    func testCSVQuotingIsRFC4180() {
        XCTAssertEqual(Export.escape("plain"), "plain")
        XCTAssertEqual(Export.escape("a,b"), "\"a,b\"")
        XCTAssertEqual(Export.escape("say \"hi\""), "\"say \"\"hi\"\"\"")
        XCTAssertEqual(Export.escape("two\nlines"), "\"two\nlines\"")
        let csv = Export.csv(sample())
        XCTAssertTrue(csv.contains("\"Anna Rossi, Lanificio\""), csv)
        XCTAssertTrue(csv.contains("\"Lanificio \"\"Rossi\"\"\""), csv)
    }

    func testJSONRoundTrips() throws {
        let data = try Export.json(sample())
        let rows = try JSONDecoder().decode([PersonRow].self, from: data)
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows[0].rank, 1)
        XCTAssertEqual(rows[0].linkedInURL, "https://www.linkedin.com/in/anna-rossi")
        XCTAssertTrue(rows[0].inContacts)
    }
}
