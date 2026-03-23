// DashboardView.swift — Main dashboard: live camera (left) + stats + modes (right)
import SwiftUI

struct DashboardView: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @State private var showHelp: Bool = false

    var body: some View {
        ZStack {
            // Background with grid
            GarudaTheme.bgPrimary.ignoresSafeArea()
            GridOverlay()
            
            VStack(spacing: 0) {
                // Top bar with help button
                HStack {
                    Spacer()
                    Button {
                        showHelp.toggle()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "questionmark.circle.fill")
                                .font(.system(size: 13))
                            Text("Quick Help")
                                .font(GarudaFont.mono(size: 11, weight: .medium))
                        }
                        .foregroundColor(GarudaTheme.textTertiary)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(
                            RoundedRectangle(cornerRadius: 8, style: .continuous)
                                .fill(GarudaTheme.bgSurface2.opacity(0.5))
                        )
                    }
                    .buttonStyle(.plain)
                    .focusable(false)
                }
                .padding(.horizontal, 20)
                .padding(.top, 16)
                .padding(.bottom, 8)
                
                HStack(spacing: 16) {
                    // Left: Camera panel (flexible width)
                    CameraPanel()

                    // Right: Stats + Modes stacked (fixed width)
                    VStack(spacing: 16) {
                        StatsPanel()
                            .frame(maxHeight: .infinity)
                        ModesPanel()
                            .frame(maxHeight: .infinity)
                    }
                    .frame(width: GarudaTheme.rightPanelWidth)
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 20)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(isPresented: $showHelp) {
            QuickHelpSheet()
        }
        .onAppear {
            Task { await sessionManager.pollState() }
        }
    }
}

// MARK: - Quick Help Sheet
struct QuickHelpSheet: View {
    @Environment(\.dismiss) var dismiss
    
    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                HStack(spacing: 8) {
                    Image(systemName: "book.fill")
                        .font(.system(size: 16))
                        .foregroundColor(GarudaTheme.accent)
                    Text("Dashboard Guide")
                        .font(GarudaFont.mono(size: 16, weight: .semibold))
                        .foregroundColor(GarudaTheme.textPrimary)
                }
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(GarudaTheme.textTertiary)
                }
                .buttonStyle(.plain)
                .focusable(false)
            }
            .padding(24)
            
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    HelpSection(
                        title: "Camera Feed",
                        icon: "video.fill",
                        items: [
                            "Live MJPEG stream from Raspberry Pi 5 camera",
                            "Real-time detection overlays with bounding boxes",
                            "Alert indicator shows red when person detected",
                            "Connection status shown in top-right corner"
                        ]
                    )
                    
                    HelpSection(
                        title: "System Stats",
                        icon: "chart.bar.fill",
                        items: [
                            "Uptime: How long the system has been running",
                            "Detections Today: Number of detections in past 24h",
                            "Last Alert: Time since most recent alert",
                            "Threshold: Confidence level required for detection"
                        ]
                    )
                    
                    HelpSection(
                        title: "System Modes",
                        icon: "switch.2",
                        items: [
                            "Privacy Blur: Blurs faces in video feed",
                            "Night Mode: Enhanced detection for low light",
                            "Do Not Disturb: Mutes audio alerts",
                            "Idle: Pauses all detections temporarily",
                            "Email Alerts Off: Disables email notifications",
                            "Emergency: Triggers immediate alert protocols"
                        ]
                    )
                    
                    HelpSection(
                        title: "Emergency Stop",
                        icon: "exclamationmark.octagon.fill",
                        items: [
                            "Immediately stops any active alerts",
                            "Clears alert state and resets system",
                            "Only active when an alert is triggered"
                        ]
                    )
                    
                    HelpSection(
                        title: "Keyboard Shortcuts",
                        icon: "command",
                        items: [
                            "Cmd+Shift+Q: Sign out",
                            "Cmd+R: Refresh system state",
                            "Cmd+,: Open settings"
                        ]
                    )
                }
                .padding(24)
            }
        }
        .frame(width: 560, height: 620)
        .background(GarudaTheme.bgPrimary)
    }
}

struct HelpSection: View {
    let title: String
    let icon: String
    let items: [String]
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 14))
                    .foregroundColor(GarudaTheme.accent)
                Text(title)
                    .font(GarudaFont.mono(size: 13, weight: .semibold))
                    .foregroundColor(GarudaTheme.textPrimary)
            }
            
            VStack(alignment: .leading, spacing: 8) {
                ForEach(items, id: \.self) { item in
                    HStack(alignment: .top, spacing: 8) {
                        Circle()
                            .fill(GarudaTheme.accent.opacity(0.6))
                            .frame(width: 4, height: 4)
                            .padding(.top, 6)
                        Text(item)
                            .font(GarudaFont.mono(size: 11))
                            .foregroundColor(GarudaTheme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(.leading, 8)
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(GarudaTheme.bgSurface1.opacity(0.5))
        )
    }
}

#Preview("DashboardView") {
    let state = AppState()
    let sm = SessionManager()
    return DashboardView()
        .environmentObject(state)
        .environmentObject(sm)
        .frame(width: 1000, height: 660)
}
