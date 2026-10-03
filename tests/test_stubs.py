from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from pyanalyzer import analyze_file, analyze_source
from pyanalyzer.cli import main
from pyanalyzer.gui import build_parser as gui_parser
from pyanalyzer.types import callable_return, union


def write(root: Path, name: str, source: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(source), encoding="utf-8")
    return path


def test_stub_resolution_is_opt_in_and_never_executes_dependency_code(tmp_path):
    stub = write(tmp_path, "dependency.pyi", '''
        def read() -> list[tuple[int, str]]: ...
        def unknown(): ...
        raise RuntimeError("never execute stub bodies")
    ''')
    write(tmp_path, "dependency.py", 'raise RuntimeError("never import dependencies")')
    source = "from dependency import read, unknown\nresult = read()\nmissing = unknown()\n"
    filename = str(tmp_path / "app.py")
    assert analyze_source(source, filename).variables["result"].inferred == "Unknown"
    report = analyze_source(source, filename, use_stubs=True)
    assert not report.errors
    assert report.variables["result"].inferred == "list[tuple[int, str]]"
    assert report.variables["missing"].inferred == "Unknown"
    assert report.stub_files == {"dependency": str(stub)}
    assert report.variables["read"].inferred == "Callable[[], list[tuple[int, str]]]"


def test_module_aliases_submodules_and_reexports(tmp_path):
    write(tmp_path, "package/__init__.pyi", "from .api import fetch as fetch\n")
    write(tmp_path, "package/api.pyi", "def fetch() -> dict[str, list[int]]: ...\n")
    report = analyze_source(dedent('''
        import package.api
        import package.api as api
        from package import fetch as read
        from package import api as submodule
        a = package.api.fetch()
        b = api.fetch()
        c = read()
        d = submodule.fetch()
    '''), stub_paths=[tmp_path])
    assert not report.errors
    assert all(report.variables[name].inferred == "dict[str, list[int]]" for name in "abcd")


def test_classes_methods_properties_and_annotated_parameters(tmp_path):
    write(tmp_path, "service.pyi", '''
        from typing import Self
        class Base:
            label: str
            def run(self) -> list[str]: ...
        class Client(Base):
            enabled: bool
            def clone(self) -> Self: ...
            @property
            def size(self) -> int: ...
            @size.setter
            def size(self, value: int) -> None: ...
        def connect() -> Client: ...
    ''')
    report = analyze_source(dedent('''
        import service as s
        from service import Client
        client = Client()
        connected = s.connect()
        names = client.run()
        label = connected.label
        size = client.size
        clone = connected.clone()
        clone_names = clone.run()
        def run(client: Client):
            return client.run()
    '''), stub_paths=[tmp_path])
    assert not report.errors
    expected = {
        "client": "service.Client", "connected": "service.Client", "names": "list[str]",
        "label": "str", "size": "int", "clone": "service.Client", "clone_names": "list[str]",
    }
    assert {name: report.variables[name].inferred for name in expected} == expected
    assert report.functions[0].returns == "list[str]"


@pytest.mark.parametrize("layout", ["Lib/site-packages", "lib/python3.12/site-packages", "lib64/python3.11/site-packages"])
def test_explicit_virtual_environment_and_stub_only_packages(tmp_path, layout):
    environment = tmp_path / "environment"
    site = environment / layout
    stub = write(site, "dependency-stubs/__init__.pyi", "def read() -> bytes: ...\n")
    write(site, "dependency/__init__.pyi", "def read() -> str: ...\n")
    report = analyze_source("import dependency\nresult = dependency.read()\n", venv=environment)
    assert not report.errors
    assert report.variables["result"].inferred == "bytes"
    assert report.stub_files == {"dependency": str(stub)}


def test_auto_discovered_project_venv_and_custom_stub_precedence(tmp_path):
    write(tmp_path, ".venv/Lib/site-packages/dependency.pyi", "def read() -> int: ...\n")
    app = write(tmp_path, "src/app.py", "from dependency import read\nresult = read()\n")
    assert analyze_file(app, use_stubs=True).variables["result"].inferred == "int"
    custom = tmp_path / "custom"
    write(custom, "dependency.pyi", "def read() -> str: ...\n")
    assert analyze_file(app, stub_paths=[custom]).variables["result"].inferred == "str"


def test_direct_stub_file_and_source_import_shadowing(tmp_path):
    stub = write(tmp_path, "dependency.pyi", "def read() -> int: ...\n")
    report = analyze_source(dedent('''
        from dependency import read
        before = read()
        read = "shadowed"
        after = read()
        dependency = "not imported"
        hidden = dependency.read()
    '''), stub_paths=[stub])
    assert report.variables["before"].inferred == "int"
    assert report.variables["after"].inferred == "Unknown"
    assert report.variables["hidden"].inferred == "Unknown"


def test_external_names_override_builtin_and_helper_inference(tmp_path):
    write(tmp_path, "dependency.pyi", '''
        def int() -> str: ...
        def join(value: str) -> bytes: ...
    ''')
    report = analyze_source(
        'from dependency import int, join\nnumber = int()\njoined = join("value")\n',
        stub_paths=[tmp_path],
    )
    assert report.variables["number"].inferred == "str"
    assert report.variables["joined"].inferred == "bytes"


def test_overloads_preserve_nested_return_types(tmp_path):
    write(tmp_path, "dependency.pyi", '''
        from typing import overload
        @overload
        def read(value: int) -> list[tuple[int, str]]: ...
        @overload
        def read(value: str) -> dict[str, int]: ...
    ''')
    report = analyze_source("from dependency import read\nresult = read(1)\n", stub_paths=[tmp_path])
    assert report.variables["result"].inferred == "Union[list[tuple[int, str]], dict[str, int]]"
    assert callable_return("Callable[[], tuple[int, str]] raises ValueError") == "tuple[int, str]"
    assert union(["Union[dict[str, int], tuple[int, str]]", "dict[str, int]"]) == "Union[dict[str, int], tuple[int, str]]"


def test_cython_cimports_use_pxd_stubs(tmp_path):
    write(tmp_path, "native.pxd", '''
        ctypedef unsigned long Size
        cdef double square(double value) noexcept
        cdef class Counter:
            cdef int value
            cpdef int increment(self, int amount=*)
    ''')
    report = analyze_source(dedent('''
        from native cimport square, Counter
        result = square(2)
        counter = Counter()
        value = counter.value
        next_value = counter.increment(1)
    '''), filename="app.pyx", stub_paths=[tmp_path])
    assert not report.errors
    assert report.variables["result"].inferred == "double"
    assert report.variables["value"].inferred == "int"
    assert report.variables["next_value"].inferred == "int"


def test_imports_inside_functions_and_source_relative_imports(tmp_path):
    write(tmp_path, "project/__init__.py", "")
    write(tmp_path, "project/dependency.pyi", "def read() -> str: ...\n")
    app = write(tmp_path, "project/app.py", '''
        def read_value():
            from .dependency import read
            return read()
    ''')
    report = analyze_file(app, use_stubs=True)
    assert not report.errors
    assert report.functions[0].returns == "str"


def test_type_aliases_and_quoted_class_returns(tmp_path):
    write(tmp_path, "dependency.pyi", '''
        from typing import TypeAlias
        def read() -> Payload: ...
        def client() -> "Client": ...
        Payload: TypeAlias = dict[str, list[int]]
        class Client:
            def read(self) -> Payload: ...
    ''')
    report = analyze_source(
        "import dependency\nresult = dependency.read()\nclient = dependency.client()\nother = client.read()\n",
        stub_paths=[tmp_path],
    )
    assert not report.errors
    assert report.variables["result"].inferred == "dict[str, list[int]]"
    assert report.variables["client"].inferred == "dependency.Client"
    assert report.variables["other"].inferred == "dict[str, list[int]]"


def test_missing_stubs_stay_unknown_and_bad_stubs_are_reported(tmp_path):
    source = "from absent_dependency import read\nresult = read()\n"
    report = analyze_source(source, stub_paths=[tmp_path])
    assert not report.errors and report.variables["result"].inferred == "Unknown"
    write(tmp_path, "broken.pyi", "def invalid(\n")
    report = analyze_source("import broken\n", stub_paths=[tmp_path])
    assert report.errors and "broken.pyi" in report.errors[0]


def test_wildcards_respect_all_and_explicit_reexports(tmp_path):
    write(tmp_path, "helpers.pyi", "def private() -> int: ...\ndef public() -> str: ...\n")
    write(tmp_path, "dependency.pyi", '''
        from helpers import private
        from helpers import public as public
        _secret: bytes
        count: int
        __all__ = ["public", "count"]
    ''')
    report = analyze_source("from dependency import *\nresult = public()\n", stub_paths=[tmp_path])
    assert report.variables["result"].inferred == "str"
    assert "private" not in report.variables and "_secret" not in report.variables
    assert report.variables["count"].inferred == "int"


def test_cyclic_stub_imports_do_not_recurse_forever(tmp_path):
    write(tmp_path, "first.pyi", "import second\ndef read() -> int: ...\n")
    write(tmp_path, "second.pyi", "import first\ndef write() -> str: ...\n")
    report = analyze_source("import first\nresult = first.read()\n", stub_paths=[tmp_path])
    assert not report.errors
    assert report.variables["result"].inferred == "int"


def test_cli_and_gui_options_and_invalid_configuration(tmp_path, capsys):
    stub = write(tmp_path, "dependency.pyi", "def read() -> int: ...\n")
    app = write(tmp_path, "app.py", "from dependency import read\nresult = read()\n")
    assert main(["--stub-path", str(stub), "--json", str(app)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["variables"]["result"]["inferred"] == "int"
    assert payload["stub_files"]["dependency"] == str(stub)
    args = gui_parser().parse_args(["--use-stubs", "--stub-path", str(tmp_path), "--venv", str(tmp_path), str(app)])
    assert args.use_stubs and args.stub_path == [tmp_path] and args.venv == tmp_path
    with pytest.raises(ValueError, match="Stub path"):
        analyze_source("", stub_paths=[tmp_path / "missing"])
    with pytest.raises(ValueError, match="Virtual environment not found"):
        analyze_source("", venv=tmp_path / "missing")
    with pytest.raises(SystemExit) as exc:
        main(["--venv", str(tmp_path), str(app)])
    assert exc.value.code == 2
