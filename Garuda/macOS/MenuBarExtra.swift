// MenuBarExtra.swift — Menu bar icon with status and quick controls
import SwiftUI

struct GarudaMenuBarExtra: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @Environment(\.openWindow) var openWindow

    var body: some View {
        // Connection status
        HStack(spacing: 8) {
            ConnectionDot(status: appState.connectionStatus)
            Text(connectionLabel)
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textPrimary)
            Spacer()
        }
        .padding(.horizontal, 4)

        Divider()

        // Stats
        if let state = appState.systemState {
            Text("Detections today: \(state.detections_today)")
                .font(GarudaFont.mono(size: 11))
                .foregroundColor(GarudaTheme.textSecondary)

            if state.modes.dnd || state.modes.night || state.modes.emergency || state.modes.privacy {
                Divider()
                Text("Active modes:")
                    .font(GarudaFont.mono(size: 10))
                    .foregroundColor(GarudaTheme.textTertiary)
                if state.modes.dnd       { modeItem("DND",       color: GarudaTheme.warning) }
                if state.modes.night     { modeItem("NIGHT",     color: .indigo) }
                if state.modes.emergency { modeItem("EMERGENCY", color: GarudaTheme.danger) }
                if state.modes.privacy   { modeItem("PRIVACY",   color: GarudaTheme.accent) }
            }
        }

        Divider()

        // Actions
        Button("Open Garuda") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        .keyboardShortcut("g", modifiers: [.command, .shift])

        if appState.isAlertActive {
            Button("Stop Alert") {
                Task { await sessionManager.emergencyStop() }
            }
            .keyboardShortcut("e", modifiers: [.command, .shift])
        }

        Divider()

        if appState.authState == .authenticated {
            Button("Sign Out") {
                Task { await sessionManager.logout() }
            }
        }
    }

    // MARK: - Helpers
    private var connectionLabel: String {
        switch appState.connectionStatus {
        case .connected:    return "Connected — \(appState.rpiHost)"
        case .connecting:   return "Connecting…"
        case .disconnected: return "Disconnected"
        case .error(let m): return "Error: \(m)"
        }
    }

    private func modeItem(_ label: String, color: Color) -> some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label)
                .font(GarudaFont.mono(size: 11))
                .foregroundColor(GarudaTheme.textSecondary)
        }
    }
}
