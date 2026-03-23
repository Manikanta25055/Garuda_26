// ModesPanel.swift — Mode toggles (DND/Night/Emergency/Privacy/Idle/Email Off) + Emergency Stop
import SwiftUI

struct ModesPanel: View {

    // MARK: - State (all declared at top)
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @State private var loadingModes: Set<String> = []

    // Mode definitions: key / label / icon / active colour / help text
    private struct ModeConfig {
        let key: String
        let label: String
        let icon: String
        let color: Color
        let helpText: String
    }
    private let modes: [ModeConfig] = [
        ModeConfig(key: "privacy",   label: "Privacy Blur",  icon: "eye.slash",         color: GarudaTheme.accent, helpText: "Blur faces and sensitive areas in video feed"),
        ModeConfig(key: "night",     label: "Night Mode",    icon: "moon.fill",          color: .indigo, helpText: "Enhanced detection for low-light conditions"),
        ModeConfig(key: "dnd",       label: "Do Not Disturb",icon: "bell.slash.fill",    color: GarudaTheme.warning, helpText: "Mute audio alerts and notifications"),
        ModeConfig(key: "idle",      label: "Idle",          icon: "pause.circle",       color: GarudaTheme.textSecondary, helpText: "Pause all detections temporarily"),
        ModeConfig(key: "email_off", label: "Email Alerts Off",icon: "envelope.badge.slash",color: GarudaTheme.textSecondary, helpText: "Disable email notifications"),
        ModeConfig(key: "emergency", label: "Emergency",     icon: "exclamationmark.triangle.fill", color: GarudaTheme.danger, helpText: "Trigger immediate emergency protocols"),
    ]

    var body: some View {
        VStack(spacing: 0) {
            // Modern header
            HStack(spacing: 8) {
                Image(systemName: "switch.2")
                    .font(.system(size: 13))
                    .foregroundColor(GarudaTheme.accent)
                Text("System Modes")
                    .font(GarudaFont.mono(size: 13, weight: .semibold))
                    .foregroundColor(GarudaTheme.textPrimary)
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.top, 18)
            .padding(.bottom, 16)

            VStack(spacing: 8) {
                if let state = appState.systemState {
                    ForEach(modes, id: \.key) { mode in
                        ModernModeToggle(
                            label: mode.label,
                            icon: mode.icon,
                            isOn: .constant(modeValue(mode.key, from: state)),
                            activeColor: mode.color,
                            isLoading: loadingModes.contains(mode.key),
                            helpText: mode.helpText
                        ) {
                            Task { await toggleMode(mode.key, state: state) }
                        }
                    }
                } else {
                    ForEach(0..<6, id: \.self) { _ in
                        RoundedRectangle(cornerRadius: 12)
                            .fill(GarudaTheme.bgSurface2.opacity(0.4))
                            .frame(height: 44)
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 16)

            // Emergency Stop button
            emergencyStopButton
                .padding(.horizontal, 12)

            Spacer()
        }
        .padding(.bottom, 18)
        .modernGlassCard(cornerRadius: 20)
    }

    // MARK: - Emergency Stop
    private var emergencyStopButton: some View {
        Button {
            Task { await sessionManager.emergencyStop() }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.octagon.fill")
                    .font(.system(size: 15, weight: .semibold))
                Text("Emergency Stop")
                    .font(GarudaFont.mono(size: 13, weight: .semibold))
            }
            .foregroundColor(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(appState.isAlertActive ? GarudaTheme.danger : GarudaTheme.bgSurface3)
            )
        }
        .buttonStyle(.plain)
        .disabled(!appState.isAlertActive)
        .focusable(false)
    }

    // MARK: - Toggle Action
    private func toggleMode(_ key: String, state: SystemStateResponse) async {
        loadingModes.insert(key)
        let current = modeValue(key, from: state)
        try? await sessionManager.apiClient.setMode(key, value: !current)
        await sessionManager.pollState()
        loadingModes.remove(key)
    }

    // MARK: - Mode Value Extractor
    private func modeValue(_ key: String, from state: SystemStateResponse) -> Bool {
        switch key {
        case "dnd":       return state.modes.dnd
        case "night":     return state.modes.night
        case "emergency": return state.modes.emergency
        case "idle":      return state.modes.idle
        case "email_off": return state.modes.email_off
        case "privacy":   return state.modes.privacy
        default:          return false
        }
    }
}
