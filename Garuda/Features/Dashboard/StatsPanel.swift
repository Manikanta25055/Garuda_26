// StatsPanel.swift — System readout panel using bREADth-style ReadoutRow components
import SwiftUI

struct StatsPanel: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState

    var body: some View {
        VStack(spacing: 0) {
            // Modern header with icon
            HStack(spacing: 8) {
                Image(systemName: "chart.bar.fill")
                    .font(.system(size: 13))
                    .foregroundColor(GarudaTheme.accent)
                Text("System Stats")
                    .font(GarudaFont.mono(size: 13, weight: .semibold))
                    .foregroundColor(GarudaTheme.textPrimary)
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.top, 18)
            .padding(.bottom, 16)

            if let state = appState.systemState {
                VStack(spacing: 16) {
                    statRow {
                        ModernStatCard(
                            icon: "clock.fill",
                            label: "Uptime",
                            value: state.uptime,
                            valueColor: GarudaTheme.success,
                            helpText: "How long the system has been running continuously"
                        )
                    }
                    statRow {
                        ModernStatCard(
                            icon: "bell.badge.fill",
                            label: "Detections Today",
                            value: "\(state.detections_today)",
                            valueColor: state.detections_today > 0 ? GarudaTheme.warning : GarudaTheme.textSecondary,
                            helpText: "Total number of detections in the past 24 hours"
                        )
                    }
                    statRow {
                        ModernStatCard(
                            icon: "clock.arrow.circlepath",
                            label: "Last Alert",
                            value: state.last_alert.flatMap { relativeTime($0) } ?? "None",
                            valueColor: GarudaTheme.textSecondary,
                            helpText: "Time elapsed since the most recent alert was triggered"
                        )
                    }
                    statRow {
                        ModernStatCard(
                            icon: "gauge.high",
                            label: "Threshold",
                            value: String(format: "%.0f%%", state.detection_threshold * 100),
                            valueColor: GarudaTheme.accent,
                            helpText: "Minimum confidence level required to trigger detection"
                        )
                    }
                }
                .padding(.horizontal, 12)
            } else {
                // Skeleton placeholder
                VStack(spacing: 12) {
                    ForEach(0..<4, id: \.self) { _ in
                        skeletonCard
                    }
                }
                .padding(.horizontal, 12)
            }

            Spacer()
        }
        .padding(.bottom, 18)
        .modernGlassCard(cornerRadius: 20)
    }

    // MARK: - Row Wrapper
    private func statRow<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
    }

    // MARK: - Skeleton
    private var skeletonCard: some View {
        RoundedRectangle(cornerRadius: 12)
            .fill(GarudaTheme.bgSurface2.opacity(0.4))
            .frame(height: 68)
    }

    // MARK: - Helpers
    private func relativeTime(_ isoString: String) -> String? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = formatter.date(from: isoString) else { return isoString }
        let elapsed = Int(-date.timeIntervalSinceNow)
        if elapsed < 60  { return "\(elapsed)s ago" }
        if elapsed < 3600 { return "\(elapsed/60)m ago" }
        return "\(elapsed/3600)h ago"
    }
}
