from __future__ import annotations

from typing import Any

from evolution.reflection import ReflectionEngine
from services.potential_scanner import PotentialStore, normalize_agent_type, utc_iso


class PromptEvolutionEngine:
    def __init__(self, store: PotentialStore):
        self.store = store
        self.reflection = ReflectionEngine(store)

    def trigger(self, agent_type: str, *, trigger: str = "manual") -> dict[str, Any]:
        agent = normalize_agent_type(agent_type)
        current = self.current(agent)
        version = int(current.get("version") or 0) + 1
        changes = self.reflection.reflect(agent, trigger=trigger)
        changes["version"] = version
        changes["evolved_at"] = utc_iso()
        self.store.set_memory(agent, f"evolution_v{version}", changes, category="long")
        self.store.set_memory(agent, "current_evolution", changes, category="long")
        self.store.add_evolution_log(agent, version, changes, trigger)
        return changes

    def current(self, agent_type: str) -> dict[str, Any]:
        memory = self.store.get_memory(normalize_agent_type(agent_type))
        item = memory.get("current_evolution") or {}
        value = item.get("value") if isinstance(item, dict) else {}
        return value if isinstance(value, dict) else {}

    def logs(self, agent_type: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.evolution_logs(agent_type, limit=limit)
