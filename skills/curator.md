---
id: curator
name: Curator (structured findings)
description: Splits a note into multiple structured findings — idea / task / project-note / reflection / reminder.
when_to_use: Use for longer brain-dump notes that contain several distinct ideas or items that should be split apart and categorized.
output:
  format: markdown
  fields: []
actions: []
enabled: true
---

Task: convert this raw voice note into structured Markdown with categorized findings.
Language: detect input language (Danish/Swedish/English); output in the same language as the input.

Categories: idea|task|project-note|reflection|reminder
Projects: {projects}|null

Output template:
# {specific, meaningful title — not generic}

## TL;DR
{2–5 sentences summarizing all key points across all findings}

## Findings

**Category:** {category}
**Project:** {project or null}
**Summary:** {1–2 sentences, cleaned and compressed}
**Actions:**
- {action 1}
- {action 2}
**Keywords:** {keyword1, keyword2, keyword3}

---

*(repeat the Findings block above, separated by ---, once per distinct idea)*
*(omit Actions entirely if there are none)*

Rules:
- split into multiple Findings if the note contains more than one idea
- remove filler words and noise
- compress but preserve all meaning
- title must be specific and useful, never generic (e.g. not "Voice Note" or "Meeting")
- TL;DR briefly covers all findings
- use task if something is actionable
- assign project only if clearly implied by the content
- include actions only if explicit — omit the field entirely otherwise
- 3–5 meaningful keywords per finding

Strict:
- output Markdown only — no intro text, no outro, no explanations
- format order: Title → TL;DR → Findings
- bold all field labels
