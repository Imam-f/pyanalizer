"""AST-based analysis engine.

The analyzer deliberately stays local and conservative: it never imports or executes
the file being inspected.  Its report is evidence, not a replacement for a full
type checker.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .model import (
    AnalysisReport,
    CaptureReport,
    ClassReport,
    DictReport,
    FunctionReport,
    PropReport,
    RefinementReport,
    TypeReport,
)
from .types import ExpressionInferer, promote_literal, source_name, union


MUTATING_METHODS = {
    "add", "append", "clear", "discard", "extend", "insert", "pop",
    "popitem", "remove", "reverse", "setdefault", "sort", "update",
}
BUILTIN_NAMES = set(dir(builtins))


@dataclass
class _TypeFacts:
    observed: list[str] = field(default_factory=list)
    locations: list[int] = field(default_factory=list)
    declared: str | None = None
    writes: int = 0
    mutable: bool = False

    def add(self, type_name: str, lineno: int, *, write: bool = True) -> None:
        if write:
            self.writes += 1
            if self.writes > 1:
                self.mutable = True
        if type_name not in self.observed:
            self.observed.append(type_name)
        if lineno not in self.locations:
            self.locations.append(lineno)

    def report(self) -> TypeReport:
        inferred = self.declared or union(self.observed)
        return TypeReport(
            inferred=inferred,
            observed=self.observed.copy(),
            locations=self.locations.copy(),
            mutability="mutable" if self.mutable else "const",
        )


@dataclass
class _ScopeResult:
    variables: dict[str, TypeReport]
    dicts: dict[str, DictReport]
    props: list[PropReport]
    functions: list[FunctionReport]
    classes: list[ClassReport]
    symbols: dict[str, str]


def analyze_file(path: str | Path) -> AnalysisReport:
    file_path = Path(path)
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = file_path.read_text()
    return analyze_source(source, filename=str(file_path))


def analyze_source(source: str, filename: str = "<string>") -> AnalysisReport:
    try:
        tree = ast.parse(source, filename=filename, type_comments=True)
    except SyntaxError as exc:
        location = f"{filename}:{exc.lineno}:{exc.offset}"
        return AnalysisReport(filename=filename, errors=[f"{location}: {exc.msg}"])

    engine = _Analyzer(filename)
    result = engine.analyze_scope(tree.body, "", {})
    report = AnalysisReport(
        filename=filename,
        variables=result.variables,
        dicts=result.dicts,
        props=result.props,
        functions=result.functions,
        classes=result.classes,
    )
    engine.apply_exception_effects()
    engine.annotate_module_variables(report.variables)
    return report


class _Analyzer:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self._class_nodes: dict[str, ast.ClassDef] = {}
        self._class_reports: dict[str, ClassReport] = {}
        self._class_method_attributes: dict[tuple[str, str], dict[str, TypeReport]] = {}
        self._props: dict[str, PropReport] = {}
        self._function_nodes: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self._function_owners: dict[str, str | None] = {}
        self._function_reports: dict[str, FunctionReport] = {}
        self._analyzing_classes: set[str] = set()

    def analyze_scope(
        self,
        body: list[ast.stmt],
        prefix: str,
        inherited_symbols: dict[str, str],
        *,
        instance_attributes: dict[str, _TypeFacts] | None = None,
    ) -> _ScopeResult:
        facts: dict[str, _TypeFacts] = {}
        dicts: dict[str, DictReport] = {}
        props: list[PropReport] = []
        functions: list[FunctionReport] = []
        classes: list[ClassReport] = []
        symbols = inherited_symbols.copy()

        # Definitions are visible throughout the scope for useful callable inference.
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols[statement.name] = self._callable_signature(statement, symbols)
                prop = self._register_prop(statement, prefix)
                if prop is not None:
                    props.append(prop)
            elif isinstance(statement, ast.ClassDef):
                symbols[statement.name] = f"type[{statement.name}]"
                qualified = f"{prefix}.{statement.name}" if prefix else statement.name
                self._class_nodes[qualified] = statement
                self._class_nodes.setdefault(statement.name, statement)

        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = self.analyze_function(statement, prefix, symbols)
                if not self._is_prop(statement):
                    functions.append(function)
                symbols[statement.name] = function.signature
            elif isinstance(statement, ast.ClassDef):
                classes.append(self.analyze_class(statement, prefix, symbols))
            else:
                self._statement(statement, facts, dicts, symbols, instance_attributes)

        return _ScopeResult(
            variables={name: item.report() for name, item in facts.items()},
            dicts=dicts,
            props=props,
            functions=functions,
            classes=classes,
            symbols=symbols,
        )

    def analyze_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        prefix: str,
        inherited_symbols: dict[str, str],
        *,
        instance_attributes: dict[str, _TypeFacts] | None = None,
        owner_class: str | None = None,
    ) -> FunctionReport:
        qualified = f"{prefix}.{node.name}" if prefix else node.name
        self._function_nodes[qualified] = node
        self._function_owners[qualified] = owner_class
        parameters = self._parameters(node, inherited_symbols)
        if owner_class and node.args.args:
            first = node.args.args[0].arg
            if parameters.get(first) == "Unknown" and first in {"self", "cls"}:
                parameters[first] = owner_class if first == "self" else f"type[{owner_class}]"
        local_symbols = inherited_symbols | parameters
        result = self.analyze_scope(
            node.body,
            qualified,
            local_symbols,
            instance_attributes=instance_attributes,
        )
        super_calls = self._traverse_super_calls(node, owner_class, instance_attributes)
        if self._is_prop(node) and parameters:
            # A guarantee wraps the declaration in an identity transform.
            returns = next(iter(parameters.values()))
        else:
            returns = self._annotation_type(node.returns) if node.returns else self._infer_returns(
                node, result.symbols, owner_class=owner_class
            )
        captures = self._captures_owned_by(node, qualified)
        mutated_roots = _scope_mutated_roots(node)
        for name in mutated_roots:
            if name in result.variables:
                result.variables[name].mutability = "mutable"
        parameter_variables = self._parameter_variable_reports(
            node, parameters, result.variables, captures, mutated_roots
        )
        self._apply_capture_state(result.variables, captures)
        report = FunctionReport(
            name=node.name,
            qualified_name=qualified,
            signature=f"Callable[[{', '.join(parameters.values())}], {returns}]",
            async_=isinstance(node, ast.AsyncFunctionDef),
            parameters=parameters,
            parameter_variables=parameter_variables,
            returns=returns,
            variables=result.variables,
            dicts=result.dicts,
            captures=captures,
            super_calls=super_calls,
            refinements=self._find_refinements(node, result.symbols, owner_class),
            props=result.props,
            nested_functions=result.functions,
        )
        self._function_reports[qualified] = report
        return report

    def analyze_class(
        self,
        node: ast.ClassDef,
        prefix: str,
        inherited_symbols: dict[str, str],
    ) -> ClassReport:
        qualified = f"{prefix}.{node.name}" if prefix else node.name
        cached = self._class_reports.get(qualified) or self._class_reports.get(node.name)
        if cached is not None:
            return cached
        class_facts: dict[str, _TypeFacts] = {}
        class_dicts: dict[str, DictReport] = {}
        class_symbols = inherited_symbols.copy()
        instance_facts: dict[str, _TypeFacts] = {}
        declared_methods: list[FunctionReport] = []
        initializer_attributes: dict[str, TypeReport] = {}
        dynamic_attribute_facts: dict[str, _TypeFacts] = {}

        if qualified in self._analyzing_classes:
            return ClassReport(
                name=node.name,
                qualified_name=qualified,
                bases=[source_name(base) for base in node.bases],
            )
        self._analyzing_classes.add(qualified)

        # Resolve same-file bases first so their method effects are available to super().
        base_reports: list[ClassReport] = []
        for base in node.bases:
            base_node = self._class_nodes.get(self._base_name(base))
            if base_node is not None:
                base_reports.append(self.analyze_class(base_node, "", inherited_symbols))

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                class_symbols[item.name] = self._callable_signature(item, class_symbols)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_attributes: dict[str, _TypeFacts] = {}
                method = self.analyze_function(
                    item,
                    qualified,
                    class_symbols,
                    instance_attributes=method_attributes,
                    owner_class=qualified,
                )
                self._merge_facts(instance_facts, method_attributes)
                method_attribute_reports = {
                    name: facts.report() for name, facts in method_attributes.items()
                }
                self._class_method_attributes[(qualified, item.name)] = method_attribute_reports
                self._class_method_attributes[(node.name, item.name)] = method_attribute_reports
                if item.name == "__init__":
                    initializer_attributes = self._copy_type_reports(method_attribute_reports)
                else:
                    self._merge_reports(dynamic_attribute_facts, method_attribute_reports)
                declared_methods.append(method)
                class_symbols[item.name] = method.signature
            elif not isinstance(item, ast.ClassDef):
                self._statement(item, class_facts, class_dicts, class_symbols, None)

        class_body_attributes = {name: item.report() for name, item in class_facts.items()}
        declared_instance_attributes = {name: item.report() for name, item in instance_facts.items()}
        dynamic_attributes = {name: item.report() for name, item in dynamic_attribute_facts.items()}
        inherited_class_variables = self._flatten_inherited_attributes(base_reports, "class_variables")
        inherited_instance_attributes = self._flatten_inherited_attributes(base_reports, "instance_attributes")
        declared_method_names = {method.name for method in declared_methods}
        inherited_methods = {
            qualified_name: signature
            for qualified_name, signature in self._flatten_inherited_methods(base_reports).items()
            if qualified_name.rsplit(".", 1)[-1] not in declared_method_names
        }
        class_variables = self._copy_type_reports(inherited_class_variables)
        class_variables.update(self._copy_type_reports(class_body_attributes))
        instance_attributes = self._copy_type_reports(inherited_instance_attributes)
        instance_attributes.update(self._copy_type_reports(declared_instance_attributes))
        methods_by_name: dict[str, FunctionReport] = {}
        for base in base_reports:
            for method in base.methods:
                methods_by_name.setdefault(method.name, method)
        for method in declared_methods:
            methods_by_name[method.name] = method

        report = ClassReport(
            name=node.name,
            qualified_name=qualified,
            bases=[source_name(base) for base in node.bases],
            class_body_attributes=class_body_attributes,
            initializer_attributes=initializer_attributes,
            dynamic_attributes=dynamic_attributes,
            inherited_class_variables=inherited_class_variables,
            inherited_instance_attributes=inherited_instance_attributes,
            inherited_methods=inherited_methods,
            class_variables=class_variables,
            instance_attributes=instance_attributes,
            methods=list(methods_by_name.values()),
        )
        self._annotate_class_variable_sharing(node, qualified, report)
        self._class_reports[qualified] = report
        self._class_reports.setdefault(node.name, report)
        self._analyzing_classes.discard(qualified)
        return report

    @staticmethod
    def _copy_type_reports(source: dict[str, TypeReport]) -> dict[str, TypeReport]:
        return {
            name: TypeReport(
                inferred=value.inferred,
                observed=value.observed.copy(),
                locations=value.locations.copy(),
                mutability=value.mutability,
                sharing=value.sharing,
                shared_with=value.shared_with.copy(),
            )
            for name, value in source.items()
        }

    def _flatten_inherited_attributes(
        self,
        base_reports: list[ClassReport],
        attribute: str,
    ) -> dict[str, TypeReport]:
        flattened: dict[str, TypeReport] = {}
        for base in base_reports:
            for name, value in getattr(base, attribute).items():
                flattened.setdefault(name, self._copy_type_reports({name: value})[name])
        return flattened

    @staticmethod
    def _flatten_inherited_methods(base_reports: list[ClassReport]) -> dict[str, str]:
        flattened: dict[str, str] = {}
        seen_names: set[str] = set()
        for base in base_reports:
            for method in base.methods:
                if method.name in seen_names:
                    continue
                seen_names.add(method.name)
                flattened[method.qualified_name] = method.signature
        return flattened

    def _statement(
        self,
        node: ast.stmt,
        facts: dict[str, _TypeFacts],
        dicts: dict[str, DictReport],
        symbols: dict[str, str],
        instance_attributes: dict[str, _TypeFacts] | None,
    ) -> None:
        inferer = ExpressionInferer(symbols, self._props)
        self._mark_direct_mutations(node, facts, instance_attributes)
        if isinstance(node, ast.Assign):
            inferred = inferer.infer(node.value)
            for target in node.targets:
                self._assign_target(target, inferred, node.lineno, facts, symbols, instance_attributes)
                self._mark_target_mutated(target, facts, instance_attributes)
                self._record_dict(target, node.value, dicts, inferer)
            return
        if isinstance(node, ast.AnnAssign):
            annotation = self._annotation_type(node.annotation)
            inferred = inferer.infer(node.value) if node.value else annotation
            self._assign_target(
                node.target, inferred, node.lineno, facts, symbols, instance_attributes,
                declared=annotation,
            )
            self._mark_target_mutated(node.target, facts, instance_attributes)
            if node.value:
                self._record_dict(node.target, node.value, dicts, inferer)
            return
        if isinstance(node, ast.AugAssign):
            previous = inferer.infer(node.target)
            inferred = ExpressionInferer(symbols, self._props).infer(ast.BinOp(node.target, node.op, node.value))
            self._assign_target(node.target, inferred or previous, node.lineno, facts, symbols, instance_attributes)
            self._mark_target_mutated(node.target, facts, instance_attributes)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            iterable = inferer.infer(node.iter)
            element = self._iterable_element(iterable)
            self._assign_target(node.target, element, node.lineno, facts, symbols, instance_attributes)
            for child in [*node.body, *node.orelse]:
                self._statement(child, facts, dicts, symbols, instance_attributes)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    self._assign_target(item.optional_vars, "Unknown", node.lineno, facts, symbols, instance_attributes)
            for child in node.body:
                self._statement(child, facts, dicts, symbols, instance_attributes)
            return
        if isinstance(node, ast.If):
            for child in [*node.body, *node.orelse]:
                self._statement(child, facts, dicts, symbols, instance_attributes)
            return
        if isinstance(node, (ast.While, ast.Try, ast.TryStar)):
            groups: list[ast.stmt] = [*node.body, *node.orelse]
            groups.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                if handler.name:
                    self._assign_target(ast.Name(handler.name), "Exception", handler.lineno, facts, symbols, instance_attributes)
                groups.extend(handler.body)
            for child in groups:
                self._statement(child, facts, dicts, symbols, instance_attributes)
            return
        if isinstance(node, ast.Match):
            for case in node.cases:
                for child in case.body:
                    self._statement(child, facts, dicts, symbols, instance_attributes)
            return
        if isinstance(node, ast.Delete):
            return
        # Detect d["key"] = value even when buried in constructs not modeled above.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt) and not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._statement(child, facts, dicts, symbols, instance_attributes)

    def _mark_direct_mutations(
        self,
        node: ast.stmt,
        facts: dict[str, _TypeFacts],
        instance_attributes: dict[str, _TypeFacts] | None,
    ) -> None:
        for item in _direct_effect_nodes(node):
            if (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and item.func.attr in MUTATING_METHODS
            ):
                root = _mutation_root(item.func.value)
                if root in facts:
                    facts[root].mutable = True
                attribute = _instance_attribute_root(item.func.value)
                if instance_attributes is not None and attribute is not None:
                    instance_attributes.setdefault(attribute, _TypeFacts()).mutable = True

    @staticmethod
    def _mark_target_mutated(
        target: ast.AST,
        facts: dict[str, _TypeFacts],
        instance_attributes: dict[str, _TypeFacts] | None,
    ) -> None:
        if isinstance(target, ast.Name):
            return
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in {"self", "cls"}
        ):
            return
        root = _mutation_root(target)
        if root in facts:
            facts[root].mutable = True
        attribute = _instance_attribute_root(target)
        if instance_attributes is not None and attribute is not None:
            instance_attributes.setdefault(attribute, _TypeFacts()).mutable = True

    def _assign_target(
        self,
        target: ast.AST,
        type_name: str,
        lineno: int,
        facts: dict[str, _TypeFacts],
        symbols: dict[str, str],
        instance_attributes: dict[str, _TypeFacts] | None,
        declared: str | None = None,
    ) -> None:
        if isinstance(target, ast.Name):
            item = facts.setdefault(target.id, _TypeFacts())
            item.declared = item.declared or declared
            item.add(type_name, lineno)
            symbols[target.id] = item.declared or union(item.observed)
        elif isinstance(target, (ast.Tuple, ast.List)):
            parts = self._destructure(type_name, len(target.elts))
            for element, part in zip(target.elts, parts, strict=False):
                self._assign_target(element, part, lineno, facts, symbols, instance_attributes)
        elif (
            instance_attributes is not None
            and isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in {"self", "cls"}
        ):
            item = instance_attributes.setdefault(target.attr, _TypeFacts())
            item.declared = item.declared or declared
            item.add(type_name, lineno)

    def _record_dict(
        self,
        target: ast.AST,
        value: ast.AST,
        dicts: dict[str, DictReport],
        inferer: ExpressionInferer,
    ) -> None:
        if isinstance(target, ast.Name) and isinstance(value, ast.Dict):
            report = dicts.setdefault(target.id, DictReport(name=target.id))
            for key, item in zip(value.keys, value.values, strict=True):
                if key is None:
                    report.dynamic_keys.append("**unpacked")
                elif isinstance(key, ast.Constant):
                    self._add_dict_key(report, repr(key.value), promote_literal(inferer.infer(item)))
                else:
                    rendered = source_name(key)
                    if rendered not in report.dynamic_keys:
                        report.dynamic_keys.append(rendered)
        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            report = dicts.setdefault(target.value.id, DictReport(name=target.value.id))
            if isinstance(target.slice, ast.Constant):
                self._add_dict_key(report, repr(target.slice.value), promote_literal(inferer.infer(value)))
            else:
                rendered = source_name(target.slice)
                if rendered not in report.dynamic_keys:
                    report.dynamic_keys.append(rendered)

    @staticmethod
    def _add_dict_key(report: DictReport, key: str, value_type: str) -> None:
        previous = report.keys.get(key)
        report.keys[key] = union([previous, value_type]) if previous else value_type

    def _parameters(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        symbols: dict[str, str],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        positional = [*node.args.posonlyargs, *node.args.args]
        defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
        inferer = ExpressionInferer(symbols, self._props)
        for argument, default in zip(positional, defaults, strict=True):
            result[argument.arg] = self._annotation_type(argument.annotation) if argument.annotation else (
                promote_literal(inferer.infer(default)) if default is not None else "Unknown"
            )
        if node.args.vararg:
            annotation = self._annotation_type(node.args.vararg.annotation) if node.args.vararg.annotation else "Unknown"
            result[f"*{node.args.vararg.arg}"] = f"tuple[{annotation}, ...]"
        for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
            result[argument.arg] = self._annotation_type(argument.annotation) if argument.annotation else (
                promote_literal(inferer.infer(default)) if default is not None else "Unknown"
            )
        if node.args.kwarg:
            annotation = self._annotation_type(node.args.kwarg.annotation) if node.args.kwarg.annotation else "Unknown"
            result[f"**{node.args.kwarg.arg}"] = f"dict[str, {annotation}]"
        return result

    def _callable_signature(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        symbols: dict[str, str],
        inferred_return: str | None = None,
    ) -> str:
        parameters = self._parameters(node, symbols)
        if self._is_prop(node) and parameters:
            returns = next(iter(parameters.values()))
        else:
            returns = self._annotation_type(node.returns) if node.returns else (inferred_return or "Unknown")
        return f"Callable[[{', '.join(parameters.values())}], {returns}]"

    def _infer_returns(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        symbols: dict[str, str],
        *,
        owner_class: str | None = None,
    ) -> str:
        values: list[str] = []
        inferer = ExpressionInferer(symbols, self._props)
        for statement in _walk_without_nested(node):
            if isinstance(statement, ast.Return):
                super_method = _super_method_name(statement.value)
                resolved = self._resolve_super_method(owner_class, super_method) if super_method else None
                values.append(resolved[1].returns if resolved else inferer.infer(statement.value))
        if not values:
            return "None"
        return union(values)

    def _traverse_super_calls(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        owner_class: str | None,
        instance_attributes: dict[str, _TypeFacts] | None,
    ) -> list[str]:
        if owner_class is None:
            return []
        traversed: list[str] = []
        for item in _walk_without_nested(node):
            method_name = _super_method_name(item)
            if method_name is None:
                continue
            resolved = self._resolve_super_method(owner_class, method_name)
            if resolved is None:
                label = f"unresolved.{method_name}"
            else:
                base_name, _ = resolved
                label = f"{base_name}.{method_name}"
                if instance_attributes is not None:
                    inherited = self._class_method_attributes.get((base_name, method_name), {})
                    self._merge_reports(instance_attributes, inherited)
            if label not in traversed:
                traversed.append(label)
        return traversed

    def _resolve_super_method(
        self,
        owner_class: str | None,
        method_name: str | None,
        seen: set[str] | None = None,
    ) -> tuple[str, FunctionReport] | None:
        if owner_class is None or method_name is None:
            return None
        seen = seen or set()
        if owner_class in seen:
            return None
        seen.add(owner_class)
        node = self._class_nodes.get(owner_class) or self._class_nodes.get(owner_class.rsplit(".", 1)[-1])
        if node is None:
            return None
        for base in node.bases:
            base_name = self._base_name(base)
            report = self._class_reports.get(base_name)
            if report is None:
                base_node = self._class_nodes.get(base_name)
                if base_node is not None:
                    report = self.analyze_class(base_node, "", {})
            if report is None:
                continue
            method = next((candidate for candidate in report.methods if candidate.name == method_name), None)
            if method is not None:
                return report.qualified_name, method
            inherited = self._resolve_super_method(report.qualified_name, method_name, seen)
            if inherited is not None:
                return inherited
        return None

    def _register_prop(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        prefix: str,
    ) -> PropReport | None:
        if not self._is_prop(node):
            return None
        positional = [*node.args.posonlyargs, *node.args.args]
        if not positional:
            return None
        parameter = positional[0]
        parameter_type = source_name(parameter.annotation).strip("'\"") if parameter.annotation else "Unknown"
        predicate = self._guarantee_predicate(node)
        qualified = f"{prefix}.{node.name}" if prefix else node.name
        report = PropReport(
            name=node.name,
            qualified_name=qualified,
            parameter=parameter.arg,
            parameter_type=parameter_type,
            returns=parameter_type,
            predicate=predicate,
            constructed_type=f"Refined[{parameter_type}, {node.name}]",
            line=node.lineno,
        )
        self._props[qualified] = report
        self._props.setdefault(node.name, report)
        return report

    @staticmethod
    def _is_prop(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return any(
            source_name(decorator).rsplit(".", 1)[-1] in {"guarantee", "prop"}
            for decorator in node.decorator_list
        )

    @staticmethod
    def _guarantee_predicate(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
        assertions = [item for item in _walk_without_nested(node) if isinstance(item, ast.Assert)]
        if len(assertions) == 1:
            return source_name(assertions[0].test)
        returns = [item for item in _walk_without_nested(node) if isinstance(item, ast.Return)]
        annotation = source_name(node.returns).strip("'\"") if node.returns else "Unknown"
        if annotation == "bool" and len(returns) == 1 and returns[0].value is not None:
            return source_name(returns[0].value)
        return None

    def _find_refinements(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        symbols: dict[str, str],
        owner_class: str | None,
    ) -> list[RefinementReport]:
        # Guarantees attach evidence to returned values; unlike the old
        # predicate API, they do not make boolean branches meaningful.
        del node, symbols, owner_class
        return []

    def _annotation_type(self, annotation: ast.AST) -> str:
        rendered = source_name(annotation).strip("'\"")
        prop = self._props.get(rendered)
        if prop is not None:
            return prop.constructed_type
        if isinstance(annotation, ast.Subscript):
            constructor = source_name(annotation.value)
            if constructor.rsplit(".", 1)[-1] == "Annotated":
                arguments = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
                if not arguments:
                    return rendered
                guarantees: set[str] = set()
                for metadata in arguments[1:]:
                    name = source_name(metadata)
                    guarantee = self._props.get(name) or self._props.get(name.rsplit(".", 1)[-1])
                    if guarantee is not None:
                        guarantees.add(guarantee.name)
                if len(guarantees) == 1:
                    return f"Refined[{source_name(arguments[0])}, {guarantees.pop()}]"
                if guarantees:
                    return f"Refined[{source_name(arguments[0])}, {{{', '.join(sorted(guarantees))}}}]"
            prop = self._props.get(constructor)
            if prop is not None:
                return f"Refined[{source_name(annotation.slice)}, {prop.name}]"
        return rendered

    def apply_exception_effects(self) -> None:
        """Compute and attach checked exception effects to every local function."""
        effects: dict[str, set[str]] = {name: set() for name in self._function_nodes}
        # Re-evaluating bodies reaches a fixed point for forward calls, recursion,
        # and mutually recursive groups without depending on source order.
        for _ in range(max(1, len(effects) + 1)):
            updated: dict[str, set[str]] = {}
            for name, node in self._function_nodes.items():
                updated[name] = self._exceptions_in_statements(
                    node.body,
                    effects,
                    caller=name,
                    owner_class=self._function_owners.get(name),
                )
            if updated == effects:
                break
            effects = updated

        for name, report in self._function_reports.items():
            exceptions = sorted(effects.get(name, set()))
            report.exceptions = exceptions
            report.signature = self._signature_with_exceptions(report.signature, exceptions)

        seen_props: set[int] = set()
        for prop in self._props.values():
            if id(prop) in seen_props:
                continue
            seen_props.add(id(prop))
            prop.exceptions = sorted(effects.get(prop.qualified_name, set()))

    def _exceptions_in_statements(
        self,
        statements: list[ast.stmt],
        effects: dict[str, set[str]],
        *,
        caller: str,
        owner_class: str | None,
        reraised: set[str] | None = None,
    ) -> set[str]:
        result: set[str] = set()
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(statement, (ast.Try, ast.TryStar)):
                result.update(
                    self._exceptions_in_try(statement, effects, caller=caller, owner_class=owner_class)
                )
                continue

            if isinstance(statement, ast.Raise):
                if statement.exc is None:
                    result.update(reraised or {"UnknownException"})
                else:
                    result.add(_raised_exception_name(statement.exc))
            elif isinstance(statement, ast.Assert):
                result.add("AssertionError")

            for effect_node in _direct_effect_nodes(statement):
                if isinstance(effect_node, ast.Call):
                    result.update(self._join_exception_effects(effect_node, effects))
                    callee = self._resolve_call(effect_node, caller, owner_class)
                    if callee is not None:
                        result.update(effects.get(callee, set()))

            for child_body in _child_statement_bodies(statement):
                result.update(
                    self._exceptions_in_statements(
                        child_body,
                        effects,
                        caller=caller,
                        owner_class=owner_class,
                        reraised=reraised,
                    )
                )
        return result

    def _join_exception_effects(
        self,
        call: ast.Call,
        effects: dict[str, set[str]],
    ) -> set[str]:
        if source_name(call.func).rsplit(".", 1)[-1] != "join" or len(call.args) < 2:
            return set()
        guarantees = call.args[1]
        if not isinstance(guarantees, (ast.Tuple, ast.List, ast.Set)):
            return set()
        result: set[str] = set()
        for item in guarantees.elts:
            name = source_name(item)
            prop = self._props.get(name) or self._props.get(name.rsplit(".", 1)[-1])
            if prop is not None:
                result.update(effects.get(prop.qualified_name, set()))
        return result

    def _exceptions_in_try(
        self,
        statement: ast.Try | ast.TryStar,
        effects: dict[str, set[str]],
        *,
        caller: str,
        owner_class: str | None,
    ) -> set[str]:
        raised = self._exceptions_in_statements(
            statement.body, effects, caller=caller, owner_class=owner_class
        )
        unhandled = set(raised)
        handler_effects: set[str] = set()
        for handler in statement.handlers:
            catches = _caught_exception_names(handler.type)
            matched = {
                exception for exception in unhandled if self._exception_is_caught(exception, catches)
            }
            unhandled.difference_update(matched)
            handler_effects.update(
                self._exceptions_in_statements(
                    handler.body,
                    effects,
                    caller=caller,
                    owner_class=owner_class,
                    reraised=matched,
                )
            )

        result = unhandled | handler_effects
        result.update(
            self._exceptions_in_statements(
                statement.orelse, effects, caller=caller, owner_class=owner_class
            )
        )
        result.update(
            self._exceptions_in_statements(
                statement.finalbody, effects, caller=caller, owner_class=owner_class
            )
        )
        return result

    def _resolve_call(
        self,
        call: ast.Call,
        caller: str,
        owner_class: str | None,
    ) -> str | None:
        function = call.func
        if isinstance(function, ast.Name):
            scope = caller.rsplit(".", 1)[0] if "." in caller else ""
            candidates = [f"{caller}.{function.id}"]
            if scope:
                candidates.append(f"{scope}.{function.id}")
            candidates.append(function.id)
            resolved = next((candidate for candidate in candidates if candidate in self._function_nodes), None)
            if resolved is not None:
                return resolved
            return self._resolve_constructor(function.id)

        if not isinstance(function, ast.Attribute):
            return None
        if isinstance(function.value, ast.Name):
            if function.value.id in {"self", "cls"} and owner_class:
                candidate = f"{owner_class}.{function.attr}"
                if candidate in self._function_nodes:
                    return candidate
                inherited = self._resolve_super_function(owner_class, function.attr)
                if inherited is not None:
                    return inherited
            candidate = f"{function.value.id}.{function.attr}"
            if candidate in self._function_nodes:
                return candidate
            parameter_class = self._parameter_class(caller, function.value.id)
            if parameter_class:
                candidate = f"{parameter_class}.{function.attr}"
                if candidate in self._function_nodes:
                    return candidate
        if (
            isinstance(function.value, ast.Call)
            and isinstance(function.value.func, ast.Name)
            and function.value.func.id == "super"
        ):
            return self._resolve_super_function(owner_class, function.attr)
        rendered = source_name(function)
        return rendered if rendered in self._function_nodes else None

    def _resolve_constructor(self, class_name: str, seen: set[str] | None = None) -> str | None:
        node = self._class_nodes.get(class_name)
        if node is None:
            return None
        seen = seen or set()
        if class_name in seen:
            return None
        seen.add(class_name)
        direct = f"{class_name}.__init__"
        if direct in self._function_nodes:
            return direct
        for base in node.bases:
            inherited = self._resolve_constructor(self._base_name(base), seen)
            if inherited is not None:
                return inherited
        return None

    def _exception_is_caught(
        self,
        exception: str,
        catches: set[str] | None,
        seen: set[str] | None = None,
    ) -> bool:
        if _builtin_exception_is_caught(exception, catches):
            return True
        if catches is None:
            return True
        seen = seen or set()
        if exception in seen:
            return False
        seen.add(exception)
        node = self._class_nodes.get(exception)
        if node is None:
            return False
        for base in node.bases:
            base_name = self._base_name(base)
            if base_name in catches or self._exception_is_caught(base_name, catches, seen):
                return True
        return False

    def _parameter_class(self, caller: str, parameter_name: str) -> str | None:
        node = self._function_nodes.get(caller)
        if node is None:
            return None
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        argument = next((item for item in arguments if item.arg == parameter_name), None)
        if argument is None or argument.annotation is None:
            return None
        rendered = source_name(argument.annotation).strip("'\"")
        return rendered.rsplit(".", 1)[-1] if rendered.isidentifier() else None

    def _resolve_super_function(
        self,
        owner_class: str | None,
        method_name: str,
        seen: set[str] | None = None,
    ) -> str | None:
        if owner_class is None:
            return None
        seen = seen or set()
        if owner_class in seen:
            return None
        seen.add(owner_class)
        node = self._class_nodes.get(owner_class) or self._class_nodes.get(owner_class.rsplit(".", 1)[-1])
        if node is None:
            return None
        for base in node.bases:
            base_name = self._base_name(base)
            candidate = f"{base_name}.{method_name}"
            if candidate in self._function_nodes:
                return candidate
            inherited = self._resolve_super_function(base_name, method_name, seen)
            if inherited is not None:
                return inherited
        return None

    @staticmethod
    def _signature_with_exceptions(signature: str, exceptions: list[str]) -> str:
        base = signature.split(" raises ", 1)[0]
        return f"{base} raises {' | '.join(exceptions)}" if exceptions else base

    @staticmethod
    def _parameter_variable_reports(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parameters: dict[str, str],
        local_variables: dict[str, TypeReport],
        captures: list[CaptureReport],
        mutated_roots: set[str],
    ) -> dict[str, TypeReport]:
        argument_lines = {
            argument.arg: argument.lineno
            for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        }
        if node.args.vararg:
            argument_lines[node.args.vararg.arg] = node.args.vararg.lineno
        if node.args.kwarg:
            argument_lines[node.args.kwarg.arg] = node.args.kwarg.lineno
        capture_map = {capture.name: capture for capture in captures}
        reports: dict[str, TypeReport] = {}
        for displayed_name, type_name in parameters.items():
            name = displayed_name.lstrip("*")
            capture = capture_map.get(name)
            reports[displayed_name] = TypeReport(
                inferred=type_name,
                observed=[type_name],
                locations=[argument_lines.get(name, node.lineno)],
                mutability="mutable" if name in local_variables or name in mutated_roots else "const",
                sharing=capture.kind if capture else "single",
                shared_with=capture.captured_by.copy() if capture else [],
            )
            if capture and capture.mutable:
                reports[displayed_name].mutability = "mutable"
        return reports

    @staticmethod
    def _apply_capture_state(
        variables: dict[str, TypeReport],
        captures: list[CaptureReport],
    ) -> None:
        for capture in captures:
            variable = variables.get(capture.name)
            if variable is None:
                continue
            variable.sharing = capture.kind
            variable.shared_with = capture.captured_by.copy()
            if capture.mutable:
                variable.mutability = "mutable"

    def annotate_module_variables(self, variables: dict[str, TypeReport]) -> None:
        """Attach cross-function sharing and mutation to module bindings."""
        users: dict[str, set[str]] = {name: set() for name in variables}
        mutators: set[str] = set()
        for qualified, node in self._function_nodes.items():
            local_names = _function_locals(node)
            loads, mutations = _function_usage(node)
            for name in variables:
                if name in local_names:
                    continue
                if name in loads or name in mutations:
                    users[name].add(qualified)
                if name in mutations:
                    mutators.add(name)
        for name, variable in variables.items():
            shared_with = sorted(users[name])
            variable.shared_with = shared_with
            variable.sharing = "shared" if len(shared_with) > 1 else "single"
            if name in mutators:
                variable.mutability = "mutable"

    @staticmethod
    def _annotate_class_variable_sharing(
        node: ast.ClassDef,
        qualified: str,
        report: ClassReport,
    ) -> None:
        instance_users: dict[str, set[str]] = {
            name: set(variable.shared_with)
            for name, variable in report.instance_attributes.items()
        }
        class_users: dict[str, set[str]] = {
            name: set(variable.shared_with)
            for name, variable in report.class_variables.items()
        }
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method_name = f"{qualified}.{item.name}"
            for visited in _walk_without_nested(item):
                if not isinstance(visited, ast.Attribute) or not isinstance(visited.value, ast.Name):
                    continue
                if visited.value.id == "self" and visited.attr in instance_users:
                    instance_users[visited.attr].add(method_name)
                if visited.value.id in {"self", "cls", node.name} and visited.attr in class_users:
                    class_users[visited.attr].add(method_name)
        for name, variable in report.instance_attributes.items():
            variable.shared_with = sorted(instance_users[name])
            variable.sharing = "shared" if len(variable.shared_with) > 1 else "single"
        for name, variable in report.class_variables.items():
            variable.shared_with = sorted(class_users[name])
            variable.sharing = "shared" if len(variable.shared_with) > 1 else "single"

    @staticmethod
    def _base_name(base: ast.AST) -> str:
        name = source_name(base)
        return name.split("[", 1)[0].rsplit(".", 1)[-1]

    @staticmethod
    def _merge_facts(target: dict[str, _TypeFacts], source: dict[str, _TypeFacts]) -> None:
        for name, incoming in source.items():
            current = target.setdefault(name, _TypeFacts())
            current.declared = current.declared or incoming.declared
            current.writes += incoming.writes
            current.mutable = current.mutable or incoming.mutable or current.writes > 1
            for observed in incoming.observed:
                for line in incoming.locations or [0]:
                    current.add(observed, line, write=False)

    @staticmethod
    def _merge_reports(target: dict[str, _TypeFacts], source: dict[str, TypeReport]) -> None:
        for name, incoming in source.items():
            current = target.setdefault(name, _TypeFacts())
            incoming_writes = max(1, len(incoming.locations))
            current.writes += incoming_writes
            current.mutable = (
                current.mutable
                or incoming.mutability == "mutable"
                or current.writes > 1
            )
            for observed in incoming.observed or [incoming.inferred]:
                for line in incoming.locations or [0]:
                    current.add(observed, line, write=False)

    def _captures_owned_by(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        qualified: str,
    ) -> list[CaptureReport]:
        owner_locals = _function_locals(node)
        captured: dict[str, list[tuple[str, bool]]] = {}
        for nested, nested_name in _nested_functions(node, qualified):
            nested_locals = _function_locals(nested)
            loads, mutations = _function_usage(nested)
            for name in sorted((loads | mutations) - nested_locals - BUILTIN_NAMES):
                if name in owner_locals:
                    captured.setdefault(name, []).append((nested_name, name in mutations))
        return [
            CaptureReport(
                name=name,
                kind="shared" if len({owner for owner, _ in users}) > 1 else "single",
                mutable=any(mutable for _, mutable in users),
                captured_by=sorted({owner for owner, _ in users}),
            )
            for name, users in sorted(captured.items())
        ]

    @staticmethod
    def _iterable_element(type_name: str) -> str:
        if "[" in type_name and type_name.endswith("]"):
            return type_name[type_name.find("[") + 1 : -1].split(",", 1)[0]
        return "Unknown"

    @staticmethod
    def _destructure(type_name: str, count: int) -> list[str]:
        if type_name.startswith("tuple[") and type_name.endswith("]"):
            items = [item.strip() for item in type_name[6:-1].split(",")]
            if len(items) == count:
                return items
        return ["Unknown"] * count


def _walk_without_nested(node: ast.AST) -> Iterable[ast.AST]:
    """Walk a function body without entering nested function or class scopes."""
    stack = list(reversed(getattr(node, "body", [])))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(item) for item in target.elts)) if target.elts else set()
    return set()


def _function_locals(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {
        argument.arg
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    }
    if node.args.vararg:
        names.add(node.args.vararg.arg)
    if node.args.kwarg:
        names.add(node.args.kwarg.arg)
    globals_: set[str] = set()
    nonlocals: set[str] = set()
    for item in _walk_without_nested(node):
        if isinstance(item, ast.Global):
            globals_.update(item.names)
        elif isinstance(item, ast.Nonlocal):
            nonlocals.update(item.names)
        elif isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                names.update(_target_names(target))
        elif isinstance(item, (ast.For, ast.AsyncFor)):
            names.update(_target_names(item.target))
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(item.name)
        elif isinstance(item, ast.NamedExpr):
            names.update(_target_names(item.target))
    return names - globals_ - nonlocals


def _function_usage(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[set[str], set[str]]:
    loads: set[str] = set()
    mutations: set[str] = set()
    nonlocals: set[str] = set()
    for item in _walk_without_nested(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
            loads.add(item.id)
        elif isinstance(item, ast.Nonlocal):
            nonlocals.update(item.names)
        elif isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                root = _mutation_root(target)
                if root:
                    mutations.add(root)
        elif (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr in MUTATING_METHODS
        ):
            root = _mutation_root(item.func.value)
            if root:
                mutations.add(root)
    mutations.update(nonlocals)
    return loads, mutations


def _mutation_root(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _instance_attribute_root(node: ast.AST) -> str | None:
    """Return the first ``self``/``cls`` attribute owning a mutation target."""
    current = node
    while isinstance(current, ast.Subscript):
        current = current.value
    while isinstance(current, ast.Attribute):
        if isinstance(current.value, ast.Name) and current.value.id in {"self", "cls"}:
            return current.attr
        current = current.value
        while isinstance(current, ast.Subscript):
            current = current.value
    return None


def _scope_mutated_roots(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Find bindings whose values or binding slots are mutated in this scope."""
    mutations: set[str] = set()
    for item in _walk_without_nested(node):
        if isinstance(item, ast.AugAssign):
            root = _mutation_root(item.target)
            if root:
                mutations.add(root)
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    continue
                root = _mutation_root(target)
                if root:
                    mutations.add(root)
        elif isinstance(item, ast.Delete):
            for target in item.targets:
                root = _mutation_root(target)
                if root:
                    mutations.add(root)
        elif (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr in MUTATING_METHODS
        ):
            root = _mutation_root(item.func.value)
            if root:
                mutations.add(root)
    return mutations


def _super_method_name(node: ast.AST | None) -> str | None:
    """Return the method name for a direct ``super(...).method(...)`` call."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    receiver = node.func.value
    if (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == "super"
    ):
        return node.func.attr
    return None


def _condition_prop_calls(
    node: ast.AST,
    props: dict[str, PropReport],
) -> list[tuple[ast.Call, PropReport, bool | None, bool | None]]:
    """Find ``@prop`` calls and map condition truth to checker truth.

    Each tuple contains ``(call, prop, state_when_condition_true,
    state_when_condition_false)``. ``None`` means the compound boolean does not
    make that checker's state certain on that branch.
    """
    if isinstance(node, ast.Call) and node.args:
        name = source_name(node.func)
        prop = props.get(name) or props.get(name.rsplit(".", 1)[-1])
        if prop is not None:
            return [(node, prop, True, False)]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return [
            (call, prop, false_state, true_state)
            for call, prop, true_state, false_state in _condition_prop_calls(node.operand, props)
        ]
    if isinstance(node, ast.BoolOp):
        children = [item for value in node.values for item in _condition_prop_calls(value, props)]
        if isinstance(node.op, ast.And):
            return [(call, prop, true_state, None) for call, prop, true_state, _ in children]
        return [(call, prop, None, false_state) for call, prop, _, false_state in children]
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
        and isinstance(node.left, ast.Call)
        and isinstance(node.comparators[0], ast.Constant)
        and isinstance(node.comparators[0].value, bool)
        and isinstance(node.ops[0], (ast.Eq, ast.Is, ast.NotEq, ast.IsNot))
    ):
        direct = _condition_prop_calls(node.left, props)
        if not direct:
            return []
        expected = node.comparators[0].value
        if isinstance(node.ops[0], (ast.NotEq, ast.IsNot)):
            expected = not expected
        return [(call, prop, expected, not expected) for call, prop, _, _ in direct]
    return []


def _direct_effect_nodes(statement: ast.stmt) -> Iterable[ast.AST]:
    """Walk expressions owned by one statement without entering child statements."""
    stack: list[ast.AST] = [statement]
    while stack:
        current = stack.pop()
        if current is not statement and isinstance(current, ast.stmt):
            continue
        if current is not statement and isinstance(
            current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            continue
        if current is not statement:
            yield current
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _child_statement_bodies(statement: ast.stmt) -> Iterable[list[ast.stmt]]:
    """Yield control-flow bodies; try statements are handled separately."""
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return
    for _, value in ast.iter_fields(statement):
        if isinstance(value, list):
            body = [item for item in value if isinstance(item, ast.stmt)]
            if body:
                yield body
            # match_case and except-handler-like containers own statement lists.
            for item in value:
                if isinstance(item, ast.match_case):
                    yield item.body


def _raised_exception_name(exception: ast.AST) -> str:
    target = exception.func if isinstance(exception, ast.Call) else exception
    name = source_name(target).rsplit(".", 1)[-1]
    if name.endswith(("Error", "Exception")) or name in {
        "StopIteration", "StopAsyncIteration", "SystemExit", "KeyboardInterrupt", "GeneratorExit",
    }:
        return name
    return "UnknownException"


def _caught_exception_names(exception: ast.AST | None) -> set[str] | None:
    if exception is None:
        return None
    if isinstance(exception, ast.Tuple):
        return {source_name(item).rsplit(".", 1)[-1] for item in exception.elts}
    return {source_name(exception).rsplit(".", 1)[-1]}


def _builtin_exception_is_caught(exception: str, catches: set[str] | None) -> bool:
    if catches is None:
        return True
    exception_type = getattr(builtins, exception, None)
    for caught in catches:
        caught_type = getattr(builtins, caught, None)
        if (
            isinstance(exception_type, type)
            and isinstance(caught_type, type)
            and issubclass(exception_type, caught_type)
        ):
            return True
        if exception == caught:
            return True
    return False


def _nested_functions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    prefix: str,
) -> Iterable[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    def visit(statements: list[ast.stmt], parent: str) -> Iterable[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{parent}.{statement.name}"
                yield statement, qualified
                yield from visit(statement.body, qualified)
            elif not isinstance(statement, ast.ClassDef):
                child_bodies: list[list[ast.stmt]] = []
                for _, value in ast.iter_fields(statement):
                    if isinstance(value, list) and value and all(isinstance(x, ast.stmt) for x in value):
                        child_bodies.append(value)
                for body in child_bodies:
                    yield from visit(body, parent)

    yield from visit(node.body, prefix)
