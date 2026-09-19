# Image assets the README expects

`hero.svg` exists and is what the README shows: a hand-written SVG (1760x640
viewBox) rather than a generated bitmap, so its labels are exact and it stays
sharp at any width. Every number on it comes from `docs/measurements.md`; change
the two together. `social-preview.png` is set in the repository Settings and is
not referenced from Markdown.

| File | Size | Where it is used | What it shows |
|---|---|---|---|
| `hero.png` | 1760x640 px (displayed at 880 px wide, 2x for retina), PNG with a transparent or near-black background | The centred header block in `README.md`, currently inside an HTML comment | A single tool call travelling left to right through the guard: the call enters, a code pre-filter drops most of the traffic out of the bottom as "free", one call carries on to a typed Jev judgement with a confidence bar, and four outcomes leave the right edge (allow, warn, block, rewrite). The wordmark `jev-kit` sits top-left. |
| `social-preview.png` | 1280x640 px, PNG, no transparency (GitHub flattens it) | Repository Settings, Social preview. Not referenced from any Markdown | The wordmark `jev-kit` with the short promise underneath, on the same palette as the hero. Text must stay legible as a 640x320 thumbnail in a chat unfurl, so no body copy and no diagram detail. |

## Rules for both

- **Rendered text is the name and at most five words.** Every other label in
  the hero is a shape or an icon, not a word. Misspelled generated text is the
  usual failure, and fewer words is fewer chances.
- No logos of Anthropic, Claude, TypeSafe, GitHub or any other third party.
- No photographic faces, no stock-photo people, no keyboard-and-hoodie imagery.
- Legible at 50% scale and in dark mode. GitHub renders the README on both
  light and dark backgrounds, so avoid anything that depends on a white page.
- Keep the file under 500 KB. Run it through `oxipng` or `pngquant` before
  committing.

The ready-to-paste generation prompts for both images are kept with the task
report rather than here, so this file stays a specification of what is needed
rather than a record of how one attempt was made.
