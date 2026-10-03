"""Filesystem-only resolution of external Python and Cython declarations."""

from __future__ import annotations

import ast
import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .cython import parse_cython
from .types import callable_return, source_name, union


def _site_packages(venv: Path) -> list[Path]:
    candidates = [venv / "Lib" / "site-packages"]
    for directory in ("lib", "lib64"):
        candidates.extend(sorted((venv / directory).glob("python*/site-packages")))
    return [path for path in candidates if path.is_dir()]


def add_stub_options(parser) -> None:
    """Share stub configuration between the command-line and GUI launchers."""
    parser.add_argument("--use-stubs", action="store_true", help="resolve external imports from .pyi/.pxd stubs")
    parser.add_argument(
        "--stub-path", action="append", default=[], type=Path, metavar="PATH",
        help="stub directory or file to search first (repeatable; enables stubs)",
    )
    parser.add_argument("--venv", type=Path, help="virtual environment containing dependency stubs (enables stubs)")


@dataclass
class _Module:
    name: str
    path: Path
    symbols: dict[str, str] = field(default_factory=dict)
    exports: set[str] = field(default_factory=set)


class StubResolver:
    def __init__(
        self, filename: str, *, stub_paths: Iterable[str | Path] = (), venv: str | Path | None = None,
    ) -> None:
        self.roots = [Path(path).resolve() for path in stub_paths]
        for path in self.roots:
            if not path.is_dir() and not (path.is_file() and path.suffix in {".pyi", ".pxd"}):
                raise ValueError(f"Stub path must be a directory or .pyi/.pxd file: {path}")
        source_directory = Path(filename).resolve().parent if not filename.startswith("<") else Path.cwd()
        self.package = ""
        package_directory = source_directory
        while any((package_directory / ("__init__" + suffix)).is_file() for suffix in (".py", ".pyi", ".pxd")):
            self.package = package_directory.name + ("." + self.package if self.package else "")
            package_directory = package_directory.parent
        self.roots.extend([source_directory, package_directory])
        environment = Path(venv).resolve() if venv is not None else None
        if environment is not None:
            if not environment.is_dir():
                raise ValueError(f"Virtual environment not found: {environment}")
            sites = _site_packages(environment)
            if not sites:
                raise ValueError(f"No site-packages directory found in virtual environment: {environment}")
            self.roots.extend(sites)
        else:
            for parent in (source_directory, *source_directory.parents):
                sites = _site_packages(parent / ".venv")
                if sites:
                    self.roots.extend(sites)
                    break
            self.roots.extend(Path(path).resolve() for path in sys.path if path)
        self.roots = list(dict.fromkeys(self.roots))
        self.symbols: dict[str, str] = {}
        self.files: dict[str, str] = {}
        self.errors: list[str] = []
        self._modules: dict[str, _Module | None] = {}

    def _find(self, module: str) -> Path | None:
        parts = module.split(".")
        if not parts or not all(part.isidentifier() for part in parts):
            return None
        for root in self.roots:
            if root.is_file():
                name = root.parent.name if root.stem == "__init__" else root.stem
                if module == name:
                    return root
                continue
            # Stub-only distributions are installed as e.g. requests-stubs.
            for base in (root.joinpath(parts[0] + "-stubs", *parts[1:]), root.joinpath(*parts)):
                for suffix in (".pyi", ".pxd"):
                    for path in (base / ("__init__" + suffix), base.with_suffix(suffix)):
                        if path.is_file():
                            return path
        return None

    @staticmethod
    def _statements(body: list[ast.stmt]):
        for node in body:
            if isinstance(node, ast.If):
                yield from StubResolver._statements(node.body)
                yield from StubResolver._statements(node.orelse)
            else:
                yield node

    def _annotation(self, node: ast.AST | None, bindings: dict[str, str], owner: str = "") -> str:
        if node is None:
            return "Unknown"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return node.value

        class Qualify(ast.NodeTransformer):
            def visit_Attribute(self, attribute: ast.Attribute):
                if attribute.attr == "Self" and owner:
                    return ast.copy_location(ast.Name(id=owner, ctx=ast.Load()), attribute)
                return self.generic_visit(attribute)

            def visit_Name(self, name: ast.Name):
                if name.id == "Self" and owner:
                    return ast.copy_location(ast.Name(id=owner, ctx=ast.Load()), name)
                binding = bindings.get(name.id, "")
                if binding.startswith(("type[", "Module[")):
                    qualified = binding[binding.index("[") + 1:-1]
                    return ast.copy_location(ast.Name(id=qualified, ctx=ast.Load()), name)
                return name

        return source_name(Qualify().visit(copy.deepcopy(node)))

    def _signature(self, node, bindings: dict[str, str], owner: str = "") -> str:
        parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments = [self._annotation(argument.annotation, bindings, owner) for argument in parameters]
        if owner and parameters and parameters[0].arg in {"self", "cls"}:
            arguments = arguments[1:]
        if node.args.vararg or node.args.kwarg:
            arguments.append("...")
        returns = self._annotation(node.returns, bindings, owner)
        return f"Callable[[{', '.join(arguments)}], {returns}]"

    def _record(self, symbols: dict[str, str], name: str, value: str) -> None:
        previous = symbols.get(name)
        if previous and previous.startswith("Callable[") and value.startswith("Callable["):
            # No argument matching: represent every overload's possible return.
            returns = union([callable_return(previous), callable_return(value)], widen_literals=False)
            value = f"Callable[..., {returns}]"
        symbols[name] = value

    def _load(self, name: str) -> _Module | None:
        if name in self._modules:
            return self._modules[name]
        path = self._find(name)
        if path is None:
            self._modules[name] = None
            return None
        module = _Module(name, path)
        self._modules[name] = module  # Cyclic re-exports can refer to this placeholder.
        self.files[name] = str(path)
        try:
            source = path.read_text(encoding="utf-8-sig")
            tree = parse_cython(source, str(path)) if path.suffix == ".pxd" else ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            self.errors.append(f"{path}: unable to read stub: {exc}")
            return module
        package = name if path.stem == "__init__" else name.rpartition(".")[0]
        body = list(self._statements(tree.body))
        bindings: dict[str, str] = {}
        imported: set[str] = set()
        reexports: set[str] = set()
        explicit_all: set[str] | None = None
        classes = [node for node in body if isinstance(node, ast.ClassDef)]
        # Make forward references to classes visible before reading signatures.
        for node in classes:
            bindings[node.name] = f"type[{name}.{node.name}]"
        module.symbols.update(bindings)
        module.exports.update(bindings)
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                values = self.bind_import(node, package=package)
                bindings.update(values)
                imported.update(values)
                reexports.update(alias.asname for alias in node.names if alias.asname == alias.name)
                if any(alias.name == "*" for alias in node.names):
                    reexports.update(values)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            ):
                try:
                    names = ast.literal_eval(node.value)
                    if isinstance(names, (list, tuple)) and all(isinstance(item, str) for item in names):
                        explicit_all = set(names)
                except (ValueError, TypeError):
                    pass
        module.symbols.update(bindings)
        for node in body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                annotation = self._annotation(node.annotation, bindings)
                if annotation.rsplit(".", 1)[-1] == "TypeAlias" and node.value is not None:
                    annotation = f"type[{self._annotation(node.value, bindings)}]"
                module.symbols[node.target.id] = annotation
                bindings[node.target.id] = annotation
            elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Subscript, ast.BinOp)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        value = f"type[{self._annotation(node.value, bindings)}]"
                        module.symbols[target.id] = bindings[target.id] = value
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._record(module.symbols, node.name, self._signature(node, bindings))
        for node in classes:
            owner = f"{name}.{node.name}"
            for member in self._statements(node.body):
                key = f"{owner}.{getattr(member, 'name', '')}"
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    properties = {source_name(decorator).rsplit(".", 1)[-1] for decorator in member.decorator_list}
                    if properties.intersection({"setter", "deleter"}):
                        continue
                    value = self._annotation(member.returns, bindings, owner) if "property" in properties else self._signature(member, bindings, owner)
                    self._record(self.symbols, key, value)
                elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                    value = self._annotation(member.annotation, bindings, owner)
                    self.symbols[f"{owner}.{member.target.id}"] = value
            self.symbols[owner] = f"type[{owner}]"
        # Copy inherited members after all direct members are indexed.
        for _ in classes:
            for node in classes:
                owner = f"{name}.{node.name}"
                for base in node.bases:
                    base_name = self._annotation(base, bindings)
                    for key, value in list(self.symbols.items()):
                        if key.startswith(base_name + "."):
                            self.symbols.setdefault(owner + key[len(base_name):], value)
        module.exports = explicit_all if explicit_all is not None else {
            symbol for symbol in module.symbols
            if not symbol.startswith("_") and (symbol not in imported or symbol in reexports or path.suffix == ".pxd")
        }
        for symbol, value in module.symbols.items():
            if symbol in module.exports:
                self.symbols[f"{name}.{symbol}"] = value
        self._register_module(name)
        return module

    def _register_module(self, name: str) -> None:
        parts = name.split(".")
        for length in range(1, len(parts) + 1):
            prefix = ".".join(parts[:length])
            self.symbols[prefix] = f"Module[{prefix}]"

    def bind_import(self, node: ast.Import | ast.ImportFrom, *, package: str | None = None) -> dict[str, str]:
        bindings: dict[str, str] = {}
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._load(alias.name)
                self._register_module(alias.name)
                module = alias.name if alias.asname else alias.name.split(".")[0]
                bindings[alias.asname or module] = f"Module[{module}]"
            return bindings
        module_name = node.module or ""
        if node.level:
            parts = (self.package if package is None else package).split(".")
            if not parts[0] or node.level > len(parts):
                return bindings
            module_name = ".".join(parts[:len(parts) - node.level + 1] + ([module_name] if module_name else []))
        module = self._load(module_name)
        for alias in node.names:
            if alias.name == "*":
                if module:
                    bindings.update({name: module.symbols[name] for name in module.exports if name in module.symbols})
                continue
            value = module.symbols.get(alias.name) if module and alias.name in module.exports else None
            if value is None:
                child = f"{module_name}.{alias.name}"
                if self._load(child) is not None:
                    self._register_module(child)
                    value = f"Module[{child}]"
            bindings[alias.asname or alias.name] = value or "Unknown"
        return bindings
