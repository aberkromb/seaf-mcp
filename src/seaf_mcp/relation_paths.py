"""Multi-step relation paths between sections.

Built on the $rels map SeafTools.relations() extracts from $manifestschema();
no external link_graph.json. BFS finds the shortest routes (up to 10, at most
5 steps) and explains which fields to JOIN through — it never executes the
JOIN or returns entity data.
"""

from collections import deque

MAX_PATHS = 10
MAX_STEPS = 5
MAX_EXPANSIONS = 20_000  # safety cap against exponential shortest-path counts


def build_section_graph(relmap: dict[str, list[dict[str, str]]]) -> dict[str, list[tuple]]:
    """{section: [(field, neighbor, direction)]} with both directions:
    "out" follows the field as stored, "in" traverses it backwards."""
    graph: dict[str, list[tuple]] = {}
    for src, rels in relmap.items():
        for rel in rels:
            field, target = rel["field"], rel["target"]
            graph.setdefault(src, []).append((field, target, "out"))
            graph.setdefault(target, []).append((field, src, "in"))
    return graph


def find_paths(graph: dict[str, list[tuple]], start: str, end: str,
               via: list[str] | None = None,
               max_paths: int = MAX_PATHS, max_steps: int = MAX_STEPS,
               max_expansions: int = MAX_EXPANSIONS) -> tuple[list[list[dict]], bool]:
    """BFS shortest routes start→end. Returns (paths, truncated).

    Intermediate sections are restricted to `via` when given (start/end are
    never filtered); a section never repeats within a route, so loops and
    cycles are excluded. Enumeration stops as soon as max_paths routes of the
    shortest length are collected (the count may grow exponentially).
    """
    allowed = set(via or [])
    paths: list[list[dict]] = []
    truncated = False
    shortest_len: int | None = None
    expansions = 0
    queue = deque([(start, [], frozenset({start}))])
    while queue:
        section, steps, visited = queue.popleft()
        if shortest_len is not None and len(steps) >= shortest_len:
            break  # дальше только более длинные маршруты
        if len(steps) >= max_steps:
            continue
        for field, neighbor, direction in sorted(set(graph.get(section, []))):
            expansions += 1
            if expansions > max_expansions:
                return paths, True
            if neighbor in visited:
                continue
            step = {"from_section": section, "field": field,
                    "to_section": neighbor, "direction": direction}
            if neighbor == end:
                paths.append(steps + [step])
                shortest_len = len(steps) + 1
                if len(paths) >= max_paths:
                    return paths, True
                continue
            if allowed and neighbor not in allowed:
                continue
            queue.append((neighbor, steps + [step], visited | {neighbor}))
    return paths, truncated
