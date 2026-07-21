# Design

## Source of truth

- Status: Active
- Last refreshed: 2026-07-16
- Primary product surfaces: Windows desktop main window, task editor, approval dialog, log/run detail, system tray
- Evidence reviewed: `docs/PLAN.md` §§2, 4, 7, 8, 10, 14–16; `README.md`; `docs/ARCHITECTURE.md`; current domain/application services
- No screenshots, brand assets, component library, or prior UI implementation were present.

## Brand

- Personality: calm, operational, trustworthy, compact
- Trust signals: explicit AI/status labels, visible project path and command before execution, timestamped logs, destructive actions behind confirmation
- Avoid: chat-like decoration, hidden automation, status communicated by color alone, dense dashboard ornament

## Product goals

- Goals: let one technical user register, prioritize, approve, monitor, retry, and inspect Claude/Codex work without a terminal
- Non-goals: team collaboration, cloud sync, mobile UI, arbitrary agent plugins, branch-per-task orchestration
- Success signals: core queue actions are reachable from one window; current agent/task state is visible without opening dialogs; failures lead directly to logs and recovery actions

## Personas and jobs

- Primary personas: a developer or technical power user running Claude and Codex locally on Windows
- User jobs: queue work ahead of quota resets, prevent project collisions, approve risky starts, see what is running, inspect failures, recover after restart
- Key contexts of use: background operation during development, brief tray checks, focused failure investigation

## Information architecture

- Primary navigation: one main window plus system tray menu
- Core screens: main queue, task add/edit dialog, execution approval dialog, settings dialog, run/log detail
- Content hierarchy: agent availability → queue controls → task list → selected task details and logs

## Design principles

- Automation stays legible: show the next action, target agent, path, and state transition.
- Safe defaults remain visible: confirmation mode and allowed roots are never buried.
- Operational density beats decoration: use a compact table and native controls with clear grouping.
- Tradeoff: desktop-first fixed information density; no small-screen/mobile adaptation in v1.

## Visual language

- Color: native Qt palette with one blue action accent; semantic green/amber/red supported by text labels and icons
- Typography: platform UI font; monospaced font only for commands and logs
- Spacing/layout rhythm: 8 px base rhythm, 12–16 px group padding, compact table rows
- Shape/radius/elevation: native widget shapes; restrained borders; no decorative shadows
- Motion: only native progress/activity feedback; no nonessential animation
- Imagery/iconography: standard Qt/system icons; no custom imagery in v1

## Components

- Existing components to reuse: Qt item views, dialogs, splitters, tab widgets, date/time edits, system tray icon/menu
- New/changed components: agent status panel, task table model/view, task dialog, approval dialog, log viewer, main window, scheduler controller
- Variants and states: Claude/Codex agent cards; waiting/reserved/running/retry/failed/succeeded task rows; empty/log-missing/error states
- Token/component ownership: palette and spacing stay local to `ui/`; no separate design-system dependency

## Accessibility

- Target standard: keyboard-operable desktop UI with WCAG-inspired contrast/readability
- Keyboard/focus behavior: logical tab order, default/cancel buttons, labeled fields, table actions available from keyboard and menus
- Contrast/readability: semantic states always include text; log viewer uses selectable high-contrast text
- Screen-reader semantics: every input and action has an accessible label/tool tip where the visible label is insufficient
- Reduced motion and sensory considerations: rely on native Qt behavior; no flashing or continuous decorative motion

## Responsive behavior

- Supported breakpoints/devices: Windows desktop, minimum useful window 960×640; high-DPI scaling delegated to Qt
- Layout adaptations: horizontal/vertical splitters remain user-resizable; columns can stretch or be resized
- Touch/hover differences: mouse and keyboard are primary; no hover-only actions

## Interaction states

- Loading: status-bar message while refreshing or launching work
- Empty: task table and log view show explicit empty messages
- Error: concise modal for blocking actions plus persistent task/log detail
- Success: status-bar/tray notification and updated task state
- Disabled: start/approve controls disabled when agent, path, or task state is ineligible
- Offline/slow network: CLI work remains visibly running until process completion or timeout; no fake progress percentage

## Content voice

- Tone: concise Korean operational labels, with raw CLI output preserved verbatim in logs
- Terminology: Claude, Codex, 작업, 대기, 예약, 실행, 재시도, 실패, 완료, 사용 가능 시각
- Microcopy rules: actions use verbs; confirmations name the task and path; errors say what failed and where to inspect details

## Implementation constraints

- Framework/styling system: PySide6 native widgets; no web view or third-party theme
- Design-token constraints: use Qt palette and a small local spacing/status-color mapping only
- Performance constraints: DB/process work must not block the UI thread; polling uses Qt timers and async worker coordination
- Compatibility constraints: Python 3.12+, Windows primary; naive UTC in persistence converted only at the UI boundary
- Test/screenshot expectations: headless Qt widget/model tests with `QT_QPA_PLATFORM=offscreen`; smoke launch and tray/menu construction; no pixel-match reference exists

## Open questions

- [x] v1 uses Korean display labels while persistence and raw CLI output remain English/verbatim.
- [x] Startup registration is opt-in and uses the current-user HKCU Run key; no elevated privilege or Task Scheduler entry is required.
