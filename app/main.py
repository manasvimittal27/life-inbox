import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.database import (
    add_item_to_db,
    add_note_to_db,
    complete_analysis_run,
    delete_item_from_db,
    delete_note_from_db,
    fail_analysis_run,
    get_latest_successful_analysis,
    init_db,
    list_items_from_db,
    list_notes_from_db,
    list_tasks_from_db,
    set_task_status,
    start_analysis_run,
    update_item_in_db,
    update_note_in_db,
)
from app.organizer import analyze_inbox_with_openai, validate_analysis_result
from app.schemas import (
    InboxItem,
    InboxItemCreate,
    InboxItemUpdate,
    Note,
    NoteCreate,
    NoteUpdate,
    PersistedAnalysis,
    Priority,
    Task,
    TaskListStatus,
)


APP_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

app = FastAPI(title="Life Inbox")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/items", response_model=InboxItem)
def create_item(payload: InboxItemCreate):
    try:
        return add_item_to_db(payload.raw_text)
    except Exception as exc:
        logger.exception("Failed to save inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save item. Please try again.",
        ) from exc


@app.get("/api/items", response_model=List[InboxItem])
def get_items(
    search: Optional[str] = Query(None, alias="q", max_length=200),
    item_type: Optional[str] = Query(None, max_length=100),
    category: Optional[str] = Query(None, max_length=100),
    priority: Optional[Priority] = Query(None),
    processing_status: Optional[str] = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=100),
):
    try:
        return list_items_from_db(
            search=search,
            item_type=item_type,
            category=category,
            priority=priority,
            processing_status=processing_status,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("Failed to load inbox items")
        raise HTTPException(
            status_code=500,
            detail="Unable to load items. Please try again.",
        ) from exc


@app.put("/api/items/{item_id}", response_model=InboxItem)
def update_item(item_id: int, payload: InboxItemUpdate):
    try:
        item = update_item_in_db(item_id, payload.raw_text)
    except Exception as exc:
        logger.exception("Failed to update inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to update item. Please try again.",
        ) from exc

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    return item


@app.delete(
    "/api/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_item(item_id: int):
    try:
        deleted = delete_item_from_db(item_id)
    except Exception as exc:
        logger.exception("Failed to delete inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete item. Please try again.",
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Item not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/notes", response_model=Note, deprecated=True)
def create_note(payload: NoteCreate):
    try:
        return add_note_to_db(payload.text)
    except Exception as exc:
        logger.exception("Failed to save note")
        raise HTTPException(
            status_code=500,
            detail="Unable to save note. Please try again.",
        ) from exc


@app.get("/api/notes", response_model=List[Note], deprecated=True)
def get_notes():
    try:
        return list_notes_from_db()
    except Exception as exc:
        logger.exception("Failed to load notes")
        raise HTTPException(
            status_code=500,
            detail="Unable to load notes. Please try again.",
        ) from exc


@app.put("/api/notes/{note_id}", response_model=Note, deprecated=True)
def update_note(note_id: int, payload: NoteUpdate):
    try:
        note = update_note_in_db(note_id, payload.text)
    except Exception as exc:
        logger.exception("Failed to update note")
        raise HTTPException(
            status_code=500,
            detail="Unable to update note. Please try again.",
        ) from exc

    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    return note


@app.delete(
    "/api/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    deprecated=True,
)
def delete_note(note_id: int):
    try:
        deleted = delete_note_from_db(note_id)
    except Exception as exc:
        logger.exception("Failed to delete note")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete note. Please try again.",
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/tasks", response_model=List[Task])
def get_tasks(
    task_status: TaskListStatus = Query("open", alias="status"),
):
    try:
        return list_tasks_from_db(task_status)
    except Exception as exc:
        logger.exception("Failed to load tasks")
        raise HTTPException(
            status_code=500,
            detail="Unable to load tasks. Please try again.",
        ) from exc


def _transition_task(task_id: int, task_status: str):
    try:
        task = set_task_status(task_id, task_status)
    except Exception as exc:
        logger.exception("Failed to update task status")
        raise HTTPException(
            status_code=500,
            detail="Unable to update task. Please try again.",
        ) from exc

    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.post("/api/tasks/{task_id}/complete", response_model=Task)
def complete_task(task_id: int):
    return _transition_task(task_id, "completed")


@app.post("/api/tasks/{task_id}/reopen", response_model=Task)
def reopen_task(task_id: int):
    return _transition_task(task_id, "open")


@app.post("/api/tasks/{task_id}/dismiss", response_model=Task)
def dismiss_task(task_id: int):
    return _transition_task(task_id, "dismissed")


@app.get(
    "/api/analyze/latest",
    response_model=PersistedAnalysis,
)
def get_latest_analysis():
    try:
        result = get_latest_successful_analysis()
    except Exception as exc:
        logger.exception("Failed to load latest analysis")
        raise HTTPException(
            status_code=500,
            detail="Unable to load the latest analysis. Please try again.",
        ) from exc

    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@app.post("/api/analyze", response_model=PersistedAnalysis)
def analyze_inbox():
    run_id = None
    try:
        run_id = start_analysis_run()
        notes = list_notes_from_db()
        raw_result = analyze_inbox_with_openai(notes)
        result = validate_analysis_result(raw_result, notes)
        return complete_analysis_run(run_id, result)
    except Exception as exc:
        if run_id is not None:
            try:
                fail_analysis_run(run_id)
            except Exception:
                logger.exception("Failed to mark analysis run as failed")
        logger.exception("Failed to analyze inbox")
        raise HTTPException(
            status_code=500,
            detail="Unable to analyze inbox. Please try again.",
        ) from exc


@app.get("/", response_class=HTMLResponse)
def index():
    return (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
