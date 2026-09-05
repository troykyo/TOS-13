import XCTest
@testable import OrdituraCore

final class IdentityTests: XCTestCase {
    func testEmailForms() {
        XCTAssertEqual(Identity.email("A.Rossi@Lanificio.IT")?.raw, "mailto:a.rossi@lanificio.it")
        XCTAssertEqual(Identity.email("  <a@b.co>  ")?.raw, "mailto:a@b.co")
        // The angle-bracket extraction has to run before any <> stripping, or
        // the closing bracket disappears first and nothing matches.
        XCTAssertEqual(Identity.email("\"Anna Rossi\" <anna@b.co>")?.raw, "mailto:anna@b.co")
        XCTAssertEqual(Identity.email("Anna Rossi <anna@b.co>")?.raw, "mailto:anna@b.co")
        XCTAssertNil(Identity.email("not-an-address"))
        XCTAssertNil(Identity.email("two@at@signs.co"))
        XCTAssertNil(Identity.email("a@localhost"))
        XCTAssertNil(Identity.email(""))
        XCTAssertNil(Identity.email(nil))
    }

    func testPhoneKeysOnTheLastNineDigits() {
        let forms = ["+39 055 123 4567", "0039 055 1234567", "055 1234567", "39-055-1234567"]
        let keys = Set(forms.compactMap { Identity.phone($0)?.raw })
        XCTAssertEqual(keys, ["tel:551234567"])
    }

    func testPhoneRejectsShortInput() {
        XCTAssertNil(Identity.phone("123"))
        XCTAssertNil(Identity.phone(""))
    }

    func testHandleDispatchesOnTheAtSign() {
        XCTAssertEqual(Identity.handle("x@y.zz")?.kind, .email)
        XCTAssertEqual(Identity.handle("+390551234567")?.raw, "tel:551234567")
        XCTAssertNil(Identity.handle(nil))
    }

    func testRoleAddressDetection() throws {
        for bad in ["no-reply@linkedin.com", "notifications@github.com", "billing@stripe.com",
                    "newsletter@vogue.it", "bounce+123@sendgrid.net", "info@studio.it"] {
            let id = try XCTUnwrap(Identity.email(bad))
            XCTAssertTrue(RoleAddress.matches(id), bad)
        }
        for good in ["anna.rossi@lanificio.it", "m.bianchi@politecnico.it"] {
            let id = try XCTUnwrap(Identity.email(good))
            XCTAssertFalse(RoleAddress.matches(id), good)
        }
        XCTAssertFalse(RoleAddress.matches(try XCTUnwrap(Identity.phone("+390551234567"))))
    }

    func testDisjointSetFusesTransitively() {
        var dsu = DisjointSet<String>()
        dsu.union("a", "b")
        dsu.union("b", "c")
        XCTAssertEqual(dsu.find("a"), dsu.find("c"))
        XCTAssertNotEqual(dsu.find("a"), dsu.find("z"))
    }
}
