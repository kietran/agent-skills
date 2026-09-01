# Review Artifact Pattern

Use this reference for report-style HTML, visual evidence packets, and plans
rendered for human review. Adapt the composition to the request; the sections
below are a decision guide, not a mandatory outline.

## Default artifact shape

1. **Answer first**
   - Name the artifact and strongest supported takeaway.
   - Put the material evidence scope beside the headline: clean, validation,
     leaky, exploratory, external, modeled, or incomplete.
2. **System, method, or plan structure**
   - Use an accessible custom SVG when one source or component affects at least
     three downstream branches, or when architecture is difficult to explain
     linearly.
   - Label dimensions, state, ownership, frozen/trainable boundaries, and the
     final output. Offer a standalone SVG when the figure may be reused.
3. **Exact evidence**
   - Use a table for benchmark values, metrics, costs, stage scores, decisions,
     plan phases, or acceptance criteria.
   - Keep external values marked as quoted rather than rerun. Put profiler,
     cohort, split, and preprocessing mismatches beside the comparison.
4. **Mechanism or reasoning evidence**
   - Use a small number of charts for relationships such as gain, attribution,
     deletion effects, uncertainty, transition behavior, or plan progress.
   - Pair each chart with one concise interpretation and its claim boundary.
5. **Questions, contributions, or reviewer decisions**
   - Show research questions, contributions, unresolved decisions, risks, or
     requested reviewer feedback as short source-backed cards or a compact
     table.
6. **Claim boundary and sources**
   - State what the artifact does not prove.
   - Preserve component-scoped source inspection and a compact handoff README.

## Visual choice

- Architecture, data flow, state, or ownership: custom SVG.
- Exact multi-metric comparison: table.
- Category ranking or model comparison: horizontal bar or ranked list.
- Paired or multi-series metric comparison: grouped bar.
- Decomposition: stacked bars only when terms are genuinely additive.
- Uncertainty: intervals or explicit CI columns, not unsupported error bars.
- Sequence of plan phases: ordered stage table or timeline when dependencies
  matter; otherwise prose is clearer.

Use the canonical Data App primitives for report charts and tables. Use a
technical `scientific-blue` theme by default for research or engineering
artifacts and `codex-classic` for general review reports unless the user chooses
another style.

## Suggested directory contract

```text
<artifact-root>/
├── README.md
├── figures/                  # optional standalone SVGs
├── src/
│   ├── data.json             # reviewed rows, definitions, provenance
│   └── content/report/       # authored narrative and visuals
└── dist/
    ├── index.html            # editable local build
    └── shareable.html        # sanitized self-contained handoff
```

For another language or substantially different audience, create a sibling
artifact with a new stable ID. Preserve numeric rows and source identities;
translate narrative, labels, alt text, definitions, caveats, and document
language. Do not overwrite the original version.

## Validation checklist

- The artifact answers the review question and contains every requested section.
- Headline metrics independently reconcile with the authoritative sources.
- Metric definitions, populations, units, and comparison protocols are visible.
- Every source-backed component points to the correct reviewed query and rows.
- Custom SVGs have a title, description, readable labels, and valid markup.
- Referenced assets exist and are embedded in the built HTML.
- The standard Data App build passes.
- The shareable HTML is self-contained, uses the intended `lang`, and contains
  no local thread/session metadata.
- The original artifact remains unchanged.
- Repository checks such as `git diff --check` pass.
- Final handoff lists artifact paths, validation performed, and unresolved risks.
