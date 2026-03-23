// KeyboardShortcuts.swift — Custom Garuda menu with app-wide keyboard shortcuts
import SwiftUI

struct GarudaCommands: Commands {

    // Need to read appState — use @ObservedObject (ObservableObject already in env)
    @ObservedObject var appState: AppState
    @ObservedObject var sessionManager: SessionManager

    var body: some Commands {
        // Replace the default "New" menu item with nothing (no file creation)
        CommandGroup(replacing: .newItem) {}

        // Garuda-specific menu
        CommandMenu("Garuda") {
            // Navigation
            Button("Dashboard") { appState.selectedSidebarItem = .dashboard }
                .keyboardShortcut("1", modifiers: .command)

            Button("Alerts") { appState.selectedSidebarItem = .alerts }
                .keyboardShortcut("2", modifiers: .command)

            Button("Narada") { appState.selectedSidebarItem = .narada }
                .keyboardShortcut("3", modifiers: .command)

            if appState.currentRole == .admin {
                Button("Admin Panel") { appState.selectedSidebarItem = .admin }
                    .keyboardShortcut("4", modifiers: .command)
            }

            Divider()

            // System actions
            Button("Emergency Stop") {
                Task { await sessionManager.emergencyStop() }
            }
            .keyboardShortcut("e", modifiers: [.command, .shift])
            .disabled(!appState.isAlertActive)

            Button("Reconnect") {
                Task {
                    await sessionManager.apiClient.configure(
                        host: appState.rpiHost,
                        token: appState.sessionToken
                    )
                    await sessionManager.pollState()
                }
            }
            .keyboardShortcut("r", modifiers: [.command, .option])

            Divider()

            Button("Sign Out") {
                Task { await sessionManager.logout() }
            }
            .keyboardShortcut("q", modifiers: [.command, .shift])
        }
    }
}
