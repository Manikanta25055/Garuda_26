// ComponentLibrary.swift — Reusable atomic components used across all views
import SwiftUI

// MARK: - GarudaDivider
struct GarudaDivider: View {
    var body: some View {
        Rectangle()
            .fill(GarudaTheme.dividerColor)
            .frame(height: 0.5)
    }
}

// MARK: - StatusBadge
// 11pt bold mono text on a coloured pill — used for NORMAL, ONLINE, ALERT etc.
struct StatusBadge: View {
    let label: String
    let color: Color

    var body: some View {
        Text(label)
            .font(GarudaFont.badgeText())
            .foregroundColor(.white)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(color)
            .clipShape(RoundedRectangle(cornerRadius: GarudaTheme.radiusSM, style: .continuous))
    }
}

// MARK: - ReadoutRow
// bREADth-style: 10pt bold label / 18pt light value / 10pt unit
struct ReadoutRow: View {
    let label: String
    let value: String
    let unit: String
    var valueColor: Color = GarudaTheme.textPrimary

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label)
                .font(GarudaFont.label())
                .foregroundColor(GarudaTheme.textTertiary)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value)
                    .font(GarudaFont.value())
                    .foregroundColor(value == "--" ? GarudaTheme.textTertiary : valueColor)
                    .contentTransition(.numericText())
                Text(unit)
                    .font(GarudaFont.unit())
                    .foregroundColor(GarudaTheme.textTertiary)
            }
        }
    }
}

// MARK: - ModeToggle (Legacy)
// Pill-style boolean toggle with active tint colour
struct ModeToggle: View {
    let label: String
    let icon: String
    @Binding var isOn: Bool
    let activeColor: Color
    var isLoading: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 12))
                    .foregroundColor(isOn ? activeColor : GarudaTheme.textTertiary)
                Text(label)
                    .font(GarudaFont.mono(size: 11, weight: .medium))
                    .foregroundColor(isOn ? GarudaTheme.textPrimary : GarudaTheme.textSecondary)
                Spacer()
                if isLoading {
                    ProgressView()
                        .scaleEffect(0.6)
                        .frame(width: 14, height: 14)
                } else {
                    Circle()
                        .fill(isOn ? activeColor : GarudaTheme.textQuaternary)
                        .frame(width: 8, height: 8)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: GarudaTheme.radiusSM, style: .continuous)
                    .fill(isOn ? activeColor.opacity(0.1) : GarudaTheme.bgSurface2)
            )
            .overlay(
                RoundedRectangle(cornerRadius: GarudaTheme.radiusSM, style: .continuous)
                    .strokeBorder(isOn ? activeColor.opacity(0.3) : GarudaTheme.borderColor, lineWidth: 0.5)
            )
        }
        .buttonStyle(.plain)
        .focusable(false)
    }
}

// MARK: - ModernModeToggle
// Glassmorphic mode toggle with smooth animations
struct ModernModeToggle: View {
    let label: String
    let icon: String
    @Binding var isOn: Bool
    let activeColor: Color
    var isLoading: Bool = false
    var helpText: String? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                ZStack {
                    Circle()
                        .fill(isOn ? activeColor.opacity(0.15) : GarudaTheme.bgSurface3)
                        .frame(width: 32, height: 32)
                    Image(systemName: icon)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(isOn ? activeColor : GarudaTheme.textTertiary)
                }
                
                Text(label)
                    .font(GarudaFont.mono(size: 12, weight: .medium))
                    .foregroundColor(isOn ? GarudaTheme.textPrimary : GarudaTheme.textSecondary)
                
                Spacer()
                
                if isLoading {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .scaleEffect(0.7)
                        .tint(activeColor)
                } else {
                    ZStack {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(isOn ? activeColor : GarudaTheme.bgSurface3)
                            .frame(width: 40, height: 22)
                        Circle()
                            .fill(.white)
                            .frame(width: 18, height: 18)
                            .offset(x: isOn ? 9 : -9)
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(GarudaTheme.bgSurface2.opacity(0.4))
            )
        }
        .buttonStyle(.plain)
        .animation(.spring(response: 0.3, dampingFraction: 0.7), value: isOn)
        .help(helpText ?? "")
    }
}

// MARK: - PulsingDot
// Animated circle used for alert-active indicators
struct PulsingDot: View {
    let color: Color
    var size: CGFloat = 8
    @State private var pulse: Bool = false

    var body: some View {
        ZStack {
            Circle()
                .fill(color.opacity(0.3))
                .frame(width: size * 2, height: size * 2)
                .scaleEffect(pulse ? 1.4 : 1.0)
                .opacity(pulse ? 0 : 1)
                .animation(.easeOut(duration: 1.0).repeatForever(autoreverses: false), value: pulse)
            Circle()
                .fill(color)
                .frame(width: size, height: size)
        }
        .frame(width: size * 2, height: size * 2)
        .onAppear { pulse = true }
    }
}

// MARK: - GridOverlay
// 48pt subtle grid drawn with Canvas — Garuda web background grid translated to SwiftUI
struct GridOverlay: View {
    var spacing: CGFloat = 48
    var lineColor: Color = .white.opacity(0.03)

    var body: some View {
        Canvas { ctx, size in
            var path = Path()
            // Vertical lines
            var x: CGFloat = 0
            while x <= size.width {
                path.move(to: CGPoint(x: x, y: 0))
                path.addLine(to: CGPoint(x: x, y: size.height))
                x += spacing
            }
            // Horizontal lines
            var y: CGFloat = 0
            while y <= size.height {
                path.move(to: CGPoint(x: 0, y: y))
                path.addLine(to: CGPoint(x: size.width, y: y))
                y += spacing
            }
            ctx.stroke(path, with: .color(lineColor), lineWidth: 0.5)
        }
        .allowsHitTesting(false)
    }
}

// MARK: - AlertBannerView
// Icon + 15pt bold title + 11pt detail — used in Alerts list rows
struct AlertBannerView: View {
    let event: DetectionEvent

    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle()
                    .fill(Color(hex: event.boxColor).opacity(0.15))
                    .frame(width: 36, height: 36)
                Image(systemName: event.label == "person" ? "person.fill" : "exclamationmark.triangle.fill")
                    .font(.system(size: 16))
                    .foregroundColor(Color(hex: event.boxColor))
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(event.label.uppercased())
                        .font(GarudaFont.alertTitle())
                        .foregroundColor(GarudaTheme.textPrimary)
                    StatusBadge(
                        label: String(format: "%.0f%%", event.confidence * 100),
                        color: confidenceColor(event.confidence)
                    )
                }
                Text(event.timestamp.formatted(.dateTime.hour().minute().second()))
                    .font(GarudaFont.alertDetail())
                    .foregroundColor(GarudaTheme.textTertiary)
            }
            Spacer()
            if let user = event.user {
                Text(user)
                    .font(GarudaFont.statusSmall())
                    .foregroundColor(GarudaTheme.textTertiary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func confidenceColor(_ c: Double) -> Color {
        if c >= 0.8 { return GarudaTheme.danger }
        if c >= 0.5 { return GarudaTheme.warning }
        return GarudaTheme.textTertiary
    }
}

// MARK: - ConnectionDot
// Small status indicator used in sidebar and menu bar
struct ConnectionDot: View {
    let status: ConnectionStatus

    var body: some View {
        Circle()
            .fill(dotColor)
            .frame(width: 7, height: 7)
    }

    private var dotColor: Color {
        switch status {
        case .connected:    return GarudaTheme.success
        case .connecting:   return GarudaTheme.warning
        case .disconnected: return GarudaTheme.textQuaternary
        case .error:        return GarudaTheme.danger
        }
    }
}

// MARK: - SectionHeader
// 10pt bold mono label used above groups in panels
struct SectionHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(GarudaFont.sectionHeader())
            .foregroundColor(GarudaTheme.textTertiary)
            .padding(.bottom, 4)
    }
}

// MARK: - ModernStatCard
// Glassmorphic stat card with icon, label, and value
struct ModernStatCard: View {
    let icon: String
    let label: String
    let value: String
    var valueColor: Color = GarudaTheme.textPrimary
    var helpText: String? = nil
    
    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(valueColor.opacity(0.12))
                    .frame(width: 40, height: 40)
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(valueColor)
            }
            
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(GarudaFont.mono(size: 10, weight: .medium))
                    .foregroundColor(GarudaTheme.textTertiary)
                Text(value)
                    .font(GarudaFont.mono(size: 16, weight: .semibold))
                    .foregroundColor(valueColor)
                    .lineLimit(1)
            }
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(GarudaTheme.bgSurface2.opacity(0.4))
        )
        .help(helpText ?? "")
    }
}

// MARK: - Preview
#Preview("Component Library") {
    ScrollView {
        VStack(alignment: .leading, spacing: 24) {
            StatusBadge(label: "NORMAL", color: GarudaTheme.success)
            StatusBadge(label: "ALERT", color: GarudaTheme.danger)
            StatusBadge(label: "NIGHT", color: GarudaTheme.accent)

            ReadoutRow(label: "DETECTIONS TODAY", value: "12", unit: "events", valueColor: GarudaTheme.accent)
            ReadoutRow(label: "UPTIME", value: "03:42:11", unit: "h", valueColor: GarudaTheme.textPrimary)
            ReadoutRow(label: "NO DATA", value: "--", unit: "")

            ModeToggle(label: "Privacy Mode", icon: "eye.slash", isOn: .constant(true), activeColor: GarudaTheme.accent) {}
            ModeToggle(label: "DND", icon: "bell.slash", isOn: .constant(false), activeColor: GarudaTheme.warning) {}

            HStack(spacing: 12) {
                PulsingDot(color: GarudaTheme.danger)
                PulsingDot(color: GarudaTheme.success)
                ConnectionDot(status: .connected)
                ConnectionDot(status: .disconnected)
                ConnectionDot(status: .connecting)
            }

            AlertBannerView(event: DetectionEvent(label: "person", confidence: 0.92))
        }
        .padding(24)
    }
    .frame(width: 400, height: 700)
    .background(GarudaTheme.bgPrimary)
}
