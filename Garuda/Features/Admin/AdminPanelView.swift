// AdminPanelView.swift — Admin-only control panel with Form sections
import SwiftUI

struct AdminPanelView: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @State private var showUserManagement: Bool = false

    var body: some View {
        Group {
            if appState.currentRole == .admin {
                adminContent
            } else {
                accessDenied
            }
        }
        .background(GarudaTheme.bgPrimary)
    }

    // MARK: - Admin Content
    private var adminContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Page header
                HStack {
                    Image(systemName: "lock.shield.fill")
                        .font(.system(size: 18))
                        .foregroundColor(GarudaTheme.danger)
                    Text("Admin Panel")
                        .font(GarudaFont.mono(size: 20, weight: .semibold))
                        .foregroundColor(GarudaTheme.textPrimary)
                }
                .padding(.bottom, 4)

                // System Control section
                adminSection("SYSTEM CONTROL") {
                    systemControlContent
                }

                // Users section
                adminSection("USERS") {
                    usersContent
                }

                // Diagnostics section
                adminSection("DIAGNOSTICS") {
                    diagnosticsContent
                }
            }
            .padding(24)
        }
        .sheet(isPresented: $showUserManagement) {
            UserManagementView()
                .environmentObject(appState)
                .environmentObject(sessionManager)
        }
    }

    // MARK: - System Control
    private var systemControlContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Emergency stop
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("EMERGENCY STOP")
                        .font(GarudaFont.label())
                        .foregroundColor(GarudaTheme.textTertiary)
                    Text("Immediately clears active alert and stops alarm")
                        .font(GarudaFont.mono(size: 10))
                        .foregroundColor(GarudaTheme.textQuaternary)
                }
                Spacer()
                Button {
                    Task { await sessionManager.emergencyStop() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "stop.circle.fill")
                        Text("STOP ALERT")
                            .font(GarudaFont.ctaButton())
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(RoundedRectangle(cornerRadius: GarudaTheme.radiusMD)
                        .fill(appState.isAlertActive ? GarudaTheme.danger : GarudaTheme.textQuaternary))
                }
                .buttonStyle(.plain)
                .disabled(!appState.isAlertActive)
            }

            GarudaDivider()

            // Active modes display
            if let state = appState.systemState {
                HStack(spacing: 8) {
                    if state.modes.dnd       { StatusBadge(label: "DND",       color: GarudaTheme.warning) }
                    if state.modes.night     { StatusBadge(label: "NIGHT",     color: .indigo) }
                    if state.modes.emergency { StatusBadge(label: "EMERGENCY", color: GarudaTheme.danger) }
                    if state.modes.privacy   { StatusBadge(label: "PRIVACY",   color: GarudaTheme.accent) }
                    if state.modes.idle      { StatusBadge(label: "IDLE",      color: GarudaTheme.textSecondary) }
                    if state.modes.email_off { StatusBadge(label: "EMAIL OFF", color: GarudaTheme.textSecondary) }
                    if !anyModeActive(state) {
                        StatusBadge(label: "ALL NORMAL", color: GarudaTheme.success)
                    }
                }
            }
        }
    }

    // MARK: - Users
    private var usersContent: some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text("\(appState.users.count) registered users")
                    .font(GarudaFont.mono(size: 13))
                    .foregroundColor(GarudaTheme.textPrimary)
                Text("View display names, usernames, and profile colours")
                    .font(GarudaFont.mono(size: 10))
                    .foregroundColor(GarudaTheme.textQuaternary)
            }
            Spacer()
            Button("Manage") { showUserManagement = true }
                .buttonStyle(.plain)
                .font(GarudaFont.ctaButton())
                .foregroundColor(.white)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(RoundedRectangle(cornerRadius: GarudaTheme.radiusMD).fill(GarudaTheme.accent))
        }
    }

    // MARK: - Diagnostics
    private var diagnosticsContent: some View {
        VStack(spacing: 0) {
            if let state = appState.systemState {
                diagRow(label: "UPTIME",     value: state.uptime)
                GarudaDivider().padding(.leading, 0)
                diagRow(label: "DETECTIONS TODAY", value: "\(state.detections_today)")
                GarudaDivider().padding(.leading, 0)
                diagRow(label: "THRESHOLD",  value: String(format: "%.0f%%", state.detection_threshold * 100))
                GarudaDivider().padding(.leading, 0)
            }
            diagRow(label: "RPi5 HOST", value: appState.rpiHost)
            GarudaDivider().padding(.leading, 0)
            diagRow(label: "CONNECTION",
                    value: appState.connectionStatus == .connected ? "Connected" : "Disconnected",
                    valueColor: appState.connectionStatus == .connected ? GarudaTheme.success : GarudaTheme.danger)
            GarudaDivider().padding(.leading, 0)
            diagRow(label: "ROLE", value: appState.currentRole.rawValue.uppercased())
        }
    }

    private func diagRow(label: String, value: String, valueColor: Color = GarudaTheme.textPrimary) -> some View {
        HStack {
            Text(label)
                .font(GarudaFont.label())
                .foregroundColor(GarudaTheme.textTertiary)
                .frame(width: 160, alignment: .leading)
            Text(value)
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(valueColor)
            Spacer()
        }
        .padding(.vertical, 10)
    }

    // MARK: - Section Container
    private func adminSection<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: title)
                .padding(.bottom, 10)
            content()
                .padding(16)
                .garudaCard()
        }
    }

    // MARK: - Access Denied
    private var accessDenied: some View {
        VStack(spacing: 16) {
            Image(systemName: "lock.fill")
                .font(.system(size: 48))
                .foregroundColor(GarudaTheme.textQuaternary)
            Text("Admin Access Required")
                .font(GarudaFont.mono(size: 16, weight: .semibold))
                .foregroundColor(GarudaTheme.textSecondary)
            Text("Log in as admin to access this panel.")
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Helpers
    private func anyModeActive(_ state: SystemStateResponse) -> Bool {
        state.modes.dnd || state.modes.night || state.modes.emergency ||
        state.modes.privacy || state.modes.idle || state.modes.email_off
    }

}
