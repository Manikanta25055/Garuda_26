// NotificationManager.swift — macOS UserNotifications wrapper
// Full implementation in Phase 8. Stub here so Phase 1 compiles.
import Foundation
import UserNotifications

@MainActor
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {

    static let shared = NotificationManager()
    private let center = UNUserNotificationCenter.current()

    private override init() {
        super.init()
        center.delegate = self
    }

    // MARK: - Request Permission
    func requestAuthorization() async {
        do {
            let granted = try await center.requestAuthorization(options: [.alert, .sound, .badge])
            if !granted { print("[Garuda] Notifications permission denied.") }
        } catch {
            print("[Garuda] Notification auth error: \(error)")
        }
    }

    // MARK: - Send Detection Notification
    func sendDetectionNotification(event: DetectionEvent) {
        let content = UNMutableNotificationContent()
        content.title = "\(event.label.capitalized) Detected"
        content.body  = String(format: "Confidence: %.0f%%  •  %@",
                               event.confidence * 100,
                               event.timestamp.formatted(.dateTime.hour().minute().second()))
        content.sound = .default

        let request = UNNotificationRequest(
            identifier: event.id.uuidString,
            content: content,
            trigger: nil   // deliver immediately
        )
        center.add(request) { error in
            if let error { print("[Garuda] Notification error: \(error)") }
        }
    }

    // MARK: - Show notification even when app is in foreground
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler handler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        handler([.banner, .sound])
    }
}
