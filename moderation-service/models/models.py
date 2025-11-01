from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
import time


class UserRequest(BaseModel):
    user_id: str = Field(...)
    text: str = Field(..., min_length=1)
    session_id: Optional[str] = Field(None)
    timestamp: float = Field(default_factory=lambda: time.time())

    @validator("text")
    def truncate_text(cls, v: str) -> str:
        return v[:4096]


class ModerationVerdict(BaseModel):
    allowed: bool
    risk: float
    action: str
    reasons: List[str] = Field(default_factory=list)


class RAGResponse(BaseModel):
    context: str


class LLMAnswer(BaseModel):
    answer: str
    tokens: int
    model: str


class ProcessResponse(BaseModel):
    trace_id: str
    answer: Optional[str]
    risk: float
    template: Optional[str]
    meta: Dict[str, Any] = Field(default_factory=dict)


class AuditLogEntry(BaseModel):
    trace_id: str
    event: str
    details: Dict[str, Any]