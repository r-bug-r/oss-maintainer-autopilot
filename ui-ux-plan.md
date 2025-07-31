# OSS Maintainer Autopilot — UI/UX Plan

> Project-specific section extracted from `../open-source-projects-ui-ux-plan.md`. Shared UI/UX gates in that source remain authoritative.

## Shared UI/UX Quality Gate (authoritative source excerpt)

## 1. UI/UX quality gate

### 1.1 Product-wide design contract

Every project UI must:

- Put the primary user task first: orient, inspect, act, confirm.
- Use product-specific data and labels; never invent users, testimonials, metrics, or activity.
- Define loading, empty, error, success, disabled, permission, offline, and destructive-confirmation states before implementation.
- Keep primary actions visible at the point where the task ends; avoid scroll-back for approval, save, or export.
- Use semantic HTML, visible keyboard focus, labels, useful error copy, and screen-reader announcements.
- Never rely on color alone to communicate status.
- Use fixed-height persistent chrome and reserve space for transient status so loading or error text cannot move the layout.
- Preserve the user's context when a side panel, modal, filter, or tab changes.
- Make data deletion, provider use, retention, and permission boundaries visible at the moment they matter.
- Support 320, 390, 768, 1024, and 1440 CSS-pixel widths without horizontal overflow.

### 1.2 Shared design stance

**Aesthetic direction:** editorial industrial utility. The interface should feel like a trustworthy working instrument: crisp borders, compact data tables, deliberate whitespace, one restrained accent, and typography that makes hierarchy obvious. Avoid purple SaaS gradients, floating-card stacks, glassmorphism, excessive pills, giant marketing heroes, and decorative AI motifs.

**Differentiation anchor:** every project exposes its most important trust boundary as a visible structural element:

- Maintainer Autopilot: a proposal-to-approval rail.
- Trace-to-Eval: a trace timeline with pass/fail evidence.
- Agent Firewall: an allow/approval/deny decision ledger.
- Gateway: a live budget meter and routing decision.
- Home File: a document citation rail connecting extracted fields to the source page.

If the logo is removed, this anchor should still identify the product.

**DFII estimate:** 11/15 for developer tools and 9/15 for Home File. The direction fits operational products, is feasible with CSS and semantic HTML, performs well without effects, and remains maintainable when tokens are enforced.

### 1.3 Token architecture

Create one versioned token file per product, with names stable across light and dark themes. No component may use an unregistered color or spacing value.

```css
:root {
  --color-canvas: #f5f3ee;
  --color-surface: #fffdf8;
  --color-surface-raised: #ffffff;
  --color-ink: #20221f;
  --color-ink-muted: #646860;
  --color-border: #d6d2c8;
  --color-border-strong: #9a9d94;
  --color-accent: #b6422b;
  --color-accent-ink: #fffaf2;
  --color-success: #356b4b;
  --color-warning: #8c641e;
  --color-danger: #a72f2f;
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-12: 3rem;
  --radius-control: 0.25rem;
  --radius-panel: 0.5rem;
  --focus-ring: 0 0 0 3px #f5f3ee, 0 0 0 5px #b6422b;
  --motion-fast: 150ms;
  --motion-standard: 220ms;
}
```

These are starting tokens, not measured brand values. Before production, calculate every text/background pair with a WCAG checker and record the ratio in a token register. The accent is for actions and emphasis, not body text unless it passes the contrast gate.

Typography:

- Display: a locally available, licensed serif or strong editorial face such as Newsreader only if its license is verified; otherwise use a local system fallback with no external download.
- Body/UI: a readable sans-serif already available on the platform or a checked open font; do not make a paid font a dependency.
- Data/code: a monospace face only for IDs, hashes, timestamps, policies, token counts, and logs.
- Use a type scale rather than viewport-scaled text. Keep letter spacing at 0 except where a documented all-caps label needs spacing.

Component primitives:

- `AppShell`, `SkipLink`, `PageHeader`, `Breadcrumbs`
- `Button` with text and familiar icon only when the icon improves recognition
- `IconButton` with a tooltip and accessible name
- `StatusBadge` with text plus icon/pattern
- `DataTable` with responsive stacked fallback
- `FilterBar`, `SearchField`, `SegmentedControl`
- `Panel` for genuinely framed tools; do not nest cards inside cards
- `Timeline`, `Diff`, `Citation`, `AuditEvent`
- `Dialog`, `Drawer`, `Toast`, `ConfirmAction`
- `Skeleton`, `EmptyState`, `ErrorState`, `PermissionState`

### 1.4 Interaction and motion

One motion personality per product: **Snap** for developer tools, **Silk** for Home File. Motion communicates state, never decorates the screen.

- 150ms control feedback.
- 220ms panel and drawer transitions.
- Skeleton shimmer only for known content shapes.
- Success confirmation uses a short, non-blocking pop-in.
- No animated KPI numbers, infinite ambient loops, parallax, or delayed primary content.
- Honor `prefers-reduced-motion` by removing transforms and keeping state changes immediate.

### 1.5 Accessibility and responsive gates

Before a UI release:

- Run axe-core and fix all serious/critical findings; document accepted false positives.
- Tab through every flow without a mouse; focus must remain visible and ordered.
- Test with a screen reader on the primary workflow and verify headings, labels, status announcements, dialog focus, and table relationships.
- Meet WCAG 2.2 AA contrast targets: 4.5:1 normal text, 3:1 large text and essential UI indicators.
- Use 44px minimum touch targets for compact controls where practical; never make a primary action smaller than 44px high.
- Use `aria-live="polite"` for asynchronous result/status updates and `role="alert"` only for urgent errors.
- Test zoom at 200% and text-only reflow at 320px.
- Check `document.documentElement.scrollWidth - document.documentElement.clientWidth === 0` at 390px, 768px, 1024px, and 1440px.
- Render screenshots at each breakpoint and inspect them for overlap, clipping, ragged alignment, unreadable density, and broken focus states.
- Use `min-width: 0`, `overflow-wrap: anywhere`, wrapping toolbars, reserved status heights, and `body { overflow-x: clip; }` as a backstop. The backstop does not replace root-cause fixes.

### 1.6 UI implementation sequence

1. Write the task flow and acceptance states.
2. Create tokens and a semantic component inventory.
3. Design the primary screen in a plain local fixture with real-length copy.
4. Add all states before adding visual polish.
5. Implement keyboard and screen-reader behavior.
6. Add responsive rules and verify narrow overflow.
7. Add restrained motion and reduced-motion behavior.
8. Run browser screenshots, axe checks, and a fresh-eyes critique.
9. Fix measured gates first, then deliberate visual signals.
10. Record the design decision and ship a changelog entry.

---

---

# 2. UI/UX Plan: OSS Maintainer Autopilot

## User task

A maintainer needs to inspect a machine-generated proposal, understand its evidence and risk, approve or reject it, and know exactly what happened afterward.

**Primary task flow:** connect repository -> receive event -> inspect proposal -> check source evidence -> approve/reject -> confirm side effect -> inspect audit record.

## Information architecture

- **Overview:** repository health, pending proposals, recent outcomes, current provider mode, data-retention status.
- **Proposals:** queue filtered by repository, type, risk, age, and status.
- **Proposal detail:** source issue/PR, generated summary, evidence, model metadata, proposed side effect, approval controls.
- **Audit:** chronological events, actor, request hash, provider, outcome, and links.
- **Settings:** workflows, permissions, retention, provider, budgets, webhooks, deletion.
- **Help:** threat model, self-hosting, permissions, and support.

## Visual direction

Use a pale document-canvas with dark ink and restrained red accent. The memorable anchor is a vertical **decision rail** connecting `received -> analyzed -> proposed -> approved/rejected -> applied`. The rail must remain readable in the narrow layout as a horizontal progress strip.

## Screens and acceptance criteria

### A. Repository overview

Content:

- Repository selector and connection status.
- Pending proposal count with text status.
- Review-time trend only when real data exists; otherwise show “No measurements yet.”
- Recent activity table.
- Primary action: “Review proposals.”

States:

- First-run connection empty state with permission explanation.
- Loading skeleton matching table rows.
- Partial data banner when provider or GitHub data is stale.
- Error state with retry and diagnostic ID.
- Permission state for a user without maintainer access.

Acceptance:

- A maintainer reaches the pending queue in one primary click.
- No invented metrics appear when there is no data.
- Repository and provider identity are visible before any action.

### B. Proposal queue

Use a dense list/table with columns: type, repository, source, risk, status, created, next action. Filters are URL-addressable. Each row has one primary action and a clear keyboard target.

States:

- Empty: “No proposals need review.” Include a link to workflow settings.
- Search/filter no-result: preserve filters and offer clear filters.
- Loading and retrying.
- Stale proposal: visibly expired and non-actionable.
- Bulk selection is out of MVP; do not add it for visual density.

Acceptance:

- Keyboard users can filter, open a proposal, and return without losing filter state.
- Risk is conveyed by text/icon and not color alone.

### C. Proposal detail

Layout in task order:

1. Header: proposal type, repository, status, source link.
2. Summary and structured fields.
3. Evidence rail: source excerpts with line/comment citations.
4. Proposed action preview in an explicit “will change” block.
5. Risk and model metadata in a collapsible details section.
6. Sticky or bottom action bar: Approve, Reject, Edit draft, Return to queue.

Approval dialog:

- Shows exact side effect, target, actor, and expiry.
- Requires an explicit confirmation for public comments or labels.
- Disables the action during submission.
- Announces success/error without moving focus unexpectedly.

Acceptance:

- The user can identify what the model proposed, why, and what will change without leaving the screen.
- Unapproved proposals cannot expose an enabled destructive action.
- After approval, the detail shows the final GitHub result and audit link.

### D. Settings and permission review

Use sections rather than nested cards: workflows, GitHub permissions, provider mode, retention, budget, deletion. Put the permission summary beside the control that changes it.

Acceptance:

- Every external data path has a visible off switch and retention description.
- Destructive deletion requires confirmation and reports what will be deleted.

## UI verification

- Playwright flow: connect fixture repository, open queue, filter, review, reject, approve a synthetic proposal, verify status and audit event.
- Axe scan on overview, queue, proposal detail, dialog, and settings.
- Keyboard-only test with focus screenshots.
- Narrow screenshot and scroll-width gate at 390px.
- Test long issue titles, repository names, hashes, and provider errors for wrapping.

---
