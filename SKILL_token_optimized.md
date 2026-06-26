# SKILL: Transcription Curator (Token-Optimized)

Task: convert raw note → JSON

Categories: idea|task|project-note|reflection|reminder
Projects: dalgas-crm|euc-syd-intranet|horning-floor|personal|ai-experiments|null

Output:
{"entries":[{"summary":"","category":"","project":null,"actions":[],"keywords":[],"confidence":0.0}]}

Rules:
- split if multi-idea
- remove filler
- compress text
- task if action
- project only if clear
- actions only if explicit
- keywords=3-5
- confidence 0-1

Strict:
- JSON only
- no extra text
- valid JSON
