from __future__ import annotations

from typing import Any

from services.potential_scanner import PotentialStore


class TrajectoryRecorder:
    def __init__(self, store: PotentialStore):
        self.store = store

    def record(self, agent_type: str, action_type: str, *, input_data: Any = None, decision: Any = None, outcome: Any = None, session_id: str | None = None) -> int:
        return self.store.log_trajectory(agent_type, action_type, input_data=input_data, decision=decision, outcome=outcome, session_id=session_id)

    def recent(self, agent_type: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_trajectory(agent_type, limit=limit)
