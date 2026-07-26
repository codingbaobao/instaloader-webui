from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(frozen=True)

    success: bool
    data: T | None
    error: dict[str, str] | None = None
    meta: dict[str, object] = Field(default_factory=dict)
