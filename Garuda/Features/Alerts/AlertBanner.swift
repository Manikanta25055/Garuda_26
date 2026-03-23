// AlertBanner.swift — Full-width active alert banner pinned at the top of AppShell
import SwiftUI

struct ActiveAlertBanner: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager

    var body: some View {
        HStack(spacing: 12) {
            PulsingDot(color: GarudaTheme.danger, size: 6)

            VStack(alignment: .leading, spacing: 2) {
                Text("ALERT ACTIVE")
                    .font(GarudaFont.alertTitle())
                    .foregroundColor(GarudaTheme.danger)
                if let latest = appState.detectionEvents.first {
                    Text("\(latest.label.capitalized) detected at \(latest.timestamp.formatted(.dateTime.hour().minute().second()))")
                        .font(GarudaFont.alertDetail())
                        .foregroundColor(GarudaTheme.textSecondary)
                }
            }

            Spacer()

            // Detections count badge
            if !appState.detectionEvents.isEmpty {
                StatusBadge(
                    label: "\(appState.detectionEvents.count) events",
                    color: GarudaTheme.textQuaternary
                )
            }

            // Stop button
            Button {
                Task { await sessionManager.emergencyStop() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 11))
                    Text("STOP")
                        .font(GarudaFont.mono(size: 12, weight: .bold))
                }
                .foregroundColor(.white)
                .padding(.horizontal, 14).padding(.vertical, 7)
                .background(RoundedRectangle(cornerRadius: GarudaTheme.radiusMD)
                    .fill(GarudaTheme.danger))
            }
            .buttonStyle(.plain)
            .keyboardShortcut("e", modifiers: [.command, .shift])
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(GarudaTheme.danger.opacity(0.12))
        .overlay(alignment: .bottom) { GarudaDivider() }
    }
}
