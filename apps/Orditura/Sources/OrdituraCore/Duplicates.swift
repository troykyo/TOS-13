import Foundation

/// Case-, accent- and spacing-insensitive form of a display name.
func foldName(_ name: String) -> String {
    name.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: nil)
        .split(whereSeparator: { $0.isWhitespace })
        .joined(separator: " ")
}

/// People who look like one person split across two identities.
///
/// Identity fusion only merges what a single address-book card links together,
/// so someone who writes from a second address that is not on their card
/// appears twice, with their tie strength divided between the halves. The two
/// shapes this takes in practice are an identical name, and one name that is a
/// prefix of a longer one — a double or married surname added later.
///
/// This reports rather than merges. Fusing on a name alone silently combines
/// homonyms, and the real repair is to merge the cards in Contacts, which fixes
/// every future run and everything else that reads the address book.
/// `RankingOptions.fuseByName` is there for when that is not worth the trouble.
public func likelyDuplicates(_ people: [Person]) -> [[Person]] {
    var byName: [String: [Person]] = [:]
    for person in people {
        let folded = foldName(person.name)
        if !folded.isEmpty { byName[folded, default: []].append(person) }
    }

    var groups = byName.values.filter { $0.count > 1 }
    var claimed = Set(groups.flatMap { $0 }.map(\.id))

    // "Bruna Goveia" and "Bruna Goveia da Rocha": one name extending another at
    // a word boundary, which an exact match cannot see. A single given name is
    // never enough — it would swallow everyone who shares it.
    let names = byName.keys.sorted()
    for (i, short) in names.enumerated() where short.split(separator: " ").count >= 2 {
        for long in names[(i + 1)...] {
            guard long.hasPrefix(short + " ") else { break }
            let merged = (byName[short]! + byName[long]!).filter { !claimed.contains($0.id) }
            if merged.count > 1 {
                groups.append(merged)
                claimed.formUnion(merged.map(\.id))
            }
        }
    }
    return groups.map { $0.sorted { $0.score > $1.score } }
}

/// Merge the groups `likelyDuplicates` finds. Opt-in: see its documentation.
public func fuseByName(_ people: [Person]) -> [Person] {
    let groups = likelyDuplicates(people)
    guard !groups.isEmpty else { return people }

    var absorbed = Set<Identity>()
    var merged: [Person] = []
    for group in groups {
        var head = group[0]
        for other in group.dropFirst() {
            absorbed.insert(other.id)
            head.keys.formUnion(other.keys)
            head.channels.formUnion(other.channels)
            head.days.formUnion(other.days)
            for (name, count) in other.displays { head.displays[name, default: 0] += count }
            head.outScore += other.outScore
            head.inScore += other.inScore
            head.sent += other.sent
            head.received += other.received
            head.meetings += other.meetings
            head.calls += other.calls
            head.firstSeen = [head.firstSeen, other.firstSeen].compactMap { $0 }.min()
            head.lastSeen = [head.lastSeen, other.lastSeen].compactMap { $0 }.max()
            if head.card == nil { head.card = other.card }
        }
        absorbed.insert(head.id)
        merged.append(head)
    }
    let untouched = people.filter { !absorbed.contains($0.id) }
    return (untouched + merged).sorted { a, b in
        a.score == b.score ? a.name < b.name : a.score > b.score
    }
}
