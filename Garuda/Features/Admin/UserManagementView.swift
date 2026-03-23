// UserManagementView.swift — Public user list sheet (read-only)
import SwiftUI

struct UserManagementView: View {

    // MARK: - State
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @Environment(\.dismiss) var dismiss
    @State private var isLoading: Bool = false

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("System Users")
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

            if isLoading {
                ProgressView("Loading users…")
                    .font(GarudaFont.mono(size: 12))
                    .foregroundColor(GarudaTheme.textSecondary)
                    .padding(40)
            } else if appState.users.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "person.2.slash")
                        .font(.system(size: 32))
                        .foregroundColor(GarudaTheme.textQuaternary)
                    Text("No users found")
                        .font(GarudaFont.mono(size: 12))
                        .foregroundColor(GarudaTheme.textTertiary)
                }
                .padding(40)
            } else {
                List(appState.users) { user in
                    userRow(user)
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .background(GarudaTheme.bgPrimary)
            }

            Spacer()

            // Refresh
            GarudaDivider()
            HStack {
                Spacer()
                Button {
                    Task {
                        isLoading = true
                        if let users = try? await sessionManager.apiClient.getUsers() {
                            appState.users = users
                        }
                        isLoading = false
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                        .font(GarudaFont.mono(size: 12))
                        .foregroundColor(GarudaTheme.accent)
                }
                .buttonStyle(.plain)
                .padding()
            }
        }
        .frame(width: 420, height: 400)
        .background(GarudaTheme.bgSurface1)
    }

    private func userRow(_ user: PublicUser) -> some View {
        HStack(spacing: 12) {
            // Color dot
            Circle()
                .fill(Color(hex: user.box_color))
                .frame(width: 10, height: 10)
            // Display name
            VStack(alignment: .leading, spacing: 2) {
                Text(user.display_name)
                    .font(GarudaFont.mono(size: 13))
                    .foregroundColor(GarudaTheme.textPrimary)
                Text(user.username)
                    .font(GarudaFont.mono(size: 10))
                    .foregroundColor(GarudaTheme.textTertiary)
            }
            Spacer()
            // Online/role indicator (we only have public info)
            StatusBadge(
                label: user.username == "admin" ? "ADMIN" : "USER",
                color: user.username == "admin" ? GarudaTheme.danger : GarudaTheme.accent
            )
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}
