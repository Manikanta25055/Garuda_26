// Typography.swift — All font constructors matching the bREADth + Garuda design spec
import SwiftUI

enum GarudaFont {

    // ── Brand / Logo ───────────────────────────────────────
    // Used only in WelcomeView: "GARUDA" 32pt thin serif
    static func brand(size: CGFloat = 32) -> Font {
        .system(size: size, weight: .thin, design: .serif)
    }

    // ── Monospaced Data Fonts ─────────────────────────────
    // All live readouts, values, badges
    static func mono(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced)
    }

    // Preset sizes matching the bREADth design system
    static func label() -> Font    { mono(size: 10, weight: .bold) }
    static func value() -> Font    { mono(size: 18, weight: .light) }
    static func unit() -> Font     { mono(size: 10) }
    static func badgeText() -> Font { mono(size: 11, weight: .bold) }
    static func alertTitle() -> Font { mono(size: 15, weight: .bold) }
    static func alertDetail() -> Font { mono(size: 11) }
    static func sectionHeader() -> Font { mono(size: 10, weight: .bold) }
    static func tabLabel() -> Font   { mono(size: 10, weight: .bold) }
    static func statusSmall() -> Font { mono(size: 9, weight: .medium) }
    static func consoleText(size: CGFloat = 11) -> Font { mono(size: size) }

    // ── UI / Control Fonts (non-monospaced) ───────────────
    static func heading(size: CGFloat = 14) -> Font {
        .system(size: size, weight: .semibold)
    }
    static func body(size: CGFloat = 13) -> Font {
        .system(size: size, weight: .regular)
    }
    static func subheading(size: CGFloat = 12) -> Font {
        .system(size: size, weight: .medium)
    }
    static func ctaButton() -> Font {
        .system(size: 13, weight: .semibold, design: .monospaced)
    }
    static func otpDigit() -> Font {
        .system(size: 32, weight: .light, design: .monospaced)
    }
}
