// Models.swift — All Codable structs mirroring RPi5 FastAPI JSON schemas
import Foundation

// MARK: - Auth
struct LoginRequest: Codable {
    let username: String
    let password: String
}

struct LoginResponse: Codable {
    let role: String
    let display_name: String
    let token: String?          // session token returned in body for cross-origin use
}

struct OTPRequest: Codable {
    let username: String
}

struct OTPVerifyRequest: Codable {
    let username: String
    let otp: String
}

// MARK: - System State
struct SystemModes: Codable {
    let dnd: Bool
    let night: Bool
    let emergency: Bool
    let idle: Bool
    let email_off: Bool
    let privacy: Bool
}

struct SystemStateResponse: Codable {
    let modes: SystemModes
    let uptime: String              // e.g. "03:42:11" — formatted by RPi backend
    let detections_today: Int
    let last_alert: String?         // ISO timestamp or nil
    let alert_active: Bool
    let detection_threshold: Double
}

struct SetModeRequest: Codable {
    let mode: String
    let value: Bool
}

// MARK: - Logs
struct LogsResponse: Codable {
    let system_updates: [String]
    let voice_log: [String]
    let voice_responses: [String]
}

// MARK: - Users
struct PublicUser: Codable, Identifiable {
    let username: String
    let display_name: String
    let box_color: String
    var id: String { username }
}

// MARK: - WebSocket Detection Event
// NOTE: 'id' is synthesised client-side and excluded from JSON decoding
struct DetectionEvent: Identifiable, Hashable {
    let id: UUID
    let timestamp: Date
    let label: String
    let confidence: Double
    let boxColor: String
    let user: String?

    // Initialise from WebSocket JSON payload
    init(from dict: [String: Any]) {
        self.id         = UUID()
        self.timestamp  = Date()
        self.label      = dict["label"] as? String ?? "unknown"
        self.confidence = dict["confidence"] as? Double ?? 0.0
        self.boxColor   = dict["box_color"] as? String ?? "#2997FF"
        self.user       = dict["user"] as? String
    }

    // Convenience for previews / mock data
    init(label: String, confidence: Double, boxColor: String = "#34C759", user: String? = nil) {
        self.id         = UUID()
        self.timestamp  = Date()
        self.label      = label
        self.confidence = confidence
        self.boxColor   = boxColor
        self.user       = user
    }
}

// MARK: - API Errors
enum APIError: LocalizedError {
    case invalidURL
    case unauthorized
    case serverError(Int, String)
    case decodingFailed(String)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:                return "Invalid server URL."
        case .unauthorized:              return "Invalid credentials."
        case .serverError(let c, let m): return "Server error \(c): \(m)"
        case .decodingFailed(let m):     return "Response parse error: \(m)"
        case .networkError(let e):       return e.localizedDescription
        }
    }
}
