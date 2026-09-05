import Foundation

/// The tie-strength model. See docs/proposals/0001 §2.3 for the rationale;
/// the short version is that counting messages ranks your newsletters above
/// your collaborators, and each term below corrects one way in which it does.
public enum Scoring {
    /// Channel and direction weights. Outbound acts cost the user effort and
    /// are stronger evidence of an intentional tie than inbound ones, which
    /// the counterparty can manufacture at no cost.
    public static let weights: [Event.Channel: (out: Double, incoming: Double)] = [
        .calendar: (6.0, 6.0),   // mutual by construction; split in `credit`
        .call:     (5.0, 4.0),
        .mail:     (3.0, 1.0),
        .imessage: (2.0, 1.5),
        .coreduet: (1.0, 0.5),
    ]

    /// A wholly one-sided tie keeps this share of its volume: a publisher you
    /// read every week is something, just not a contact.
    public static let reciprocityFloor = 0.25

    public static let defaultHalfLifeDays = 180.0
    public static let defaultWindowDays = 1095.0   // three years

    /// A message addressed to *n* counterparties carries 1/(1+log₂n) of the
    /// weight of a private one. Smooth decay rather than an arbitrary cutoff:
    /// a mail to thirty people is worth about 17% of a mail to you alone.
    public static func audienceDamping(_ n: Int) -> Double {
        1.0 / (1.0 + log2(Double(max(1, n))))
    }

    public static func recencyDecay(age: TimeInterval, halfLifeDays: Double) -> Double {
        let lambda = log(2.0) / (halfLifeDays * 86_400)
        return exp(-lambda * max(0, age))
    }

    public static func weight(for event: Event, now: Date, halfLifeDays: Double) -> Double {
        guard let w = weights[event.channel] else { return 0 }
        let base = event.direction == .incoming ? w.incoming : w.out
        return base
            * audienceDamping(event.audience)
            * recencyDecay(age: now.timeIntervalSince(event.date), halfLifeDays: halfLifeDays)
    }
}

public struct RankingOptions {
    public var window: TimeInterval
    public var halfLifeDays: Double
    public var minimumActiveDays: Int
    public var dropRoleAddresses: Bool
    /// Drop senders you never write back to. See `Ranker.isBroadcaster`.
    public var dropBroadcasters: Bool
    /// Merge people who appear twice under different addresses. Off by default:
    /// see `likelyDuplicates`.
    public var fuseByName: Bool
    public var includeCoreDuet: Bool
    /// Meetings above this size are ignored: an all-hands is not a relationship.
    public var maximumAttendees: Int

    public init(windowDays: Double = Scoring.defaultWindowDays,
                halfLifeDays: Double = Scoring.defaultHalfLifeDays,
                minimumActiveDays: Int = 3,
                dropRoleAddresses: Bool = true,
                dropBroadcasters: Bool = true,
                fuseByName: Bool = false,
                includeCoreDuet: Bool = false,
                maximumAttendees: Int = 25) {
        self.window = windowDays * 86_400
        self.halfLifeDays = halfLifeDays
        self.minimumActiveDays = minimumActiveDays
        self.dropRoleAddresses = dropRoleAddresses
        self.dropBroadcasters = dropBroadcasters
        self.fuseByName = fuseByName
        self.includeCoreDuet = includeCoreDuet
        self.maximumAttendees = maximumAttendees
    }

    public func since(_ now: Date) -> Date { now.addingTimeInterval(-window) }
}

public enum Ranker {
    /// UTC day index. Cheaper and more robust than formatting a date string
    /// once per event, and the only property needed is that two interactions on
    /// the same day collapse to one bucket.
    static func dayIndex(of date: Date) -> Int {
        Int(floor(date.timeIntervalSince1970 / 86_400))
    }

    /// Fuse identities, accumulate weighted evidence, attach address-book
    /// records, and sort by tie strength.
    public static func rank(events: [Event],
                            cards: [Card],
                            now: Date = Date(),
                            options: RankingOptions = RankingOptions()) -> [Person] {
        var dsu = DisjointSet<Identity>()
        var cardByIdentity: [Identity: Card] = [:]
        for card in cards {
            let ids = card.identities
            for id in ids where cardByIdentity[id] == nil { cardByIdentity[id] = card }
            for id in ids.dropFirst() { dsu.union(ids[0], id) }
        }

        var people: [Identity: Person] = [:]
        for event in events {
            if options.dropRoleAddresses && RoleAddress.matches(event.key) { continue }
            let root = dsu.find(event.key)
            var person = people[root] ?? Person(id: root)

            person.keys.insert(event.key)
            person.channels.insert(event.channel)
            if let display = event.display?.trimmingCharacters(in: .whitespacesAndNewlines),
               !display.isEmpty, !display.contains("@") {
                person.displays[display, default: 0] += 1
            }

            // The counters are counts of interactions, not of weighted ones, so
            // they increment regardless of how far the weight has decayed.
            let w = Scoring.weight(for: event, now: now, halfLifeDays: options.halfLifeDays)
            switch event.direction {
            case .mutual:
                // A meeting is inherently mutual: credit both directions at
                // half weight, so it strengthens the tie without distorting
                // reciprocity.
                person.outScore += w / 2
                person.inScore += w / 2
                person.meetings += 1
            case .outgoing:
                person.outScore += w
                person.sent += 1
                if event.channel == .call { person.calls += 1 }
            case .incoming:
                person.inScore += w
                person.received += 1
                if event.channel == .call { person.calls += 1 }
            }

            person.days.insert(dayIndex(of: event.date))
            person.firstSeen = min(person.firstSeen ?? event.date, event.date)
            person.lastSeen = max(person.lastSeen ?? event.date, event.date)
            people[root] = person
        }

        for root in Array(people.keys) {
            guard let keys = people[root]?.keys else { continue }
            people[root]?.card = keys.sorted().compactMap { cardByIdentity[$0] }.first
        }

        let ranked = people.values
            .filter { keep($0, options: options) }
            .sorted { a, b in
                a.score == b.score ? a.name < b.name : a.score > b.score
            }
        return options.fuseByName ? fuseByName(ranked) : ranked
    }

    /// Someone you have never written to — or write to less than once in twenty
    /// — is a broadcaster, however much they send you.
    ///
    /// Structural rather than lexical, which is the point: a keyword list only
    /// ever catches the senders someone thought of, and every newsletter that
    /// slips through does so because its address looks like a person's. Volume
    /// of inbound mail is not evidence of a relationship; a reply is. A call or
    /// a meeting overrides it outright.
    static func isBroadcaster(_ person: Person) -> Bool {
        if person.meetings > 0 || person.calls > 0 { return false }
        if person.sent == 0 && person.received >= 5 { return true }
        if person.received >= 20,
           Double(person.sent) / Double(person.received) < 0.05 { return true }
        return false
    }

    /// Drop one-shot bursts. A tie is evidenced by recurrence across distinct
    /// days, not by one busy afternoon — unless a call or a meeting took place,
    /// which is strong enough on its own. Kept as a filter rather than folded
    /// into the score, so that exclusions stay auditable.
    static func keep(_ person: Person, options: RankingOptions) -> Bool {
        if options.dropBroadcasters && isBroadcaster(person) { return false }
        return person.activeDays >= options.minimumActiveDays
            || person.meetings > 0
            || person.calls > 0
    }
}
