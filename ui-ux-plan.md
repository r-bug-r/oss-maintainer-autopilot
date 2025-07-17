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

