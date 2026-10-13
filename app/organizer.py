import json
import logging
import os

from app.schemas import AnalysisResult


MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
logger = logging.getLogger(__name__)


def extract_json_object(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return json.loads(raw[start:end + 1])


def priority_for_text(text: str) -> str:
    lowered = text.lower()

    high_markers = [
        "today", "tomorrow", "urgent", "asap", "due", "before friday",
        "before thursday", "expires", "deadline"
    ]
    medium_markers = [
        "this week", "weekend", "next week", "follow up", "remember",
        "need to", "appointment", "renew"
    ]

    if any(x in lowered for x in high_markers):
        return "High"
    if any(x in lowered for x in medium_markers):
        return "Medium"
    return "Low"


def bucket_for_text(text: str) -> str:
    lowered = text.lower()

    rules = [
        ("Work", ["team", "meeting", "architecture", "recruiter", "finance", "interview", "document"]),
        ("Money & Admin", ["bill", "tax", "insurance", "domain", "property tax", "reimbursement", "ca "]),
        ("Learning", ["read", "watch", "lecture", "article", "learn", "vector database", "prompt injection"]),
        ("Shopping & Errands", ["buy", "groceries", "pick up", "order", "dry cleaning"]),
        ("Health", ["dentist", "workout", "health"]),
        ("Travel", ["trip", "travel", "hotel", "jaipur"]),
        ("Ideas", ["idea", "build", "app", "startup", "product"]),
        ("People", ["call", "ask", "rohan", "papa", "mom"]),
    ]

    for name, markers in rules:
        if any(marker in lowered for marker in markers):
            return name
    return "Personal / Reference"


def fallback_analyze(notes: list[dict]) -> dict:
    """Deterministic fallback so the demo still works if the API is unavailable."""
    buckets = {}
    todos = []
    priority_view = {"High": [], "Medium": [], "Low": []}

    action_markers = [
        "call", "buy", "send", "ask", "finish", "submit", "pay", "book",
        "pick up", "prepare", "renew", "follow up", "need to", "remember to",
        "research", "look into", "order"
    ]

    for note in notes:
        text = note["text"]
        bucket = bucket_for_text(text)
        priority = priority_for_text(text)
        title = text[:72] + ("..." if len(text) > 72 else "")

        item = {
            "id": note["id"],
            "title": title,
            "priority": priority,
        }

        buckets.setdefault(bucket, []).append(item)
        priority_view[priority].append({"id": note["id"], "title": title})

        if any(marker in text.lower() for marker in action_markers):
            todos.append({
                "task": text,
                "priority": priority,
                "source_note_ids": [note["id"]],
                "when": None,
            })

    return {
        "buckets": [
            {"name": name, "notes": items}
            for name, items in buckets.items()
        ],
        "todos": todos,
        "priority_view": priority_view,
        "mode": "fallback_no_api_call",
    }


def validate_analysis_result(result: dict, notes: list[dict]) -> dict:
    validated = AnalysisResult.model_validate(result)
    validated.validate_for_item_ids({note["id"] for note in notes})
    return validated.model_dump()


def analyze_inbox_with_openai(notes: list[dict]) -> dict:
    if not notes:
        return validate_analysis_result(
            {
                "buckets": [],
                "todos": [],
                "priority_view": {"High": [], "Medium": [], "Low": []},
                "mode": "empty_inbox",
            },
            notes,
        )

    if not os.getenv("OPENAI_API_KEY"):
        return validate_analysis_result(fallback_analyze(notes), notes)

    try:
        from openai import OpenAI
        client = OpenAI()

        compact_notes = [{"id": n["id"], "text": n["text"]} for n in notes]

        prompt = f"""
You are the organization engine for a personal Life Inbox.

Analyze ALL saved notes together and turn the messy inbox into a practical dashboard.

Return ONLY valid JSON in exactly this shape:

{{
  "buckets": [
    {{
      "name": "short useful category name",
      "notes": [
        {{
          "id": 1,
          "title": "concise title preserving the note's meaning",
          "priority": "High"
        }}
      ]
    }}
  ],
  "todos": [
    {{
      "task": "concrete action",
      "priority": "High",
      "source_note_ids": [1],
      "when": "timing mentioned in the note, or null"
    }}
  ],
  "priority_view": {{
    "High": [
      {{"id": 1, "title": "concise title"}}
    ],
    "Medium": [],
    "Low": []
  }}
}}

Rules:
1. Organize the full inbox, not each note independently.
2. Every saved note must appear in exactly one bucket.
3. Use a small number of useful buckets such as Work, Personal, Money & Admin,
   Learning, Shopping & Errands, Health, Travel, Ideas, People, or another
   category only when it is genuinely clearer.
4. Priority must be one of High, Medium, Low.
5. High priority means explicit urgency, a near deadline, or a clearly time-sensitive obligation.
6. Medium means actionable but not immediately urgent.
7. Low means reference material, someday ideas, or non-urgent items.
8. Extract ToDos only when the note contains an explicit or strongly implied action.
9. A note can produce multiple ToDos if it genuinely contains multiple actions.
10. Do not invent dates, deadlines, people, facts, or actions.
11. Preserve relative timing exactly when present, for example "tomorrow" or "this weekend".
12. Keep titles and ToDos short and concrete. No motivational language, commentary, or prose outside the JSON.
13. priority_view should include every note once, grouped by priority.

Saved notes:
{json.dumps(compact_notes, ensure_ascii=False)}
"""

        response = client.responses.create(model=MODEL, input=prompt)
        result = extract_json_object(response.output_text)
        result["mode"] = "openai"
        return validate_analysis_result(result, notes)

    except Exception:
        logger.exception("OpenAI analysis failed; using deterministic fallback")
        fallback = fallback_analyze(notes)
        fallback["mode"] = "fallback_after_api_error"
        return validate_analysis_result(fallback, notes)
