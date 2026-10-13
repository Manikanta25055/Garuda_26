// Keychain.swift — The session token, kept in the Keychain.
// It used to sit in UserDefaults: a plain preferences file any process running
// as the user could read, and one that ends up in backups.
import Foundation
import Security

enum Keychain {
    private static let service = "in.veeramanikanta.garuda"

    static func string(for account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String:  true,
            kSecMatchLimit as String:  kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func set(_ value: String, for account: String) {
        remove(account)
        let query: [String: Any] = [
            kSecClass as String:          kSecClassGenericPassword,
            kSecAttrService as String:    service,
            kSecAttrAccount as String:    account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String:      Data(value.utf8),
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    static func remove(_ account: String) {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
