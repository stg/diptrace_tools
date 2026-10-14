from __future__ import annotations

# Intended use:
# Query abstraction for normalized structural tree nodes.

from typing import Any, Callable


def _node_type(node: dict[str, Any]) -> str:
    t = node.get("type")
    return str(t) if isinstance(t, str) else ""


def _node_props(node: dict[str, Any]) -> dict[str, Any]:
    p = node.get("props")
    return p if isinstance(p, dict) else {}


def _node_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    ch = node.get("children")
    return [c for c in ch if isinstance(c, dict)] if isinstance(ch, list) else []


class NodeQuery:
    def __init__(self, node: dict[str, Any], root: dict[str, Any] | None = None):
        self._node = node
        self._root = root if isinstance(root, dict) else node

    @property
    def node(self) -> dict[str, Any]:
        return self._node

    @property
    def type(self) -> str:
        return _node_type(self._node)

    @property
    def props(self) -> dict[str, Any]:
        return _node_props(self._node)

    def text(self, key: str) -> str:
        return str(self.props.get(key, "")).strip()

    def scalar(self, key: str, default: Any = None) -> Any:
        v = self.props.get(key, default)
        if isinstance(v, dict) and "value" in v:
            return v.get("value", default)
        return v

    def texts(self, key: str) -> list[str]:
        vals = self.props.get(key, [])
        if not isinstance(vals, list):
            return []
        out: list[str] = []
        for v in vals:
            s = str(v).strip()
            if s != "":
                out.append(s)
        return out

    def children(self, type_name: str | None = None) -> list["NodeQuery"]:
        out = [NodeQuery(c, self._root) for c in _node_children(self._node)]
        if type_name is None:
            return out
        return [n for n in out if n.type == type_name]

    def child(self, type_name: str) -> "NodeQuery | None":
        for n in self.children(type_name):
            return n
        return None

    def find(self, type_name: str) -> "NodeQuery | None":
        matches = self.children(type_name)
        if not matches:
            return None
        matches.sort(key=lambda n: n.offset())
        return matches[0]

    def find_all(self, type_name: str, *, sort_by_offset: bool = False) -> list["NodeQuery"]:
        out = self.children(type_name)
        if sort_by_offset:
            out.sort(key=lambda n: n.offset())
        return out

    def descendants(self, type_name: str | None = None) -> list["NodeQuery"]:
        out: list[NodeQuery] = []
        stack = self.children()
        while stack:
            n = stack.pop()
            if type_name is None or n.type == type_name:
                out.append(n)
            stack.extend(n.children())
        return out

    def offset(self) -> int:
        try:
            return int(self._node.get("_internal", {}).get("offset", -1))
        except (TypeError, ValueError):
            return -1


class TreeQuery(NodeQuery):
    def __init__(self, root: dict[str, Any]):
        super().__init__(root, root=root)

    def memo(self, key: str, build_fn: Callable[[], Any]) -> Any:
        if key in self._root:
            return self._root[key]
        v = build_fn()
        self._root[key] = v
        return v
