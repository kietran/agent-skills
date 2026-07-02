# Academic Conference Template Style

Derived from the user's sample exported conference slide deck. Match the layout and visual feeling, not the exact original. JYU is only the institution branding in the sample template; ParkiDxAI is only the sample paper title/content.

## Canvas

- 16:9 landscape.
- Reference deck export: 960 x 540 pt, rendered cleanly at 2001 x 1125 px.
- Best generation target: 1920 x 1080 PNG.

## Palette

- Primary navy: deep university blue, approximately `#002F5F` to `#003864`.
- Text: black `#000000` on white or very light gray.
- Accent coral/red-orange: approximately `#F15A40`, used sparingly for bullets, underlines, and small emphasis rules.
- Secondary tan/gold and cool gray appear only as small footer/title accents.
- Content slide background: off-white/light gray, approximately `#F2F2F2` or clean white.

## Typography

- Use a modern sans-serif for body and most headings: Arial, Aptos, Helvetica, or similar.
- Title slide headline: very large, bold, centered, white.
- Content slide title: large bold, navy or black, top-left aligned.
- Section divider title: large bold navy over a pale campus/photo background.
- Body bullets: large, black, high line spacing.
- Use bold only for key phrases such as model names, metrics, clinical outcomes, or final takeaways.

## Core Layout Types

### Title Slide

- Full navy background.
- Centered white academic logo/mark area near upper center when available.
- Main title centered horizontally, placed slightly below center.
- Authors and affiliation centered near lower third.
- Thin segmented footer strip at the bottom: coral, tan/gold, gray.

### Outline and Section Divider

- Light, washed-out campus/photo background.
- Wide vertical navy band on the right third.
- White institution, conference, or template brand text centered inside the band when the provided template uses it.
- Short coral underline inside the navy band.
- Big navy heading on the left.
- Outline uses large numbered sections with indented bullet subtopics.

### Standard Content Slide

- White or very pale gray background.
- Narrow navy block with white academic mark at top-left.
- Large title across the top.
- Main content begins below the title with generous margins.
- Footer at bottom-right: template-specific institution/conference text, date, and slide number.
- Thin navy bar along the bottom.

### Content With Visual

- Left column: short subtitle plus 3-5 bullets.
- Right column: one large illustration, chart, screenshot, or conceptual diagram.
- Keep visuals flat, clean, and explanatory. Clinical and AI visuals should look academic, not marketing-like.

### Diagram Slide

- Use simple white boxes, black arrows, and sparse labels.
- Prefer a 2x2 or left-to-right pipeline:
  patient input -> model -> explainability -> dashboard/report.
- Keep labels large. Do not use tiny internal chart labels.

### Two-Column Discussion Slide

- Use two balanced columns with navy subheadings.
- Use coral bullets and short dash sub-bullets.
- Keep the title large and leave extra space above the columns.

### Thank-You Slide

- Minimal.
- White or navy background.
- Large `Thank You. Questions?` plus presenter name, email, and affiliation.

## Visual Language

- Use academic restraint: clean, spacious, direct.
- Prefer simple medical/AI illustrations, system diagrams, SHAP/LIME-inspired charts, and UI screenshots.
- Keep generated images sharp and uncluttered.
- Avoid cartoon-heavy elements unless the source slide already uses a simple cartoon for a clinical symptom.
- Avoid dark neon, glossy 3D, excessive gradients, rounded decorative cards, and stock-photo hero compositions.

## Known Source-Deck Issues To Improve

- Some long content slides can clip into the footer. Avoid this by reducing bullets, shrinking body text slightly, or using a two-column layout.
- Dense explainability screenshots are hard to read. Replace them with simplified chart-like visuals unless exact screenshots are required.
- Do not rely on text extraction alone for layout decisions; visually inspect rendered pages.

## Prompt Skeleton

Use this skeleton when generating a slide image:

```text
Create a polished 16:9 academic conference slide image, 1920x1080, inspired by the provided academic template style.
Slide type: [title/outline/section/content/diagram/results/discussion/thank-you].
Visual style: clean university presentation, deep navy accents, white or very pale gray background, large sans-serif typography, coral accent bullets, stable bottom footer area, no decorative gradients.
Layout: [describe columns, visual position, footer, title placement].
Visible text:
[exact text, concise]
Visual content:
[diagram/chart/illustration description]
Quality requirements: all text must be legible, no overlap, no clipped footer, no invented data, professional scientific tone.
```
