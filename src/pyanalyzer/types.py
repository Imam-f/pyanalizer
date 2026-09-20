"""Type rendering and conservative expression inference."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import PropReport


NUMERIC_RANK = {"bool": 0, "int": 1, "float": 2, "complex": 3}


def source_name(node: ast.AST | None) -> str:
    if node is None:
        return "Unknown"
    try:
        return ast.unparse(node)
    except (ValueError, AttributeError):
        return node.__class__.__name__


def literal_type(value: object) -> str:
    if value is None:
        return "None"
    if value is Ellipsis:
        return "Literal[...]"
    if isinstance(value, bool):
        return f"Literal[{value}]"
    if isinstance(value, int):
        return f"Literal[{value}]"
    if isinstance(value, float):
        return f"Literal[{value!r}]"
    if isinstance(value, complex):
        return f"Literal[{value!r}]"
    if isinstance(value, str):
        return f"Literal[{value!r}]"
    if isinstance(value, bytes):
        return f"Literal[{value!r}]"
    return type(value).__name__


def promote_literal(type_name: str) -> str:
    if not type_name.startswith("Literal["):
        return type_name
    value = type_name[8:-1]
    if value in {"True", "False"}:
        return "bool"
    if value == "...":
        return "ellipsis"
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return "Unknown"
    # Comma-separated Literal parameters form a type expression, not one tuple literal.
    if isinstance(parsed, tuple) and "," in value:
        return type_name
    return type(parsed).__name__


def union(types: Iterable[str], *, widen_literals: bool = True) -> str:
    values: list[str] = []
    for item in types:
        if widen_literals:
            item = promote_literal(item)
        if item.startswith("Union[") and item.endswith("]"):
            candidates = [part.strip() for part in item[6:-1].split(",")]
        else:
            candidates = [item]
        for candidate in candidates:
            if candidate not in values:
                values.append(candidate)
    if not values:
        return "Unknown"
    if len(values) == 1:
        return values[0]
    return f"Union[{', '.join(values)}]"


def numeric_promote(left: str, right: str) -> str | None:
    left = promote_literal(left)
    right = promote_literal(right)
    if left in NUMERIC_RANK and right in NUMERIC_RANK:
        rank = max(NUMERIC_RANK[left], NUMERIC_RANK[right])
        return next(name for name, value in NUMERIC_RANK.items() if value == rank)
    return None


class ExpressionInferer:
    def __init__(self, symbols: dict[str, str], props: dict[str, "PropReport"] | None = None) -> None:
        self.symbols = symbols
        self.props = props or {}

    def infer(self, node: ast.AST | None) -> str:
        if node is None:
            return "None"
        if isinstance(node, ast.Constant):
            return literal_type(node.value)
        if isinstance(node, ast.Name):
            if node.id in {"str", "int", "float", "complex", "bool", "bytes", "object", "None"}:
                return f"type[{node.id}]"
            return self.symbols.get(node.id, "Unknown")
        if isinstance(node, ast.List):
            return f"list[{union((self.infer(e) for e in node.elts))}]"
        if isinstance(node, ast.Set):
            return f"set[{union((self.infer(e) for e in node.elts))}]"
        if isinstance(node, ast.Tuple):
            return f"tuple[{', '.join(self.infer(e) for e in node.elts)}]"
        if isinstance(node, ast.Dict):
            key_types = union((self.infer(k) for k in node.keys if k is not None))
            value_types = union((self.infer(v) for v in node.values))
            return f"dict[{key_types}, {value_types}]"
        if isinstance(node, ast.Lambda):
            args = [self._annotation(a.annotation) for a in node.args.args]
            return f"Callable[[{', '.join(args)}], {self.infer(node.body)}]"
        if isinstance(node, ast.IfExp):
            return union([self.infer(node.body), self.infer(node.orelse)])
        if isinstance(node, ast.NamedExpr):
            return self.infer(node.value)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return "bool"
            return promote_literal(self.infer(node.operand))
        if isinstance(node, ast.BoolOp) or isinstance(node, ast.Compare):
            return "bool"
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.BitOr) and self._is_type_expression(node):
                return source_name(node)
            left, right = self.infer(node.left), self.infer(node.right)
            promoted = numeric_promote(left, right)
            if promoted:
                return promoted
            if isinstance(node.op, ast.Add) and promote_literal(left) == promote_literal(right):
                return promote_literal(left)
            return union([left, right])
        if isinstance(node, ast.JoinedStr):
            return "str"
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            element = self.infer(node.elt)
            kind = {ast.ListComp: "list", ast.GeneratorExp: "Iterator", ast.SetComp: "set"}[type(node)]
            return f"{kind}[{promote_literal(element)}]"
        if isinstance(node, ast.DictComp):
            return f"dict[{promote_literal(self.infer(node.key))}, {promote_literal(self.infer(node.value))}]"
        if isinstance(node, ast.Await):
            return self.infer(node.value)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Attribute):
            return "Unknown"
        if isinstance(node, ast.Subscript):
            if source_name(node.value).split(".")[-1] in {
                "Annotated", "Callable", "ClassVar", "Final", "Literal", "Optional",
                "TypeAlias", "Union",
            }:
                return source_name(node)
            container = self.infer(node.value)
            if container.startswith("list[") or container.startswith("set["):
                return container[container.find("[") + 1 : -1]
            return "Unknown"
        return "Unknown"

    @staticmethod
    def _is_type_expression(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id not in {"True", "False"}
        if isinstance(node, ast.Attribute):
            return True
        if isinstance(node, ast.Subscript):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return ExpressionInferer._is_type_expression(node.left) and ExpressionInferer._is_type_expression(node.right)
        return False

    def _annotation(self, node: ast.AST | None) -> str:
        return source_name(node) if node is not None else "Unknown"

    def _call(self, node: ast.Call) -> str:
        name = source_name(node.func)
        prop = self.props.get(name) or self.props.get(name.rsplit(".", 1)[-1])
        if prop is not None and node.args:
            return f"Refined[{self.infer(node.args[0])}, {prop.name}]"
        if name.rsplit(".", 1)[-1] == "join" and node.args:
            guarantees = self._join_guarantees(node.args[1]) if len(node.args) > 1 else []
            value = self.infer(node.args[0])
            if guarantees:
                return f"Refined[{value}, {{{', '.join(guarantees)}}}]"
            return value
        constructors = {
            "str": "str", "int": "int", "float": "float", "complex": "complex",
            "bool": "bool", "bytes": "bytes", "list": "list[Unknown]",
            "dict": "dict[Unknown, Unknown]", "set": "set[Unknown]", "tuple": "tuple[Unknown]",
            "len": "int", "sum": "Unknown", "min": "Unknown", "max": "Unknown",
            "sorted": "list[Unknown]", "range": "range", "enumerate": "enumerate",
        }
        if name in constructors:
            return constructors[name]
        known = self.symbols.get(name)
        if known and known.startswith("type["):
            return name
        if known and known.startswith("Callable["):
            return known.rsplit(", ", 1)[-1].rstrip("]")
        return "Unknown"

    def _join_guarantees(self, node: ast.AST) -> list[str]:
        """Return recognized guarantees in a stable order for ``join``."""
        if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return []
        names: set[str] = set()
        for item in node.elts:
            name = source_name(item)
            prop = self.props.get(name) or self.props.get(name.rsplit(".", 1)[-1])
            if prop is not None:
                names.add(prop.name)
        return sorted(names)
