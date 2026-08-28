---
name: codebase-visualizer
description: Convert docs/codebase Markdown analysis into a human-first static HTML dashboard that minimizes codebase comprehension load. Use after acquire-codebase-knowledge has generated architecture, structure, stack, integrations, conventions, testing, and concerns documents. When an ELI5 or dead-simple explanation is requested, present the same evidence as a beginner-friendly HTML picture explainer with large visuals and few words. Do not merely render or summarize Markdown; reorganize the information into visual flows, state models, responsibility maps, risks, and drill-down links.
---

# Codebase Visualizer

## Goal

Reduce the amount of source code and prose a human must read before understanding the system.

The output is NOT a Markdown documentation page and NOT a prettier rendering of the source documents.

The output must let a reviewer understand, in this order:

1. What the system is
2. What the main runtime flow is
3. Which components own which responsibilities
4. Which states and transitions are important
5. Which external systems are involved
6. Where the main risks are
7. Which risks are covered by tests
8. Where to drill down for detail

When the user asks for an ELI5, dead-simple, or beginner-friendly explanation, treat the codebase as the topic. The HTML dashboard is the explanation artifact: use large visual relationships, short labels, and progressive disclosure so someone with no prior technical knowledge can understand the main idea before seeing implementation detail. This changes presentation, not the evidence standard or the required architecture information.

## Input

Read all available Markdown files under:

`docs/codebase/`

Expected files may include:

- `ARCHITECTURE.md`
- `STRUCTURE.md`
- `STACK.md`
- `INTEGRATIONS.md`
- `CONVENTIONS.md`
- `TESTING.md`
- `CONCERNS.md`

Do not assume every file exists. Use the files that are present.

Treat these documents as source material. If a claim needs validation, inspect the relevant source code.

## Beginner-friendly / ELI5 mode

Use this mode when the request explicitly asks to explain the codebase simply, like a five-year-old, or for a non-technical reader. If a topic or scope is supplied as an argument, use it to focus the explanation; otherwise explain the whole system.

- Start with one plain-language sentence about what the system does.
- Make the main runtime flow the dominant picture. Use large boxes, arrows, and concrete verbs such as “asks”, “checks”, “saves”, and “answers”.
- Keep each visible node to a short label and at most 1-2 short sentences. Put technical names, caveats, and source paths behind `<details>`, `<dialog>`, tabs, or a side panel.
- Explain unavoidable jargon the first time it appears. Use a clearly labeled analogy only to clarify a verified relationship; never use an analogy to invent behavior.
- Prefer a few large, meaningful diagrams and cards over dense tables or long prose. Keep risks and test gaps reachable from the overview without making the first viewport technical.
- Preserve all evidence-based facts, uncertainty markers, risk severities, and source-document links from the normal dashboard mode.

The result must remain a static HTML artifact that opens directly in a browser. Do not answer the ELI5 request with a prose-only summary or replace the dashboard with an ungrounded illustration.

## Required output

Create:

```text
docs/codebase/dashboard/
├── index.html
├── styles.css
├── app.js
└── codebase-map.json
```

Do not add React, Vue, Vite, npm, or another frontend framework.

Use plain HTML, CSS and JavaScript.

The existing MkDocs documentation remains the detailed documentation layer. The dashboard is the fast comprehension layer.

## Required information architecture

### 1. First viewport: System at a glance

The first screen must fit on a normal desktop viewport without requiring the user to read long paragraphs.

Show:

- system purpose: maximum 2 short sentences
- main runtime path
- 4-8 major components
- external services
- count of high-risk items
- count of known test gaps

Avoid prose blocks.

### 2. Main runtime flow

Create a visual flow using boxes and arrows.

For the AI interviewer project, prefer the actual discovered flow. Do not hard-code a flow if the source documents say something different.

Distinguish:

- normal path
- asynchronous path
- retry/fallback path
- cancellation/barge-in path
- persistence/state updates

Each node must expose:

- responsibility
- inputs
- outputs
- related source file/module when available

Use progressive disclosure: show details only when a node is clicked.

### 3. Component responsibility map

Create cards or a matrix for major components.

Each component must show:

- responsibility
- what it owns
- what it must not own
- dependencies
- relevant code location

Do not copy long Markdown passages.

### 4. State model

If state machines or lifecycle states exist, visualize them.

Show:

- state
- allowed next states
- trigger
- important guard/condition
- rollback/cancel behavior

If multiple state machines exist, separate them.

### 5. Integrations

Group external systems by role:

- authentication
- AI/model
- speech
- storage/search
- queue/background processing
- observability

For each integration show:

- purpose
- request direction
- retry/failure behavior
- relevant configuration or source location

### 6. Risks and test coverage

Merge information from `CONCERNS.md` and `TESTING.md`.

Create a matrix:

| Risk | Severity | Failure mode | Test coverage | Gap | Related code |

Do not fabricate severity. If severity is not present, infer only when the evidence is strong and label it `inferred`.

Prioritize items that can cause:

- data inconsistency
- incorrect state transition
- lost user input
- duplicate processing
- authorization failure
- output interruption
- unrecoverable external service failure

### 7. Code map

Show the important directory/module hierarchy only.

Do not dump the entire repository tree.

For each important path show why a reviewer would open it.

### 8. Drill-down

Link to existing detailed Markdown documents.

If MkDocs URLs cannot be known reliably, link using relative `.md` paths and label them as source documents.

The dashboard should not duplicate full detail.

## Visual requirements

Read `references/visual-rules.md`.

In ELI5 mode, apply the same visual rules with a stronger beginner-friendly bias: make pictures and directional relationships large enough to read at a glance, reduce visible words, and keep secondary detail expandable. Visuals must still encode real architecture rather than serve as decoration.

## Data model

Before writing the UI, create `codebase-map.json`.

It must contain structured sections similar to:

```json
{
  "system": {},
  "components": [],
  "flows": [],
  "states": [],
  "integrations": [],
  "risks": [],
  "tests": [],
  "codeMap": [],
  "sources": []
}
```

Generate the UI from this model.

The JSON is a reviewable intermediate representation and must not contain invented facts.

## Implementation rules

- No framework.
- No build step.
- `index.html` must open directly in a browser.
- Avoid CDN dependencies where possible.
- Prefer CSS-based diagrams and SVG generated inline.
- If Mermaid is used, provide a graceful textual fallback.
- Use semantic HTML.
- Support desktop widths from 1280px upward.
- Use responsive behavior for smaller screens.
- No long page-width paragraphs.
- For ELI5 mode, make the HTML artifact usable as a picture-first explanation: large visual elements, short labels, and no dense technical text in the first viewport.
- Details should use dialog, disclosure, tabs, or side panels.
- Use color as a secondary signal, never the only signal.
- Keep the visual hierarchy strong and restrained.

## Validation

After generation run:

```bash
python .github/skills/codebase-visualizer/scripts/validate_dashboard.py \
  docs/codebase/dashboard
```

Fix every validation error.

Then start a local server:

```bash
python -m http.server 8765 --directory docs/codebase/dashboard
```

If browser tooling is available, inspect the page at `http://127.0.0.1:8765`.

Check at minimum:

- first viewport is useful without scrolling
- no raw Markdown dump is visible
- system flow is visually understandable
- risks and tests are correlated
- details can be expanded without leaving the overview
- all local asset links resolve
- no console error

## Done criteria

The task is complete only when all of these are true:

- `codebase-map.json` exists and is evidence-based
- `index.html` exists
- `styles.css` exists
- `app.js` exists
- validator passes
- the dashboard is visually structured, not a prose document
- existing Markdown files were not replaced
- user can reach detailed source documents from the dashboard
- when ELI5 mode was requested, a non-technical reader can understand the main system flow from the first viewport without prior codebase knowledge
