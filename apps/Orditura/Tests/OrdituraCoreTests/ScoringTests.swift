import XCTest
@testable import OrdituraCore

final class ScoringTests: XCTestCase {
    func testAudienceDamping() {
        XCTAssertEqual(Scoring.audienceDamping(1), 1.0, accuracy: 1e-12)
        XCTAssertEqual(Scoring.audienceDamping(2), 0.5, accuracy: 1e-12)
        XCTAssertLessThan(Scoring.audienceDamping(50), Scoring.audienceDamping(10))
        XCTAssertGreaterThan(Scoring.audienceDamping(50), 0)
        XCTAssertEqual(Scoring.audienceDamping(0), 1.0, accuracy: 1e-12)   // clamped
    }

    private func person(out: Double, incoming: Double) -> Person {
        var p = Person(id: Identity.email("x@y.zz")!)
        p.outScore = out
        p.inScore = incoming
        return p
    }

    func testReciprocityBounds() {
        XCTAssertEqual(person(out: 10, incoming: 10).reciprocity, 1.0, accuracy: 1e-12)
        XCTAssertEqual(person(out: 10, incoming: 0).reciprocity, 0.0, accuracy: 1e-12)
        XCTAssertEqual(person(out: 9, incoming: 1).reciprocity, 0.6, accuracy: 1e-12)
        XCTAssertEqual(person(out: 0, incoming: 0).reciprocity, 0.0)
    }

    func testOneSidedTieKeepsOnlyTheFloor() {
        XCTAssertEqual(person(out: 0, incoming: 100).score,
                       100 * Scoring.reciprocityFloor, accuracy: 1e-9)
    }

    func testReciprocalBeatsOneSidedAtEqualVolume() {
        let balanced = person(out: 50, incoming: 50)
        let oneSided = person(out: 0, incoming: 100)
        XCTAssertEqual(balanced.volume, oneSided.volume, accuracy: 1e-12)
        XCTAssertGreaterThan(balanced.score, oneSided.score)
    }

    func testRecencyDecayAtTheHalfLife() throws {
        let events = [event("a@x.it", .mail, .outgoing, daysAgo(180)),
                      event("b@x.it", .mail, .outgoing, daysAgo(0))]
        let ranked = Ranker.rank(events: events, cards: [], now: testNow,
                                 options: RankingOptions(minimumActiveDays: 1))
        let old = try XCTUnwrap(ranked.first { $0.id.value == "a@x.it" })
        let fresh = try XCTUnwrap(ranked.first { $0.id.value == "b@x.it" })
        XCTAssertEqual(old.outScore / fresh.outScore, 0.5, accuracy: 1e-9)
    }

    func testIdentitiesAreFusedThroughTheAddressBook() throws {
        var card = Card(uid: "U1")
        card.first = "Anna"
        card.last = "Rossi"
        card.emails = [Identity.email("anna@lanificio.it")!]
        card.phones = [Identity.phone("+390551234567")!]

        let events = [event("anna@lanificio.it", .mail, .outgoing, daysAgo(1)),
                      event("+390551234567", .imessage, .incoming, daysAgo(2))]
        let ranked = Ranker.rank(events: events, cards: [card], now: testNow,
                                 options: RankingOptions(minimumActiveDays: 1))
        XCTAssertEqual(ranked.count, 1)
        XCTAssertEqual(ranked[0].name, "Anna Rossi")
        XCTAssertEqual(ranked[0].channels, [.mail, .imessage])
        XCTAssertEqual(ranked[0].keys.count, 2)
    }

    func testMeetingsAreCountedAsMutual() throws {
        let ranked = Ranker.rank(events: [event("a@x.it", .calendar, .mutual, daysAgo(1),
                                                audience: 3)],
                                 cards: [], now: testNow, options: RankingOptions())
        let p = try XCTUnwrap(ranked.first)
        XCTAssertEqual(p.outScore, p.inScore, accuracy: 1e-12)
        XCTAssertEqual(p.reciprocity, 1.0, accuracy: 1e-12)
        XCTAssertEqual(p.meetings, 1)
        // A meeting is kept on its own: one is evidence enough.
        XCTAssertEqual(p.activeDays, 1)
    }

    func testRoleAddressesAreDroppedByDefault() {
        let events = (0..<10).map { event("no-reply@linkedin.com", .mail, .incoming,
                                          daysAgo(Double($0))) }
        XCTAssertTrue(Ranker.rank(events: events, cards: [], now: testNow).isEmpty)

        var keep = RankingOptions()
        keep.dropRoleAddresses = false
        XCTAssertEqual(Ranker.rank(events: events, cards: [], now: testNow,
                                   options: keep).count, 1)
    }

    func testMinimumActiveDaysSparesCallsAndMeetings() {
        let options = RankingOptions(minimumActiveDays: 3)
        var burst = Person(id: Identity.email("a@x.it")!); burst.days = [1]
        var called = Person(id: Identity.email("b@x.it")!); called.days = [1]; called.calls = 1
        var met = Person(id: Identity.email("c@x.it")!); met.days = [1]; met.meetings = 1
        XCTAssertFalse(Ranker.keep(burst, options: options))
        XCTAssertTrue(Ranker.keep(called, options: options))
        XCTAssertTrue(Ranker.keep(met, options: options))
    }

    /// The four counterparties from proposal §2.3, each dominating on one naive
    /// metric, so that only the full model orders them the way a person would.
    private func corpus() -> [Event] {
        var events: [Event] = []
        for i in 0..<12 {
            events.append(event("anna@lanificio.it", .mail, .outgoing, daysAgo(Double(i) * 5)))
            events.append(event("anna@lanificio.it", .mail, .incoming, daysAgo(Double(i) * 5 + 1)))
        }
        events.append(event("anna@lanificio.it", .calendar, .mutual, daysAgo(9), audience: 3))
        for i in 0..<40 {
            events.append(event("bruno@consorzio.it", .mail, .outgoing,
                                daysAgo(Double(i) * 3), audience: 30))
        }
        for i in 0..<80 {
            events.append(event("no-reply@textilenews.com", .mail, .incoming, daysAgo(Double(i))))
        }
        for i in 0..<30 {
            events.append(event("ghost@old.it", .mail, .outgoing, daysAgo(700 + Double(i))))
            events.append(event("ghost@old.it", .mail, .incoming, daysAgo(700 + Double(i))))
        }
        return events
    }

    func testReciprocalCorrespondentRanksFirst() throws {
        let ranked = Ranker.rank(events: corpus(), cards: [], now: testNow)
        XCTAssertEqual(try XCTUnwrap(ranked.first).id.value, "anna@lanificio.it")
    }

    func testBulkSenderIsExcludedEntirely() {
        let ranked = Ranker.rank(events: corpus(), cards: [], now: testNow)
        XCTAssertFalse(ranked.contains { $0.id.value == "no-reply@textilenews.com" })
    }

    func testBroadcasterLosesToACorrespondentWithFarLessTraffic() throws {
        let ranked = Ranker.rank(events: corpus(), cards: [], now: testNow)
        let anna = try XCTUnwrap(ranked.first { $0.id.value == "anna@lanificio.it" })
        let bruno = try XCTUnwrap(ranked.first { $0.id.value == "bruno@consorzio.it" })
        XCTAssertGreaterThan(bruno.sent, anna.sent)            // more raw messages
        XCTAssertGreaterThan(anna.score, 2 * bruno.score)      // far weaker tie all the same
        XCTAssertEqual(bruno.reciprocity, 0.0, accuracy: 1e-12)
    }

    /// A three-year default window is deliberately generous: an important
    /// counterparty who went quiet is still a counterparty. Recency is decay,
    /// not a cliff — and `window` is the control that removes the dormant.
    func testNarrowingTheWindowDropsDormantTies() throws {
        let recent = corpus().filter { $0.date >= daysAgo(365) }
        let ranked = Ranker.rank(events: recent, cards: [], now: testNow,
                                 options: RankingOptions(windowDays: 365))
        XCTAssertFalse(ranked.contains { $0.id.value == "ghost@old.it" })
        XCTAssertEqual(try XCTUnwrap(ranked.first).id.value, "anna@lanificio.it")

        let full = Ranker.rank(events: corpus(), cards: [], now: testNow)
        let ghost = try XCTUnwrap(full.first { $0.id.value == "ghost@old.it" })
        XCTAssertGreaterThan(ghost.reciprocity, 0.8)
    }
}
