---
name: MailFallBack
description: Self-hosted mail backup, drawn as airmail stationery. Every mailbox is an envelope in your care.
colors:
  paper: "#dce7f2"
  paper-rail: "#d2dfec"
  paper-env: "#eef4fa"
  paper-sunk: "#cbd9e8"
  stamp-paper: "#f6f9fd"
  ink: "#13254b"
  ink-2: "#3b4f72"
  rule: "#9db3cf"
  rule-strong: "#6f87a8"
  ok: "#1d5e43"
  active: "#155a8a"
  attention: "#7a4b00"
  error: "#a4122c"
  muted: "#4f5d73"
  error-wash: "rgba(164, 18, 44, 0.07)"
  attention-wash: "rgba(122, 75, 0, 0.06)"
  par-red: "#d9452b"
  par-blue: "#2b4fb8"
  focus: "#2b4fb8"
  selection: "rgba(43, 79, 184, 0.22)"
  overlay: "rgba(19, 37, 75, 0.42)"
  paper-dark: "#0e1a30"
  paper-rail-dark: "#0b1628"
  paper-env-dark: "#14243f"
  paper-sunk-dark: "#1c2e4e"
  stamp-paper-dark: "#1b2f52"
  ink-dark: "#e9e6dc"
  ink-2-dark: "#b3bdcc"
  rule-dark: "#2c3f5e"
  rule-strong-dark: "#4a6189"
  ok-dark: "#86d3ae"
  active-dark: "#8ec5f0"
  attention-dark: "#f2c46b"
  error-dark: "#ff9a9a"
  muted-dark: "#a3aec0"
  error-wash-dark: "rgba(255, 154, 154, 0.09)"
  attention-wash-dark: "rgba(242, 196, 107, 0.07)"
  par-red-dark: "#e8664c"
  par-blue-dark: "#6e8fe6"
  focus-dark: "#9db6ff"
  selection-dark: "rgba(157, 182, 255, 0.3)"
  overlay-dark: "rgba(3, 8, 18, 0.62)"
typography:
  display:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(1.75rem, 1.1rem + 2.2vw, 2.75rem)"
    fontWeight: 500
    lineHeight: 1.08
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "2.125rem"
    fontWeight: 500
    lineHeight: 1.15
    letterSpacing: "-0.012em"
  title-lg:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1.625rem"
    fontWeight: 500
    lineHeight: 1.15
    letterSpacing: "-0.012em"
  title:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 500
    lineHeight: 1.15
    letterSpacing: "-0.012em"
  body:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: 1.5
  body-sm:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.5
  label:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.25
  stamp:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: "0.07em"
    fontFeature: "tnum"
  postmark-time:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: 1.15
    letterSpacing: "0.02em"
    fontFeature: "tnum"
  mono:
    fontFamily: "ui-monospace, SF Mono, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.875em"
    fontWeight: 500
rounded:
  hairline: "1px"
  sheet: "2px"
  radius: "3px"
  oval: "999px"
  round: "50%"
spacing:
  s-1: "4px"
  s-2: "8px"
  s-3: "12px"
  s-4: "16px"
  s-5: "24px"
  s-6: "32px"
  s-7: "48px"
  s-8: "64px"
  col: "72rem"
  rail: "232px"
  topbar-h: "56px"
  chevron-h: "7px"
components:
  button-label:
    backgroundColor: "{colors.paper-env}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.radius}"
    padding: "8px 14px"
    height: "40px"
  button-label-hover:
    backgroundColor: "{colors.paper-sunk}"
    textColor: "{colors.ink}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper-env}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.radius}"
    padding: "8px 14px"
    height: "40px"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.error}"
    rounded: "{rounded.radius}"
    padding: "8px 14px"
    height: "40px"
  button-danger-hover:
    backgroundColor: "{colors.error-wash}"
    textColor: "{colors.error}"
  stamp-ok:
    textColor: "{colors.ok}"
    typography: "{typography.stamp}"
    rounded: "{rounded.oval}"
    padding: "3px 11px 3px 10px"
  stamp-active:
    textColor: "{colors.active}"
    typography: "{typography.stamp}"
    rounded: "0"
    padding: "3px 9px 3px 7px"
  stamp-attention:
    backgroundColor: "{colors.attention-wash}"
    textColor: "{colors.attention}"
    typography: "{typography.stamp}"
    rounded: "0"
    padding: "3px 9px 3px 7px"
  stamp-error:
    backgroundColor: "{colors.error-wash}"
    textColor: "{colors.error}"
    typography: "{typography.stamp}"
    rounded: "{rounded.hairline}"
    padding: "3px 9px 3px 7px"
  stamp-muted:
    textColor: "{colors.muted}"
    typography: "{typography.stamp}"
    rounded: "{rounded.sheet}"
    padding: "3px 9px 3px 7px"
  envelope:
    backgroundColor: "{colors.paper-env}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    padding: "{spacing.s-5}"
  envelope-lead:
    backgroundColor: "{colors.paper-env}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    padding: "{spacing.s-6}"
  sheet:
    backgroundColor: "{colors.paper-env}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    padding: "{spacing.s-5}"
  postmark:
    textColor: "{colors.ink}"
    rounded: "{rounded.round}"
    size: "108px"
  postmark-lg:
    textColor: "{colors.ink}"
    rounded: "{rounded.round}"
    size: "136px"
  provider-stamp:
    backgroundColor: "{colors.stamp-paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    size: "72px"
  ledger-cell:
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    padding: "11px 14px"
  ledger-head:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    padding: "12px 14px 8px"
  input:
    backgroundColor: "{colors.paper-env}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.radius}"
    height: "40px"
  address-field-input:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.title}"
    rounded: "0"
    padding: "8px 0"
  nav-item:
    textColor: "{colors.ink-2}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.radius}"
    padding: "8px 12px"
    height: "40px"
  nav-item-active:
    backgroundColor: "{colors.paper-env}"
    textColor: "{colors.ink}"
  seg-pressed:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper-env}"
    typography: "{typography.body-sm}"
    height: "40px"
  tag:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.sheet}"
    padding: "1px 7px"
---

# Design System: MailFallBack

## Overview

**Creative North Star: "Airmail in Your Care"**

Every mailbox is an airmail envelope lying on pale blue onionskin. A circular postmark says when the local backup was last copied, in tabular date and time. A rotated rubber hand stamp says, only when something needs saying, what state the mailbox is in. The red-and-blue par-avion chevron runs along the top edge of the shell and nowhere else. Admin pages are the sorting office: a ruled ledger with the route strip (Source, Local backup, Repository, Snapshot) printed in the same box, so the map and the list share one space.

Density is calm on the owner side and close-ruled on the admin side. Both run in one 72rem content column. The interface has one face at one weight, so hierarchy comes from size alone. Paper is never neutral white: the light ground is onionskin blue and the dark ground is navy-night paper written in chalk ink. State is always carried by shape plus icon plus text, never by colour alone, and red means only a real failure.

This world was built to reject the category default: a Pico/SaaS admin console with same-size KPI cards, sidebar badges and neon-on-dark status pills.

**Key Characteristics:**
- Onionskin ground (light) or navy-night paper (dark); envelopes are one step lighter stock.
- One self-hosted grotesque, Atkinson Hyperlegible Next Medium, never synthesised bold.
- Hand stamps with a distinct silhouette per tone, slightly rotated and printed through an ink-grain mask.
- Circular postmarks with wavy cancellation bars and tabular figures; a dashed empty ring when nothing was ever copied.
- Registered-mail labels for actions: a hairline frame, a paper face, and on the primary label the icon in its own ruled cell.
- One authored motion: the postmark stamps down when a sync lands.

## Colors

Navy ink on blue paper, five status inks that each pair with a shape, and a vermilion/ultramarine pair kept for the frame edge. Every value has a dark counterpart (the `-dark` keys). Dark is declared twice in the stylesheet, once for `prefers-color-scheme` and once for the explicit `data-theme="dark"` toggle, and both copies must stay identical.

### Primary
- **Registry Navy** (`ink`): body text, headings, primary label fill, button frames, the address-field underline, postmark rings. Contrast is 12.0:1 on paper and 13.6:1 on envelope stock. In dark mode it becomes **Chalk** (`ink-dark`).
- **Faded Navy** (`ink-2`): secondary text, column heads, metadata, placeholders, icons at rest. It stays at 6.6:1 or better on paper.

### Secondary
- **Par-Avion Vermilion** (`par-red`) and **Par-Avion Ultramarine** (`par-blue`): used only for the chevron frame edge (7px, along the top of every screen, plus the bottom on login). They are deliberately different from the carmine error ink and the steel-blue active ink.
- **Focus Ultramarine** (`focus`): the 2px focus outline (offset 2px), the address-field focus underline and the caret.

### Tertiary (status inks: each one is tied to a shape)
- **Cancellation Green** (`ok`): current, up to date. Double oval stamp, ring mark.
- **Transit Steel** (`active`): working or recovering on its own (syncing, initial sync, paused, migrating). Framed rectangle with heavy side bars, open-square mark.
- **Held Amber** (`attention`): the owner must act but nothing is broken yet (sign-in needed, out of date, never synced). Double-rule box on an amber wash, double-box mark.
- **Return Carmine** (`error`): a real failure. Heavy box on a red wash, solid-block mark. It is the only red.
- **Unfranked Slate** (`muted`): deliberately off, stopped, waiting, suspended. Dashed outline, dashed-ring mark.

### Neutral
- **Onionskin** (`paper`): the page ground.
- **Rail Onionskin** (`paper-rail`): the fixed left navigation rail.
- **Envelope Stock** (`paper-env`): envelopes, sheets, ledgers, inputs, menus, the face of a label button.
- **Sunk Onionskin** (`paper-sunk`): hover fills, wells, disabled inputs, inline code.
- **Stamp Face** (`stamp-paper`): the face of the perforated provider stamp and of card glyph tiles.
- **Hairline Rule** (`rule`) and **Strong Rule** (`rule-strong`): dividers, envelope edges, ledger rules, dashed fold lines, the 1px under-shadow on labels.

### Named Rules
**The Only-Red Rule.** `error` is the one red, and only the resolver's `error` tone uses it. Throttled, transient, budget-paused and interrupted states recover on their own, so they wear `active`, never red.

**The Frame-Edge Rule.** Par-avion vermilion and ultramarine belong to the chevron frame edge. They never mark status, never fill a button and never decorate content.

**The Never-White Rule.** No surface is neutral white or neutral grey. Every paper is tinted toward the navy hue.

## Typography

**Display Font:** Atkinson Hyperlegible Next Medium (self-hosted woff2, OFL), falling back to system-ui
**Body Font:** the same face at the same weight
**Label/Mono Font:** ui-monospace stack, only for tokens, paths and code

**Character:** One legible grotesque at weight 500 throughout. The `@font-face` declares a 100–900 range so the browser never fakes a bold, and `font-synthesis: none` backs that up. `strong`, `th`, `label`, `summary` and headings inherit the weight. Emphasis comes from size and ink, not boldness.

### Hierarchy
- **Display** (500, clamp 28–44px, 1.08, -0.02em): the owner-home verdict sentence ("New Gmail needs attention."), capped at 22ch. Used once per screen.
- **Headline** (500, 34px, 1.15): page titles (`h1`, page head).
- **Title Large** (500, 26px, 1.15): mailbox names on envelopes, `h2`.
- **Title** (500, 20px, 1.15): section titles, sheet titles, the verdict reason, the address-field query text.
- **Body** (500, 16px, 1.5): running text. Line length is held by ch caps (46–72ch), not by narrowing the column.
- **Body Small** (500, 14px): ledger cells, buttons, nav items, metadata, page summaries.
- **Label** (500, 12px): ledger column heads (0.03em tracking), route stage names, postmark dates, tags. 12px is the floor.
- **Stamp** (500, 12px, uppercase, 0.07em): hand-stamp text only. The uppercase tracking is part of the rubber-stamp material.

### Named Rules
**The One-Weight Rule.** Hierarchy comes from size steps (12/14/16/20/26/34/verdict), never from weight.

**The Tabular-Figures Rule.** Tables, `time`, postmarks, stamps, counts and summaries use `tabular-nums`. Numbers are real and line up; no invented percentages.

## Layout

The grid is declared, not improvised. It uses an 8px unit and a spacing ladder of 4/8/12/16/24/32/48/64 (`s-1`–`s-8`). The shell has a 7px chevron edge fixed at the top, a 232px fixed rail on the left, and a sticky 56px top bar. Every page sits in one content column, `--col: 72rem`, with 32px gutters (`max-width: calc(var(--col) + 2 * var(--s-6))`). Owner home, ledgers, admin and System all share that column. Reading measure inside it comes from ch caps on text blocks.

Each screen has one dominant envelope. On the owner home this is the lead envelope: at least 300px tall, return address top-left, a 136px postmark top-right, the verdict and addressee bottom-left, and the verdict stamp overhanging its bottom-right corner. Below it the address field is the primary control, then the stacked mailbox envelopes (16px apart). The admin dashboard leads with the route strip sitting directly on top of the ledger in one box.

Responsive changes:
- **768px:** the rail becomes an off-canvas drawer (260ms slide) with an overlay, the top bar shows a hamburger and the wordmark, and content padding drops to 16px.
- **720px:** ledgers restack into flex rows with the stamp first and the action pushed right; ledger and route boxes bleed to the screen edge.
- **860px / 420px:** the route strip goes to two columns, then one.
- **640px:** sheets bleed edge to edge.
- **560px:** segmented choices go full width, two per row.

## Elevation & Depth

Depth here is paper on paper: lighter envelope stock on a tinted ground, hairline edges, and soft navy-tinted drops. Shadows are ambient and structural, never decorative. Labels get a 1px rule-coloured under-edge so they read as stuck on. Floating slips (menus, health panel, calendar, drawer, toasts) get the deeper pop shadow. Pressing a label moves it down 1px and removes its under-edge.

### Shadow Vocabulary
- **Envelope drop** (`box-shadow: 0 1px 0 rgba(19,37,75,0.06), 0 12px 28px -20px rgba(19,37,75,0.55)`; dark `0 1px 0 rgba(0,0,0,0.35), 0 14px 30px -20px rgba(0,0,0,0.85)`): envelopes, sheets, framed ledgers.
- **Pop slip** (`box-shadow: 0 2px 4px rgba(19,37,75,0.12), 0 18px 40px -16px rgba(19,37,75,0.45)`; dark `0 2px 4px rgba(0,0,0,0.4), 0 20px 44px -16px rgba(0,0,0,0.9)`): dropdown menus, the health panel, flatpickr, the open mobile drawer, toasts.
- **Label under-edge** (`box-shadow: 0 1px 0 var(--rule-strong)`): label buttons, segmented choices, provider cards.
- **Picked frame** (`box-shadow: inset 0 0 0 1px var(--ink)`): a picked provider card or checked checkbox pill, which gets a doubled ink frame.
- **Stamp lift** (`filter: drop-shadow(0 1px 1px var(--stamp-shadow))`): the perforated provider stamp only.

### Named Rules
**The Paper-Not-Plastic Rule.** Shadows are tinted with the ink hue and blur outward softly. No glows and no coloured halos.

### Motion
- **Postmark strike** (`postmark-strike`, 380ms, `cubic-bezier(0.16, 1, 0.3, 1)`): the postmark scales 1.32→0.965→1 and rotates -17°→-9°, landing out of a 1.2px blur. The cancellation bars ink in from the right over 340ms after a 120ms delay. `postmark.js` triggers it only when a re-rendered postmark carries a newer copy time than the one it replaces.
- **State transitions** (140–160ms, same ease-out): background and colour changes on labels, nav items and rows. The drawer slides in 260ms.
- **Reduced motion:** under `prefers-reduced-motion: reduce`, the postmark strike is skipped both in JS (the check runs before the class is added) and in CSS (`animation: none`). The drawer slide, label press, toasts and workspace transitions are also turned off.

**The One-Authored-Motion Rule.** The postmark stamp-down is the only authored motion in the interface. Everything else is a short state transition.

## Shapes

Corners are nearly square. Envelopes, sheets, tags and slips use 2px; controls use 3px (`--radius`); stamps use 0–1px, except the ok oval (999px). Circles appear only where the world has them: postmarks, the brand mark (a ring with an offset outer ring), wizard step numbers and the ok tone mark. Borders carry meaning by style. A solid hairline is structure. A dashed rule is a fold line inside a sheet (envelope notes, quick-add, route note, expanded sub-rows). A double rule sits under ledger heads and marks attention. A heavy rule marks error. The provider stamp has a real perforated edge made with a radial-gradient mask (2.4px holes on an 8px pitch) and an inset framed face. Stamps and postmarks print through a fractal-noise ink-grain mask (alpha 0.75–1, never enough to break contrast).

**The Shape-Carries-State Rule.** Each tone has its own silhouette, in stamps (oval / side-barred box / double box / heavy box / dashed), in marks (ring / open square / double box / solid block / dashed ring), and in notes and the health toggle (hairline / double / heavy). A colour-blind reader can still tell every state apart.

## Components

### Buttons (registered-mail labels)
A label you stick on: framed, plain, and pressed flat when used.
- **Shape:** gently squared (3px), at least 40×40px, 8px 14px padding, 14px text, 8px icon gap.
- **Default:** envelope-stock face, a 1px ink frame and the label under-edge. Hover fills with sunk onionskin.
- **Primary:** an ink fill with envelope-stock text. The leading icon sits in its own cell, separated by a 1px divider at 45% paper. Hover mixes 86% ink with paper.
- **Danger:** error ink and frame on a transparent face, with the error wash on hover. Disabled danger falls back to faded ink.
- **Active:** moves down 1px and drops the under-edge. Disabled sits at 45% opacity.
- **Large / Block:** 48px tall, 12px 20px padding, 16px text / full width.
- **Link button:** underlined text with a strong-rule underline that turns ink on hover.

### Hand stamps (signature)
The resolver's label (`services/mailbox_status.py`), inked. The text is always the resolver label: "Up to date", "Syncing", "Initial sync", "Paused", "Sign-in needed", "Out of date", "Sync failed", "Stopped", "Suspended", "Waiting for first sync". The tone picks the shape. Text is 12px uppercase with 0.07em tracking and a 13px Lucide icon, rotated per tone (ok -3°, active -1.5°, attention 2°, error -2.5°), and printed through the ink grain. The large variant (14px, 6px 14px padding) overhangs the lead envelope's corner as the verdict stamp.

### Postmark (signature)
A circular date stamp: 108px, or 136px for the large variant. It has a 2px ring with a 1px inner ring inset 8px, is rotated -9°, and carries a "Last copied" label, a DD MON YYYY date and an HH:MM time with a small zone. Four wavy cancellation bars at 55% opacity run off to its left. The server prints the time in UTC and `postmark.js` re-sets it in the viewer's own zone. If nothing was ever copied, it is a dashed empty ring reading "Not copied yet", with no cancellation bars. Ledgers use the one-line form: `30 SEP 2026 · 22:06`.

### Provider stamp
A 72px perforated postage stamp in the envelope corner. It has a stamp-face background, a 1px strong-rule frame inset 6px, a neutral Lucide glyph and the provider name in 12px type. It never uses a third-party logo.

### Cards / Containers (envelopes and sheets)
- **Corner Style:** 2px.
- **Background:** envelope stock on the onionskin ground.
- **Shadow Strategy:** envelope drop.
- **Border:** 1px hairline. A mailbox envelope that needs attention moves to the strong rule.
- **Internal Padding:** 24px for mailbox envelopes and sheets, 32px for the lead and hero envelopes.
- **Rule:** a page is a stack of sheets, never boxes nested in boxes. Subsections inside a sheet are split by a dashed fold line.

### Ledger
The sorting-office register. It uses real tables on envelope stock with 11px 14px cells, 14px text and hairline row rules. Column heads are 12px faded ink over a 3px double strong rule. Group heads ("Needs attention (n)") sit on the onionskin ground. The state column comes first, and both state and action shrink to fit. Row hover is a 35% sunk tint. Numeric columns are right-aligned in tabular figures. Below 720px each row restacks with the stamp first.

### Route strip
Source → Local backup → Repository → Snapshot, laid out in four equal columns on the onionskin ground. It sits in the same box as the ledger, with no bottom border between them. Each stage has a 12px name with an icon, a 14px count and a tone mark, and stages are joined by drawn chevrons. An optional explainer note folds above it behind a dashed fold line.

### Inputs / Fields
- **Style:** envelope stock, a 1px strong-rule frame, 3px corners, at least 40px tall, 16px text with tabular figures. Labels are 14px faded ink.
- **Focus:** the border turns ink and a 2px selection ring appears.
- **Error / Disabled:** the frame turns error ink / the field fills with sunk onionskin and faded ink.
- **Address field (signature):** the search is written on an address line. There is no box, only a 2px ink underline that turns into a focus underline with a 2px under-glow. Query text is 20px with a 20px label above it, and the search actions sit on the same line.

### Navigation
A 232px fixed rail in rail onionskin, holding the brand wordmark with its ringed mark. Items are 40px tall with 14px faded-ink text and a 12px icon gap. Hover fills with sunk onionskin. The active item gets an envelope-stock face, a hairline frame and a 1px under-edge, so it reads as a label placed on the rail. Section labels are 12px faded ink below a hairline. The top bar is 56px on the ground colour and holds the single admin health indicator. That indicator is calm text at rest; when there is a problem it becomes a toggle with a tone border (dashed / solid active / double attention / heavy error) and opens a pop-slip panel. On mobile the rail becomes a drawer.

### Notes, tags, segmented choice
Notes are slips on envelope stock. Info uses the strong hairline, attention a 3px double rule on the amber wash, error a 2px rule on the red wash. Tags are 12px faded ink in a strong-rule frame with 2px corners. The segmented choice is a single ink-framed label row; the pressed segment is inked (`aria-pressed`, not a fake tablist). Copyable one-time values print on a dashed ink "slip".

## Do's and Don'ts

### Do:
- **Do** keep every page in the one `--col: 72rem` column and hold reading measure with ch caps.
- **Do** spell status with the resolver's label and its stamp shape, using the `mail.stamp` macro in `partials/airmail.html`. Never hand-roll a stamp.
- **Do** pair every state colour with its shape and an icon. A grayscale screenshot must still read.
- **Do** show the last-copied time as a postmark (`mail.postmark` / `mail.postmark_line`) in tabular figures, and show the dashed empty ring when there is no copy.
- **Do** lead each screen with one dominant envelope or one ledger. Everything else stacks under it.
- **Do** honour `prefers-reduced-motion` for any new motion, the way the postmark strike does.
- **Do** keep both dark-token blocks (OS preference and explicit toggle) identical when changing a dark value.

### Don't:
- **Don't** use `error` red for anything except a real failure. Throttled, transient, budget and interrupted states use `active`.
- **Don't** use the par-avion vermilion or ultramarine outside the chevron frame edge.
- **Don't** set any surface in neutral white or neutral grey.
- **Don't** add a second font weight or a second display face. Hierarchy is size.
- **Don't** build same-size KPI card grids, sidebar count badges or neon-on-dark status pills.
- **Don't** put a third-party provider logo in the provider stamp.
- **Don't** add a second authored motion. State changes stay at 140–260ms ease-out.
- **Don't** nest framed boxes inside a sheet. Use a dashed fold line.
