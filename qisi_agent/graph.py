from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class KnowledgeGraph:
    edges: dict[str, set[tuple[str, str]]] = field(default_factory=dict)

    def add(self, subject: str, relation: str, target: str) -> None:
        self.edges.setdefault(subject, set()).add((relation, target))

    def expand(self, entities: list[str], max_hops: int = 1) -> list[tuple[str, str, str]]:
        found: list[tuple[str, str, str]] = []
        frontier = set(entities)
        for _ in range(max_hops):
            next_frontier: set[str] = set()
            for subject in frontier:
                for relation, target in self.edges.get(subject, set()):
                    found.append((subject, relation, target))
                    next_frontier.add(target)
            frontier = next_frontier
        return found
