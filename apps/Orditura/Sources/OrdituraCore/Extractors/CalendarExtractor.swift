import Foundation

public enum CalendarExtractor {
    /// Meeting co-attendance. The strongest professional signal available on
    /// the machine, and the one a message-counting approach misses entirely.
    public static func extract(from url: URL,
                               since: Date,
                               maximumAttendees: Int = 25) throws -> [Event] {
        let db = try SQLiteStore(snapshotting: url)
        guard db.hasTables("CalendarItem", "Participant") else {
            throw StoreError.unexpectedSchema(url, "expected CalendarItem and Participant")
        }
        let participantColumns = db.columns(of: "Participant")
        let itemColumns = db.columns(of: "CalendarItem")
        guard participantColumns.contains("owner_id"), itemColumns.contains("start_date") else {
            throw StoreError.unexpectedSchema(url, "missing owner_id or start_date")
        }
        let email = participantColumns.contains("email") ? "p.email" : "NULL"
        let phone = participantColumns.contains("phone_number") ? "p.phone_number" : "NULL"
        let name = participantColumns.contains("display_name") ? "p.display_name" : "NULL"
        let isSelf = participantColumns.contains("is_self") ? "COALESCE(p.is_self, 0)" : "0"

        let sql = """
            SELECT ci.ROWID, ci.start_date, \(email), \(phone), \(name), \(isSelf)
            FROM Participant p JOIN CalendarItem ci ON ci.ROWID = p.owner_id
            WHERE ci.start_date >= ?
            ORDER BY ci.ROWID
            """

        var events: [Event] = []
        var currentItem: Int?
        var currentDate: Date?
        var attendees: [(Identity, String?)] = []

        func flush() {
            defer { attendees.removeAll() }
            guard let date = currentDate, date >= since, !attendees.isEmpty,
                  attendees.count <= maximumAttendees else { return }
            for (peer, name) in attendees {
                events.append(Event(key: peer, channel: .calendar, direction: .mutual,
                                    date: date, audience: attendees.count, display: name))
            }
        }

        try db.query(sql, [since.timeIntervalSinceReferenceDate]) { row in
            let item = row.int(0)
            if item != currentItem {
                flush()
                currentItem = item
                currentDate = AppleTime.fromReference(row.double(1))
            }
            guard (row.int(5) ?? 0) == 0 else { return }   // the user is not their own contact
            if let peer = Identity.email(row.string(2)) ?? Identity.phone(row.string(3)) {
                attendees.append((peer, row.string(4)))
            }
        }
        flush()
        return events
    }
}
