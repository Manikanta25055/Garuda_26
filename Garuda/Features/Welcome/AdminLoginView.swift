// AdminLoginView.swift — Two-step OTP authentication sheet for admin access
import SwiftUI

struct AdminLoginView: View {

    // MARK: - State (all declared at top)
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @Environment(\.dismiss) var dismiss

    @State private var step: AdminLoginStep = .username
    @State private var usernameInput: String = ""
    @State private var otpInput: String = ""
    @State private var isLoading: Bool = false
    @State private var errorMessage: String? = nil

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Image(systemName: "lock.shield.fill")
                    .font(.system(size: 16))
                    .foregroundColor(GarudaTheme.danger)
                Text("Admin Access")
                    .font(GarudaFont.mono(size: 16, weight: .semibold))
                    .foregroundColor(GarudaTheme.textPrimary)
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(GarudaTheme.textTertiary)
                }
                .buttonStyle(.plain)
            }
            .padding(20)

            GarudaDivider()

            // Step content
            VStack(spacing: 24) {
                if step == .username {
                    usernameStepView
                        .transition(.asymmetric(
                            insertion: .move(edge: .trailing).combined(with: .opacity),
                            removal:   .move(edge: .leading).combined(with: .opacity)))
                } else {
                    otpStepView
                        .transition(.asymmetric(
                            insertion: .move(edge: .trailing).combined(with: .opacity),
                            removal:   .move(edge: .leading).combined(with: .opacity)))
                }
            }
            .padding(24)
            .animation(.easeInOut(duration: 0.25), value: step)
        }
        .frame(width: 360)
        .background(GarudaTheme.bgSurface1)
    }

    // MARK: - Step 1: Username
    private var usernameStepView: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Text("ADMIN USERNAME")
                    .font(GarudaFont.label())
                    .foregroundColor(GarudaTheme.textTertiary)
                DarkTextField(placeholder: "admin", text: $usernameInput)
                    .onSubmit { Task { await sendOTP() } }
            }

            if let error = errorMessage {
                StatusBadge(label: error, color: GarudaTheme.danger)
            }

            Button(action: { Task { await sendOTP() } }) {
                HStack {
                    if isLoading { ProgressView().scaleEffect(0.7).frame(width: 14, height: 14) }
                    Text(isLoading ? "Sending OTP…" : "Send OTP to Email")
                        .font(GarudaFont.ctaButton())
                }
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 11)
                .background(RoundedRectangle(cornerRadius: GarudaTheme.radiusLG)
                    .fill(isLoading ? GarudaTheme.danger.opacity(0.6) : GarudaTheme.danger))
            }
            .buttonStyle(.plain)
            .disabled(isLoading || usernameInput.isEmpty)
        }
    }

    // MARK: - Step 2: OTP
    private var otpStepView: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Enter the 6-digit OTP sent to your email")
                .font(GarudaFont.mono(size: 11))
                .foregroundColor(GarudaTheme.textTertiary)

            // Large OTP input display
            TextField("000000", text: $otpInput)
                .font(GarudaFont.otpDigit())
                .foregroundColor(GarudaTheme.textPrimary)
                .multilineTextAlignment(.center)
                .textFieldStyle(.plain)
                .padding(16)
                .background(GarudaTheme.bgSurface2)
                .clipShape(RoundedRectangle(cornerRadius: GarudaTheme.radiusMD))
                .overlay(RoundedRectangle(cornerRadius: GarudaTheme.radiusMD)
                    .strokeBorder(GarudaTheme.accent.opacity(0.3), lineWidth: 1))
                .onSubmit { Task { await verifyOTP() } }

            if let error = errorMessage {
                StatusBadge(label: error, color: GarudaTheme.danger)
            }

            HStack(spacing: 12) {
                Button("Back") {
                    withAnimation { step = .username }
                    errorMessage = nil
                    otpInput = ""
                }
                .buttonStyle(.plain)
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textTertiary)

                Spacer()

                Button(action: { Task { await verifyOTP() } }) {
                    HStack {
                        if isLoading { ProgressView().scaleEffect(0.7).frame(width: 14, height: 14) }
                        Text(isLoading ? "Verifying…" : "Verify OTP")
                            .font(GarudaFont.ctaButton())
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 20).padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: GarudaTheme.radiusLG)
                        .fill(isLoading ? GarudaTheme.danger.opacity(0.6) : GarudaTheme.danger))
                }
                .buttonStyle(.plain)
                .disabled(isLoading || otpInput.count < 6)
            }
        }
    }

    // MARK: - Actions
    private func sendOTP() async {
        isLoading = true; errorMessage = nil
        do {
            try await sessionManager.sendAdminOTP(username: usernameInput)
            withAnimation { step = .otp }
        } catch let err as APIError {
            errorMessage = err.errorDescription ?? "Failed to send OTP"
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    private func verifyOTP() async {
        isLoading = true; errorMessage = nil
        do {
            try await sessionManager.verifyAdminOTP(username: usernameInput, otp: otpInput)
            dismiss()
        } catch let err as APIError {
            errorMessage = err.errorDescription ?? "Invalid OTP"
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

#Preview("AdminLoginView") {
    AdminLoginView()
        .environmentObject(AppState())
        .environmentObject(SessionManager())
}
