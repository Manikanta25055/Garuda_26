// AppShell.swift — NavigationSplitView root shown after authentication
import SwiftUI

struct AppShell: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @State private var columnVisibility: NavigationSplitViewVisibility = .all

    var body: some View {
        ZStack(alignment: .top) {
            NavigationSplitView(columnVisibility: $columnVisibility) {
                SidebarView()
                    .navigationSplitViewColumnWidth(
                        min: GarudaTheme.sidebarWidth,
                        ideal: GarudaTheme.sidebarWidth,
                        max: GarudaTheme.sidebarWidth + 40
                    )
            } detail: {
                detailContent
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(GarudaTheme.bgPrimary)
            }
            .navigationSplitViewStyle(.balanced)
            .frame(minWidth: GarudaTheme.windowMinWidth, minHeight: GarudaTheme.windowMinHeight)

            // Full-width alert banner overlaid at the top of the detail area
            if appState.isAlertActive {
                ActiveAlertBanner()
                    .environmentObject(appState)
                    .environmentObject(sessionManager)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .zIndex(100)
            }
        }
        .animation(.easeInOut(duration: 0.3), value: appState.isAlertActive)
        .onAppear {
            sessionManager.attach(to: appState)
        }
    }

    // MARK: - Detail Content Router
    @ViewBuilder
    private var detailContent: some View {
        switch appState.selectedSidebarItem {
        case .dashboard: DashboardView()
        case .alerts:    AlertsView()
        case .narada:    NaradaView()
        case .admin:     AdminPanelView()
        }
    }
}
