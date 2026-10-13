from typing import List, Literal, Optional, Set

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class NoteText(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def reject_whitespace_only_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Note text must contain non-whitespace characters")
        return value


class NoteCreate(NoteText):
    pass


class NoteUpdate(NoteText):
    pass


class Note(BaseModel):
    id: int
    text: str
    created_at: str


class InboxItemText(BaseModel):
    raw_text: str = Field(min_length=1, max_length=2000)

    @field_validator("raw_text")
    @classmethod
    def reject_whitespace_only_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Item text must contain non-whitespace characters")
        return value


class InboxItemCreate(InboxItemText):
    pass


class InboxItemUpdate(InboxItemText):
    pass


class InboxItem(BaseModel):
    id: int
    item_type: str
    title: Optional[str]
    raw_text: Optional[str]
    source_url: Optional[str]
    original_filename: Optional[str]
    mime_type: Optional[str]
    processing_status: str
    created_at: str
    updated_at: Optional[str]


Priority = Literal["High", "Medium", "Low"]
AnalysisMode = Literal[
    "openai",
    "fallback_no_api_call",
    "fallback_after_api_error",
    "empty_inbox",
]


class StrictAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisNote(StrictAnalysisModel):
    id: int
    title: str = Field(min_length=1)
    priority: Priority


class AnalysisBucket(StrictAnalysisModel):
    name: str = Field(min_length=1)
    notes: List[AnalysisNote]


class AnalysisTodo(StrictAnalysisModel):
    task: str = Field(min_length=1)
    priority: Priority
    source_note_ids: List[int] = Field(min_length=1)
    when: Optional[str] = None


class AnalysisPriorityItem(StrictAnalysisModel):
    id: int
    title: str = Field(min_length=1)


class AnalysisPriorityView(StrictAnalysisModel):
    High: List[AnalysisPriorityItem]
    Medium: List[AnalysisPriorityItem]
    Low: List[AnalysisPriorityItem]


class AnalysisResult(StrictAnalysisModel):
    buckets: List[AnalysisBucket]
    todos: List[AnalysisTodo]
    priority_view: AnalysisPriorityView
    mode: AnalysisMode

    @model_validator(mode="after")
    def validate_consistent_item_views(self):
        bucket_ids = [
            note.id
            for bucket in self.buckets
            for note in bucket.notes
        ]
        priority_ids = [
            item.id
            for level in ("High", "Medium", "Low")
            for item in getattr(self.priority_view, level)
        ]
        if len(bucket_ids) != len(set(bucket_ids)):
            raise ValueError("Each item must appear in exactly one bucket")
        if len(priority_ids) != len(set(priority_ids)):
            raise ValueError(
                "Each item must appear once in the priority view"
            )
        if set(bucket_ids) != set(priority_ids):
            raise ValueError(
                "Bucket and priority views must contain the same items"
            )
        return self

    def validate_for_item_ids(self, expected_ids: Set[int]) -> None:
        bucket_ids = {
            note.id
            for bucket in self.buckets
            for note in bucket.notes
        }
        if bucket_ids != expected_ids:
            raise ValueError(
                "Analysis item IDs must match the current inbox"
            )
        todo_source_ids = {
            item_id
            for todo in self.todos
            for item_id in todo.source_note_ids
        }
        if not todo_source_ids <= expected_ids:
            raise ValueError("ToDos reference unknown inbox items")


class PersistedAnalysis(AnalysisResult):
    analysis_run_id: int
    generated_at: str


TaskStatus = Literal["open", "completed", "dismissed"]
TaskListStatus = Literal["open", "completed", "dismissed", "all"]


class Task(BaseModel):
    id: int
    text: str
    priority: Priority
    timing_text: Optional[str]
    status: TaskStatus
    source_item_ids: List[int]
    created_at: str
    updated_at: str
