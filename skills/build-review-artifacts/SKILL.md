---
name: build-review-artifacts
description: Build polished, source-backed HTML artifacts from plans, technical reports, experimental results, or evidence when the user wants something durable to share, hand off, or send for review. Do not use for ordinary internal plans, quick chat summaries, or routine code changes that do not need a review artifact.
---

# Build Review Artifacts

Turn repository truth into a review-ready artifact whose claims, visuals, and
sources can be inspected. Preserve the user's requested language, scope, format,
and comparison policy.

## Route the request

- For a report-style HTML artifact, use the available
  `data-analytics:build-report` workflow and its canonical Data App contract.
- When quantitative visuals materially improve the review, also use
  `data-analytics:visualize-data`.
- For a plan being sent for review, keep the canonical plan file as authority
  and render a separate artifact containing objectives, decisions, phases,
  dependencies, risks, acceptance criteria, and evidence links.
- Read [references/review-artifact-pattern.md](references/review-artifact-pattern.md)
  before creating any HTML report, visual review packet, or rendered plan.

## Workflow

1. Identify the reviewer, decision, requested language, and exact deliverables.
   Ask only when different answers would materially change the artifact.
2. Inventory authoritative sources. Prefer audited reports, frozen outputs,
   repository plans, implementation code, and primary publications. Never
   fabricate missing metrics or silently mix incompatible protocols.
3. Reconcile the headline numbers and claim boundaries before designing.
   Keep clean, leaky, exploratory, external, and modeled evidence visibly
   distinct wherever the distinction changes interpretation.
4. Choose the smallest useful composition. Use prose for the argument, tables
   for exact lookup, charts for comparison or shape, and a custom accessible SVG
   for architecture or workflow relationships.
5. Build from the canonical Data App report template rather than hand-writing a
   standalone page. Keep reviewed rows and component-scoped provenance in
   `src/data.json`; keep narrative and visuals in the authored content boundary.
6. Produce an editable local report and a sanitized self-contained HTML for
   sharing. Preserve the original source artifact and create a sibling version
   for another language or audience.
7. Validate the claims against their sources, build with the standard helper,
   verify assets and links, remove local thread metadata from the shareable
   copy, set the correct document language, and run repository-required checks.

## Default handoff

Lead with the completed artifact, then list only the files a reviewer needs:

- sanitized `dist/shareable.html`;
- editable source report and reviewed `src/data.json`;
- standalone SVG figures when reuse in a paper or slide is likely; and
- a short README describing scope, headline evidence, and caveats.

Report what was validated and any remaining claim limitation. Do not claim
browser QA, publication, sending, or reviewer approval unless those actions were
explicitly authorized and actually completed.

## Boundaries

- Do not publish, email, upload, commit, or widen access without explicit user
  authorization.
- Do not replace a requested PDF, slide deck, document, or plain Markdown
  deliverable with HTML; use HTML only when requested or useful alongside it.
- Do not add decorative charts, generic executive summaries, or fixed section
  quotas. Every visual and section must answer a real review question.
- Do not weaken visible caveats to make a result look stronger.
