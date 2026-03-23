// APIClient.swift — All REST calls to the RPi5 FastAPI backend
// Uses Swift actor for safe concurrent access; all methods are async/throws
import Foundation

actor APIClient {

    // MARK: - State (all declared at top per project convention)
    private var baseURL: String = ""
    private var sessionToken: String? = nil
    private let session: URLSession

    // MARK: - Init
    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 30
        self.session = URLSession(configuration: config)
    }

    // MARK: - Configuration
    func configure(host: String, token: String?) {
        self.baseURL = "http://\(host)"
        self.sessionToken = token
    }

    func setToken(_ token: String?) {
        self.sessionToken = token
    }

    // MARK: - Generic Request
    private func request<T: Decodable>(
        _ path: String,
        method: String = "GET",
        body: Encodable? = nil
    ) async throws -> T {
        guard let url = URL(string: baseURL + path) else { throw APIError.invalidURL }

        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = sessionToken {
            req.setValue(token, forHTTPHeaderField: "X-Garuda-Token")
        }
        if let body {
            req.httpBody = try JSONEncoder().encode(body)
        }

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.networkError(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }
        if http.statusCode == 401 { throw APIError.unauthorized }
        if http.statusCode >= 400 {
            let msg = String(data: data, encoding: .utf8) ?? "Unknown"
            throw APIError.serverError(http.statusCode, msg)
        }

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decodingFailed(error.localizedDescription)
        }
    }

    // Fire-and-forget version for endpoints that return no useful body
    private func send(_ path: String, method: String = "POST", body: Encodable? = nil) async throws {
        guard let url = URL(string: baseURL + path) else { throw APIError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = sessionToken { req.setValue(token, forHTTPHeaderField: "X-Garuda-Token") }
        if let body { req.httpBody = try JSONEncoder().encode(body) }
        let (_, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { return }
        if http.statusCode == 401 { throw APIError.unauthorized }
    }

    // MARK: - Auth Endpoints
    func login(username: String, password: String) async throws -> LoginResponse {
        try await request("/api/login", method: "POST",
                          body: LoginRequest(username: username, password: password))
    }

    func sendOTP(username: String) async throws {
        try await send("/api/admin/send-otp", body: OTPRequest(username: username))
    }

    func verifyOTP(username: String, otp: String) async throws -> LoginResponse {
        try await request("/api/admin/verify-otp", method: "POST",
                          body: OTPVerifyRequest(username: username, otp: otp))
    }

    func logout() async throws {
        try await send("/api/logout")
    }

    // MARK: - State Endpoints
    func getState() async throws -> SystemStateResponse {
        try await request("/api/state")
    }

    func setMode(_ mode: String, value: Bool) async throws {
        try await send("/api/set-mode", body: SetModeRequest(mode: mode, value: value))
    }

    func emergencyStop() async throws {
        try await send("/api/emergency-stop")
    }

    // MARK: - Data Endpoints
    func getLogs() async throws -> LogsResponse {
        try await request("/api/logs")
    }

    func getUsers() async throws -> [PublicUser] {
        try await request("/api/users-public")
    }

    // MARK: - Session Info (for cross-origin token restore)
    func getSession() async throws -> LoginResponse {
        try await request("/api/session")
    }

    // MARK: - Build WebSocket URL (called by WebSocketManager)
    func buildWebSocketURL() -> URL? {
        var comps = URLComponents(string: baseURL + GarudaConstants.wsPath)
        comps?.scheme = "ws"
        if let token = sessionToken {
            comps?.queryItems = [URLQueryItem(name: "token", value: token)]
        }
        return comps?.url
    }
}
