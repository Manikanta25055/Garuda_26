// AppState.swift — Single observable source of truth for the entire app.
// All views read from this; only SessionManager writes to it.
import Foundation
import Combine
import SwiftUI

@MainActor
final class AppState: ObservableObject {

    // MARK: - Auth / Session
    @Published var authState: AuthState = .unauthenticated
    @Published var currentRole: UserRole = .user
    @Published var displayName: String = ""
    @Published var sessionToken: String? = nil

    // MARK: - Connection
    @Published var connectionStatus: ConnectionStatus = .disconnected
    @Published var rpiHost: String {
        didSet { UserDefaults.standard.set(rpiHost, forKey: GarudaConstants.hostDefaultsKey) }
    }

    // MARK: - System State (from /api/state)
    @Published var systemState: SystemStateResponse? = nil
    @Published var isAlertActive: Bool = false

    // MARK: - Real-time Events (from WebSocket)
    @Published var detectionEvents: [DetectionEvent] = []

    // MARK: - Logs (from /api/logs)
    @Published var systemLogs: LogsResponse? = nil

    // MARK: - Users (from /api/users-public)
    @Published var users: [PublicUser] = []

    // MARK: - Navigation
    @Published var selectedSidebarItem: SidebarItem = .dashboard

    // MARK: - Init
    init() {
        rpiHost = UserDefaults.standard.string(forKey: GarudaConstants.hostDefaultsKey)
                  ?? GarudaConstants.defaultHost
        if let token = UserDefaults.standard.string(forKey: GarudaConstants.tokenDefaultsKey) {
            sessionToken = token
        }
    }

    // MARK: - Computed
    var baseURL: String { "http://\(rpiHost)" }
    var streamURL: URL? { URL(string: "\(baseURL)\(GarudaConstants.streamPath)") }

    var menuBarIcon: String {
        if isAlertActive { return "exclamationmark.triangle.fill" }
        switch connectionStatus {
        case .connected:    return "eye.fill"
        case .connecting:   return "eye"
        case .disconnected: return "eye.slash"
        case .error:        return "eye.slash"
        }
    }

    // MARK: - Mutations (called by SessionManager only)
    func setAuthenticated(role: UserRole, displayName: String, token: String?) {
        self.currentRole = role
        self.displayName = displayName
        self.sessionToken = token
        if let token { UserDefaults.standard.set(token, forKey: GarudaConstants.tokenDefaultsKey) }
        self.authState = .authenticated
    }

    func clearSession() {
        authState    = .unauthenticated
        currentRole  = .user
        displayName  = ""
        sessionToken = nil
        systemState  = nil
        isAlertActive = false
        detectionEvents = []
        systemLogs   = nil
        users        = []
        connectionStatus = .disconnected
        UserDefaults.standard.removeObject(forKey: GarudaConstants.tokenDefaultsKey)
    }

    func appendDetectionEvent(_ event: DetectionEvent) {
        detectionEvents.insert(event, at: 0)
        // Keep last 500 events in memory
        if detectionEvents.count > 500 { detectionEvents = Array(detectionEvents.prefix(500)) }
        if event.label == "person" { isAlertActive = true }
    }

    func updateSystemState(_ state: SystemStateResponse) {
        systemState = state
        isAlertActive = state.alert_active
    }
}
