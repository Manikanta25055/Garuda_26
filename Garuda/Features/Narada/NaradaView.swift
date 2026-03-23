// NaradaView.swift — Narada voice assistant logs: voice input, responses, system updates
import SwiftUI

struct NaradaView: View {

    // MARK: - State (all declared at top)
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @State private var selectedTab: NaradaTab = .voiceLog
    @State private var isRefreshing: Bool = false
    @State private var filterText: String = ""

    var body: some View {
        VStack(spacing: 0) {
            header
            GarudaDivider()
            tabBar
            GarudaDivider()
            logContent
        }
        .background(GarudaTheme.bgPrimary)
        .task {
            await loadLogs()
        }
    }

    // MARK: - Header
    private var header: some View {
        HStack {
            Image(systemName: "waveform.and.mic")
                .font(.system(size: 14))
                .foregroundColor(GarudaTheme.accent)
            Text("NARADA")
                .font(GarudaFont.mono(size: 14, weight: .bold))
                .foregroundColor(GarudaTheme.textPrimary)
                .tracking(2)
            Spacer()
            // Refresh
            Button {
                Task { await loadLogs() }
            } label: {
                HStack(spacing: 4) {
                    if isRefreshing {
                        ProgressView().scaleEffect(0.6).frame(width: 12, height: 12)
                    } else {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 12))
                    }
                    Text("Refresh")
                        .font(GarudaFont.mono(size: 11))
                }
                .foregroundColor(GarudaTheme.accent)
            }
            .buttonStyle(.plain)
            .disabled(isRefreshing)
            .keyboardShortcut("r", modifiers: .command)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    // MARK: - Tab Bar
    private var tabBar: some View {
        HStack(spacing: 0) {
            ForEach(NaradaTab.allCases) { tab in
                tabButton(tab)
            }
            Spacer()

            // Filter field
            HStack(spacing: 6) {
                Image(systemName: "line.3.horizontal.decrease")
                    .font(.system(size: 11))
                    .foregroundColor(GarudaTheme.textTertiary)
                TextField("Filter…", text: $filterText)
                    .font(GarudaFont.mono(size: 11))
                    .foregroundColor(GarudaTheme.textPrimary)
                    .textFieldStyle(.plain)
                    .frame(width: 120)
                if !filterText.isEmpty {
                    Button { filterText = "" } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 11))
                            .foregroundColor(GarudaTheme.textTertiary)
                    }.buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(GarudaTheme.bgSurface2)
            .clipShape(RoundedRectangle(cornerRadius: GarudaTheme.radiusSM))
            .padding(.trailing, 16)
        }
        .background(GarudaTheme.bgSurface1)
    }

    private func tabButton(_ tab: NaradaTab) -> some View {
        Button {
            withAnimation(.easeInOut(duration: 0.15)) { selectedTab = tab }
        } label: {
            Text(tab.rawValue)
                .font(GarudaFont.mono(size: 11, weight: selectedTab == tab ? .bold : .regular))
                .foregroundColor(selectedTab == tab ? GarudaTheme.textPrimary : GarudaTheme.textTertiary)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(
                    selectedTab == tab
                        ? GarudaTheme.accent.opacity(0.1)
                        : Color.clear
                )
                .overlay(alignment: .bottom) {
                    if selectedTab == tab {
                        Rectangle().fill(GarudaTheme.accent).frame(height: 2)
                    }
                }
        }
        .buttonStyle(.plain)
    }

    // MARK: - Log Content
    private var logContent: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(activeLines.enumerated()), id: \.offset) { idx, line in
                        logRow(line, index: idx)
                            .id(idx)
                    }
                    if activeLines.isEmpty {
                        emptyState
                    }
                }
            }
            .background(GarudaTheme.bgPrimary)
            .onChange(of: activeLines.count) { _, _ in
                if let last = activeLines.indices.last {
                    withAnimation { proxy.scrollTo(last, anchor: .bottom) }
                }
            }
        }
    }

    private func logRow(_ line: String, index: Int) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(String(format: "%04d", index))
                .font(GarudaFont.consoleText(size: 10))
                .foregroundColor(GarudaTheme.textQuaternary)
                .frame(width: 36, alignment: .trailing)
            Text(line)
                .font(GarudaFont.consoleText(size: 11))
                .foregroundColor(lineColor(line))
                .textSelection(.enabled)
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 3)
        .background(index % 2 == 0 ? Color.clear : GarudaTheme.bgSurface1.opacity(0.4))
    }

    // MARK: - Helpers
    private var activeLines: [String] {
        guard let logs = appState.systemLogs else { return [] }
        let raw: [String]
        switch selectedTab {
        case .voiceLog:  raw = logs.voice_log
        case .responses: raw = logs.voice_responses
        case .system:    raw = logs.system_updates
        }
        if filterText.isEmpty { return raw }
        return raw.filter { $0.localizedCaseInsensitiveContains(filterText) }
    }

    private func lineColor(_ line: String) -> Color {
        if line.contains("ERROR") || line.contains("FAIL")   { return GarudaTheme.danger }
        if line.contains("WARN")  || line.contains("ALERT")  { return GarudaTheme.warning }
        if line.contains("OK")    || line.contains("success"){ return GarudaTheme.success }
        return selectedTab == .responses ? GarudaTheme.accent : GarudaTheme.textSecondary
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Spacer(minLength: 60)
            Image(systemName: "waveform.slash")
                .font(.system(size: 32))
                .foregroundColor(GarudaTheme.textQuaternary)
            Text("No \(selectedTab.rawValue.lowercased()) available")
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textTertiary)
            Spacer(minLength: 60)
        }
        .frame(maxWidth: .infinity)
    }

    private func loadLogs() async {
        isRefreshing = true
        await sessionManager.refreshLogs()
        isRefreshing = false
    }
}
