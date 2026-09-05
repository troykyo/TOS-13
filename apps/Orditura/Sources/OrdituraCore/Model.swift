import Foundation

/// A single observed interaction between the user and one counterparty.
public struct Event: Equatable {
    public enum Channel: String, CaseIterable {
        case mail, imessage, call, calendar, coreduet
    }

    /// Named from the user's point of view. `mutual` is a meeting, which is
    /// mutual by construction and is credited to both directions at half
    /// weight. The raw values are the vocabulary used in exports.
    public enum Direction: String {
        case outgoing = "out"
        case incoming = "in"
        case mutual = "meet"
    }

    public let key: Identity
    public let channel: Channel
    public let direction: Direction
    public let date: Date
    /// Number of counterparties addressed at once. 1 for a private exchange.
    public let audience: Int
    public let display: String?

    public init(key: Identity, channel: Channel, direction: Direction,
                date: Date, audience: Int = 1, display: String? = nil) {
        self.key = key
        self.channel = channel
        self.direction = direction
        self.date = date
        self.audience = max(1, audience)
        self.display = display
    }
}

/// A Contacts.app record, read straight from the address book store.
public struct Card: Equatable {
    public let uid: String
    public var first: String = ""
    public var last: String = ""
    public var organisation: String = ""
    public var title: String = ""
    public var emails: [Identity] = []
    public var phones: [Identity] = []
    public var linkedIn: String = ""
    public var urls: [String] = []
    public var hasImage: Bool = false

    public init(uid: String) { self.uid = uid }

    public var name: String {
        let n = [first, last].filter { !$0.isEmpty }.joined(separator: " ")
        return n.isEmpty ? organisation : n
    }

    public var identities: [Identity] {
        var seen = Set<Identity>()
        return (emails + phones).filter { seen.insert($0).inserted }
    }
}

/// One human, fused from every identity that resolves to them.
public struct Person: Identifiable, Equatable {
    public let id: Identity
    public var keys: Set<Identity> = []
    public var displays: [String: Int] = [:]
    public var outScore: Double = 0
    public var inScore: Double = 0
    public var sent: Int = 0
    public var received: Int = 0
    public var meetings: Int = 0
    public var calls: Int = 0
    /// Distinct UTC days on which any interaction occurred.
    public var days: Set<Int> = []
    public var firstSeen: Date?
    public var lastSeen: Date?
    public var channels: Set<Event.Channel> = []
    public var card: Card?

    public init(id: Identity) { self.id = id }

    public var volume: Double { outScore + inScore }

    /// The ratio of the geometric to the arithmetic mean of the two directional
    /// volumes: 1 for a balanced exchange, 0 for a wholly one-sided one, smooth
    /// and scale-free in between. This is what separates a correspondence from
    /// a feed, and it is the term raw message counts are missing.
    public var reciprocity: Double {
        let total = outScore + inScore
        guard total > 0 else { return 0 }
        return 2 * (max(outScore, 0) * max(inScore, 0)).squareRoot() / total
    }

    public var score: Double {
        volume * (Scoring.reciprocityFloor + (1 - Scoring.reciprocityFloor) * reciprocity)
    }

    public var activeDays: Int { days.count }

    public var name: String {
        if let card = card, !card.name.isEmpty { return card.name }
        // Most frequently observed display name wins; ties break on the name
        // itself so the result is stable across runs.
        let best = displays.sorted { a, b in
            a.value == b.value ? a.key < b.key : a.value > b.value
        }.first?.key
        if let best = best { return best }
        return keys.min()?.display ?? "?"
    }

    public var organisation: String { card?.organisation ?? "" }
    public var title: String { card?.title ?? "" }
    public var linkedIn: String { card?.linkedIn ?? "" }
    public var hasPhoto: Bool { card?.hasImage ?? false }
    public var inContacts: Bool { card != nil }
}
