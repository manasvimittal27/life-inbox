import json
import logging
import os
import re
from datetime import date, timedelta
from typing import Optional


URL_RE = re.compile(r"https?://[^\s]+")


def extract_url(text: str) -> Optional[str]:
    match = URL_RE.search(text or "")
    return match.group(0).rstrip(").,") if match else None

from app.schemas import AnalysisResult


MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# The single-note parser needs stronger reading comprehension (coreference,
# segmenting run-on speech, self-corrections) than the bulk/progress calls, so
# it gets its own, more capable model. Still a fast tier — worth ~1s.
PARSE_MODEL = os.getenv("OPENAI_PARSE_MODEL", "gpt-4o")
logger = logging.getLogger(__name__)


def _kwargs_for(model: str) -> dict:
    """Reasoning effort only applies to GPT-5 reasoning models; keep it off the
    fast non-reasoning models (gpt-4o / gpt-4o-mini etc.)."""
    if model.startswith("gpt-5"):
        return {"reasoning": {"effort": "minimal"}}
    return {}


def _model_kwargs() -> dict:
    return _kwargs_for(MODEL)


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


def _nth_of_month(today: date, n: int) -> Optional[date]:
    """The Nth of the current month, rolled forward to next month (or next year)
    if that day has already passed or doesn't exist this month."""
    if not 1 <= n <= 31:
        return None
    year, month = today.year, today.month
    for _ in range(13):  # try this month, then roll forward until a valid future day
        try:
            candidate = date(year, month, n)
        except ValueError:
            candidate = None
        if candidate is not None and candidate >= today:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


_NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _resolve_date(lowered: str, today: date) -> Optional[str]:
    if "day after tomorrow" in lowered:
        return (today + timedelta(days=2)).isoformat()
    # "in 2 days" / "2 days later" / "three days from now" → today + N.
    offset = re.search(
        r"\b(?:in\s+)?(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
        r"\s+days?\s+(?:later|from now|from today|ahead)\b",
        lowered,
    ) or re.search(
        r"\bin\s+(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\b",
        lowered,
    )
    if offset:
        token = offset.group(1)
        n = int(token) if token.isdigit() else _NUM_WORDS.get(token)
        if n:
            return (today + timedelta(days=n)).isoformat()
    if "tomorrow" in lowered:
        return (today + timedelta(days=1)).isoformat()
    if "today" in lowered or "tonight" in lowered:
        return today.isoformat()
    if "next weekend" in lowered:
        return (_next_weekday(today, 5) + timedelta(days=7)).isoformat()
    if "weekend" in lowered:
        return _next_weekday(today, 5).isoformat()  # coming Saturday
    if "next week" in lowered:
        return (today + timedelta(days=7)).isoformat()
    if "this week" in lowered:
        return _next_weekday(today, 4).isoformat()  # coming Friday
    for name, idx in WEEKDAYS.items():
        if name in lowered:
            return _next_weekday(today, idx).isoformat()
    # "the 15th" / "on the 3rd" / "before the 20th" → that day, rolled forward.
    nth = re.search(r"\b(?:the|on|by|before|due)\s+(\d{1,2})(?:st|nd|rd|th)\b", lowered)
    if not nth:
        nth = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", lowered)
    if nth:
        resolved = _nth_of_month(today, int(nth.group(1)))
        if resolved is not None:
            return resolved.isoformat()
    # A bare clock time ("9pm", "21:00") with no day means today.
    if re.search(r"\b\d{1,2}\s*(?:am|pm)\b", lowered) or re.search(r"\b\d{1,2}:\d{2}\b", lowered):
        return today.isoformat()
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
                "link": extract_url(text),
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


def _fallback_progress(note: str) -> int:
    """Rough keyword estimate used when no OpenAI key is available."""
    lowered = note.lower()
    if any(w in lowered for w in ["done", "complete", "finished", "fully", "all of"]):
        return 100
    if any(w in lowered for w in ["almost", "nearly", "most of"]):
        return 80
    if any(w in lowered for w in ["half", "halfway"]):
        return 50
    if any(w in lowered for w in ["start", "began", "begun", "a bit", "barely", "little"]):
        return 25
    return 50


def estimate_progress(task_text: str, note: str) -> int:
    """Estimate today's completion % (0-100) from a free-text progress note."""
    if not note or not note.strip():
        return 0
    if not os.getenv("OPENAI_API_KEY"):
        return _fallback_progress(note)

    try:
        import re
        from openai import OpenAI

        client = OpenAI()
        prompt = (
            "Estimate how complete a person's work on a task is, based on their "
            "short progress note, as a single integer from 0 to 100.\n"
            f"Task: {task_text}\n"
            f"Progress note: {note}\n"
            "Reply with ONLY the number."
        )
        response = client.responses.create(
            model=MODEL,
            input=prompt,
            **_model_kwargs(),
        )
        match = re.search(r"\d{1,3}", response.output_text or "")
        if match:
            return max(0, min(100, int(match.group())))
        return _fallback_progress(note)
    except Exception:
        logger.exception("Progress estimation failed; using keyword fallback")
        return _fallback_progress(note)


FILE_EXTRACTION_PROMPT = (
    "You are reading a file a person added to their personal task inbox "
    "(a photo, screenshot, or document). Transcribe its actionable content as "
    "plain text, exactly as if the person had typed quick notes to themselves: "
    "tasks, deadlines, dates, names, amounts, and key details. Keep it concise "
    "and factual, one item per line. Do not add commentary, headings, or "
    "explanation — output only the extracted notes. If there is nothing "
    "actionable or readable, reply with an empty response."
)


def extract_content_from_file(data: bytes, mime: str, filename: str) -> str:
    """Use the vision model to turn an image/PDF into plain-text inbox notes."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("An OpenAI API key is required to read uploaded files")

    import base64
    from openai import OpenAI

    client = OpenAI()
    b64 = base64.b64encode(data).decode("ascii")

    if mime == "application/pdf":
        file_part = {
            "type": "input_file",
            "filename": filename or "upload.pdf",
            "file_data": f"data:application/pdf;base64,{b64}",
        }
    else:
        file_part = {
            "type": "input_image",
            "image_url": f"data:{mime};base64,{b64}",
        }

    response = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": FILE_EXTRACTION_PROMPT},
                    file_part,
                ],
            }
        ],
        **_model_kwargs(),
    )
    return (response.output_text or "").strip()


def _fallback_single_note(text: str, today: date) -> list[dict]:
    """Deterministic single-note todos (no API key)."""
    schedule = resolve_schedule(text, today)
    return [{
        "task": text.strip(),
        "bucket": bucket_for_todo(text, schedule, today),
        "due_date": schedule["due_date"],
        "task_type": schedule["task_type"],
        "recurrence": schedule["recurrence"],
        "importance": importance_for_text(text),
        "effort_min": effort_for_text(text, schedule),
        "link": extract_url(text),
    }]


def clean_transcript(text: str) -> str:
    """De-garble a raw speech-to-text transcript before it is parsed.

    Voice capture (Web Speech API) often mangles dictation — mis-hearings,
    repeated/filler words, false starts (e.g. "today's letter 2 days later
    today's later yeah that's it"). This runs ONE light LLM pass to fix obvious
    transcription errors and drop filler, while preserving every real task,
    date, time, name and number. It deliberately does NOT resolve relative dates
    (keeps "tomorrow"/"2 days later" verbatim) — that's analyze_single_note's
    job. No API key, an error, or empty input → the original text unchanged.
    """
    text = (text or "").strip()
    if not text or not os.getenv("OPENAI_API_KEY"):
        return text
    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = f"""You clean raw speech-to-text transcripts before they are parsed into tasks.
The text was dictated and may contain mis-hearings, repeated or filler words, and
garbled fragments (e.g. "today's letter 2 days later today's later yeah that's it").

Your ONLY job is to remove noise and fix mis-hearings — never to drop real content.

CRITICAL — time and date phrases are the most important words to keep:
- NEVER delete a time/date expression: "tomorrow", "tonight", "today", "2 days
  later", "in 3 days", "next Friday", "this weekend", "the 12th", "6 p.m.", "4:30".
- They are often buried inside garble. "today's letter 2 days later today's later"
  is garble wrapped around the real phrase "2 days later" — KEEP "2 days later"
  and attach it to the task it belongs to; drop only the surrounding noise.
- Do NOT resolve or convert dates — keep them worded exactly as spoken.

Also:
- PRESERVE every real task, person's name, and number as intended.
- Remove only clear filler and false starts ("um", "yeah that's it", repeated words).
- Do NOT add information or invent tasks. When unsure whether a word is noise or
  meaning, KEEP it. If the text is already clean, return it unchanged.

Return ONLY the cleaned transcript text, nothing else.

Transcript: {json.dumps(text)}
"""
        response = client.responses.create(
            model=PARSE_MODEL,
            input=prompt,
            **_kwargs_for(PARSE_MODEL),
        )
        cleaned = (response.output_text or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.replace("```", "").strip()
        # Models sometimes echo the text wrapped in quotes — unwrap a single
        # pair that spans the whole string.
        if len(cleaned) >= 2 and cleaned[0] in "\"'" and cleaned[-1] == cleaned[0]:
            cleaned = cleaned[1:-1].strip()
        # Guard against a model that returns nothing or a refusal.
        return cleaned or text
    except Exception:
        logger.exception("Transcript cleanup failed; using original text")
        return text


def analyze_single_note(text: str, today: Optional[date] = None) -> list[dict]:
    """Fast path: turn ONE note into its structured to-do(s). Much faster than
    re-analyzing the whole inbox."""
    today = today or date.today()
    if not os.getenv("OPENAI_API_KEY"):
        return _fallback_single_note(text, today)

    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = f"""
Today is {today.strftime("%A")}, {today.isoformat()}.
You turn ONE messy personal note (often rambling, spoken out loud, with
self-corrections) into clean, SEPARATE actionable to-dos.

Return ONLY a JSON array. Each element:
{{
  "task": "concrete action",
  "bucket": "P0 | P1 | P2",
  "due_date": "YYYY-MM-DD or null",
  "task_type": "one_off | recurring",
  "recurrence": "daily | weekly | null",
  "importance": "high | normal",
  "effort_min": 60,
  "link": "meeting/call URL if present, else null"
}}

HOW TO SPLIT (most important). The placeholders below (<A>, <X>, <topic>) are
ONLY illustrations of the rules — NEVER output them as tasks. Every task you
return must come from the user's Note at the bottom, never from these instructions:
- Every DISTINCT deliverable, exam, submission, assignment, challenge, event, or
  errand is its OWN element. Never merge two of them into one task.
- Different named things are different tasks even if mentioned in one breath:
  "study for <A>, <B>, and <C>" → THREE separate study tasks, one per deliverable.
- Resolve references defined later in the note. If it says "<X> is for <topic>",
  that task is the <topic> one — label it so, e.g. "Study for <X> (<topic>)".
- Honor self-corrections: if the note revises a fact ("sorry, <A> is on the 12th,
  <B> on the 13th"), use the CORRECTED value and ignore the earlier one.

TIMING (today's date is given above — compute from it):
- Resolve ALL timing to a real ISO date. No date given → today. A bare time
  ("9pm") or "tonight" → today. "next <weekday>"/"<weekday>" → the coming one.
- "in N days" / "N days later" / "N days from now" → exactly N days from TODAY
  (the date given above), e.g. "2 days later" = today + 2. Count from TODAY, NOT
  from another task's date, UNLESS the note explicitly says "N days after <that
  event>". "day after tomorrow" = today + 2.
- "the Nth"/"on the 12th"/"before the Nth" → the Nth of the CURRENT month, BUT if
  that day has already passed this month, use the Nth of NEXT month instead.
- "this weekend"/"the weekend" → the coming Saturday. "next weekend" → the Saturday
  after that. "this week" → the coming Friday.
- NEVER output a due_date earlier than today. If your resolved date is in the past,
  roll it forward (to next month, or next year for a month that already passed).
- A deadline you must PREPARE for over several days ("I have the 9th, 10th, 11th
  to prepare", "start studying from now", "study every day until") → make the
  study task task_type "recurring", recurrence "daily", and set due_date to the
  deadline/exam date. That way it shows up each prep day and stops at the deadline.
- One-time events (a party, a call) are task_type "one_off" on their date.

BUCKET (priority):
- P0 = urgent deadlines AND anything academic or work-related: exams, submissions,
  assignments, tests, deliverables, challenges, client/work tasks, medical appts.
- P1 = important deep work with no hard deadline (general learning, brainstorming,
  side projects).
- P2 = operational errands and hobbies (laundry, shopping, parties, leisure).
- importance "high" if it's academic/work/a deadline, or the note stresses it
  ("crucial", "important").

OTHER:
- effort_min: studying/deep work ~60, quick errands ~15-30, short events ~30.

Note: {json.dumps(text)}
"""
        response = client.responses.create(
            model=PARSE_MODEL,
            input=prompt,
            **_kwargs_for(PARSE_MODEL),
        )
        raw = (response.output_text or "").strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end == -1:
            return _fallback_single_note(text, today)
        todos = json.loads(raw[start:end + 1])
        return todos if isinstance(todos, list) and todos else _fallback_single_note(text, today)
    except Exception:
        logger.exception("Single-note analysis failed; using fallback")
        return _fallback_single_note(text, today)


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
      "effort_min": 60,
      "link": "meeting/call URL if the note has one, else null"
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
    timing is implied). If a note gives only a clock time ("9pm", "21:00") or
    "tonight" with no explicit day, treat it as today.
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
15a. "link": if the note contains a meeting or call URL (Google Meet, Zoom,
    Teams, a lecture/webinar link), copy that full URL into "link". Otherwise null.
15. Keep titles and ToDos short and concrete. No motivational language, commentary, or prose outside the JSON.
16. priority_view should include every note once, grouped by priority.

Saved notes:
{json.dumps(compact_notes, ensure_ascii=False)}
"""

        response = client.responses.create(
            model=MODEL,
            input=prompt,
            **_model_kwargs(),
        )
        result = extract_json_object(response.output_text)
        result["mode"] = "openai"
        return validate_analysis_result(result, notes)

    except Exception:
        logger.exception("OpenAI analysis failed; using deterministic fallback")
        fallback = fallback_analyze(notes)
        fallback["mode"] = "fallback_after_api_error"
        return validate_analysis_result(fallback, notes)
