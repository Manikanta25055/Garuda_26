// Constants.swift — Central config values and enumerations
import Foundation

// MARK: - App Constants
enum GarudaConstants {
    static let defaultHost = "192.168.1.100:8080"
    static let streamPath  = "/stream"
    static let wsPath      = "/ws"
    static let wsReconnectDelay: TimeInterval = 3.0
    static let statePollInterval: TimeInterval = 5.0
    static let streamTimeout: TimeInterval = 10.0
    static let hostDefaultsKey = "garudaRpiHost"
    static let tokenDefaultsKey = "garudaSessionToken"
}

// MARK: - Sidebar Navigation Items
enum SidebarItem: String, CaseIterable, Identifiable, Hashable {
    case dashboard = "Dashboard"
    case alerts    = "Alerts"
    case narada    = "Narada"
    case admin     = "Admin"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .dashboard: return "viewfinder.circle.fill"
        case .alerts:    return "bell.fill"
        case .narada:    return "waveform.and.mic"
        case .admin:     return "lock.shield.fill"
        }
    }
}

// MARK: - Auth States
enum AuthState: Equatable {
    case unauthenticated
    case authenticating
    case authenticated
}

// MARK: - User Role
enum UserRole: String {
    case admin = "admin"
    case user  = "user"
}

// MARK: - Connection Status
enum ConnectionStatus: Equatable {
    case disconnected
    case connecting
    case connected
    case error(String)

    static func == (lhs: ConnectionStatus, rhs: ConnectionStatus) -> Bool {
        switch (lhs, rhs) {
        case (.disconnected, .disconnected): return true
        case (.connecting, .connecting):     return true
        case (.connected, .connected):       return true
        case (.error(let a), .error(let b)): return a == b
        default: return false
        }
    }
}

// MARK: - Admin Login Step
enum AdminLoginStep {
    case username
    case otp
}

// MARK: - Narada Tab
enum NaradaTab: String, CaseIterable, Identifiable {
    case voiceLog    = "Voice Log"
    case responses   = "Responses"
    case system      = "System"
    var id: String { rawValue }
}
