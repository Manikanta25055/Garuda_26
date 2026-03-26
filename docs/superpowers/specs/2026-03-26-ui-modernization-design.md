# Garuda UI Modernization — Design Spec

## Context
The Garuda web UI has a polished login page but the dashboard and admin pages feel cramped and utilitarian. The goal is to bring every page up to the login page's quality level — Apple-inspired, minimal, elegant — while adding user-friendly features and two-way Pi-to-web communication.

## Constraints
- Keep login page layout (fix: remove yellow autofill, polish docs modal)
- Keep background grid pattern
- Keep dark theme, existing color palette, SF Pro font stack
- Keep vanilla JS/CSS architecture (no frameworks)
- Keep all existing functionality

## Files to Modify
- `basic_pipelines/garuda_web/style.css` — Complete CSS overhaul
- `basic_pipelines/garuda_web/index.html` — Restructure dashboard, admin pages, add new components
- `basic_pipelines/garuda_web/app.js` — Toast system, tabbed logs, activity feed, push notifications

---

## 1. Login Page Fixes

### 1A. Remove Yellow Autofill
Add CSS to override browser autofill background:
```css
input:-webkit-autofill,
input:-webkit-autofill:hover,
input:-webkit-autofill:focus {
  -webkit-box-shadow: 0 0 0 1000px var(--surface) inset !important;
  -webkit-text-fill-color: var(--t1) !important;
  caret-color: var(--t1);
  transition: background-color 5000s ease-in-out 0s;
}
```

### 1B. Premium Docs Modal
- Replace dense text dump with tabbed sections (Overview | Detection | Auth | Stack)
- Each tab has a clean card layout with icon headers
- Flowcharts use styled step indicators (numbered circles connected by lines) instead of ASCII
- Code references use monospace inline pills
- Consistent 16px padding, proper heading hierarchy

---

## 2. Dashboard Overhaul

### 2A. Security Status Hero
- Full-width hero card with centered content
- Status ring with ambient glow (CSS box-shadow, colored by state)
  - Clear: subtle green glow `0 0 40px rgba(52,199,89,0.15)`
  - Alert: pulsing red glow `0 0 40px rgba(255,59,48,0.3)`
- Owner badge integrated cleanly below status text
- "Last alert" as subtle footer text

### 2B. Metric Tiles (replace hardware stats bar)
- 4 tiles in a row: CPU, Memory, Temperature, FPS
- Each tile: circular progress ring (SVG) + value + label
- Ring color: green (<60%), yellow (60-80%), red (>80%)
- Temperature ring: green (<65C), yellow (65-75C), red (>75C)
- Subtle background card with `var(--surface)` + border

### 2C. Activity Feed (new, replaces raw console for users)
- Live timeline: icon + event text + timestamp
- Events: "Person detected", "Owner arrived", "Alert triggered", "Mode changed"
- Max 30 items, newest at top
- Admin still gets full console as a separate expandable section

### 2D. Cards Redesign
- All cards: 12px border-radius, 20px padding, `var(--surface)` bg, `var(--border)` border
- 24px gap between cards
- Card headers: overline label style, no underlines
- Heatmap card: proper legend alignment, month labels fixed
- Recent Detections: timeline with colored dots (red=danger, yellow=watch, blue=info)

### 2E. Layout
- Keep two-column (main + sidebar) on desktop
- Sidebar width: 320px fixed
- Main column: fluid
- Mobile: stack to single column
- 24px gaps everywhere

---

## 3. Admin Pages — Settings Style

### 3A. Consistent Card Sections
Every admin page uses grouped cards:
```
[Card: Section Title + description]
  [Setting Row: label .................. control]
  [Setting Row: label .................. control]
[/Card]

[Card: Next Section]
  ...
```

### 3B. Users Page
- User cards (not table): avatar circle (colored), name, role badge, edit/delete icons
- Grid layout: 2 columns on desktop, 1 on mobile
- Add User button in page header

### 3C. Email Settings
- Single card with clean form rows
- App Password hint as a subtle info banner (not a green `.msg` div)
- Test Email button shows result as a toast

### 3D. System Settings
Split into discrete cards:
1. **Detection** — threshold slider, danger label
2. **Privacy** — blur toggle with description
3. **Devices** — device list + add form + scan button
4. **Narada** — API key input + status indicator (configured/not configured)
5. **Watch Labels** — tag-style input
6. **Master Keys** — separate card with managed list + add flow

### 3E. Logs Page — Tabbed Viewer
- 4 tabs: System | Detection | Presence | Voice
- Single console area that switches content
- Filter input applies to active tab
- Export buttons in header
- Each tab shows count badge

### 3F. Commands Page
- Clean card list instead of table
- Each command: phrase in accent color, response below, delete icon right
- Add Command opens inline form (not modal)

---

## 4. New Features

### 4A. Toast Notification System
- Bottom-center floating toasts
- Types: success (green), error (red), info (blue), warning (yellow)
- Auto-dismiss after 4s, manual dismiss with X
- Stack up to 3 toasts
- Replace all inline `.msg` elements

### 4B. Push Alert Notifications
- When WebSocket receives `alert_active: true`:
  - Banner slides down from top (already exists, polish it)
  - Browser Notification API (request permission on first alert)
  - Mobile vibration via `navigator.vibrate()`
- Quick actions: "View Camera", "Dismiss"

### 4C. System Health Warnings
- Dashboard shows warning cards when:
  - CPU > 85%
  - Temperature > 75C
  - FPS drops below 5
  - Camera blindness detected
- Yellow warning card with dismiss option

### 4D. Page Transitions
- All page switches: 120ms fade (opacity 0→1)
- Modal opens: scale(0.95) + fade in
- Modal closes: fade out

### 4E. Loading & Empty States
- Skeleton shimmer on dashboard cards while WS connects
- Empty states with text: "No alerts this week", "No detections yet", "No devices registered"

---

## 5. Global CSS Polish

### 5A. Card Component (standardized)
```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
```

### 5B. Setting Row Component
```css
.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
}
.setting-row:last-child { border-bottom: none; }
```

### 5C. Consistent Spacing
- Section gaps: 24px
- Card internal padding: 20px
- Form element gaps: 16px
- Text margins: 8px

---

## Verification
After implementation:
1. Open localhost:8080 — login page should have no yellow autofill
2. Login as user — dashboard should feel spacious with metric rings
3. Login as admin — all admin pages should use card-based settings layout
4. Logs page should have working tabs
5. Toast appears on settings save
6. Alert banner works with quick actions
7. Mobile responsive check — all pages stack properly
8. Docs modal should look premium with tabbed sections
