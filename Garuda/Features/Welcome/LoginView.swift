// LoginView.swift — User login form rendered inside WelcomeView right panel
import SwiftUI

struct LoginView: View {

    // MARK: - State (all declared at top)
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager

    @State private var username: String = ""
    @State private var password: String = ""
    @State private var isLoading: Bool = false
    @State private var errorMessage: String? = nil
    @State private var showAdminLogin: Bool = false
    @State private var showSettings: Bool = false

    var body: some View {
        VStack(alignment: .center, spacing: 0) {
            // Title
            Text("Sign In")
                .font(GarudaFont.mono(size: 28, weight: .semibold))
                .foregroundColor(GarudaTheme.textPrimary)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.bottom, 8)

            Text("Access your security system")
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textTertiary)
                .padding(.bottom, 36)

            // Host indicator with glassmorphic background
            HStack(spacing: 8) {
                ConnectionDot(status: appState.connectionStatus)
                Text(appState.rpiHost)
                    .font(GarudaFont.mono(size: 11))
                    .foregroundColor(GarudaTheme.textSecondary)
                Spacer()
                Button("Change") { showSettings = true }
                    .buttonStyle(.plain)
                    .font(GarudaFont.mono(size: 10, weight: .medium))
                    .foregroundColor(GarudaTheme.accent)
                    .focusable(false)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: GarudaTheme.radiusMD, style: .continuous)
                    .fill(GarudaTheme.bgSurface1.opacity(0.6))
                    .overlay(
                        RoundedRectangle(cornerRadius: GarudaTheme.radiusMD, style: .continuous)
                            .strokeBorder(GarudaTheme.borderColor.opacity(0.3), lineWidth: 1)
                    )
            )
            .padding(.bottom, 28)

            // Username field
            ModernTextField(placeholder: "Username", text: $username, isSecure: false)
                .padding(.bottom, 14)

            // Password field
            ModernTextField(placeholder: "Password", text: $password, isSecure: true)
                .padding(.bottom, 28)
                .onSubmit { Task { await doLogin() } }

            // Error badge
            if let error = errorMessage {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 11))
                    Text(error)
                        .font(GarudaFont.mono(size: 11))
                }
                .foregroundColor(GarudaTheme.danger)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(
                    RoundedRectangle(cornerRadius: GarudaTheme.radiusMD, style: .continuous)
                        .fill(GarudaTheme.danger.opacity(0.1))
                )
                .padding(.bottom, 20)
            }

            // Sign In button (modern glassmorphic)
            Button(action: { Task { await doLogin() } }) {
                HStack(spacing: 10) {
                    if isLoading {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .scaleEffect(0.8)
                            .tint(.white)
                    } else {
                        Image(systemName: "arrow.right")
                            .font(.system(size: 14, weight: .semibold))
                    }
                    Text(isLoading ? "Connecting..." : "Sign In")
                        .font(GarudaFont.mono(size: 13, weight: .semibold))
                }
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(
                    RoundedRectangle(cornerRadius: GarudaTheme.radiusLG, style: .continuous)
                        .fill(isLoading ? GarudaTheme.accent.opacity(0.5) : GarudaTheme.accent)
                )
            }
            .buttonStyle(.plain)
            .disabled(isLoading || username.isEmpty || password.isEmpty)
            .focusable(false)
            .padding(.bottom, 18)

            // Admin access link
            Button {
                showAdminLogin = true
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "lock.shield.fill")
                        .font(.system(size: 10))
                    Text("Admin Access")
                        .font(GarudaFont.mono(size: 11))
                }
                .foregroundColor(GarudaTheme.textTertiary)
            }
            .buttonStyle(.plain)
            .focusable(false)
        }
        .padding(.horizontal, 40)
        .padding(.vertical, 44)
        .frame(maxWidth: 420)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(GarudaTheme.bgSurface1.opacity(0.4))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .strokeBorder(GarudaTheme.borderColor.opacity(0.2), lineWidth: 1)
                )
        )
        .sheet(isPresented: $showAdminLogin) {
            AdminLoginView()
                .environmentObject(appState)
                .environmentObject(sessionManager)
        }
        .sheet(isPresented: $showSettings) {
            SettingsSheet()
                .environmentObject(appState)
        }
    }

    // MARK: - Action
    private func doLogin() async {
        guard !username.isEmpty, !password.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        do {
            try await sessionManager.login(username: username, password: password)
        } catch let err as APIError {
            errorMessage = err.errorDescription ?? "Login failed"
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

// MARK: - Modern Text Field with Glassmorphic Background
struct ModernTextField: View {
    let placeholder: String
    @Binding var text: String
    var isSecure: Bool = false
    @FocusState private var isFocused: Bool

    var body: some View {
        Group {
            if isSecure {
                SecureField(placeholder, text: $text)
                    .focused($isFocused)
            } else {
                TextField(placeholder, text: $text)
                    .focused($isFocused)
            }
        }
        .font(GarudaFont.mono(size: 13))
        .foregroundColor(GarudaTheme.textPrimary)
        .textFieldStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
        .background(
            RoundedRectangle(cornerRadius: GarudaTheme.radiusLG, style: .continuous)
                .fill(GarudaTheme.bgSurface2.opacity(0.6))
        )
        .overlay(
            RoundedRectangle(cornerRadius: GarudaTheme.radiusLG, style: .continuous)
                .strokeBorder(
                    isFocused ? GarudaTheme.accent.opacity(0.5) : GarudaTheme.borderColor.opacity(0.3),
                    lineWidth: isFocused ? 1.5 : 1
                )
        )
        .focusable(false)
    }
}

// MARK: - Dark Styled Text Field (legacy, kept for SettingsSheet)
struct DarkTextField: View {
    let placeholder: String
    @Binding var text: String
    var isSecure: Bool = false

    var body: some View {
        Group {
            if isSecure {
                SecureField(placeholder, text: $text)
            } else {
                TextField(placeholder, text: $text)
            }
        }
        .font(GarudaFont.mono(size: 13))
        .foregroundColor(GarudaTheme.textPrimary)
        .textFieldStyle(.plain)
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .background(GarudaTheme.bgSurface2)
        .clipShape(RoundedRectangle(cornerRadius: GarudaTheme.radiusMD, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: GarudaTheme.radiusMD, style: .continuous)
                .strokeBorder(GarudaTheme.borderColor, lineWidth: 0.5)
        )
    }
}

// MARK: - Settings Sheet (RPi host config)
struct SettingsSheet: View {
    @EnvironmentObject var appState: AppState
    @Environment(\.dismiss) var dismiss
    @State private var hostInput: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Connection Settings")
                .font(GarudaFont.mono(size: 16, weight: .semibold))
                .foregroundColor(GarudaTheme.textPrimary)

            VStack(alignment: .leading, spacing: 6) {
                Text("RASPBERRY PI 5 HOST")
                    .font(GarudaFont.label())
                    .foregroundColor(GarudaTheme.textTertiary)
                DarkTextField(placeholder: "e.g. 192.168.1.100:8080", text: $hostInput)
                Text("Enter the IP address and port of your Garuda backend.")
                    .font(GarudaFont.mono(size: 10))
                    .foregroundColor(GarudaTheme.textQuaternary)
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .buttonStyle(.plain)
                    .font(GarudaFont.mono(size: 12))
                    .foregroundColor(GarudaTheme.textTertiary)
                    .padding(.trailing, 12)
                Button("Save") {
                    let trimmed = hostInput.trimmingCharacters(in: .whitespaces)
                    if !trimmed.isEmpty { appState.rpiHost = trimmed }
                    dismiss()
                }
                .buttonStyle(.plain)
                .font(GarudaFont.ctaButton())
                .foregroundColor(.white)
                .padding(.horizontal, 16).padding(.vertical, 8)
                .background(RoundedRectangle(cornerRadius: GarudaTheme.radiusMD)
                    .fill(GarudaTheme.accent))
            }
        }
        .padding(28)
        .frame(width: 380)
        .background(GarudaTheme.bgSurface1)
        .onAppear { hostInput = appState.rpiHost }
    }
}
