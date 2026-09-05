import Foundation

/// What one store contributed to a run, or why it contributed nothing.
public struct SourceReport: Identifiable, Equatable {
    public let store: Store
    public let url: URL?
    public let events: Int
    public let error: String?

    public var id: String { store.rawValue + "|" + (url?.path ?? "") }
    public var succeeded: Bool { error == nil }
}

public struct RankingResult {
    public let people: [Person]
    public let reports: [SourceReport]
    public let cardCount: Int
    public let generated: Date
    public let options: RankingOptions

    public var totalEvents: Int { reports.reduce(0) { $0 + $1.events } }
    public var failures: [SourceReport] { reports.filter { !$0.succeeded } }
}

public enum ContactGraph {
    /// Collect every available source, fuse identities, and rank.
    ///
    /// A store that is absent, blocked by TCC or shaped unexpectedly costs a
    /// source, never the run — the result reports what happened per store so
    /// the UI can say so precisely rather than silently under-reporting.
    public static func build(options: RankingOptions = RankingOptions(),
                             now: Date = Date(),
                             home: URL = StoreLocations.home,
                             progress: ((Store) -> Void)? = nil) -> RankingResult {
        let since = options.since(now)
        var events: [Event] = []
        var reports: [SourceReport] = []

        func run(_ store: Store, _ url: URL, _ body: () throws -> [Event]) {
            do {
                let produced = try body()
                events.append(contentsOf: produced)
                reports.append(SourceReport(store: store, url: url,
                                            events: produced.count, error: nil))
            } catch {
                reports.append(SourceReport(store: store, url: url, events: 0,
                                            error: error.localizedDescription))
            }
        }

        for store in Store.allCases {
            if store == .contacts { continue }
            if store.isOptIn && !options.includeCoreDuet { continue }
            progress?(store)
            let urls = store.locations(home: home)
            if urls.isEmpty {
                reports.append(SourceReport(store: store, url: nil, events: 0,
                                            error: "not present"))
                continue
            }
            for url in urls {
                switch store {
                case .mail:
                    run(store, url) { try MailExtractor.extract(from: url, since: since) }
                case .imessage:
                    run(store, url) { try MessagesExtractor.extract(from: url, since: since) }
                case .call:
                    run(store, url) { try CallExtractor.extract(from: url, since: since) }
                case .calendar:
                    run(store, url) {
                        try CalendarExtractor.extract(from: url, since: since,
                                                      maximumAttendees: options.maximumAttendees)
                    }
                case .coreduet:
                    run(store, url) { try CoreDuetExtractor.extract(from: url, since: since) }
                case .contacts:
                    continue
                }
            }
        }

        progress?(.contacts)
        let cardURLs = Store.contacts.locations(home: home)
        let cards = ContactsExtractor.extract(from: cardURLs)
        reports.append(SourceReport(store: .contacts,
                                    url: cardURLs.first,
                                    events: cards.count,
                                    error: cardURLs.isEmpty ? "not present" : nil))

        let people = Ranker.rank(events: events, cards: cards, now: now, options: options)
        return RankingResult(people: people, reports: reports, cardCount: cards.count,
                             generated: now, options: options)
    }
}
