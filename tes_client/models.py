from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FileType(str, Enum):
    FILE = "FILE"
    DIRECTORY = "DIRECTORY"


class TesState(str, Enum):
    UNKNOWN = "UNKNOWN"
    QUEUED = "QUEUED"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    EXECUTOR_ERROR = "EXECUTOR_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CANCELED = "CANCELED"
    CANCELING = "CANCELING"

    def is_terminal(self) -> bool:
        return self in {
            TesState.COMPLETE,
            TesState.EXECUTOR_ERROR,
            TesState.SYSTEM_ERROR,
            TesState.CANCELED,
        }

    def is_failure(self) -> bool:
        return self in {
            TesState.EXECUTOR_ERROR,
            TesState.SYSTEM_ERROR,
            TesState.CANCELED,
        }


class TesInput(BaseModel):
    url: str | None = None
    path: str
    type: FileType = FileType.FILE
    content: str | None = None
    name: str | None = None
    description: str | None = None

    model_config = {"extra": "ignore"}


class TesOutput(BaseModel):
    url: str
    path: str
    type: FileType = FileType.FILE
    name: str | None = None
    description: str | None = None

    model_config = {"extra": "ignore"}


class TesResources(BaseModel):
    cpu_cores: int | None = None
    ram_gb: float | None = None
    disk_gb: float | None = None
    preemptible: bool | None = None
    zones: list[str] | None = None
    backend_parameters: dict[str, str] | None = None
    backend_parameters_strict: bool | None = None

    model_config = {"extra": "ignore"}


class TesExecutor(BaseModel):
    image: str
    command: list[str]
    workdir: str | None = None
    stdin: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    env: dict[str, str] | None = None
    ignore_error: bool | None = None

    model_config = {"extra": "ignore"}


class TesExecutorLog(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None

    model_config = {"extra": "ignore"}


class TesTaskLog(BaseModel):
    logs: list[TesExecutorLog] = Field(default_factory=list)
    metadata: dict[str, str] | None = None
    start_time: str | None = None
    end_time: str | None = None
    outputs: list[dict[str, Any]] | None = None
    system_logs: list[str] | None = None

    model_config = {"extra": "ignore"}


class TesTask(BaseModel):
    """Represents a GA4GH TES task — used both for submission and API responses."""

    id: str | None = None
    state: TesState | None = None
    name: str | None = None
    description: str | None = None
    inputs: list[TesInput] = Field(default_factory=list)
    outputs: list[TesOutput] = Field(default_factory=list)
    resources: TesResources | None = None
    executors: list[TesExecutor] = Field(default_factory=list)
    volumes: list[str] | None = None
    tags: dict[str, str] | None = None
    logs: list[TesTaskLog] | None = None
    creation_time: str | None = None

    model_config = {"extra": "ignore"}

    # ------------------------------------------------------------------ #
    # Builder helpers — return self for chaining                           #
    # ------------------------------------------------------------------ #

    def add_input(self, path: str, url: str | None = None, **kwargs: Any) -> "TesTask":
        self.inputs.append(TesInput(path=path, url=url, **kwargs))
        return self

    def add_output(self, path: str, url: str, **kwargs: Any) -> "TesTask":
        self.outputs.append(TesOutput(path=path, url=url, **kwargs))
        return self

    def set_resources(self, **kwargs: Any) -> "TesTask":
        self.resources = TesResources(**kwargs)
        return self

    def add_executor(self, image: str, command: list[str], **kwargs: Any) -> "TesTask":
        self.executors.append(TesExecutor(image=image, command=command, **kwargs))
        return self

    def set_project_tag(self, project: str) -> "TesTask":
        if self.tags is None:
            self.tags = {}
        self.tags["project"] = project
        return self

    def set_tres_tag(self, tres: str) -> "TesTask":
        if self.tags is None:
            self.tags = {}
        self.tags["tres"] = tres
        return self

    def submission_dict(self) -> dict[str, Any]:
        """Return only the fields relevant for task submission (no server-assigned fields)."""
        exclude = {"id", "state", "logs", "creation_time"}
        return self.model_dump(exclude=exclude, exclude_none=True)

    def submission_json(self, *, indent: int | None = 2) -> str:
        """Return the submission payload as a JSON string."""
        exclude = {"id", "state", "logs", "creation_time"}
        return self.model_dump_json(exclude=exclude, exclude_none=True, indent=indent)
