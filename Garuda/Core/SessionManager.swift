// SessionManager.swift — Auth + WebSocket + polling lifecycle
// Owns APIClient and WebSocketManager; writes to AppState on main actor
import Foundation
import Combine

@MainActor
final class SessionManager: ObservableObject {

    // MARK: - Dependencies (all declared at top)
    let apiClient = APIClient()
    private let wsManager = WebSocketManager()
    private var pollTimer: Timer? = nil

    private weak var appState: AppState? = nil

    // MARK: - Init
    init() {}

    func attach(to appState: AppState) {
        self.appState = appState
        // Wire WebSocket callbacks
        wsManager.onEvent = { [weak appState] event in
            appState?.appendDetectionEvent(event)
            if event.label == "person" {
                NotificationManager.shared.sendDetectionNotification(event: event)
            }
        }
        wsManager.onConnectionChange = { [weak appState] connected in
            appState?.connectionStatus = connected ? .connected : .disconnected
        }
    }

    // MARK: - Login (standard user)
    func login(username: String, password: String) async throws {
        guard let appState else { return }
        appState.authState = .authenticating
        await apiClient.configure(
            host: appState.rpiHost,
            token: nil
        )
        do {
            let resp = try await apiClient.login(username: username, password: password)
            let role = UserRole(rawValue: resp.role) ?? .user
            // Wire the token into APIClient so all subsequent requests send X-Garuda-Token
            await apiClient.setToken(resp.token)
            appState.setAuthenticated(role: role, displayName: resp.display_name, token: resp.token)
            await startSession()
        } catch {
            appState.authState = .unauthenticated
            throw error
        }
    }

    // MARK: - Admin Login (2-step OTP)
    func sendAdminOTP(username: String) async throws {
        guard let appState else { return }
        await apiClient.configure(host: appState.rpiHost, token: nil)
        try await apiClient.sendOTP(username: username)
    }

    func verifyAdminOTP(username: String, otp: String) async throws {
        guard let appState else { return }
        let resp = try await apiClient.verifyOTP(username: username, otp: otp)
        let role = UserRole(rawValue: resp.role) ?? .admin
        await apiClient.setToken(resp.token)
        appState.setAuthenticated(role: role, displayName: resp.display_name, token: resp.token)
        await startSession()
    }

    // MARK: - Session Start (post-login)
    private func startSession() async {
        guard let appState else { return }
        appState.connectionStatus = .connecting

        // Initial state poll
        await pollState()

        // Load users for admin
        if appState.currentRole == .admin {
            if let users = try? await apiClient.getUsers() {
                appState.users = users
            }
        }

        // Start WebSocket
        if let wsURL = await apiClient.buildWebSocketURL() {
            wsManager.connect(to: wsURL)
        }

        // Start periodic polling
        startPolling()
    }

    // MARK: - Logout
    func logout() async {
        stopPolling()
        wsManager.disconnect()
        try? await apiClient.logout()
        appState?.clearSession()
    }

    // MARK: - State Polling
    func pollState() async {
        do {
            let state = try await apiClient.getState()
            appState?.updateSystemState(state)
            if appState?.connectionStatus != .connected {
                appState?.connectionStatus = .connected
            }
        } catch {
            print("State poll error: \(error)")
            if appState?.connectionStatus == .connected {
                appState?.connectionStatus = .error("Failed to fetch state")
            }
        }
    }

    private func startPolling() {
        stopPolling()
        pollTimer = Timer.scheduledTimer(withTimeInterval: GarudaConstants.statePollInterval, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in 
                await self?.pollState() 
            }
        }
        // Fire immediately on start
        Task { @MainActor [weak self] in 
            await self?.pollState() 
        }
    }

    private func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    // MARK: - Logs
    func refreshLogs() async {
        if let logs = try? await apiClient.getLogs() {
            appState?.systemLogs = logs
        }
    }

    // MARK: - Emergency Stop
    func emergencyStop() async {
        try? await apiClient.emergencyStop()
        await pollState()
    }
}
