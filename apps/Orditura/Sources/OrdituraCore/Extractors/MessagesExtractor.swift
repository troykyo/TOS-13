import Foundation

public enum MessagesExtractor {
    public static func extract(from url: URL, since: Date) throws -> [Event] {
        let db = try SQLiteStore(snapshotting: url)
        guard db.hasTables("message", "handle", "chat", "chat_message_join") else {
            throw StoreError.unexpectedSchema(
                url, "expected message, handle, chat, chat_message_join")
        }

        // Chat membership, so an outgoing group message credits everyone in the
        // room and carries the right audience size.
        var participants: [Int: [Identity]] = [:]
        if db.hasTables("chat_handle_join") {
            try? db.query("""
                SELECT j.chat_id, h.id
                FROM chat_handle_join j JOIN handle h ON h.ROWID = j.handle_id
                """) { row in
                guard let chat = row.int(0), let id = Identity.handle(row.string(1)) else { return }
                participants[chat, default: []].append(id)
            }
        }

        // chat.db holds Apple-epoch seconds before macOS 10.13 and nanoseconds
        // after it. Filter in SQL on the seconds floor, which is a superset
        // under either encoding, then narrow exactly once the encoding of each
        // row has been detected.
        let sql = """
            SELECT m.ROWID, m.date, m.is_from_me, h.id, cmj.chat_id
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE m.date >= ?
            """

        var events: [Event] = []
        try db.query(sql, [since.timeIntervalSinceReferenceDate]) { row in
            guard let date = AppleTime.fromReference(row.double(1)), date >= since else { return }
            let members = row.int(4).flatMap { participants[$0] } ?? []
            let audience = max(1, members.count)
            let fromMe = (row.int(2) ?? 0) != 0

            if fromMe {
                let targets = members.isEmpty
                    ? [Identity.handle(row.string(3))].compactMap { $0 }
                    : members
                for target in targets {
                    events.append(Event(key: target, channel: .imessage,
                                        direction: .outgoing, date: date, audience: audience))
                }
            } else if let sender = Identity.handle(row.string(3)) {
                events.append(Event(key: sender, channel: .imessage,
                                    direction: .incoming, date: date, audience: audience))
            }
        }
        return events
    }
}
