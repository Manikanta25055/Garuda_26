// WelcomeView.swift — Minimal modern login with grid background
import SwiftUI

struct WelcomeView: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager

    var body: some View {
        ZStack {
            // Dark background
            GarudaTheme.bgPrimary.ignoresSafeArea()
            
            // Animated grid overlay
            GridOverlay()
            
            // Content
            VStack(spacing: 0) {
                // Top-left branding
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("GARUDA")
                            .font(GarudaFont.mono(size: 16, weight: .bold))
                            .foregroundColor(GarudaTheme.textPrimary)
                            .tracking(4)
                        Text("AI Security Intelligence Platform")
                            .font(GarudaFont.mono(size: 10))
                            .foregroundColor(GarudaTheme.textTertiary)
                    }
                    Spacer()
                }
                .padding(.horizontal, 32)
                .padding(.top, 52)
                
                Spacer()
                
                // Center login form
                LoginView()
                    .frame(maxWidth: 420)
                
                Spacer()
                
                // Footer attribution
                VStack(spacing: 4) {
                    Text("Created by Manikanta Gonugondla")
                        .font(GarudaFont.statusSmall())
                        .foregroundColor(GarudaTheme.textQuaternary)
                    Text("Powered by Hailo AI & Raspberry Pi 5")
                        .font(GarudaFont.statusSmall())
                        .foregroundColor(GarudaTheme.textQuaternary.opacity(0.6))
                }
                .padding(.bottom, 24)
            }
        }
        .ignoresSafeArea()
    }
}

#Preview("WelcomeView") {
    WelcomeView()
        .environmentObject(AppState())
        .environmentObject(SessionManager())
        .frame(width: 900, height: 600)
}
