import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit
from signals.heuristics import heuristic_classify
from signals.labels import get_label_text
from signals.llm import groq_classify
from signals.scoring import aggregate_score

load_dotenv()

app = Flask(__name__)

# storage_uri="memory://" silences the Flask-Limiter ≥3.x in-memory warning
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# In-memory stores (keyed by UUID strings)
submissions: dict[str, dict] = {}
appeals: dict[str, dict] = {}


def _bad(msg: str, status: int = 400):
    return jsonify({"error": msg}), status


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

@app.post("/api/submit")
@app.post("/submit")           # alias used in milestone curl examples
@limiter.limit("10 per minute; 100 per day")
def submit():
    body = request.get_json(silent=True)
    if not body:
        return _bad("Request body must be JSON.")

    text = body.get("text", "")
    creator_id = body.get("creator_id", "")
    content_type = body.get("content_type", "text")

    if not isinstance(text, str) or not text.strip():
        return _bad("'text' is required and must be a non-empty string.")
    if not isinstance(creator_id, str) or not creator_id.strip():
        return _bad("'creator_id' is required and must be a non-empty string.")
    if len(text) < 20:
        return _bad("'text' must be at least 20 characters.")
    if len(text) > 50_000:
        return _bad("'text' must not exceed 50,000 characters.")

    content_id = str(uuid.uuid4())
    word_count = len(text.split())

    llm_score, llm_reasoning = groq_classify(text)
    heuristic_score, heuristic_sub = heuristic_classify(text)
    result = aggregate_score(llm_score, heuristic_score, word_count)

    label_text = get_label_text(result["label"])

    record = {
        "content_id": content_id,
        "creator_id": creator_id.strip(),
        "content_type": content_type,
        "text": text,
        "attribution": result["attribution"],
        "label": result["label"],
        "label_text": label_text,
        "final_score": result["final_score"],
        "confidence_level": result["confidence_level"],
        "llm_score": llm_score,
        "llm_reasoning": llm_reasoning,
        "heuristic_score": heuristic_score,
        "heuristic_sub": heuristic_sub,
        "short_text_flag": result["short_text_flag"],
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "status": "classified",
        "appeal_id": None,
    }

    submissions[content_id] = record

    audit.write_entry({
        "event": "submission",
        "content_id": content_id,
        "creator_id": record["creator_id"],
        "attribution": result["attribution"],
        "confidence": result["final_score"],
        "final_score": result["final_score"],
        "llm_score": llm_score,
        "heuristic_score": heuristic_score,
        "status": "classified",
        "appeal_filed": False,
        "appeal_reasoning": None,
    })

    return jsonify({
        "content_id": content_id,
        "attribution": result["attribution"],
        "label": result["label"],
        "label_text": label_text,
        "final_score": result["final_score"],
        "confidence_level": result["confidence_level"],
        "llm_score": llm_score,
        "llm_reasoning": llm_reasoning,
        "heuristic_score": heuristic_score,
        "heuristic_sub": heuristic_sub,
        "short_text_flag": result["short_text_flag"],
    }), 201


@app.get("/api/result/<content_id>")
def get_result(content_id: str):
    record = submissions.get(content_id)
    if not record:
        return _bad(f"Content '{content_id}' not found.", 404)

    return jsonify({
        "content_id": record["content_id"],
        "attribution": record["attribution"],
        "label": record["label"],
        "label_text": record["label_text"],
        "final_score": record["final_score"],
        "confidence_level": record["confidence_level"],
        "llm_score": record["llm_score"],
        "heuristic_score": record["heuristic_score"],
        "heuristic_sub": record.get("heuristic_sub"),
        "short_text_flag": record["short_text_flag"],
        "submitted_at": record["submitted_at"],
        "status": record["status"],
        "appeal_id": record.get("appeal_id"),
    })


# ---------------------------------------------------------------------------
# Appeals
# ---------------------------------------------------------------------------

@app.post("/api/appeal")
@app.post("/appeal")           # alias used in milestone curl examples
@limiter.limit("3 per minute")
def submit_appeal():
    body = request.get_json(silent=True)
    if not body:
        return _bad("Request body must be JSON.")

    content_id = body.get("content_id", "").strip()
    creator_reasoning = body.get("creator_reasoning", "").strip()

    if not content_id:
        return _bad("'content_id' is required.")
    if not creator_reasoning:
        return _bad("'creator_reasoning' is required.")
    if len(creator_reasoning) > 1000:
        return _bad("'creator_reasoning' must not exceed 1000 characters.")

    record = submissions.get(content_id)
    if not record:
        return _bad(f"Content '{content_id}' not found.", 404)

    if record.get("appeal_id"):
        return _bad("An appeal has already been filed for this content.", 409)

    appeal_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Update submission in-place
    record["status"] = "under_review"
    record["appeal_id"] = appeal_id

    appeal_record = {
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": record["creator_id"],
        "creator_reasoning": creator_reasoning,
        "original_label": record["label"],
        "original_score": record["final_score"],
        "status": "pending",
        "created_at": now,
    }
    appeals[appeal_id] = appeal_record

    audit.write_entry({
        "event": "appeal",
        "content_id": content_id,
        "creator_id": record["creator_id"],
        "attribution": record["attribution"],
        "confidence": record["final_score"],
        "final_score": record["final_score"],
        "llm_score": record["llm_score"],
        "heuristic_score": record["heuristic_score"],
        "status": "under_review",
        "appeal_filed": True,
        "appeal_id": appeal_id,
        "appeal_reasoning": creator_reasoning,
    })

    return jsonify({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "received",
        "message": (
            "Your appeal has been received. The content status has been updated "
            "to 'under review'. We aim to respond within 48 hours."
        ),
        "original_label": record["label"],
    }), 201


@app.get("/api/appeal/<appeal_id>")
def get_appeal(appeal_id: str):
    record = appeals.get(appeal_id)
    if not record:
        return _bad(f"Appeal '{appeal_id}' not found.", 404)
    return jsonify(record)


@app.get("/api/appeals/queue")
def appeals_queue():
    queue = sorted(appeals.values(), key=lambda a: a["created_at"])
    enriched = []
    for a in queue:
        sub = submissions.get(a["content_id"], {})
        enriched.append({
            **a,
            "original_text_preview": sub.get("text", "")[:300],
            "original_attribution": sub.get("attribution"),
        })
    return jsonify({"count": len(enriched), "appeals": enriched})


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@app.get("/api/log")
@app.get("/log")               # alias
def get_log():
    return jsonify({"entries": audit.get_recent(50)})


if __name__ == "__main__":
    app.run(debug=True)
