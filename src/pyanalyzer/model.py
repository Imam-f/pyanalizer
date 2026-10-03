"""Serializable report models used by the analyzer and CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TypeReport:
    inferred: str
    observed: list[str] = field(default_factory=list)
    locations: list[int] = field(default_factory=list)
    mutability: str = "const"
    sharing: str = "single"
    shared_with: list[str] = field(default_factory=list)


@dataclass
class DictReport:
    name: str
    keys: dict[str, str] = field(default_factory=dict)
    dynamic_keys: list[str] = field(default_factory=list)


@dataclass
class CaptureReport:
    name: str
    kind: str
    mutable: bool
    captured_by: list[str] = field(default_factory=list)


@dataclass
class PropReport:
    name: str
    qualified_name: str
    parameter: str
    parameter_type: str
    returns: str
    predicate: str | None = None
    constructed_type: str = "Unknown"
    exceptions: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class RefinementReport:
    variable: str
    checker: str
    condition: str
    line: int
    when_true: str | None = None
    when_false: str | None = None


@dataclass
class FunctionReport:
    name: str
    qualified_name: str
    signature: str
    async_: bool = False
    parameters: dict[str, str] = field(default_factory=dict)
    parameter_variables: dict[str, TypeReport] = field(default_factory=dict)
    returns: str = "Unknown"
    exceptions: list[str] = field(default_factory=list)
    variables: dict[str, TypeReport] = field(default_factory=dict)
    dicts: dict[str, DictReport] = field(default_factory=dict)
    captures: list[CaptureReport] = field(default_factory=list)
    super_calls: list[str] = field(default_factory=list)
    refinements: list[RefinementReport] = field(default_factory=list)
    props: list[PropReport] = field(default_factory=list)
    nested_functions: list[FunctionReport] = field(default_factory=list)


@dataclass
class ClassReport:
    name: str
    qualified_name: str
    bases: list[str] = field(default_factory=list)
    # Members declared directly on this class, grouped by how they arise.
    class_body_attributes: dict[str, TypeReport] = field(default_factory=dict)
    initializer_attributes: dict[str, TypeReport] = field(default_factory=dict)
    dynamic_attributes: dict[str, TypeReport] = field(default_factory=dict)
    # Flattened inherited members, kept separate so their origin is explicit.
    inherited_class_variables: dict[str, TypeReport] = field(default_factory=dict)
    inherited_instance_attributes: dict[str, TypeReport] = field(default_factory=dict)
    inherited_methods: dict[str, str] = field(default_factory=dict)
    # Complete views: inherited members plus members declared on this class.
    class_variables: dict[str, TypeReport] = field(default_factory=dict)
    instance_attributes: dict[str, TypeReport] = field(default_factory=dict)
    methods: list[FunctionReport] = field(default_factory=list)


@dataclass
class AnalysisReport:
    filename: str
    variables: dict[str, TypeReport] = field(default_factory=dict)
    dicts: dict[str, DictReport] = field(default_factory=dict)
    props: list[PropReport] = field(default_factory=list)
    functions: list[FunctionReport] = field(default_factory=list)
    classes: list[ClassReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stub_files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
