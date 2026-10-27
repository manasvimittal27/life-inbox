import json
import logging
import os
from datetime import date, timedelta
from typing import Optional

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


# --- Structured scheduling helpers (used by the fallback and shared rules) ---

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
RECURRING_MARKERS = ["every day", "everyday", "daily", "regularly", "each day"]
WEEKLY_MARKERS = ["every week", "weekly"]
IMPORTANCE_MARKERS = [
    "crucial", "important", "must", "critical", "essential", "vital"
]
URGENCY_MARKERS = [
    "urgent", "asap", "right now", "immediately", "due today", "due tomorrow"
]
DEEP_WORK_MARKERS = [
    "learn", "study", "code", "coding", "build", "project", "quiz", "exam",
    "read", "write", "design", "brainstorm", "analyse", "analyze", "research",
    "prepare", "practice", "revise", "dsa", "leetcode", "course", "lecture"
]
OPERATIONAL_MARKERS = [
    "buy", "groceries", "laundry", "shampoo", "pick up", "pay", "bill", "call",
    "book", "order", "clean", "errand", "renew", "send", "email", "schedule"
]


def _next_weekday(today: date, target_idx: int) -> date:
    days_ahead = (target_idx - today.weekday()) % 7
    return today + timedelta(days=days_ahead)


def _resolve_date(lowered: str, today: date) -> Optional[str]:
    if "day after tomorrow" in lowered:
        return (today + timedelta(days=2)).isoformat()
    if "tomorrow" in lowered:
        return (today + timedelta(days=1)).isoformat()
    if "today" in lowered or "tonight" in lowered:
        return today.isoformat()
    if "weekend" in lowered:
        return _next_weekday(today, 5).isoformat()  # coming Saturday
    if "next week" in lowered:
        return (today + timedelta(days=7)).isoformat()
    for name, idx in WEEKDAYS.items():
        if name in lowered:
            return _next_weekday(today, idx).isoformat()
    return None


def resolve_schedule(text: str, today: date) -> dict:
    """Infer recurrence vs. a concrete due date from free text."""
    lowered = text.lower()
    if any(m in lowered for m in RECURRING_MARKERS):
        return {"task_type": "recurring", "recurrence": "daily", "due_date": None}
    if any(m in lowered for m in WEEKLY_MARKERS):
        return {"task_type": "recurring", "recurrence": "weekly", "due_date": None}
    return {
        "task_type": "one_off",
        "recurrence": None,
        "due_date": _resolve_date(lowered, today),
    }


def bucket_for_todo(text: str, schedule: dict, today: date) -> str:
    """P0 = urgency/near deadline, P1 = deep work, P2 = operational/hobby."""
    lowered = text.lower()
    if any(m in lowered for m in URGENCY_MARKERS):
        return "P0"
    due = schedule.get("due_date")
    if due:
        delta = (date.fromisoformat(due) - today).days
        if 0 <= delta <= 1:
            return "P0"
    if any(m in lowered for m in DEEP_WORK_MARKERS):
        return "P1"
    if any(m in lowered for m in OPERATIONAL_MARKERS):
        return "P2"
    return "P2"


def importance_for_text(text: str) -> str:
    lowered = text.lower()
    return "high" if any(m in lowered for m in IMPORTANCE_MARKERS) else "normal"


def effort_for_text(text: str, schedule: dict) -> int:
    lowered = text.lower()
    if schedule["task_type"] == "recurring" or any(
        m in lowered for m in DEEP_WORK_MARKERS
    ):
        return 60
    return 30


def fallback_analyze(notes: list[dict], today: Optional[date] = None) -> dict:
    """Deterministic fallback so the demo still works if the API is unavailable."""
    today = today or date.today()
    buckets = {}
    todos = []
    priority_view = {"High": [], "Medium": [], "Low": []}

    action_markers = [
        "call", "buy", "send", "ask", "finish", "submit", "pay", "book",
        "pick up", "prepare", "renew", "follow up", "need to", "remember to",
        "research", "look into", "order", "learn", "study"
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
            schedule = resolve_schedule(text, today)
            todos.append({
                "task": text,
                "priority": priority,
                "source_note_ids": [note["id"]],
                "when": None,
                "bucket": bucket_for_todo(text, schedule, today),
                "due_date": schedule["due_date"],
                "task_type": schedule["task_type"],
                "recurrence": schedule["recurrence"],
                "importance": importance_for_text(text),
                "effort_min": effort_for_text(text, schedule),
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
        today = date.today()
        today_str = today.isoformat()
        weekday_str = today.strftime("%A")

        prompt = f"""
You are the organization engine for a personal Life Inbox.

Today is {weekday_str}, {today_str}. Use this to resolve any relative timing
into real calendar dates.

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
      "when": "timing phrase from the note, or null",
      "bucket": "P0",
      "due_date": "YYYY-MM-DD or null",
      "task_type": "one_off",
      "recurrence": "daily / weekly, or null",
      "importance": "high or normal",
      "effort_min": 60
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
10. Do not invent people, facts, or actions. You MAY compute real dates from
    relative phrases using today's date above (e.g. "this Saturday" -> the real date).
11. For each ToDo, resolve timing into "due_date" as an ISO date (YYYY-MM-DD).
    If the note implies something repeating ("daily", "regularly", "every day"),
    set "task_type" to "recurring" and leave "due_date" null; treat "regularly"
    and "every day" as "recurrence": "daily" unless a weekly cadence is explicit.
    Otherwise "task_type" is "one_off" with a "due_date" (or null if truly no
    timing is implied).
12. Assign each ToDo a "bucket":
    - P0 = urgent: a hard deadline that is today or tomorrow, or explicit urgency.
    - P1 = important deep/analytical work (studying, coding, building, brainstorming,
      learning). This is also where recurring learning habits go.
    - P2 = operational low-effort tasks (errands, laundry, shopping, admin) and hobbies.
    Urgency wins: anything due today/tomorrow is P0 regardless of its nature.
13. "importance" is "high" only when the note stresses it ("crucial", "important",
    "must", "critical"); this boosts ranking WITHIN a bucket and does NOT promote to P0.
14. "effort_min" is an estimated duration in minutes; default learning/deep-work
    sessions to 60, quick operational tasks to ~15-30.
15. Keep titles and ToDos short and concrete. No motivational language, commentary, or prose outside the JSON.
16. priority_view should include every note once, grouped by priority.

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
