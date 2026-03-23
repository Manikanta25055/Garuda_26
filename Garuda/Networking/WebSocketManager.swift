// WebSocketManager.swift — Manages /ws WebSocket connection with auto-reconnect
import Foundation
import Combine

@MainActor
final class WebSocketManager: ObservableObject {

    // MARK: - State (all declared at top)
    private var webSocketTask: URLSessionWebSocketTask? = nil
    private var reconnectTask: Task<Void, Never>? = nil
    private var receiveTask: Task<Void, Never>? = nil
    private var isConnected: Bool = false
    private var targetURL: URL? = nil
    private let urlSession: URLSession

    // Callbacks set by SessionManager
    var onEvent: ((DetectionEvent) -> Void)? = nil
    var onConnectionChange: ((Bool) -> Void)? = nil

    // MARK: - Init
    init() {
        let config = URLSessionConfiguration.default
        self.urlSession = URLSession(configuration: config)
    }

    // MARK: - Connect
    func connect(to url: URL) {
        self.targetURL = url
        openConnection(to: url)
    }

    private func openConnection(to url: URL) {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = urlSession.webSocketTask(with: url)
        webSocketTask?.resume()
        isConnected = true
        onConnectionChange?(true)
        startReceiving()
    }

    // MARK: - Disconnect
    func disconnect() {
        reconnectTask?.cancel()
        receiveTask?.cancel()
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        isConnected = false
        onConnectionChange?(false)
    }

    // MARK: - Receive Loop
    private func startReceiving() {
        receiveTask?.cancel()
        receiveTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                guard let task = self.webSocketTask else { break }
                do {
                    let msg = try await task.receive()
                    switch msg {
                    case .string(let text):
                        self.handleMessage(text)
                    case .data(let data):
                        if let text = String(data: data, encoding: .utf8) {
                            self.handleMessage(text)
                        }
                    @unknown default:
                        break
                    }
                } catch {
                    // Connection lost — schedule reconnect
                    self.isConnected = false
                    self.onConnectionChange?(false)
                    self.scheduleReconnect()
                    break
                }
            }
        }
    }

    // MARK: - Message Parsing
    private func handleMessage(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }

        // The WS sends detection events and system state updates
        if let _ = json["label"] {
            let event = DetectionEvent(from: json)
            onEvent?(event)
        }
    }

    // MARK: - Auto-Reconnect
    private func scheduleReconnect() {
        reconnectTask?.cancel()
        guard let url = targetURL else { return }
        reconnectTask = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(for: .seconds(GarudaConstants.wsReconnectDelay))
            guard !Task.isCancelled else { return }
            self.openConnection(to: url)
        }
    }
}
