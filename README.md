# Provenance Guard

A backend API that classifies submitted creative text as likely AI-generated, likely human-written, or uncertain — and surfaces a transparency label any creative platform can display to audiences. Creators who disagree with a classification can file an appeal that is logged for human review.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Detection Signals](#detection-signals)
3. [Confidence Scoring](#confidence-scoring)
4. [Transparency Labels](#transparency-labels)
5. [Appeals Workflow](#appeals-workflow)
6. [Rate Limiting](#rate-limiting)
7. [Audit Log](#audit-log)
8. [API Reference](#api-reference)
9. [Running Locally](#running-locally)
10. [Known Limitations](#known-limitations)
11. [Spec Reflection](#spec-reflection)
12. [AI Usage](#ai-usage)

---

## Architecture

### Submission Flow

```
  Creator / Platform Client
         |
         | POST /api/submit  { text, creator_id }
         v
  +--------------------+
  |  Input Validator   |  rejects: <20 chars, >50,000 chars, missing fields
  +--------------------+
         |
         v
  +-------------------------+     +---------------------------+
  |  Signal 1: Groq LLM    |     |  Signal 2: Stylometrics   |
  |  llama-3.3-70b-versatile|     |  burstiness · TTR         |
  |  → llm_score (float)   |     |  hedge density · punct.   |
  +------------------------+     |  entropy → heuristic_score|
         |                       +---------------------------+
         +-------------+-----------------+
                       |
                       v
              +------------------+
              |  Score Aggregator |
              |  0.65×LLM +      |
              |  0.35×heuristic  |
              |  → final_score   |
              +------------------+
                       |
                       v
              +------------------+
              |  Threshold Gate  |
              |  <0.28 → HUMAN   |
              |  0.28–0.72 → UNC |
              |  >0.72 → AI      |
              +------------------+
                       |
                       v
              +------------------+
              |  In-Memory Store |  keyed by UUID (content_id)
              |  + audit.log     |  JSON Lines, append-only
              +------------------+
                       |
                       v
              JSON response including content_id, label, label_text,
              final_score, llm_score, heuristic_score
```

### Appeal Flow

```
  Creator
     |
     | POST /api/appeal  { content_id, creator_reasoning }
     v
  +---------------------+
  |  Lookup + Validate  |  404 if unknown, 409 if duplicate
  +---------------------+
            |
            v
  +---------------------+
  |  Update status      |  "classified" → "under_review"
  |  Store appeal record|
  |  Write audit entry  |  event: "appeal", appeal_reasoning logged
  +---------------------+
            |
            v
  JSON response { appeal_id, status: "received", original_label }

  GET /api/appeals/queue  →  human reviewer sees all pending appeals
                              with 300-char text preview + creator statement
```

Text enters at `POST /api/submit`, passes input validation, then runs through both detection signals. The two scores are weighted and combined into a single `final_score` that is mapped to a label and stored with the full signal breakdown. On appeal, the submission status is updated and a structured audit entry is written immediately — no re-scoring happens automatically, since the goal is to route the case to a human reviewer rather than make a second automated decision.

---

## Detection Signals

### Signal 1 — Groq LLM Classifier (`llm_score`)

**Why this signal:** A large language model has internalized what AI-generated text looks like from exposure to enormous amounts of both. It can detect semantic-level patterns — generic phrasing, tonal uniformity, the absence of personal idiosyncrasy — that no set of surface statistics can reliably capture. It is the strongest single signal available without ground-truth training data.

**How it works:** The text is sent to `llama-3.3-70b-versatile` via Groq API with a system prompt that instructs it to return a JSON object `{"ai_probability": float, "reasoning": string}`. Temperature is set to 0.1 to reduce variance. The system prompt explicitly tells the model to ignore any instructions embedded in the submitted text (prompt injection defense).

**Output:** A float in `[0.0, 1.0]`. Falls back to `0.5` (neutral) on any parse error or API failure — the fallback is logged so it can be monitored.

**Weakness:** The LLM is non-deterministic; the same text may score slightly differently across calls. It also applies a literary frame to all text — formal human writing (academic prose, legal narrative) can score high because it looks like AI to a model trained on AI output patterns.

---

### Signal 2 — Stylometric Heuristics (`heuristic_score`)

**Why this signal:** Heuristics run locally, require no API call, and cannot be influenced by prompt injection in the submission text. They provide an independent grounding check on the LLM — when the two signals agree, confidence rises; when they disagree, the system correctly hedges toward UNCERTAIN rather than making a high-confidence error.

**Four sub-features (each normalized to [0, 1] where 1.0 = AI-like):**

| Feature | What it measures | AI signature |
|---|---|---|
| **Sentence Burstiness** | `std / mean` of sentence length distribution | AI produces uniform lengths (low CV); human writing is irregular |
| **Lexical Diversity (TTR)** | `unique tokens / total tokens` | AI clusters at a predictable mid-range TTR (0.5–0.75); very low or very high TTR suggests human authorship |
| **Hedge Phrase Density** | Matches against a list of ~35 phrases per 100 words | AI overuses transitions: "it is worth noting", "furthermore", "this highlights", "transformative", "stakeholders" |
| **Punctuation Entropy** | Shannon entropy of punctuation character types | AI uses orderly punctuation (mostly periods and commas); human writing uses dashes, ellipses, parentheses erratically |

The four sub-scores are averaged into a single `heuristic_score`.

**Weakness:** The heuristic signal penalizes _stylistic simplicity_, regardless of cause. Non-native English speakers writing in short declarative sentences, poets using anaphora (deliberate line repetition), and children's story writers all produce texts that score AI-like on burstiness and TTR. The hedge phrase density sub-feature is the most reliable individual signal.

---

## Confidence Scoring

### Formula

```
final_score = (0.65 × llm_score) + (0.35 × heuristic_score)
```

The LLM receives higher weight (65%) because it captures meaning-level patterns. The heuristic signal (35%) provides an independent, locally-computed check. When both signals agree, the final score lands decisively in one zone. When they disagree, the weighted average tends to fall in the UNCERTAIN band — which is the correct outcome for ambiguous text.

### Threshold Map

```
0.00 – 0.28  →  LIKELY_HUMAN   (confidence: high)
0.28 – 0.45  →  UNCERTAIN      (confidence: medium, leans human)
0.45 – 0.55  →  UNCERTAIN      (confidence: low)
0.55 – 0.72  →  UNCERTAIN      (confidence: medium, leans AI)
0.72 – 1.00  →  LIKELY_AI      (confidence: high)
```

The thresholds are set at 0.28/0.72 rather than a symmetric 0.33/0.67. The wider uncertain band reflects a deliberate design choice: the cost of falsely labeling a human creator's work as AI-generated is higher than the cost of under-labeling genuinely AI content. The system errs toward uncertainty.

### Two Real Examples

**High-confidence AI** — formal AI-style paragraph about technology and ethics:
```json
{
  "text": "Artificial intelligence represents a transformative paradigm shift...",
  "llm_score": 0.9,
  "heuristic_score": 0.716,
  "final_score": 0.836,
  "label": "LIKELY_AI",
  "confidence_level": "high"
}
```
Both signals agree strongly. Hedge density fired at 1.0 (matches: "transformative", "paradigm shift", "it is important to note", "furthermore", "essential", "stakeholders"). LLM recognized generic transitions and tonal flatness.

**High-confidence human** — casual first-person restaurant review with dialect:
```json
{
  "text": "ok so i finally tried that new ramen place downtown and honestly?...",
  "llm_score": 0.0,
  "heuristic_score": 0.471,
  "final_score": 0.165,
  "label": "LIKELY_HUMAN",
  "confidence_level": "high"
}
```
LLM scored 0.0 — strong vernacular voice, informal capitalization, no transitions. Heuristic was moderate (0.471) because the TTR is in the mid-range and burstiness is imperfect on short casual texts, but the LLM signal dominates with its 65% weight, pulling the final score well below 0.28.

**What would change for production:** The weights (0.65/0.35) and thresholds (0.28/0.72) were set by hand-testing rather than calibrated against a labeled dataset. In a real deployment, I would collect ground-truth labeled examples and fit the weights using logistic regression or isotonic regression to produce scores that are properly calibrated probabilities rather than heuristic ordinal indicators.

---

## Transparency Labels

The label displayed to audiences changes based on the `final_score`. Three variants:

---

**LIKELY_AI** (`final_score ≥ 0.72`)

> **Likely AI-Generated** — Our automated analysis suggests this work was probably produced with an AI writing tool. This determination is based on stylistic patterns and is not guaranteed to be accurate. If you are the creator and believe this label is wrong, you can submit an appeal — we review all appeals within 48 hours.

*Design notes:* "Probably" rather than "was" — preserves epistemic honesty. Appeal link is surfaced immediately. No language about intent or deception.

---

**LIKELY_HUMAN** (`final_score ≤ 0.28`)

> **Likely Human-Created** — Our automated analysis suggests this work was probably written by a person. Automated detection is imperfect and this label may not be correct in all cases.

*Design notes:* Positive label kept shorter — no recourse needed when the label is in the creator's favor. Still acknowledges fallibility symmetrically.

---

**UNCERTAIN** (`0.28 < final_score < 0.72`)

> **Origin Uncertain** — Our system was not able to determine with confidence whether this work is human-authored, AI-generated, or a combination of both. This may reflect a collaborative creative process, a distinctive personal style, or a content type our tools handle less accurately. If you are the creator, you can add context or appeal this classification.

*Design notes:* Explicitly names AI-human collaboration as a legitimate outcome rather than treating uncertainty as a failure state. Invites creator input rather than leaving them with an ambiguous label and no path forward.

---

## Appeals Workflow

**Who can appeal:** Any creator who submitted content and has the `content_id` from their submission response. The appeal window is open immediately after submission (no time limit enforced in the current implementation — a 48-hour window is the stated policy).

**What they provide:**
```bash
curl -X POST http://localhost:5000/api/appeal \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "2cb829fa-c84e-43fe-ae2a-4b43f1571519",
    "creator_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."
  }'
```

**Response:**
```json
{
  "appeal_id": "be52a9f6-84df-4059-9ca9-f4794fd4457b",
  "content_id": "2cb829fa-c84e-43fe-ae2a-4b43f1571519",
  "status": "received",
  "message": "Your appeal has been received. The content status has been updated to 'under review'. We aim to respond within 48 hours.",
  "original_label": "LIKELY_AI"
}
```

**What happens internally:**
1. The submission's `status` field is updated from `"classified"` to `"under_review"` in the in-memory store
2. An appeal record is created and stored, keyed by `appeal_id`
3. A structured audit entry is written with `event: "appeal"`, the full `creator_reasoning`, both original signal scores, and `appeal_filed: true`
4. Duplicate appeals return 409; unknown content IDs return 404

**What a human reviewer sees** (`GET /api/appeals/queue`):
```json
{
  "count": 1,
  "appeals": [{
    "appeal_id": "be52a9f6-...",
    "content_id": "2cb829fa-...",
    "creator_id": "label-test-ai",
    "creator_reasoning": "I wrote this myself from personal experience...",
    "original_label": "LIKELY_AI",
    "original_score": 0.836,
    "original_text_preview": "Artificial intelligence represents a transformative paradigm shift...",
    "status": "pending",
    "created_at": "2026-07-01T04:45:19Z"
  }]
}
```

---

## Rate Limiting

Applied via Flask-Limiter to the submission endpoint:

```
10 requests per minute
100 requests per day
```

**Reasoning:** A legitimate writer submitting their own work in a creative session rarely needs more than a handful of submissions per minute — even someone iterating on revisions is unlikely to exceed 10. The per-day limit of 100 allows a productive session while ensuring a single bad actor cannot run a scraping loop at scale. The appeal endpoint is separately limited to 3 per minute.

**Evidence — rate limit fires at request 11:**

```
201
201
201
201
201
201
201
201
201
201
429
429
```

(12 rapid requests sent; first 10 succeed, requests 11 and 12 return 429 Too Many Requests)

---

## Audit Log

Every submission and every appeal writes a structured JSON Lines entry to `audit.log`. The log is append-only and persists across server restarts.

**Submission entry fields:**

| Field | Description |
|---|---|
| `timestamp` | ISO 8601 UTC timestamp |
| `event` | `"submission"` or `"appeal"` |
| `content_id` | UUID matching the API response |
| `creator_id` | Submitting creator identifier |
| `attribution` | `"likely_ai"` / `"likely_human"` / `"uncertain"` |
| `confidence` | Same as `final_score` |
| `final_score` | Weighted combined score (0.0–1.0) |
| `llm_score` | Raw Groq LLM signal score |
| `heuristic_score` | Raw stylometric signal score |
| `status` | `"classified"` or `"under_review"` |
| `appeal_filed` | Boolean |
| `appeal_reasoning` | Creator's statement (only on appeal entries) |

**Example submission entry:**
```json
{
  "event": "submission",
  "content_id": "2cb829fa-c84e-43fe-ae2a-4b43f1571519",
  "creator_id": "label-test-ai",
  "attribution": "likely_ai",
  "confidence": 0.8357,
  "final_score": 0.8357,
  "llm_score": 0.9,
  "heuristic_score": 0.7162,
  "status": "classified",
  "appeal_filed": false,
  "appeal_reasoning": null,
  "timestamp": "2026-07-01T04:44:29.123Z"
}
```

**Example appeal entry:**
```json
{
  "event": "appeal",
  "content_id": "2cb829fa-c84e-43fe-ae2a-4b43f1571519",
  "creator_id": "label-test-ai",
  "attribution": "likely_ai",
  "confidence": 0.8357,
  "final_score": 0.8357,
  "llm_score": 0.9,
  "heuristic_score": 0.7162,
  "status": "under_review",
  "appeal_filed": true,
  "appeal_id": "be52a9f6-84df-4059-9ca9-f4794fd4457b",
  "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
  "timestamp": "2026-07-01T04:45:19.074Z"
}
```

Retrieve the most recent 50 entries: `GET /api/log`

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/submit` | Submit text for classification |
| `GET` | `/api/result/<content_id>` | Retrieve a submission result |
| `POST` | `/api/appeal` | File an appeal for a submission |
| `GET` | `/api/appeal/<appeal_id>` | Retrieve an appeal record |
| `GET` | `/api/appeals/queue` | List all pending appeals (human review queue) |
| `GET` | `/api/log` | Return the 50 most recent audit log entries |

Path aliases without `/api/` prefix are also active (`/submit`, `/appeal`, `/log`) for compatibility with milestone curl examples.

### POST /api/submit

**Request body:**
```json
{
  "text": "string (20–50,000 characters, required)",
  "creator_id": "string (required)",
  "content_type": "string (optional, default: 'text')"
}
```

**Response (201):**
```json
{
  "content_id": "uuid",
  "attribution": "likely_ai | likely_human | uncertain",
  "label": "LIKELY_AI | LIKELY_HUMAN | UNCERTAIN",
  "label_text": "Full transparency label string shown to audiences",
  "final_score": 0.836,
  "confidence_level": "high | medium | low",
  "llm_score": 0.9,
  "llm_reasoning": "One-sentence explanation from the LLM",
  "heuristic_score": 0.716,
  "heuristic_sub": {
    "burstiness": 0.621,
    "lexical_diversity": 0.465,
    "hedge_density": 1.0,
    "punct_entropy": 0.676
  },
  "short_text_flag": false
}
```

### POST /api/appeal

**Request body:**
```json
{
  "content_id": "uuid (required)",
  "creator_reasoning": "string, max 1000 characters (required)"
}
```

**Response (201):**
```json
{
  "appeal_id": "uuid",
  "content_id": "uuid",
  "status": "received",
  "message": "Your appeal has been received...",
  "original_label": "LIKELY_AI"
}
```

**Error responses:** 404 (unknown content_id), 409 (duplicate appeal), 400 (missing fields or reasoning > 1000 chars)

---

## Running Locally

```bash
# 1. Clone and set up environment
git clone <repo-url>
cd ai201-project4-provenance-guard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Set your Groq API key
echo "GROQ_API_KEY=your_key_here" > .env

# 3. Run the server
python3 -m flask run --port 5000

# 4. Test a submission
curl -s -X POST http://localhost:5000/api/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "In todays world it is important to consider how transformative AI represents a paradigm shift in stakeholder collaboration going forward.", "creator_id": "demo-user"}' \
  | python3 -m json.tool

# 5. View audit log
curl -s http://localhost:5000/api/log | python3 -m json.tool
```

**Dependencies:** Python 3.11+, Flask 3.x, Flask-Limiter 3.x, Groq Python SDK, python-dotenv

---

## Known Limitations

**1. Stylometric heuristics penalize linguistic simplicity regardless of cause.**

The burstiness and lexical diversity sub-features treat any text with uniform sentence lengths and moderate vocabulary as AI-like. A non-native English speaker writing in short declarative sentences ("The boy walked to school. He was happy. The teacher smiled.") will produce heuristic scores near 0.65–0.75, indistinguishable from a GPT output with the same structure. This is a property of the signal itself — the heuristics measure _statistical uniformity_, not _origin_. There is no way to fix this within a purely surface-level heuristic approach without adding a language-origin classifier or syntax parser that can distinguish grammatical simplicity from generative uniformity. This creates a systematic fairness risk: the system is more likely to flag writing from communities whose oral storytelling traditions and second-language patterns differ from the Western literary baseline the signals were calibrated against.

**2. The LLM signal is non-deterministic and cannot be reliably re-tested.**

The same text submitted twice may receive different `llm_score` values because temperature=0.1 is low but not zero. This means an appeal that triggers a re-score may flip the result not because the classification was wrong but because of random sampling. In the current implementation, appeals do not trigger automated re-scoring for exactly this reason — a human reviewer is more reliable than a coin-flip re-run.

**3. Short collaborative or AI-assisted texts cannot be evaluated.**

Texts under 30 words are capped at UNCERTAIN regardless of the LLM signal. A 25-word excerpt from a human poem and a 25-word AI-generated caption are both returned as `UNCERTAIN` with `short_text_flag: true`. The system makes no claim about the origin of micro-texts.

---

## Spec Reflection

**One way the spec helped:** The explicit threshold design in `planning.md` Section 2 — setting boundaries at 0.28/0.72 rather than a symmetric 0.33/0.67 — directly prevented a mistake I would have made otherwise. During M4 testing, the borderline formal academic text scored `llm_score: 0.80` (the LLM read it as AI-like) but `heuristic_score: 0.45` (no hedge phrases). With a symmetric 0.67 threshold, the final score of 0.678 would have cleared LIKELY_AI and produced a high-confidence false positive on legitimate human academic writing. With the 0.72 threshold specified in planning, it correctly landed in UNCERTAIN. The spec's reasoning — "the cost of a false accusation is higher than the cost of under-labeling" — was load-bearing.

**One way the implementation diverged:** The short-text threshold was lowered from 80 words (written in the spec) to 30 words during M4. The spec's reasoning was that burstiness is meaningless on 2 sentences and TTR inflates on haiku — both true. But 80 words is not the right boundary for that concern. A 55-word text has 3–5 sentences, which is enough for burstiness to produce a meaningful coefficient of variation. All four milestone test inputs (40–60 words) were being overridden to UNCERTAIN before scoring, which would have made the scoring function impossible to demonstrate. 30 words is the actual threshold at which statistics break down — haiku, 2-sentence blurbs, single-line captions. The spec was right about _why_ to have a threshold; the implementation found the right value empirically.

---

## AI Usage

**Instance 1 — Groq classifier function**

I provided the AI tool with the exact system prompt from planning.md Section 1 and asked it to generate a `groq_classify(text: str) -> tuple[float, str]` function that calls the Groq API, parses the JSON response, and returns `(ai_probability, reasoning)` with a `0.5` fallback on any parse error. The generated function was structurally correct but did not handle the case where the model wraps its JSON in markdown code fences (` ```json ... ``` `). After observing this in practice — the model occasionally produces ` ```json\n{"ai_probability": 0.8}\n``` ` — I added the fence-stripping block before `json.loads()`. I also reduced temperature from the default to 0.1 to make the output more consistent, which was not in the generated code.

**Instance 2 — Threshold verification before wiring in**

Before integrating the scoring function into the endpoint, I used the AI tool to write a table-test that exercised `aggregate_score()` against 8 hand-selected `(llm_score, heuristic_score, word_count)` inputs — including boundary values at 0.28, 0.45, 0.55, 0.72, and the short-text path. The generated test cases were correct but included only 5 cases and missed the short-text override. I added the short-text case manually (`score 0.95, words 15` → `UNCERTAIN`) and also added boundary-value cases at exactly 0.28 and 0.72 to confirm the threshold was implemented as `>=` and `<=` (not `>` and `<`). The verification confirmed the thresholds matched the spec before any API call was made.
