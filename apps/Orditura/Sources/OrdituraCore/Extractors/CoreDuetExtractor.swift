import Foundation

/// Apple's own interaction ledger — the store behind Siri's people suggestions,
/// and the literal system answer to "who am I in touch with most".
///
/// It is also entirely undocumented: the direction encoding has changed between
/// macOS releases and the Core Data join table is named differently on every
/// machine. So it is opt-in, and weighted as corroboration rather than as
/// evidence. Deriving the graph from Mail, Messages, Calls and Calendar is both
/// more transparent and more stable.
public enum CoreDuetExtractor {
    /// The many-to-many table linking interactions to contacts, whose name is a
    /// Core Data artefact (`Z_1INTERACTIONS`, `Z_4INTERACTIONS`, …).
    static func joinTable(in db: SQLiteStore)
        -> (table: String, interaction: String, contact: String)? {
        var candidates: [String] = []
        try? db.query("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name LIKE 'Z\\_%' ESCAPE '\\'
            """) { row in
            if let name = row.string(0) { candidates.append(name) }
        }
        for table in candidates.sorted() {
            let columns = db.columns(of: table)
            guard let interaction = columns.first(where: {
                      $0.uppercased().hasSuffix("INTERACTIONS") }),
                  let contact = columns.first(where: {
                      $0.uppercased().hasSuffix("CONTACTS") })
            else { continue }
            return (table, interaction, contact)
        }
        return nil
    }

    public static func extract(from url: URL, since: Date) throws -> [Event] {
        let db = try SQLiteStore(snapshotting: url)
        guard db.hasTables("ZINTERACTIONS", "ZCONTACTS") else {
            throw StoreError.unexpectedSchema(url, "expected ZINTERACTIONS and ZCONTACTS")
        }
        guard let join = joinTable(in: db) else {
            throw StoreError.unexpectedSchema(url, "no ZINTERACTIONS/ZCONTACTS join table")
        }
        let contactColumns = db.columns(of: "ZCONTACTS")
        let identifier = contactColumns.contains("ZIDENTIFIER") ? "c.ZIDENTIFIER" : "NULL"
        let display = contactColumns.contains("ZDISPLAYNAME") ? "c.ZDISPLAYNAME" : "NULL"

        let sql = """
            SELECT i.ZSTARTDATE, i.ZDIRECTION, \(identifier), \(display)
            FROM \(SQLiteStore.quote(join.table)) j
            JOIN ZINTERACTIONS i ON i.Z_PK = j.\(SQLiteStore.quote(join.interaction))
            JOIN ZCONTACTS c ON c.Z_PK = j.\(SQLiteStore.quote(join.contact))
            WHERE i.ZSTARTDATE >= ?
            """

        var events: [Event] = []
        try db.query(sql, [since.timeIntervalSinceReferenceDate]) { row in
            guard let date = AppleTime.fromReference(row.double(0)), date >= since,
                  let peer = Identity.handle(row.string(2)) else { return }
            let direction: Event.Direction = (row.int(1) ?? 0) != 0 ? .outgoing : .incoming
            events.append(Event(key: peer, channel: .coreduet, direction: direction,
                                date: date, audience: 1, display: row.string(3)))
        }
        return events
    }
}
