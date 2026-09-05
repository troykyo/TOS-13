import SwiftUI
import OrdituraCore

@main
struct OrdituraApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 980, minHeight: 620)
                .onAppear { model.probe() }
        }
        .commands {
            CommandGroup(after: .newItem) {
                Button("Refresh Ranking") { model.refresh() }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        if model.hasAccess || model.result != nil {
            RankingView()
        } else {
            AccessView()
        }
    }
}
