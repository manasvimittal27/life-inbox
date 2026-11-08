# Life Inbox — Product & Technical Architecture

_AI daily planner: turn messy typed, spoken, photographed, or emailed thoughts into a clean, prioritised, time-aware day plan._

Last updated: 2026-11-09 · Branch `gmail-inbound-sync` (PR #7) · ~4.7k LOC · 89 passing tests

---

## 1. What Life Inbox is

You dump whatever is on your mind — type it, speak it, snap a photo of a to-do list, upload a PDF, or connect Gmail — and Life Inbox parses each thought into **structured, dated, prioritised tasks** and lays them out on a **day-by-day view** with a **time budget**. The core promise: _"Enter your thoughts — I'll handle everything else."_

### Design principles
- **Capture friction → zero.** Text, voice, image, PDF, and email all land in the same inbox.
- **One note → the right tasks.** A rambling, self-correcting sentence becomes several clean to-dos, each with a real date.
- **Importance-first prioritisation.** P0 (urgent/academic/work), P1 (important deep work), P2 (operational/hobby).
- **Time-aware.** Recurring prep runs until a deadline, then the deadline shows as its own event; a pie shows how committed your day is.
- **Minimalist aesthetic.** Fraunces + Hanken Grotesk, calm palette, light-first.

---

## 2. Product features

### Capture
| Feature | How |
|---|---|
| **Typed capture** | Textarea → **Add & organize** (fast per-note AI pass). |
| **Voice capture** | 🎤 **Speak** uses the browser Web Speech API; a light LLM **transcript-cleanup** pass de-garbles speech-to-text before parsing (preserves dates/times, drops filler). |
| **Image / PDF** | **Read file** → OCR/text extraction → one task-line per extracted line. |
| **Gmail (read-only)** | **Connect Gmail** (`gmail.readonly`) → **Sync Gmail** pulls recent inbox emails, turns each into tasks that link back to the email; idempotent by Gmail message id. |

### Organize (the AI engine)
- **Splitting**: distinct deliverables/exams/errands become separate tasks; later-defined references resolved; self-corrections honoured.
- **Dates**: all timing resolved to a real ISO date — `tomorrow`, `tonight`, `next Friday`, `the 12th` (with month/year rollover), `this/next weekend`, `in N days` / `N days later` (counted from **today**). Never a past date.
- **Priority buckets**: P0 = deadlines + academic/work; P1 = important deep work; P2 = errands/hobbies.
- **Recurrence**: multi-day prep ("study until the exam") → recurring-daily with a `due_date`.

### Plan (the day view)
- **Day navigation** (‹ Today ›) with P0/P1/P2 sections.
- **Recurring-until-deadline**: a study/prep task shows each day up to the day **before** its deadline; on the **deadline day** it renders as the **event itself** ("Tarun's submission · DUE TODAY"); after the deadline it disappears.
- **Time pie** ("Today's time"): committed vs free hours against an editable awake-hours budget.
- **Progress logging**: per-task %, AI-estimated from a free-text note or set manually; recurring tasks track progress per day, one-offs keep a rolling value.
- **Edit / complete / reopen / dismiss / delete**; inline edit modal for bucket, type, due date, recurrence, importance, effort.
- **Last-week summary** and a searchable **"Saved notes & sources"** panel (filter by text, type, category, priority, processing status).

---

## 3. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.13 |
| Web framework | FastAPI + Uvicorn |
| ORM / DB | SQLAlchemy 2.x + SQLite (`data/life_inbox.db`, gitignored) |
| Migrations | Alembic (adopt-or-validate, downgrade disabled; head = `0011`) |
| AI | OpenAI Responses API — `gpt-4o` for single-note parsing & transcript cleanup, `gpt-4o-mini` for bulk/progress |
| Gmail | `google-auth`, `google-auth-oauthlib`, `google-api-python-client` (scope `gmail.readonly`) |
| Frontend | Vanilla JS + CSS (no framework), server-rendered HTML template |
| Tests | pytest (89 tests), FastAPI `TestClient`, mocked OpenAI/Gmail |

---

## 4. Codebase map

```
life-inbox/
├── app/
│   ├── __init__.py          # loads .env (dotenv)
│   ├── main.py              # FastAPI app + all HTTP routes
│   ├── database.py          # SQLAlchemy models + all DB access functions
│   ├── organizer.py         # AI pipeline: parse, clean, bucket, dates, OCR, progress
│   ├── schemas.py           # Pydantic request/response models
│   ├── gmail_sync.py        # OAuth flow + Gmail fetch/parse + idempotent sync
│   ├── templates/index.html # single-page UI
│   └── static/
│       ├── app.js           # all client logic (66 functions)
│       └── styles.css       # minimalist theme
├── migrations/              # Alembic (versions 0001–0011) + env.py
├── tests/                   # test_api.py, test_migrations.py, test_gmail_sync.py
├── scripts/seed_demo_notes.py
├── requirements.txt · alembic.ini · README.md · .env.example
```

### 4.1 Frontend (`app/templates/index.html`, `app/static/app.js`, `styles.css`)
- **One page**: capture card (textarea, Speak, Add & organize, Read file, Connect/Sync Gmail), day view with buckets + day nav, side column (time pie, last-week), collapsible saved-notes/sources panel with filters, and an edit-task modal.
- **`app.js`** (~1.4k lines, 66 functions): capture/organize flow, voice capture + `voiceUsed` tracking, file upload, task rendering & `dayTasksFor` day logic (incl. recurring-until-deadline + deadline-event), the donut time pie, progress logging, Gmail connect/sync, item search/filter. Talks to the backend purely over `/api/*` JSON.
- **No build step**; assets are cache-busted by mtime in the `/` route.

### 4.2 Backend API (`app/main.py`)
| Method & path | Purpose |
|---|---|
| `GET /api/health` | Liveness. |
| `POST /api/items` | Create a text inbox item. |
| `POST /api/items/{id}/organize?source=` | Fast path: parse ONE note → task(s). `source=voice` runs transcript cleanup first. |
| `POST /api/items/upload` | Image/PDF → extracted text → items (≤10 MB). |
| `GET /api/items` | List/search/filter inbox items. |
| `PUT /api/items/{id}` · `DELETE /api/items/{id}` | Edit / delete an item (delete cascades its tasks). |
| `GET /api/tasks` | List tasks by status (open/completed/dismissed/all). |
| `POST /api/tasks/{id}/complete·reopen·dismiss` | Status transitions. |
| `PUT /api/tasks/{id}` · `DELETE /api/tasks/{id}` | Edit structured fields / delete. |
| `POST /api/tasks/{id}/progress` | Log progress (manual % or AI-estimated from a note). |
| `GET /api/analyze/latest` · `POST /api/analyze` | Bulk inbox analysis (legacy/secondary path). |
| `GET /api/gmail/status` | Configured? Connected? |
| `GET /api/gmail/auth` → `GET /api/gmail/callback` | OAuth consent + token exchange. |
| `POST /api/gmail/sync` | Pull recent emails → tasks (idempotent). |
| `GET /` | Serves the single-page UI. |
| `GET /api/notes/*` | **Deprecated** compatibility shims over items. |

Every route returns friendly error messages and never leaks internals; exceptions are logged server-side.

### 4.3 AI pipeline (`app/organizer.py`)
- **`analyze_single_note(text, today)`** — the primary engine: prompts `gpt-4o` to split one note into structured todos (`task`, `bucket`, `due_date`, `task_type`, `recurrence`, `importance`, `effort_min`, `link`). Falls back to a deterministic resolver with no API key.
- **`clean_transcript(text)`** — light LLM de-garble for voice input; preserves date/time phrases, never resolves them.
- **Deterministic helpers** — `resolve_schedule`, `bucket_for_todo`, `importance_for_text`, `effort_for_text`, `_resolve_date` (handles weekdays, "the Nth", "in N days", etc.) power the no-API-key fallback.
- **`extract_content_from_file`** — image/PDF → text. **`estimate_progress`** — % from a progress note. **`analyze_inbox_with_openai`** / **`validate_analysis_result`** — the bulk path.

### 4.4 Database (`app/database.py`)
Six tables (SQLite):

| Table | Role |
|---|---|
| `inbox_items` | Every captured thought (text/image/pdf/email). Key cols: `item_type`, `raw_text`, `source_url`, `external_id` (Gmail msg id), `processing_status`. |
| `tasks` | Generated tasks: `text`, `priority`, `bucket`, `due_date`, `task_type`, `recurrence`, `importance`, `effort_min`, `link`, `status`, `dedup_key` (unique). |
| `task_source_items` | Links tasks ↔ source items (composite PK). A note can now own **several** tasks (UNIQUE(item_id) dropped in 0011). |
| `task_progress` | Per-task progress (`pct`, `note`); per-day for recurring, a `once` row for one-offs. |
| `analysis_runs` | History of bulk analyses + cached result JSON. |
| `item_insights` | Latest per-item category/priority from a run. |

- **`create_tasks_for_item(item_id, todos)`** persists one note's tasks, links all of them, and is idempotent on re-organize (refresh slots, prune surplus, keep progress).
- **Migrations 0001–0011** follow an adopt-or-validate pattern (safe on both migration-built and `create_all`-built DBs); downgrade is disabled to protect persistent data.

### 4.5 Gmail sync (`app/gmail_sync.py`)
- OAuth: `build_authorization_url` → `exchange_code` → cached token (`data/gmail_token.json`, gitignored). Confidential-client flow (PKCE off), relaxed token scope, loopback-host normalisation.
- Fetch: `list_recent_messages` (whole INBOX, newest first — no `category:primary` so Workspace accounts work) → `parse_message` (subject/sender/body/deep-link).
- `sync_recent_emails`: for each **new** message (deduped by `external_id`), store an email item → `analyze_single_note` → `create_tasks_for_item`, with the task `link` set to the Gmail URL.

---

## 5. Data flow (typed/voice capture)

```
UI textarea / 🎤
      │  POST /api/items            → inbox_items row
      ▼
POST /api/items/{id}/organize?source=voice
      │  (voice) clean_transcript → save cleaned text
      │  analyze_single_note → [todos]
      │  create_tasks_for_item → tasks + task_source_items
      ▼
GET /api/tasks → day view (dayTasksFor buckets by date) + time pie
```

Image/PDF: `POST /api/items/upload` → `extract_content_from_file` → one item per line → organize.
Gmail: `POST /api/gmail/sync` → fetch → per new email → organize (link back to Gmail).

---

## 6. Testing & setup

- **Tests**: `.venv/bin/python -m pytest` — 89 tests. API behaviour (`test_api.py`), migrations on empty/legacy/create_all DBs (`test_migrations.py`), Gmail sync with a mocked API and no live calls (`test_gmail_sync.py`). OpenAI and Gmail are always mocked.
- **Run**: `pip install -r requirements.txt` → `alembic upgrade head` → `uvicorn app.main:app --reload` → http://localhost:8000.
- **Secrets** (gitignored): `OPENAI_API_KEY` in `.env`; `data/gmail_client_secret.json` + `data/gmail_token.json` for Gmail. See README §4/§4b.

---

## 7. Roadmap / open items
- Voice **transcript-review step** (confirm cleaned text before organizing) to remove residual STT ambiguity.
- Scheduled/background Gmail polling (currently manual Sync).
- Resolve PR overlap: PR #7 (fork) / PR #3 (upstream `manasvimittal27`) now carry the combined feature set; decide merge order for the earlier parser PR #6.
