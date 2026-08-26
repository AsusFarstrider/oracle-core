from __future__ import annotations

import ast
from pathlib import Path


SERVER_PACKAGE = Path(__file__).resolve().parents[1] / "server" / "oracle_app"


def _module_name(path: Path) -> str:
    relative = path.relative_to(SERVER_PACKAGE)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("oracle_app", *parts))


def _production_import_graph() -> dict[str, set[str]]:
    paths = sorted(SERVER_PACKAGE.rglob("*.py"))
    modules = {_module_name(path): path for path in paths}
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    keep = len(package) - (node.level - 1)
                    base_parts = package[:keep]
                    if node.module:
                        base_parts.extend(node.module.split("."))
                        base = ".".join(base_parts)
                        candidates.append(base)
                        candidates.extend(f"{base}.{alias.name}" for alias in node.names)
                    else:
                        candidates.extend(".".join((*base_parts, alias.name)) for alias in node.names)
                elif node.module:
                    candidates.append(node.module)
                    candidates.extend(f"{node.module}.{alias.name}" for alias in node.names)
            graph[module].update(candidate for candidate in candidates if candidate in modules)
    return graph


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in graph[node]:
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for module in graph:
        if module not in indices:
            visit(module)
    return sorted(components)


def test_production_import_graph_is_acyclic() -> None:
    assert _strongly_connected_components(_production_import_graph()) == []
