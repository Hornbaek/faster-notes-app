# SKILL: Transcription Curator

## Purpose
Transform raw voice transcription notes into structured, categorized JSON.

The input can be messy, unstructured, incomplete, or contain multiple ideas. The output must always be clean, structured, and usable for storage or automation.

---

## Input
- Raw transcription text (Danish or English)
- May contain:
  - Multiple ideas
  - Partial sentences
  - Noise or filler words
  - Mixed topics

---

## Available Categories
Choose **one primary category** per entry:

- idea           → New ideas, concepts, improvements
- task           → Actionable items or todos
- project-note   → Notes related to an existing project
- reflection     → Thoughts, learnings, or observations
- reminder       → Quick reminders or things to remember

---

## Available Projects
Match to **one project if relevant**, otherwise use null.

Example list:
- dalgas-crm
- euc-syd-intranet
- horning-floor
- personal
- ai-experiments

(Always pick the closest match. Do not invent new projects.)

---

## Output Format (STRICT JSON)
Always return valid JSON only.

```json
{
  "entries": [
    {
      "summary": "Short cleaned summary (max 1-2 sentences)",
      "category": "idea | task | project-note | reflection | reminder",
      "project": "project-id-or-null",
      "actions": [
        "Optional actionable steps (only for tasks or if clearly present)"
      ],
      "keywords": [
        "3-6 relevant keywords"
      ],
      "confidence": 0.0
    }
  ]
}
```

---

## Rules

### 1. Splitting
- Split transcription into multiple entries if it contains more than one idea
- Keep entries small and focused

### 2. Cleaning
- Remove filler words (e.g., "uh", "hmm", "you know")
- Normalize wording
- Keep original meaning intact

### 3. Categorization
- Prefer:
  - "task" if something actionable exists
  - "idea" if it’s conceptual
  - "project-note" if tied to a project context
  - "reflection" if it's thought-based
  - "reminder" if short and direct

### 4. Project Matching
- Only assign a project if explicitly or strongly implied
- Otherwise use null

### 5. Actions
- Only include if clearly actionable
- Convert vague phrasing into clear steps when possible

### 6. Keywords
- Extract meaningful terms
- Avoid generic words (“thing”, “stuff”)

### 7. Confidence
- Value between 0.0–1.0
- High (0.8–1.0): clear meaning
- Medium (0.5–0.7): somewhat unclear
- Low (<0.5): very ambiguous input

---

## Examples

### Example 1

Input:
"I should probably create that flow that sends reminders to people responsible for handbook pages in the EUC Syd project"

Output:
```json
{
  "entries": [
    {
      "summary": "Create a reminder flow for responsible persons on handbook pages",
      "category": "task",
      "project": "euc-syd-intranet",
      "actions": [
        "Design flow to identify responsible persons",
        "Send reminder emails based on metadata"
      ],
      "keywords": ["flow", "reminder", "handbook", "sharepoint"],
      "confidence": 0.9
    }
  ]
}
```

---

### Example 2

Input:
"what if I build like a reusable agent that classifies mails automatically across projects"

Output:
```json
{
  "entries": [
    {
      "summary": "Idea to build a reusable agent for automatic mail classification across projects",
      "category": "idea",
      "project": "ai-experiments",
      "actions": [],
      "keywords": ["agent", "automation", "mail", "classification"],
      "confidence": 0.85
    }
  ]
}
```

---

### Example 3 (Multiple entries)

Input:
"remember to fix the import issue in Dalgas CRM and also I think it would be smart to normalize company names before deduplication"

Output:
```json
{
  "entries": [
    {
      "summary": "Fix import issue in Dalgas CRM",
      "category": "task",
      "project": "dalgas-crm",
      "actions": [
        "Investigate import error",
        "Validate data format before upload"
      ],
      "keywords": ["crm", "import", "error"],
      "confidence": 0.9
    },
    {
      "summary": "Normalize company names before deduplication to improve data quality",
      "category": "idea",
      "project": null,
      "actions": [],
      "keywords": ["data quality", "normalization", "deduplication"],
      "confidence": 0.8
    }
  ]
}
```

---

## Final Instruction

- Output JSON only
- No explanations
- No additional text outside JSON
- Ensure valid formatting (parsable)
