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

    /// The server's address for `host`. HTTPS unless the host is on this
    /// machine or the home network: the session token and passwords travel in
    /// every request, and plain HTTP showed them to anyone on the path. A host
    /// typed with its own scheme ("https://api.example.in") is used as typed.
    static func baseURL(for host: String) -> String {
        let trimmed = host.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("https://") || trimmed.hasPrefix("http://") {
            return trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
        }
        return (isLocal(trimmed) ? "http://" : "https://") + trimmed
    }

    /// Loopback, a `.local` name, a name with no dots, or a private IPv4 address.
    static func isLocal(_ host: String) -> Bool {
        let name = (host.split(separator: ":").first.map(String.init) ?? host).lowercased()
        if name == "localhost" || name.hasSuffix(".local") || !name.contains(".") { return true }
        let octets = name.split(separator: ".").compactMap { Int($0) }
        guard octets.count == 4 else { return false }
        switch (octets[0], octets[1]) {
        case (127, _), (10, _), (192, 168): return true
        case (172, 16...31):                return true
        default:                            return false
        }
    }
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
