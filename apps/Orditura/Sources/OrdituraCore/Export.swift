import Foundation

/// One row of the ranking, in the shape both exports use. Kept deliberately
/// identical to the `tools/contact-rank` CSV so the two implementations can be
/// diffed against each other on the same machine.
public struct PersonRow: Codable, Equatable {
    public let rank: Int
    public let name: String
    public let organisation: String
    public let title: String
    public let score: Double
    public let volume: Double
    public let reciprocity: Double
    public let sent: Int
    public let received: Int
    public let meetings: Int
    public let calls: Int
    public let activeDays: Int
    public let firstSeen: String
    public let lastSeen: String
    public let channels: String
    public let identities: String
    public let contactUID: String
    public let inContacts: Bool
    public let hasPhoto: Bool
    public let linkedInURL: String

    public static let columns = [
        "rank", "name", "organisation", "title", "score", "volume", "reciprocity",
        "sent", "received", "meetings", "calls", "active_days", "first_seen",
        "last_seen", "channels", "identities", "contact_uid", "in_contacts",
        "has_photo", "linkedin_url",
    ]

    var cells: [String] {
        [String(rank), name, organisation, title,
         Export.number(score), Export.number(volume), Export.number(reciprocity),
         String(sent), String(received), String(meetings), String(calls),
         String(activeDays), firstSeen, lastSeen, channels, identities,
         contactUID, String(inContacts), String(hasPhoto), linkedInURL]
    }
}

public enum Export {
    private static let day: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(secondsFromGMT: 0)
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    static func number(_ value: Double) -> String {
        String(format: "%.3f", value)
    }

    public static func date(_ value: Date?) -> String {
        value.map { day.string(from: $0) } ?? ""
    }

    public static func rows(_ people: [Person]) -> [PersonRow] {
        people.enumerated().map { index, person in
            PersonRow(
                rank: index + 1,
                name: person.name,
                organisation: person.organisation,
                title: person.title,
                score: person.score,
                volume: person.volume,
                reciprocity: person.reciprocity,
                sent: person.sent,
                received: person.received,
                meetings: person.meetings,
                calls: person.calls,
                activeDays: person.activeDays,
                firstSeen: date(person.firstSeen),
                lastSeen: date(person.lastSeen),
                channels: person.channels.map(\.rawValue).sorted().joined(separator: "+"),
                identities: person.keys.sorted().map(\.display).joined(separator: ";"),
                contactUID: person.card?.uid ?? "",
                inContacts: person.inContacts,
                hasPhoto: person.hasPhoto,
                linkedInURL: person.linkedIn)
        }
    }

    /// RFC 4180 quoting: double the quotes, wrap anything containing a
    /// delimiter, a quote or a newline.
    static func escape(_ field: String) -> String {
        guard field.contains(where: { $0 == "," || $0 == "\"" || $0 == "\n" || $0 == "\r" })
        else { return field }
        return "\"" + field.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }

    public static func csv(_ people: [Person]) -> String {
        var out = PersonRow.columns.joined(separator: ",") + "\n"
        for row in rows(people) {
            out += row.cells.map(escape).joined(separator: ",") + "\n"
        }
        return out
    }

    public static func json(_ people: [Person]) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(rows(people))
    }
}
