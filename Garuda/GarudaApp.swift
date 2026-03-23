// GarudaApp.swift — Final app entry point with Window + MenuBarExtra + Commands
import SwiftUI

@main
struct GarudaApp: App {

    // MARK: - State (declared at top)
    @StateObject private var appState       = AppState()
    @StateObject private var sessionManager = SessionManager()

    var body: some Scene {

        // Main window
        Window("Garuda", id: "main") {
            RootView()
                .environmentObject(appState)
                .environmentObject(sessionManager)
                .frame(
                    minWidth:  GarudaTheme.windowMinWidth,
                    minHeight: GarudaTheme.windowMinHeight
                )
                .background(GarudaTheme.bgPrimary)
                .onAppear {
                    // Attach session manager to app state and configure API client
                    sessionManager.attach(to: appState)
                    Task {
                        await sessionManager.apiClient.configure(
                            host: appState.rpiHost,
                            token: appState.sessionToken
                        )
                    }
                    // Request notification permission
                    Task { await NotificationManager.shared.requestAuthorization() }
                }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 400, height: 400)
        .commands {
            GarudaCommands(appState: appState, sessionManager: sessionManager)
        }

        // Menu bar extra — shows eye icon with connection status
        MenuBarExtra(
            "Garuda",
            systemImage: appState.menuBarIcon
        ) {
            GarudaMenuBarExtra()
                .environmentObject(appState)
                .environmentObject(sessionManager)
        }
        .menuBarExtraStyle(.menu)
    }
}

// MARK: - Root View (auth router — animated transition)
struct RootView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        Group {
            if appState.authState == .authenticated {
                AppShell()
            } else {
                WelcomeView()
            }
        }
        .animation(.easeInOut(duration: 0.4), value: appState.authState)
    }
}
