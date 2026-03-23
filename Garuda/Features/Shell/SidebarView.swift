// SidebarView.swift — Frosted glass sidebar with nav items, status, user info
import SwiftUI
import AppKit

// MARK: - NSVisualEffectView Wrapper for macOS frosted glass
struct VisualEffectBlur: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .sidebar
    var blendingMode: NSVisualEffectView.BlendingMode = .behindWindow

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material     = material
        view.blendingMode = blendingMode
        view.state        = .active
        return view
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {
        nsView.material     = material
        nsView.blendingMode = blendingMode
    }
}

// MARK: - Sidebar View
struct SidebarView: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager

    var body: some View {
        ZStack {
            VisualEffectBlur()
                .ignoresSafeArea()

            VStack(spacing: 0) {
                brandHeader
                GarudaDivider()
                navList
                GarudaDivider()
                bottomStrip
            }
        }
        .frame(minWidth: GarudaTheme.sidebarWidth, maxWidth: GarudaTheme.sidebarWidth)
    }

    // MARK: - Brand Header
    private var brandHeader: some View {
        HStack(spacing: 10) {
            Image(systemName: "eye.fill")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(GarudaTheme.danger)
            Text("GARUDA")
                .font(GarudaFont.mono(size: 13, weight: .bold))
                .foregroundColor(GarudaTheme.textPrimary)
                .tracking(3)
            Spacer()
            ConnectionDot(status: appState.connectionStatus)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    // MARK: - Alert Active Banner (shown when alert is active)
    @ViewBuilder
    private var alertActiveBanner: some View {
        if appState.isAlertActive {
            HStack(spacing: 8) {
                PulsingDot(color: GarudaTheme.danger, size: 5)
                Text("ALERT ACTIVE")
                    .font(GarudaFont.mono(size: 10, weight: .bold))
                    .foregroundColor(GarudaTheme.danger)
                Spacer()
                Button("STOP") {
                    Task { await sessionManager.emergencyStop() }
                }
                .buttonStyle(.plain)
                .focusable(false)
                .font(GarudaFont.mono(size: 9, weight: .bold))
                .foregroundColor(.white)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(RoundedRectangle(cornerRadius: 3).fill(GarudaTheme.danger))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(GarudaTheme.danger.opacity(0.1))
        }
    }

    // MARK: - Navigation List
    private var navList: some View {
        List(selection: $appState.selectedSidebarItem) {
            alertActiveBanner
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
                .listRowSeparator(.hidden)

            Section("MONITOR") {
                navRow(.dashboard)
                navRow(.alerts)
                navRow(.narada)
            }

            if appState.currentRole == .admin {
                Section("ADMIN") {
                    navRow(.admin)
                }
            }
        }
        .listStyle(.sidebar)
        .scrollContentBackground(.hidden)
        .background(Color.clear)
    }

    private func navRow(_ item: SidebarItem) -> some View {
        Label(item.rawValue, systemImage: item.icon)
            .font(GarudaFont.mono(size: 12))
            .foregroundColor(appState.selectedSidebarItem == item
                             ? GarudaTheme.textPrimary : GarudaTheme.textSecondary)
            .tag(item)
    }

    // MARK: - Bottom Strip (user info + logout)
    private var bottomStrip: some View {
        VStack(spacing: 0) {
            if let state = appState.systemState {
                HStack {
                    ReadoutRow(
                        label: "TODAY",
                        value: "\(state.detections_today)",
                        unit: "events",
                        valueColor: state.detections_today > 0 ? GarudaTheme.warning : GarudaTheme.textSecondary
                    )
                    Spacer()
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                GarudaDivider()
            }

            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(appState.displayName)
                        .font(GarudaFont.mono(size: 11, weight: .medium))
                        .foregroundColor(GarudaTheme.textPrimary)
                    StatusBadge(
                        label: appState.currentRole.rawValue.uppercased(),
                        color: appState.currentRole == .admin ? GarudaTheme.danger : GarudaTheme.accent
                    )
                }
                Spacer()
                Button {
                    Task { await sessionManager.logout() }
                } label: {
                    Image(systemName: "rectangle.portrait.and.arrow.right")
                        .font(.system(size: 14))
                        .foregroundColor(GarudaTheme.textTertiary)
                }
                .buttonStyle(.plain)
                .focusable(false)
                .help("Sign Out (Cmd+Shift+Q)")
                .keyboardShortcut("q", modifiers: [.command, .shift])
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
        }
    }
}
