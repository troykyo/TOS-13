import XCTest
@testable import OrdituraCore

/// The cases that got past the keyword list on a real mailbox of 334,866
/// messages. Keyword lists only catch the senders someone thought of, which is
/// why the rule doing the work here is structural.
final class BroadcasterTests: XCTestCase {
    private func person(sent: Int, received: Int,
                        meetings: Int = 0, calls: Int = 0) -> Person {
        var p = Person(id: Identity.email("x@y.zz")!)
        p.sent = sent
        p.received = received
        p.meetings = meetings
        p.calls = calls
        return p
    }

    func testNeverWrittenToIsABroadcaster() {
        for received in [4627, 2018, 1577, 1284] {
            XCTAssertTrue(Ranker.isBroadcaster(person(sent: 0, received: received)))
        }
    }

    func testReplyingLessThanOnceInTwentyIsABroadcaster() {
        XCTAssertTrue(Ranker.isBroadcaster(person(sent: 5, received: 405)))
    }

    func testRealCorrespondentsSurvive() {
        for (sent, received) in [(117, 354), (49, 156), (148, 274), (86, 139),
                                 (18546, 8272), (2241, 11)] {
            XCTAssertFalse(Ranker.isBroadcaster(person(sent: sent, received: received)),
                           "\(sent)/\(received)")
        }
    }

    func testACallOrMeetingOverridesIt() {
        XCTAssertFalse(Ranker.isBroadcaster(person(sent: 0, received: 900, meetings: 1)))
        XCTAssertFalse(Ranker.isBroadcaster(person(sent: 0, received: 900, calls: 1)))
    }

    func testAHandfulOfInboundIsNotYetEvidenceEitherWay() {
        XCTAssertFalse(Ranker.isBroadcaster(person(sent: 0, received: 4)))
    }

    func testRoleTokensAreMatchedAnywhereInTheLocalPart() throws {
        for bad in ["scholaralerts-noreply@google.com", "jobalerts-noreply@linkedin.com",
                    "news-noreply@dezeen.com", "team+notifications@x.io"] {
            XCTAssertTrue(RoleAddress.matches(try XCTUnwrap(Identity.email(bad))), bad)
        }
        // ...but never inside a name.
        for good in ["j.news@tue.nl", "borre@byborre.com", "alberto.digest@studio.it"] {
            XCTAssertFalse(RoleAddress.matches(try XCTUnwrap(Identity.email(good))), good)
        }
    }

    func testFilterDropsBroadcastersUnlessAskedNotTo() {
        var news = person(sent: 0, received: 900); news.days = Set(0..<30)
        var real = person(sent: 120, received: 300); real.days = Set(0..<30)
        var keep = RankingOptions()
        XCTAssertFalse(Ranker.keep(news, options: keep))
        XCTAssertTrue(Ranker.keep(real, options: keep))
        keep.dropBroadcasters = false
        XCTAssertTrue(Ranker.keep(news, options: keep))
    }
}

final class DuplicateTests: XCTestCase {
    private func person(_ name: String, _ score: Double) -> Person {
        // The score disambiguates the address: two people with the same name
        // must have *different* identities, or the fixture would not model the
        // situation being tested — one person split across two addresses.
        let slug = name.replacingOccurrences(of: " ", with: ".").lowercased()
        var p = Person(id: Identity.email("\(slug)+\(Int(score))@x.it")!)
        p.displays[name] = 1
        p.outScore = score
        p.inScore = score
        p.keys = [p.id]
        return p
    }

    func testIdenticalNamesGroup() {
        let groups = likelyDuplicates([person("Andre Neumann", 90),
                                       person("Andre Neumann", 40),
                                       person("Oscar Tomico", 200)])
        XCTAssertEqual(groups.count, 1)
        XCTAssertEqual(groups[0].count, 2)
    }

    /// A double surname added later is the common shape, and an exact match
    /// cannot see it.
    func testOneNameExtendingAnotherGroups() {
        XCTAssertEqual(likelyDuplicates([person("Bruna Goveia", 55),
                                         person("Bruna Goveia da Rocha", 32)]).count, 1)
    }

    func testAFirstNameAloneNeverGroups() {
        XCTAssertTrue(likelyDuplicates([person("Anna", 10),
                                        person("Anna Rossi", 10),
                                        person("Anna Bianchi", 10)]).isEmpty)
    }

    func testAccentsAndCaseDoNotSplitAPerson() {
        XCTAssertEqual(likelyDuplicates([person("José Teunissen", 40),
                                         person("jose teunissen", 20)]).count, 1)
    }

    func testFusionCombinesScoresAndIdentities() throws {
        let fused = fuseByName([person("Andre Neumann", 90),
                                person("Andre Neumann", 40),
                                person("Oscar Tomico", 200)])
        XCTAssertEqual(fused.count, 2)
        XCTAssertEqual(fused[0].name, "Oscar Tomico")
        let andre = try XCTUnwrap(fused.first { $0.name == "Andre Neumann" })
        XCTAssertEqual(andre.volume, 260, accuracy: 1e-9)
        XCTAssertEqual(andre.keys.count, 2)
    }
}
