# Visual Rules

## Purpose

The dashboard is a comprehension tool, not a documentation theme.

A reviewer should get the system model before reading implementation detail.

## Layout

Use a persistent left navigation or compact top navigation with these sections:

- Overview
- Runtime Flow
- Components
- States
- Integrations
- Risks & Tests
- Code Map

The first viewport should contain:

- title + concise purpose
- primary runtime flow
- small KPI/status cards

## Typography

- Prefer short labels.
- Maximum paragraph length: about 3 lines.
- Prefer bullets with 3-5 items.
- Avoid documentation-style walls of text.

## Cards

A component card should answer:

- What is this?
- Why does it exist?
- What comes in?
- What goes out?
- Where is the code?

Do not show all details until expanded.

## Flows

Use directional flow diagrams.

Normal path should dominate visually.
Retries, error paths and cancellations should be secondary branches.

Do not create decorative diagrams that do not encode architecture.

## Risks

Severity must be visible as text, not only color:

- CRITICAL
- HIGH
- MEDIUM
- LOW
- INFERRED

A risk card without a failure mode is incomplete.

## Tests

Always connect a test to a behavior/risk where possible.

Prefer:

`Risk -> Expected behavior -> Existing test -> Gap`

over an isolated list of test files.

## Code map

Show only paths with architecture significance.

Good:

`voice/output_coordinator.py — serializes and prioritizes audio output`

Bad:

`utils.py`

unless the reason for opening `utils.py` is explained.

## Interaction

Use native HTML elements where possible:

- `<details>`
- `<dialog>`
- buttons
- anchors

Avoid interaction for its own sake.
