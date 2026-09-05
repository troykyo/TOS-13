import Foundation

public enum CallExtractor {
    public static func extract(from url: URL, since: Date) throws -> [Event] {
        let db = try SQLiteStore(snapshotting: url)
        guard db.hasTables("ZCALLRECORD") else {
            throw StoreError.unexpectedSchema(url, "expected ZCALLRECORD")
        }
        let columns = db.columns(of: "ZCALLRECORD")
        guard columns.contains("ZADDRESS"), columns.contains("ZDATE") else {
            throw StoreError.unexpectedSchema(url, "ZCALLRECORD lacks ZADDRESS or ZDATE")
        }
        let originated = columns.contains("ZORIGINATED") ? "ZORIGINATED" : "NULL"
        let name = columns.contains("ZNAME") ? "ZNAME" : "NULL"

        var events: [Event] = []
        try db.query("""
            SELECT ZADDRESS, ZDATE, \(originated), \(name)
            FROM ZCALLRECORD WHERE ZDATE >= ?
            """, [since.timeIntervalSinceReferenceDate]) { row in
            // ZADDRESS is usually a blob of UTF-8 bytes rather than text.
            guard let date = AppleTime.fromReference(row.double(1)), date >= since,
                  let peer = Identity.handle(row.string(0)) else { return }
            let direction: Event.Direction = (row.int(2) ?? 0) != 0 ? .outgoing : .incoming
            events.append(Event(key: peer, channel: .call, direction: direction,
                                date: date, audience: 1, display: row.string(3)))
        }
        return events
    }
}
