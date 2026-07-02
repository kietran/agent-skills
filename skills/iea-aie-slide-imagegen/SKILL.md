---
name: iea-aie-slide-imagegen
description: "Generate or revise 16:9 raster slide images for IEA/AIE-style scientific conference presentations from a paper, outline, or template. Use when the user wants per-slide PNG/JPEG images, not a finished PPTX, with a clean academic conference template style: navy title/section slides, white content slides, large sans-serif typography, concise bullet hierarchy, clinical/AI visuals, diagrams, charts, and consistent footer branding from the provided template."
---

# IEA/AIE Slide Imagegen

## Purpose

Create complete slide images that the user can later rebuild manually as editable text and pictures in PowerPoint. Favor faithful scientific communication over decorative design.

Use `imagegen` for the final bitmap slides. Use local PDF/document tools first when the user provides papers, templates, existing slides, or exported PDFs.

## Required Workflow

1. Inspect the user's inputs.
   - If a template or prior slide deck is provided, render or screenshot representative pages.
   - If a paper is provided, extract title, research aim, methods, results, figures, limitations, and conclusion.
   - Identify paper figures, tables, equations, workflows, and result plots that should be cropped into slides; do not turn a visual paper into text-only slides.
   - If both are provided, let the template define the visual system and the paper define content.
2. Build a slide plan before image generation.
   - Use 12-18 slides for a normal conference talk unless the user specifies a number.
   - Include: title, outline, motivation, research gap, aim/contributions, method/data, model or system architecture, explainability/evaluation, key results, discussion/limitations, conclusion, Q&A.
   - Organize the talk around the research story and method flow, not a paper-outline walk-through such as Abstract -> Introduction -> Problem.
   - Keep each slide to one message. Do not overload a slide just because the paper is dense.
3. Create image prompts for each slide.
   - Request exact `16:9` landscape raster output.
   - Specify the slide type: title, outline, section divider, content with visual, diagram, result chart, two-column discussion, or thank-you.
   - Include all visible text in the prompt, but keep it concise enough for legible generation.
   - When exact labels matter, prefer simple English text and avoid small dense paragraphs inside generated images.
4. Generate slide images.
   - Save user-facing outputs under the current task's `outputs/` directory when working in a projectless thread.
   - Use stable names such as `slide-01-title.png`, `slide-02-outline.png`.
   - When revising an existing deck, write to a clean revised output folder or rebuild outputs from an explicit slide list so stale slides cannot leak into the PDF.
5. QA every generated slide visually.
   - Check text legibility, no overlaps, no clipped footer text, no invented claims, and no malformed academic terms.
   - Verify arrows align with their labels, text stays inside boxes, and body text does not overlap dark decorative shapes or footer lines.
   - Regenerate or simplify any slide with unreadable text, cramped bullets, broken charts, or visual clutter.
   - If a PDF is requested, rebuild it from the final slide images and verify the page count.

## Style Reference

Read `references/conference-template-style.md` when using this skill. It contains reusable visual rules derived from the user's sample conference slide template. Treat JYU as sample-template branding and ParkiDxAI as sample-paper content, not as the style name.

Use assets in `assets/reference-pages/` only as visual references. Do not copy logos or copyrighted imagery unless the user confirms they have rights or the assets are from their own deck.

## Content Rules

- Keep claims grounded in the provided paper. If the paper lacks a number, do not invent one.
- Prefer presenter-friendly bullets over full paragraphs.
- Use bold emphasis for important model names, metrics, or takeaways.
- For medical/AI papers, include the clinical problem, AI method, explainability/trust component, evaluation, and deployment implication.
- If using figures from the paper, preserve their meaning and simplify labels for slide legibility. Prefer cropped paper visuals for workflows, formulas, tables, and result plots when they are central evidence.
- Do not place unrelated equations or metrics together just because they appear in the paper. Explain why each metric is used before showing its result.
- For title slides, preserve the template's author formatting. Add full author names when requested, but omit affiliation numbers and contribution markers unless the user asks for them.
- If generating illustrative visuals, make them generic and non-diagnostic unless the source paper supports a specific claim.

## Layout Rules

- Use 1920x1080 or equivalent 16:9.
- Maintain wide margins and a stable footer zone.
- Never place body text in the bottom 9% of the canvas.
- Use at most 5 bullets on a normal slide; use 2 columns when content is longer.
- Use one dominant visual per slide when possible.
- Split dense evaluation material into separate explanation and result slides when needed; do not cram a full metric framework and result interpretation into one slide.
- Place takeaway text deliberately, preferably as a bottom callout spanning the content width or within an existing column. Avoid floating text blocks in odd empty space.
- Keep the final "Thank You / Questions?" as its own slide unless the user asks to combine it.
- Avoid nested cards, decorative gradients, or marketing-style hero sections.
- Avoid dark text on busy backgrounds; use a pale overlay if a photo background is used.

## Output Handoff

Return a compact list of generated slide image paths and a one-line note about any slides that need manual text cleanup after PowerPoint reconstruction.
