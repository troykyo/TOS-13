import Foundation

public enum MailExtractor {
    private static let accountInURL = Re.compile("^[a-z0-9+.-]+://([^@/]+)@")
    private static let sentMailbox = Re.compile("/sent(\\s|%20)?(messages|items|mail)?/?$")

    /// The user's own addresses, derived from the mail store itself: the
    /// account address is embedded in every mailbox URL
    /// (`imap://user%40host@server/…`), and the senders of anything sitting in
    /// a Sent mailbox are by definition the user.
    public static func ownAddresses(in db: SQLiteStore) -> Set<Identity> {
        var own = Set<Identity>()
        guard db.hasTables("mailboxes") else { return own }

        var sentMailboxIDs: [Int] = []
        try? db.query("SELECT ROWID, url FROM mailboxes") { row in
            let url = row.string(1) ?? ""
            if let user = Re.firstGroup(accountInURL, in: url),
               let decoded = user.removingPercentEncoding,
               let id = Identity.email(decoded) {
                own.insert(id)
            }
            if Re.matches(sentMailbox, url), let rowid = row.int(0) {
                sentMailboxIDs.append(rowid)
            }
        }

        if !sentMailboxIDs.isEmpty, db.hasTables("messages", "addresses") {
            // Interpolating integers read back from the database is safe; there
            // is no user input anywhere on this path.
            let list = sentMailboxIDs.map(String.init).joined(separator: ",")
            try? db.query("""
                SELECT a.address, count(*) AS c
                FROM messages m JOIN addresses a ON a.ROWID = m.sender
                WHERE m.mailbox IN (\(list))
                GROUP BY a.address ORDER BY c DESC LIMIT 25
                """) { row in
                if let id = Identity.email(row.string(0)) { own.insert(id) }
            }
        }
        return own
    }

    public static func extract(from url: URL,
                               since: Date,
                               own seed: Set<Identity> = []) throws -> [Event] {
        let db = try SQLiteStore(snapshotting: url)
        guard db.hasTables("messages", "addresses", "recipients") else {
            throw StoreError.unexpectedSchema(url, "expected messages, addresses, recipients")
        }
        let own = seed.union(ownAddresses(in: db))
        let messageColumns = db.columns(of: "messages")
        let addressColumns = db.columns(of: "addresses")

        let ts = messageColumns.contains("date_sent")
            ? "COALESCE(m.date_sent, m.date_received)" : "m.date_received"
        let notAutomated = messageColumns.contains("automated_type")
            ? " AND COALESCE(m.automated_type, 0) = 0" : ""
        let notDeleted = messageColumns.contains("deleted")
            ? " AND COALESCE(m.deleted, 0) = 0" : ""
        let hasMailboxes = messageColumns.contains("mailbox") && db.hasTables("mailboxes")
        let mailboxJoin = hasMailboxes ? "LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox" : ""
        let mailboxURL = hasMailboxes ? "mb.url" : "NULL"
        let senderName = addressColumns.contains("comment") ? "sa.comment" : "NULL"
        let recipientName = addressColumns.contains("comment") ? "ra.comment" : "NULL"

        // One pass, streamed. The per-message recipient count comes from a CTE
        // so that audience size is known without materialising the mailbox in
        // memory — a three-year window on a working mailbox is easily a million
        // rows.
        let sql = """
            WITH win AS (
              SELECT m.ROWID AS mid, \(ts) AS ts, m.sender AS sender, \(mailboxURL) AS mburl
              FROM messages m \(mailboxJoin)
              WHERE \(ts) >= ?\(notAutomated)\(notDeleted)
            ), cnt AS (
              SELECT message, count(*) AS n FROM recipients
              WHERE message IN (SELECT mid FROM win) GROUP BY message
            )
            SELECT w.mid, w.ts, w.mburl,
                   sa.address AS sender_addr, \(senderName) AS sender_name,
                   ra.address AS rcpt_addr, \(recipientName) AS rcpt_name,
                   COALESCE(cnt.n, 1) AS n
            FROM win w
            LEFT JOIN addresses sa ON sa.ROWID = w.sender
            LEFT JOIN recipients r ON r.message = w.mid
            LEFT JOIN addresses ra ON ra.ROWID = r.address
            LEFT JOIN cnt ON cnt.message = w.mid
            ORDER BY w.mid
            """

        var events: [Event] = []
        var lastIncomingMessage: Int?
        try db.query(sql, [since.timeIntervalSince1970]) { row in
            guard let date = AppleTime.fromUnixOrReference(row.double(1)) else { return }
            let sender = Identity.email(row.string(3))
            let inSentMailbox = Re.matches(sentMailbox, row.string(2) ?? "")
            let outgoing = (sender.map(own.contains) ?? false) || inSentMailbox
            let audience = row.int(7) ?? 1

            if outgoing {
                // One event per recipient: a message to five people is five
                // pieces of evidence, each damped by the audience size.
                if let recipient = Identity.email(row.string(5)), !own.contains(recipient) {
                    events.append(Event(key: recipient, channel: .mail, direction: .outgoing,
                                        date: date, audience: audience,
                                        display: row.string(6)))
                }
            } else if let sender = sender, !own.contains(sender),
                      lastIncomingMessage != row.int(0) {
                // The recipient join fans an incoming message out across rows;
                // credit its sender exactly once.
                lastIncomingMessage = row.int(0)
                events.append(Event(key: sender, channel: .mail, direction: .incoming,
                                    date: date, audience: audience, display: row.string(4)))
            }
        }
        return events
    }
}
