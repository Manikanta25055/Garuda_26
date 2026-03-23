// GarudaTheme.swift — All design tokens: colors, radii, shadows, spacing
// Fuses bREADth warm minimalism (welcome screen) with Garuda web's dark Apple aesthetic
import SwiftUI

// MARK: - Hex Color Extension
extension Color {
    init(hex: String) {
        var h = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if h.hasPrefix("#") { h = String(h.dropFirst()) }
        var val: UInt64 = 0
        Scanner(string: h).scanHexInt64(&val)
        let r = Double((val >> 16) & 0xFF) / 255.0
        let g = Double((val >> 8)  & 0xFF) / 255.0
        let b = Double(val          & 0xFF) / 255.0
        self.init(red: r, green: g, blue: b)
    }
}

// MARK: - Design Tokens
enum GarudaTheme {

    // ── Backgrounds ──────────────────────────────────────
    static let bgPrimary  = Color(hex: "#0D0D0D")
    static let bgSurface1 = Color(hex: "#111111")
    static let bgSurface2 = Color(hex: "#1A1A1A")
    static let bgSurface3 = Color(hex: "#222222")

    // ── Accents ───────────────────────────────────────────
    static let accent   = Color(hex: "#2997FF")
    static let danger   = Color(hex: "#FF3B30")
    static let success  = Color(hex: "#34C759")
    static let warning  = Color(hex: "#FF9F0A")
    static let purple   = Color(hex: "#AF52DE")

    // ── Text Hierarchy ────────────────────────────────────
    static let textPrimary    = Color(hex: "#F5F5F7")
    static let textSecondary  = Color(hex: "#A1A1A6")
    static let textTertiary   = Color(hex: "#6E6E73")
    static let textQuaternary = Color(hex: "#3A3A3C")

    // ── Welcome Screen (bREADth palette) ─────────────────
    static let warmCream     = Color(hex: "#FFFBF1")
    static let warmYellow    = Color(hex: "#FFF2D0")
    static let softPink      = Color(hex: "#FFB2B2")
    static let coral         = Color(hex: "#E36A6A")
    static let darkCinema    = Color(hex: "#141414")

    // ── Corner Radii ──────────────────────────────────────
    static let radiusXS: CGFloat = 3
    static let radiusSM: CGFloat = 4
    static let radiusMD: CGFloat = 6
    static let radiusLG: CGFloat = 12

    // ── Borders ───────────────────────────────────────────
    static let borderColor = Color.white.opacity(0.07)
    static let dividerColor = Color.white.opacity(0.08)

    // ── Window / Panel Sizing ─────────────────────────────
    static let sidebarWidth: CGFloat  = 220
    static let rightPanelWidth: CGFloat = 320
    static let windowMinWidth: CGFloat  = 1100
    static let windowMinHeight: CGFloat = 700
    static let windowDefaultWidth: CGFloat  = 1280
    static let windowDefaultHeight: CGFloat = 800
}

// MARK: - Shadow ViewModifier
struct GarudaShadow: ViewModifier {
    func body(content: Content) -> some View {
        content
            .shadow(color: .black.opacity(0.04), radius: 1, x: 0, y: 1)
    }
}

extension View {
    func garudaShadow() -> some View {
        modifier(GarudaShadow())
    }
}

// MARK: - Card Style ViewModifiers

// Modern glassmorphic card with no borders
struct ModernGlassCardModifier: ViewModifier {
    var cornerRadius: CGFloat = 16
    var opacity: Double = 0.4
    
    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(GarudaTheme.bgSurface1.opacity(opacity))
                    .shadow(color: .black.opacity(0.15), radius: 20, x: 0, y: 10)
                    .shadow(color: .black.opacity(0.1), radius: 4, x: 0, y: 2)
            )
    }
}

// Legacy card with border (for compatibility)
struct GarudaCardModifier: ViewModifier {
    var cornerRadius: CGFloat = GarudaTheme.radiusMD
    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(GarudaTheme.bgSurface1)
                    .garudaShadow()
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(GarudaTheme.borderColor, lineWidth: 0.5)
            )
    }
}

extension View {
    func garudaCard(cornerRadius: CGFloat = GarudaTheme.radiusMD) -> some View {
        modifier(GarudaCardModifier(cornerRadius: cornerRadius))
    }
    
    func modernGlassCard(cornerRadius: CGFloat = 16, opacity: Double = 0.4) -> some View {
        modifier(ModernGlassCardModifier(cornerRadius: cornerRadius, opacity: opacity))
    }
}
