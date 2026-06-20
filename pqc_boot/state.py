"""Resumable pipeline state.

The migration is a multi-stage sequence and some stages are slow (cross-compile,
deploy + reboot). We persist which stages have completed so a re-run resumes
instead of redoing work. State is a small JSON file in the workspace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

STATE_FILENAME = "state.json"


@dataclass
class PipelineState:
    path: Path
    completed: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, workspace: Path) -> "PipelineState":
        path = Path(workspace) / STATE_FILENAME
        if path.exists():
            try:
                data = json.loads(path.read_text())
                completed = list(data.get("completed", []))
            except (json.JSONDecodeError, OSError):
                completed = []
            return cls(path=path, completed=completed)
        return cls(path=path)

    def is_done(self, stage: str) -> bool:
        return stage in self.completed

    def mark_done(self, stage: str) -> None:
        if stage not in self.completed:
            self.completed.append(stage)
            self.save()

    def reset(self, stage: str | None = None) -> None:
        """Forget one stage, or the whole pipeline when stage is None."""
        if stage is None:
            self.completed = []
        elif stage in self.completed:
            self.completed.remove(stage)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"completed": self.completed}, indent=2))
