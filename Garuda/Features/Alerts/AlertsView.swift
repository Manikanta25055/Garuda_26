// AlertsView.swift — Detection event list with search, master-detail layout
import SwiftUI

struct AlertsView: View {

    // MARK: - State (all declared at top)
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var sessionManager: SessionManager
    @State private var selectedEvent: DetectionEvent? = nil
    @State private var searchText: String = ""

    // MARK: - Computed
    private var filteredEvents: [DetectionEvent] {
        if searchText.isEmpty { return appState.detectionEvents }
        return appState.detectionEvents.filter {
            $0.label.localizedCaseInsensitiveContains(searchText) ||
            ($0.user ?? "").localizedCaseInsensitiveContains(searchText)
        }
    }

    var body: some View {
        HSplitView {
            // Left: Event list
            eventList
                .frame(minWidth: 300, idealWidth: 380)

            // Right: Event detail
            eventDetail
                .frame(minWidth: 280, maxWidth: .infinity)
        }
        .background(GarudaTheme.bgPrimary)
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Button {
                    appState.detectionEvents = []
                    selectedEvent = nil
                } label: {
                    Label("Clear All", systemImage: "trash")
                        .font(.system(size: 12))
                }
                .help("Clear all events (Cmd+K)")
                .keyboardShortcut("k", modifiers: .command)
                .disabled(appState.detectionEvents.isEmpty)
            }
        }
    }

    // MARK: - Event List
    private var eventList: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                SectionHeader(title: "DETECTION EVENTS")
                Spacer()
                if !appState.detectionEvents.isEmpty {
                    StatusBadge(label: "\(appState.detectionEvents.count)", color: GarudaTheme.accent)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)

            GarudaDivider()

            // Search bar
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 12))
                    .foregroundColor(GarudaTheme.textTertiary)
                TextField("Search events…", text: $searchText)
                    .font(GarudaFont.mono(size: 12))
                    .foregroundColor(GarudaTheme.textPrimary)
                    .textFieldStyle(.plain)
                if !searchText.isEmpty {
                    Button { searchText = "" } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 12))
                            .foregroundColor(GarudaTheme.textTertiary)
                    }.buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(GarudaTheme.bgSurface2)

            GarudaDivider()

            if filteredEvents.isEmpty {
                emptyListPlaceholder
            } else {
                List(filteredEvents, selection: Binding(
                    get: { selectedEvent },
                    set: { selectedEvent = $0 }
                )) { event in
                    AlertBannerView(event: event)
                        .listRowBackground(
                            selectedEvent?.id == event.id
                                ? GarudaTheme.accent.opacity(0.1)
                                : Color.clear
                        )
                        .listRowSeparator(.hidden)
                        .listRowInsets(EdgeInsets())
                        .tag(event)
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .background(GarudaTheme.bgPrimary)
            }
        }
        .background(GarudaTheme.bgSurface1)
    }

    // MARK: - Event Detail
    private var eventDetail: some View {
        Group {
            if let event = selectedEvent {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        // Header
                        HStack(spacing: 10) {
                            ZStack {
                                Circle()
                                    .fill(Color(hex: event.boxColor).opacity(0.15))
                                    .frame(width: 48, height: 48)
                                Image(systemName: event.label == "person" ? "person.fill" : "exclamationmark.triangle.fill")
                                    .font(.system(size: 22))
                                    .foregroundColor(Color(hex: event.boxColor))
                            }
                            VStack(alignment: .leading, spacing: 4) {
                                Text(event.label.uppercased())
                                    .font(GarudaFont.mono(size: 18, weight: .semibold))
                                    .foregroundColor(GarudaTheme.textPrimary)
                                StatusBadge(
                                    label: String(format: "%.0f%% CONFIDENCE", event.confidence * 100),
                                    color: event.confidence >= 0.8 ? GarudaTheme.danger : GarudaTheme.warning
                                )
                            }
                        }
                        .padding(20)

                        GarudaDivider()

                        // Details
                        VStack(spacing: 0) {
                            detailRow(label: "TIMESTAMP",
                                      value: event.timestamp.formatted(.dateTime.year().month().day().hour().minute().second()))
                            GarudaDivider().padding(.leading, 16)
                            detailRow(label: "LABEL",      value: event.label.uppercased())
                            GarudaDivider().padding(.leading, 16)
                            detailRow(label: "CONFIDENCE", value: String(format: "%.2f%%", event.confidence * 100))
                            GarudaDivider().padding(.leading, 16)
                            detailRow(label: "USER",       value: event.user ?? "—")
                            GarudaDivider().padding(.leading, 16)
                            detailRow(label: "BOX COLOR",  value: event.boxColor.uppercased())
                        }
                    }
                }
                .background(GarudaTheme.bgPrimary)
            } else {
                emptyDetailPlaceholder
            }
        }
    }

    // MARK: - Detail Row
    private func detailRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(GarudaFont.label())
                .foregroundColor(GarudaTheme.textTertiary)
                .frame(width: 120, alignment: .leading)
            Text(value)
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textPrimary)
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    // MARK: - Placeholders
    private var emptyListPlaceholder: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "bell.slash")
                .font(.system(size: 36))
                .foregroundColor(GarudaTheme.textQuaternary)
            Text(searchText.isEmpty ? "No detection events yet" : "No results for \"\(searchText)\"")
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textTertiary)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .background(GarudaTheme.bgPrimary)
    }

    private var emptyDetailPlaceholder: some View {
        VStack(spacing: 12) {
            Image(systemName: "cursorarrow.click")
                .font(.system(size: 36))
                .foregroundColor(GarudaTheme.textQuaternary)
            Text("Select an event to view details")
                .font(GarudaFont.mono(size: 12))
                .foregroundColor(GarudaTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(GarudaTheme.bgPrimary)
    }
}
