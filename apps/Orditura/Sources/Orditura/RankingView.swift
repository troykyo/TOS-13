import SwiftUI
import AppKit
import UniformTypeIdentifiers
import OrdituraCore

struct RankingView: View {
    @EnvironmentObject private var model: AppModel
    @State private var selection: Person.ID?

    private var selected: Person? {
        model.visiblePeople.first { $0.id == selection }
    }

    var body: some View {
        NavigationSplitView {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 240, ideal: 280, max: 340)
        } detail: {
            HSplitView {
                peopleList
                    .frame(minWidth: 380)
                DetailPane(person: selected)
                    .frame(minWidth: 280, idealWidth: 320)
            }
        }
        .navigationTitle("Orditura")
        .searchable(text: $model.search, placement: .toolbar, prompt: "Name, organisation or address")
        .toolbar { toolbar }
        .task { if model.result == nil { model.refresh() } }
    }

    private var peopleList: some View {
        VStack(spacing: 0) {
            if model.isWorking && model.result == nil {
                ProgressView(model.note).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.visiblePeople.isEmpty {
                EmptyStateView()
            } else {
                List(model.visiblePeople, selection: $selection) { person in
                    PersonRowView(person: person,
                                  topScore: model.visiblePeople.first?.score ?? 1)
                }
                .listStyle(.inset)
            }
            Divider()
            HStack {
                Text(model.note).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                if model.isWorking { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Picker("Sort", selection: $model.sortKey) {
                ForEach(AppModel.SortKey.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.menu)
        }
        ToolbarItem(placement: .primaryAction) {
            Toggle(isOn: $model.onlyMissingPhotos) {
                Label("Needs a photo", systemImage: "person.crop.circle.badge.questionmark")
            }
        }
        ToolbarItem(placement: .primaryAction) {
            Menu {
                Button("Export CSV…") { export(.commaSeparatedText) }
                Button("Export JSON…") { export(.json) }
            } label: {
                Label("Export", systemImage: "square.and.arrow.up")
            }
            .disabled(model.visiblePeople.isEmpty)
        }
        ToolbarItem(placement: .primaryAction) {
            Button { model.refresh() } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .disabled(model.isWorking)
        }
    }

    /// Writes exactly what is on screen, after the current search and filters —
    /// an export that silently included rows you had filtered out would be a
    /// small betrayal, and this file is personal data in the clear.
    private func export(_ type: UTType) {
        let people = model.visiblePeople
        let panel = NSSavePanel()
        panel.allowedContentTypes = [type]
        panel.nameFieldStringValue = type == .json ? "ranking.json" : "ranking.csv"
        panel.message = "This file contains names, addresses and phone numbers in the clear."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            if type == .json {
                try Export.json(people).write(to: url)
            } else {
                try Export.csv(people).write(to: url, atomically: true, encoding: .utf8)
            }
        } catch {
            NSAlert(error: error).runModal()
        }
    }
}

// MARK: - Sidebar

struct Sidebar: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Observation window") {
                LabeledContent("Since") {
                    Text("\(Int(model.windowDays / 30)) months").monospacedDigit()
                }
                Slider(value: $model.windowDays, in: 90...1825, step: 30)
                Text("How far back to look. Narrow this to ask the present-tense "
                     + "question; widen it to surface people who have gone quiet.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Section("Recency half-life") {
                LabeledContent("Half-life") {
                    Text("\(Int(model.halfLifeDays)) days").monospacedDigit()
                }
                Slider(value: $model.halfLifeDays, in: 30...540, step: 15)
                Text("An interaction this old counts for half of a fresh one.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Section("Sources") {
                Toggle("Siri interaction store", isOn: $model.includeCoreDuet)
                Text(Store.coreduet.explanation)
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(model.statuses) { status in
                    HStack(spacing: 6) {
                        Circle()
                            .fill(status.isUsable ? Color.green : Color.secondary.opacity(0.4))
                            .frame(width: 7, height: 7)
                        Text(status.store.title).font(.callout)
                        Spacer()
                        if case .readable(let rows) = status.state {
                            Text("\(rows)").font(.caption).monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Section {
                Button("Apply") { model.refresh() }
                    .disabled(model.isWorking)
            }
        }
        .formStyle(.grouped)
    }
}

// MARK: - Rows

struct PersonRowView: View {
    let person: Person
    let topScore: Double

    private var bar: Double {
        topScore > 0 ? max(0.02, min(1, person.score / topScore)) : 0
    }

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle().fill(Color.accentColor.opacity(0.15))
                Image(systemName: person.hasPhoto ? "person.crop.circle.fill" : "person.crop.circle")
                    .foregroundStyle(person.hasPhoto ? Color.accentColor : Color.secondary)
            }
            .frame(width: 26, height: 26)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(person.name).fontWeight(.medium).lineLimit(1)
                    if !person.linkedIn.isEmpty {
                        Image(systemName: "link").font(.caption2).foregroundStyle(.secondary)
                    }
                    if person.meetings > 0 {
                        Label("\(person.meetings)", systemImage: "calendar")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: 6) {
                    if !person.organisation.isEmpty {
                        Text(person.organisation).lineLimit(1)
                    }
                    Text("·")
                    Text("\(person.activeDays) days")
                    Text("·")
                    Text(reciprocityLabel)
                }
                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                GeometryReader { geo in
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(Color.accentColor.opacity(0.55))
                        .frame(width: geo.size.width * bar, height: 3)
                }
                .frame(height: 3)
            }
            Spacer(minLength: 8)
            Text(String(format: "%.0f", person.score))
                .font(.callout).monospacedDigit().foregroundStyle(.secondary)
        }
        .padding(.vertical, 3)
    }

    /// Reciprocity is the term that separates a correspondence from a feed, so
    /// it is worth saying in words rather than as a bare number.
    private var reciprocityLabel: String {
        switch person.reciprocity {
        case ..<0.15:  return "one-sided"
        case ..<0.45:  return "lopsided"
        case ..<0.75:  return "two-way"
        default:       return "balanced"
        }
    }
}

struct EmptyStateView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "person.2.slash")
                .font(.largeTitle).foregroundStyle(.secondary)
            Text("Nothing to rank yet").font(.headline)
            if !model.denied.isEmpty {
                Text("\(model.denied.count) store(s) are blocked by Full Disk Access.")
                    .foregroundStyle(.secondary)
            } else {
                Text("No interactions in the selected window. Try widening it.")
                    .foregroundStyle(.secondary)
            }
            ForEach(model.result?.failures ?? []) { report in
                Text("\(report.store.title): \(report.error ?? "")")
                    .font(.caption).foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}

// MARK: - Detail

struct DetailPane: View {
    let person: Person?

    var body: some View {
        if let person = person {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(person.name).font(.title3).bold()
                        if !person.title.isEmpty || !person.organisation.isEmpty {
                            Text([person.title, person.organisation]
                                .filter { !$0.isEmpty }.joined(separator: " · "))
                                .foregroundStyle(.secondary)
                        }
                    }

                    Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
                        StatRow(label: "Tie strength", value: String(format: "%.1f", person.score))
                        StatRow(label: "Reciprocity",
                                value: String(format: "%.2f", person.reciprocity))
                        StatRow(label: "Sent / received",
                                value: "\(person.sent) / \(person.received)")
                        StatRow(label: "Meetings", value: "\(person.meetings)")
                        StatRow(label: "Calls", value: "\(person.calls)")
                        StatRow(label: "Days in contact", value: "\(person.activeDays)")
                        StatRow(label: "First seen", value: Export.date(person.firstSeen))
                        StatRow(label: "Last seen", value: Export.date(person.lastSeen))
                    }

                    Section2("Channels") {
                        HStack {
                            ForEach(person.channels.map(\.rawValue).sorted(), id: \.self) { name in
                                Text(name)
                                    .font(.caption)
                                    .padding(.horizontal, 7).padding(.vertical, 2)
                                    .background(Color.secondary.opacity(0.12), in: Capsule())
                            }
                        }
                    }

                    Section2("Identities") {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(person.keys.sorted(), id: \.self) { key in
                                Text(key.display)
                                    .font(.callout).textSelection(.enabled)
                            }
                        }
                    }

                    Section2("Enrichment") {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(person.hasPhoto ? "Contact photo on file"
                                                  : "No contact photo",
                                  systemImage: person.hasPhoto ? "checkmark.circle" : "circle.dashed")
                            if person.linkedIn.isEmpty {
                                Label("No LinkedIn URL on file", systemImage: "circle.dashed")
                            } else if let url = URL(string: person.linkedIn) {
                                Link(destination: url) {
                                    Label("Open LinkedIn profile", systemImage: "link")
                                }
                            } else {
                                Text(person.linkedIn).font(.callout).textSelection(.enabled)
                            }
                            if !person.inContacts {
                                Label("Not in your address book",
                                      systemImage: "exclamationmark.circle")
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .font(.callout)
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            Text("Select someone")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

struct StatRow: View {
    let label: String
    let value: String

    var body: some View {
        GridRow {
            Text(label).foregroundStyle(.secondary)
            Text(value).monospacedDigit()
        }
        .font(.callout)
    }
}

/// A small titled block. Named to avoid colliding with SwiftUI's own `Section`,
/// which is a Form/List construct rather than a general container.
struct Section2<Content: View>: View {
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(.caption2).bold().foregroundStyle(.secondary).kerning(0.6)
            content
        }
    }
}
