import Foundation
import OrdituraCore

/// Everything the window binds to. Deliberately not `@MainActor`: the ranking
/// runs on a background queue and hops back through `DispatchQueue.main`, which
/// keeps the concurrency model boring and the build portable across toolchains.
final class AppModel: ObservableObject {
    enum SortKey: String, CaseIterable, Identifiable {
        case score = "Tie strength"
        case lastSeen = "Most recent"
        case activeDays = "Most days in contact"
        case meetings = "Most meetings"
        case name = "Name"
        var id: String { rawValue }
    }

    @Published private(set) var statuses: [StoreStatus] = []
    @Published private(set) var result: RankingResult?
    @Published private(set) var isWorking = false
    @Published private(set) var note = ""

    @Published var windowDays: Double = Scoring.defaultWindowDays
    @Published var halfLifeDays: Double = Scoring.defaultHalfLifeDays
    @Published var minimumActiveDays = 3
    @Published var includeCoreDuet = false
    @Published var search = ""
    @Published var sortKey: SortKey = .score
    @Published var onlyMissingPhotos = false

    /// True once at least one Full Disk Access–gated store can actually be read.
    var hasAccess: Bool {
        statuses.contains { $0.store.needsFullDiskAccess && $0.isUsable }
    }

    var denied: [StoreStatus] {
        statuses.filter { $0.state == .denied }
    }

    var options: RankingOptions {
        RankingOptions(windowDays: windowDays,
                       halfLifeDays: halfLifeDays,
                       minimumActiveDays: minimumActiveDays,
                       includeCoreDuet: includeCoreDuet)
    }

    /// The ranked list after the view's own filters. Kept here rather than in
    /// the view so that an export writes exactly what is on screen.
    var visiblePeople: [Person] {
        guard let people = result?.people else { return [] }
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var filtered = people
        if !needle.isEmpty {
            filtered = filtered.filter { person in
                person.name.lowercased().contains(needle)
                    || person.organisation.lowercased().contains(needle)
                    || person.keys.contains { $0.display.contains(needle) }
            }
        }
        if onlyMissingPhotos {
            filtered = filtered.filter { !$0.hasPhoto }
        }
        switch sortKey {
        case .score:      return filtered            // already sorted by the ranker
        case .name:       return filtered.sorted { $0.name.localizedCompare($1.name) == .orderedAscending }
        case .activeDays: return filtered.sorted { $0.activeDays > $1.activeDays }
        case .meetings:   return filtered.sorted { $0.meetings > $1.meetings }
        case .lastSeen:
            return filtered.sorted {
                ($0.lastSeen ?? .distantPast) > ($1.lastSeen ?? .distantPast)
            }
        }
    }

    func probe() {
        DispatchQueue.global(qos: .userInitiated).async {
            let found = StoreProbe.probeAll()
            DispatchQueue.main.async { self.statuses = found }
        }
    }

    func refresh() {
        guard !isWorking else { return }
        isWorking = true
        note = "Reading local stores…"
        let options = self.options
        DispatchQueue.global(qos: .userInitiated).async {
            let statuses = StoreProbe.probeAll()
            let built = ContactGraph.build(options: options) { store in
                DispatchQueue.main.async { self.note = "Reading \(store.title)…" }
            }
            DispatchQueue.main.async {
                self.statuses = statuses
                self.result = built
                self.isWorking = false
                self.note = self.summary(of: built)
            }
        }
    }

    private func summary(of result: RankingResult) -> String {
        let people = result.people.count
        let missing = result.people.filter { !$0.hasPhoto }.count
        let known = result.people.filter { !$0.linkedIn.isEmpty }.count
        return "\(people) people from \(result.totalEvents) interactions · "
             + "\(missing) without a photo · \(known) with a LinkedIn URL already on file"
    }
}
