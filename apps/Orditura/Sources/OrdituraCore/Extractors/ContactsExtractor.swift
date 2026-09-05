import Foundation

public enum ContactsExtractor {
    /// Reads the address-book stores directly rather than through
    /// `Contacts.framework`, so that the whole of Stage A works from one
    /// permission grant and stays testable without a running app. The framework
    /// remains the right route for *writing* anything back.
    public static func extract(from urls: [URL]) -> [Card] {
        urls.flatMap { extract(from: $0) }
    }

    public static func extract(from url: URL) -> [Card] {
        guard let db = try? SQLiteStore(snapshotting: url), db.hasTables("ZABCDRECORD") else {
            return []
        }
        let columns = db.columns(of: "ZABCDRECORD")
        func column(_ name: String) -> String { columns.contains(name) ? name : "NULL" }

        var byKey: [Int: Card] = [:]
        try? db.query("""
            SELECT Z_PK, \(column("ZUNIQUEID")), \(column("ZFIRSTNAME")), \
            \(column("ZLASTNAME")), \(column("ZORGANIZATION")), \(column("ZJOBTITLE"))
            FROM ZABCDRECORD
            """) { row in
            guard let pk = row.int(0) else { return }
            let uid = row.string(1) ?? "\(url.deletingLastPathComponent().lastPathComponent)#\(pk)"
            var card = Card(uid: uid)
            card.first = (row.string(2) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            card.last = (row.string(3) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            card.organisation = (row.string(4) ?? "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            card.title = (row.string(5) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            byKey[pk] = card
        }

        if db.hasTables("ZABCDEMAILADDRESS") {
            try? db.query("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS") { row in
                guard let owner = row.int(0), let id = Identity.email(row.string(1)) else { return }
                byKey[owner]?.emails.append(id)
            }
        }
        if db.hasTables("ZABCDPHONENUMBER") {
            try? db.query("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER") { row in
                guard let owner = row.int(0), let id = Identity.phone(row.string(1)) else { return }
                byKey[owner]?.phones.append(id)
            }
        }
        // Contacts you have already filed a LinkedIn URL against. This is free,
        // already yours, and the first rung of the Stage B source ladder — check
        // how many you have before building anything that goes near the network.
        if db.hasTables("ZABCDSOCIALPROFILE") {
            let social = db.columns(of: "ZABCDSOCIALPROFILE")
            func column(_ name: String) -> String { social.contains(name) ? name : "NULL" }
            try? db.query("""
                SELECT ZOWNER, \(column("ZSERVICE")), \(column("ZURL")), \(column("ZUSERNAME"))
                FROM ZABCDSOCIALPROFILE
                """) { row in
                guard let owner = row.int(0) else { return }
                let blob = [row.string(1), row.string(2), row.string(3)]
                    .compactMap { $0 }.joined(separator: " ").lowercased()
                guard blob.contains("linkedin") else { return }
                let value = (row.string(2) ?? row.string(3) ?? "")
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if !value.isEmpty { byKey[owner]?.linkedIn = value }
            }
        }
        if db.hasTables("ZABCDURLADDRESS") {
            try? db.query("SELECT ZOWNER, ZURL FROM ZABCDURLADDRESS") { row in
                guard let owner = row.int(0), let raw = row.string(1) else { return }
                let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                byKey[owner]?.urls.append(value)
                if value.lowercased().contains("linkedin.com"),
                   byKey[owner]?.linkedIn.isEmpty == true {
                    byKey[owner]?.linkedIn = value
                }
            }
        }

        // Contact photographs live as files beside the store, named by the
        // record's UUID. Whether one already exists is exactly the input to the
        // enrichment queue: these people need nothing fetched.
        let imageDirectory = url.deletingLastPathComponent()
            .appendingPathComponent("Images", isDirectory: true)
        let imageNames = Set(
            (try? FileManager.default.contentsOfDirectory(atPath: imageDirectory.path)) ?? [])
        if !imageNames.isEmpty {
            for (pk, card) in byKey {
                let stem = card.uid.components(separatedBy: ":").first ?? card.uid
                byKey[pk]?.hasImage = imageNames.contains { $0.hasPrefix(stem) }
            }
        }

        return byKey.values.filter { !$0.emails.isEmpty || !$0.phones.isEmpty || !$0.name.isEmpty }
    }
}
