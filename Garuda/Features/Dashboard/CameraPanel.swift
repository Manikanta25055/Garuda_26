// CameraPanel.swift — Live MJPEG stream with status overlays and alert border
import SwiftUI

struct CameraPanel: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @State private var alertPulse: Bool = false

    var body: some View {
        ZStack {
            // Main stream or fallback placeholder
            if let url = appState.streamURL {
                LiveStreamView(url: url)
            } else {
                streamFallback
            }

            // Bottom overlay: "LIVE" badge + detection info
            VStack {
                Spacer()
                HStack(alignment: .bottom, spacing: 0) {
                    // Live indicator
                    HStack(spacing: 6) {
                        PulsingDot(color: appState.isAlertActive ? GarudaTheme.danger : GarudaTheme.success, size: 5)
                        Text(appState.isAlertActive ? "ALERT" : "LIVE")
                            .font(GarudaFont.mono(size: 10, weight: .bold))
                            .foregroundColor(appState.isAlertActive ? GarudaTheme.danger : GarudaTheme.success)
                    }
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(.black.opacity(0.6))
                    .clipShape(RoundedRectangle(cornerRadius: GarudaTheme.radiusSM))

                    Spacer()

                    // Latest detection label if available
                    if let latest = appState.detectionEvents.first {
                        HStack(spacing: 6) {
                            Text(latest.label.uppercased())
                                .font(GarudaFont.mono(size: 10, weight: .bold))
                                .foregroundColor(GarudaTheme.textPrimary)
                            Text(String(format: "%.0f%%", latest.confidence * 100))
                                .font(GarudaFont.mono(size: 10))
                                .foregroundColor(GarudaTheme.textSecondary)
                        }
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(.black.opacity(0.6))
                        .clipShape(RoundedRectangle(cornerRadius: GarudaTheme.radiusSM))
                    }
                }
                .padding(12)
            }

            // Top-right: connection status badge
            VStack {
                HStack {
                    Spacer()
                    connectionBadge
                        .padding(12)
                }
                Spacer()
            }

            // Alert pulsing border
            if appState.isAlertActive {
                RoundedRectangle(cornerRadius: GarudaTheme.radiusMD)
                    .strokeBorder(
                        GarudaTheme.danger.opacity(alertPulse ? 1.0 : 0.3),
                        lineWidth: 2
                    )
                    .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true), value: alertPulse)
                    .onAppear { alertPulse = true }
                    .onDisappear { alertPulse = false }
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .modernGlassCard(cornerRadius: 20, opacity: 0.5)
    }

    // MARK: - Fallback
    private var streamFallback: some View {
        ZStack {
            GarudaTheme.bgSurface1
            VStack(spacing: 12) {
                Image(systemName: "video.slash.fill")
                    .font(.system(size: 40))
                    .foregroundColor(GarudaTheme.textQuaternary)
                Text("No stream URL configured")
                    .font(GarudaFont.mono(size: 12))
                    .foregroundColor(GarudaTheme.textTertiary)
                Text("Set the RPi5 host in Connection Settings")
                    .font(GarudaFont.mono(size: 10))
                    .foregroundColor(GarudaTheme.textQuaternary)
            }
        }
    }

    // MARK: - Connection Badge
    private var connectionBadge: some View {
        HStack(spacing: 5) {
            ConnectionDot(status: appState.connectionStatus)
            Text(connectionLabel)
                .font(GarudaFont.mono(size: 10))
                .foregroundColor(GarudaTheme.textSecondary)
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .background(.black.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: GarudaTheme.radiusSM))
    }

    private var connectionLabel: String {
        switch appState.connectionStatus {
        case .connected:    return "Connected"
        case .connecting:   return "Connecting…"
        case .disconnected: return "Disconnected"
        case .error(let m): return m
        }
    }
}
