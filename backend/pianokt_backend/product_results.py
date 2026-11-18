"""Client-measured product outcomes, independent of research validation."""
from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, Field, model_validator, ConfigDict

class ProductResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    attempt_id: UUID
    song_source: Literal['builtin', 'local']
    song_id: str
    segment_id: UUID | None = None
    segment_index: int | None = Field(default=None, ge=1)
    midi_sha256: str | None = None
    frontend_note_accuracy: float | None = Field(default=None, ge=0, le=1)
    completion: float = Field(ge=0, le=1)
    is_completed: bool
    completion_reason: Literal['finished', 'exited']
    started_at: datetime
    ended_at: datetime
    duration: float = Field(ge=0, le=14400)
    scoring_version: Literal['frontend-note-v1'] = 'frontend-note-v1'

    @model_validator(mode='after')
    def validate_result(self):
        from .song_identity import validate_identity
        validate_identity(self.song_source, self.song_id, self.segment_id, self.segment_index, self.midi_sha256)
        if not self.started_at.tzinfo or not self.ended_at.tzinfo or self.ended_at < self.started_at:
            raise ValueError('Invalid challenge timestamps')
        if self.is_completed != (self.completion_reason == 'finished') or self.is_completed and self.completion != 1:
            raise ValueError('Inconsistent completion')
        return self
