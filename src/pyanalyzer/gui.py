"""Small Tkinter source viewer for interactive analyzer reports."""

from __future__ import annotations

import argparse
import builtins
import io
import keyword
import token
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import END, TclError, Tk, filedialog, messagebox, ttk
import tkinter as tk

from .analyzer import analyze_source
from .model import (
    AnalysisReport,
    CaptureReport,
    ClassReport,
    DictReport,
    FunctionReport,
    PropReport,
    TypeReport,
)


@dataclass
class SymbolNode:
    """A symbol and its displayable analyzer properties."""

    name: str
    qualified_name: str
    kind: str
    type_name: str
    properties: list[tuple[str, str]] = field(default_factory=list)
    children: list[SymbolNode] = field(default_factory=list)
    line: int | None = None


def _type_node(
    name: str,
    qualified_name: str,
    kind: str,
    value: TypeReport,
    mapping: DictReport | None = None,
) -> SymbolNode:
    properties = [
        ("Name", name),
        ("Qualified name", qualified_name),
        ("Kind", kind),
        ("Inferred type", value.inferred),
        ("State", f"{value.mutability}, {value.sharing}"),
        ("Observed", ", ".join(value.observed) or "None"),
        ("Lines", ", ".join(map(str, value.locations)) or "Unknown"),
    ]
    if value.shared_with:
        properties.append(("Shared with", ", ".join(value.shared_with)))
    if mapping is not None:
        shape = ", ".join(f"{key}: {item}" for key, item in mapping.keys.items())
        properties.append(("Dictionary keys", shape or "None"))
        properties.append(("Dynamic keys", ", ".join(mapping.dynamic_keys) or "None"))
    return SymbolNode(
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        type_name=value.inferred,
        properties=properties,
        line=value.locations[0] if value.locations else None,
    )


def _dict_node(mapping: DictReport, prefix: str) -> SymbolNode:
    qualified = f"{prefix}.{mapping.name}" if prefix else mapping.name
    shape = ", ".join(f"{key}: {value}" for key, value in mapping.keys.items())
    return SymbolNode(
        name=mapping.name,
        qualified_name=qualified,
        kind="dictionary",
        type_name="dict",
        properties=[
            ("Name", mapping.name),
            ("Qualified name", qualified),
            ("Kind", "dictionary"),
            ("Keys", shape or "None"),
            ("Dynamic keys", ", ".join(mapping.dynamic_keys) or "None"),
        ],
    )


def _prop_node(value: PropReport) -> SymbolNode:
    properties = [
        ("Name", value.name),
        ("Qualified name", value.qualified_name),
        ("Kind", "guarantee"),
        ("Parameter", f"{value.parameter}: {value.parameter_type}"),
        ("Returns", value.returns),
        ("Constructs", value.constructed_type),
        ("Predicate", value.predicate or "None"),
        ("Raises", " | ".join(value.exceptions) or "None"),
        ("Line", str(value.line)),
    ]
    return SymbolNode(
        name=value.name,
        qualified_name=value.qualified_name,
        kind="guarantee",
        type_name=value.constructed_type,
        properties=properties,
        line=value.line,
    )


def _capture_node(value: CaptureReport, prefix: str) -> SymbolNode:
    qualified = f"{prefix}.{value.name}"
    return SymbolNode(
        name=value.name,
        qualified_name=qualified,
        kind="capture",
        type_name=value.kind,
        properties=[
            ("Name", value.name),
            ("Qualified name", qualified),
            ("Kind", "capture"),
            ("Sharing", value.kind),
            ("Access", "mutable" if value.mutable else "read-only"),
            ("Captured by", ", ".join(value.captured_by)),
        ],
    )


def _function_node(value: FunctionReport, kind: str = "function") -> SymbolNode:
    properties = [
        ("Name", value.name),
        ("Qualified name", value.qualified_name),
        ("Kind", "async function" if value.async_ else kind),
        ("Signature", value.signature),
        ("Returns", value.returns),
        ("Raises", " | ".join(value.exceptions) or "None"),
    ]
    if value.super_calls:
        properties.append(("Super calls", ", ".join(value.super_calls)))

    children: list[SymbolNode] = []
    for name, report in value.parameter_variables.items():
        children.append(
            _type_node(
                name,
                f"{value.qualified_name}.{name.lstrip('*')}",
                "parameter",
                report,
            )
        )
    for name, report in value.variables.items():
        children.append(
            _type_node(
                name,
                f"{value.qualified_name}.{name}",
                "local variable",
                report,
                value.dicts.get(name),
            )
        )
    for name, mapping in value.dicts.items():
        if name not in value.variables:
            children.append(_dict_node(mapping, value.qualified_name))
    children.extend(_capture_node(capture, value.qualified_name) for capture in value.captures)
    children.extend(_prop_node(prop) for prop in value.props)
    children.extend(_function_node(nested, "nested function") for nested in value.nested_functions)
    return SymbolNode(
        name=value.name,
        qualified_name=value.qualified_name,
        kind=kind,
        type_name=value.signature,
        properties=properties,
        children=children,
    )


def _class_node(value: ClassReport) -> SymbolNode:
    properties = [
        ("Name", value.name),
        ("Qualified name", value.qualified_name),
        ("Kind", "class"),
        ("Bases", ", ".join(value.bases) or "object"),
        ("Class attributes", str(len(value.class_variables))),
        ("Instance attributes", str(len(value.instance_attributes))),
        ("Methods", str(len(value.methods))),
    ]
    children: list[SymbolNode] = []
    direct_class = set(value.class_body_attributes)
    direct_instance = set(value.initializer_attributes) | set(value.dynamic_attributes)
    for name, report in value.class_variables.items():
        kind = "class attribute" if name in direct_class else "inherited class attribute"
        children.append(_type_node(name, f"{value.qualified_name}.{name}", kind, report))
    for name, report in value.instance_attributes.items():
        kind = "instance attribute" if name in direct_instance else "inherited instance attribute"
        children.append(_type_node(name, f"{value.qualified_name}.{name}", kind, report))
    children.extend(_function_node(method, "method") for method in value.methods)
    return SymbolNode(
        name=value.name,
        qualified_name=value.qualified_name,
        kind="class",
        type_name=f"type[{value.name}]",
        properties=properties,
        children=children,
    )


def build_symbol_tree(report: AnalysisReport) -> list[SymbolNode]:
    """Convert an analysis report into the hierarchy used by the sidebar."""
    nodes: list[SymbolNode] = []
    for name, value in report.variables.items():
        nodes.append(_type_node(name, name, "variable", value, report.dicts.get(name)))
    for name, mapping in report.dicts.items():
        if name not in report.variables:
            nodes.append(_dict_node(mapping, ""))
    nodes.extend(_prop_node(prop) for prop in report.props)
    nodes.extend(_function_node(function) for function in report.functions)
    nodes.extend(_class_node(class_) for class_ in report.classes)
    return nodes


class AnalyzerGUI:
    """Interactive syntax-highlighted source editor and symbol inspector."""

    _TOKEN_TAGS = ("keyword", "builtin", "string", "comment", "number", "operator", "definition")

    def __init__(self, root: Tk, path: Path | None = None) -> None:
        self.root = root
        self.path = path
        self._after_id: str | None = None
        self._hovered_item = ""
        self._hovered_code_name = ""
        self._symbols: dict[str, SymbolNode] = {}
        self._symbols_by_name: dict[str, list[SymbolNode]] = {}
        self._name_ranges: dict[str, list[tuple[str, str]]] = {}

        root.title("pyanalyzer")
        root.geometry("1160x760")
        root.minsize(760, 480)
        self._configure_style()
        self._build_ui()

        if path is not None:
            self.open_path(path)
        else:
            self._set_source(
                "class Greeting:\n"
                "    def __init__(self, name: str):\n"
                "        self.name = name\n\n"
                "    def message(self) -> str:\n"
                "        return f\"Hello, {self.name}\"\n\n"
                "greeting = Greeting(\"world\")\n"
            )

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Toolbar.TFrame", background="#18212b")
        style.configure("Sidebar.TFrame", background="#101820")
        style.configure("Title.TLabel", background="#101820", foreground="#d8e2ed", font=("Segoe UI", 10, "bold"))
        style.configure("Status.TLabel", background="#18212b", foreground="#9fb0c0", padding=(10, 5))
        style.configure("Treeview", background="#101820", fieldbackground="#101820", foreground="#d8e2ed", font=("Segoe UI", 9), rowheight=23, borderwidth=0)
        style.configure("Treeview.Heading", background="#18212b", foreground="#9fb0c0", font=("Segoe UI", 9), relief="flat")
        style.map("Treeview", background=[("selected", "#254b68")], foreground=[("selected", "#ffffff")])

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(8, 7))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Open", command=self.open_file).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Save", command=self.save_file).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Analyze", command=self.analyze).pack(side="left")
        self.file_label = ttk.Label(toolbar, text="Untitled", style="Status.TLabel")
        self.file_label.pack(side="left", padx=12)

        panes = ttk.Panedwindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True)

        editor_frame = ttk.Frame(panes)
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(1, weight=1)
        self.gutter = tk.Text(
            editor_frame,
            width=5,
            padx=7,
            pady=12,
            takefocus=False,
            borderwidth=0,
            background="#111820",
            foreground="#526779",
            font=("Cascadia Mono", 10),
            state="disabled",
            wrap="none",
        )
        self.gutter.grid(row=0, column=0, sticky="ns")
        self.editor = tk.Text(
            editor_frame,
            undo=True,
            wrap="none",
            borderwidth=0,
            padx=14,
            pady=12,
            insertbackground="#f5f7fa",
            selectbackground="#315774",
            background="#0b1117",
            foreground="#d8e2ed",
            font=("Cascadia Mono", 10),
            tabs=(32,),
        )
        self.editor.grid(row=0, column=1, sticky="nsew")
        scrollbar = ttk.Scrollbar(editor_frame, orient="vertical", command=self._scroll_editor)
        scrollbar.grid(row=0, column=2, sticky="ns")
        xscrollbar = ttk.Scrollbar(editor_frame, orient="horizontal", command=self.editor.xview)
        xscrollbar.grid(row=1, column=1, sticky="ew")
        self.editor.configure(yscrollcommand=lambda first, last: self._on_editor_scroll(scrollbar, first, last))
        self.editor.configure(xscrollcommand=xscrollbar.set)
        self._configure_editor_tags()
        self.editor.bind("<<Modified>>", self._source_changed)
        self.editor.bind("<Motion>", self._editor_hover)
        self.editor.bind("<Leave>", self._editor_leave)

        sidebar = ttk.Frame(panes, style="Sidebar.TFrame", padding=10)
        sidebar.rowconfigure(1, weight=3)
        sidebar.rowconfigure(3, weight=2)
        sidebar.columnconfigure(0, weight=1)
        ttk.Label(sidebar, text="SYMBOLS", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 7)
        )
        self.tree = ttk.Treeview(sidebar, columns=("kind", "type"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Symbol")
        self.tree.heading("kind", text="Kind")
        self.tree.heading("type", text="Type")
        self.tree.column("#0", width=150, minwidth=90)
        self.tree.column("kind", width=105, minwidth=70)
        self.tree.column("type", width=190, minwidth=100)
        self.tree.grid(row=1, column=0, sticky="nsew")
        tree_scrollbar = ttk.Scrollbar(sidebar, orient="vertical", command=self.tree.yview)
        tree_scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scrollbar.set)
        self.tree.bind("<Motion>", self._tree_hover)
        self.tree.bind("<Leave>", lambda _event: self.tree.configure(cursor=""))
        self.tree.bind("<<TreeviewSelect>>", self._tree_select)
        self.tree.bind("<Double-1>", self._tree_open)

        ttk.Label(sidebar, text="PROPERTIES", style="Title.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(14, 7)
        )
        self.details = tk.Text(
            sidebar,
            wrap="word",
            borderwidth=0,
            padx=10,
            pady=8,
            takefocus=False,
            background="#0b1117",
            foreground="#cbd6e2",
            font=("Segoe UI", 9),
            state="disabled",
        )
        self.details.tag_configure("key", foreground="#74b9e7", font=("Segoe UI", 9, "bold"))
        self.details.tag_configure("value", foreground="#d8e2ed", spacing3=7)
        self.details.grid(row=3, column=0, sticky="nsew")
        details_scrollbar = ttk.Scrollbar(sidebar, orient="vertical", command=self.details.yview)
        details_scrollbar.grid(row=3, column=1, sticky="ns")
        self.details.configure(yscrollcommand=details_scrollbar.set)

        panes.add(editor_frame, weight=3)
        panes.add(sidebar, weight=2)
        self.status = ttk.Label(self.root, text="Ready", anchor="w", style="Status.TLabel")
        self.status.pack(fill="x")

    def _configure_editor_tags(self) -> None:
        colors = {
            "keyword": "#d98bf3",
            "builtin": "#63c7da",
            "string": "#a7d78b",
            "comment": "#65798a",
            "number": "#e7ad72",
            "operator": "#8aa5bb",
            "definition": "#69b7ff",
        }
        for name, color in colors.items():
            self.editor.tag_configure(name, foreground=color)
        self.editor.tag_configure("symbol_focus", background="#403f24", foreground="#fff3a8")
        self.editor.tag_raise("symbol_focus")

    def _scroll_editor(self, *args: str) -> None:
        self.editor.yview(*args)
        self.gutter.yview(*args)

    def _on_editor_scroll(self, scrollbar: ttk.Scrollbar, first: str, last: str) -> None:
        scrollbar.set(first, last)
        self.gutter.yview_moveto(first)

    def _source_changed(self, _event: tk.Event[tk.Misc]) -> None:
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        self._update_line_numbers()
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
        self._after_id = self.root.after(350, self.analyze)

    def _set_source(self, source: str) -> None:
        self.editor.delete("1.0", END)
        self.editor.insert("1.0", source)
        self.editor.edit_modified(False)
        self._update_line_numbers()
        self.analyze()

    def _update_line_numbers(self) -> None:
        line_count = int(self.editor.index("end-1c").split(".")[0])
        numbers = "\n".join(map(str, range(1, line_count + 1)))
        self.gutter.configure(state="normal")
        self.gutter.delete("1.0", END)
        self.gutter.insert("1.0", numbers)
        self.gutter.configure(state="disabled")

    def open_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Open Python file",
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
        )
        if selected:
            self.open_path(Path(selected))

    def open_path(self, path: Path) -> None:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("Unable to open file", str(exc))
            return
        self.path = path
        self.file_label.configure(text=str(path))
        self.root.title(f"{path.name} - pyanalyzer")
        self._set_source(source)

    def save_file(self) -> None:
        if self.path is None:
            selected = filedialog.asksaveasfilename(
                title="Save Python file",
                defaultextension=".py",
                filetypes=(("Python files", "*.py"), ("All files", "*.*")),
            )
            if not selected:
                return
            self.path = Path(selected)
        try:
            self.path.write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Unable to save file", str(exc))
            return
        self.file_label.configure(text=str(self.path))
        self.root.title(f"{self.path.name} - pyanalyzer")
        self.status.configure(text=f"Saved {self.path}")

    def analyze(self) -> None:
        self._after_id = None
        source = self.editor.get("1.0", "end-1c")
        self._highlight_source(source)
        filename = str(self.path) if self.path else "<editor>"
        report = analyze_source(source, filename=filename)
        self._populate_symbols(report)
        if report.errors:
            self.status.configure(text=report.errors[0])
        else:
            count = sum(1 for root in build_symbol_tree(report) for _ in self._walk_symbols(root))
            self.status.configure(text=f"{count} symbols analyzed")

    @staticmethod
    def _walk_symbols(root: SymbolNode):
        yield root
        for child in root.children:
            yield from AnalyzerGUI._walk_symbols(child)

    def _highlight_source(self, source: str) -> None:
        for tag_name in self._TOKEN_TAGS:
            self.editor.tag_remove(tag_name, "1.0", END)
        self.editor.tag_remove("symbol_focus", "1.0", END)
        self._name_ranges.clear()
        previous_keyword = ""
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for item in tokens:
                start = f"{item.start[0]}.{item.start[1]}"
                end = f"{item.end[0]}.{item.end[1]}"
                tag_name = ""
                if item.type == token.NAME:
                    self._name_ranges.setdefault(item.string, []).append((start, end))
                    if previous_keyword in {"def", "class"}:
                        tag_name = "definition"
                    elif keyword.iskeyword(item.string):
                        tag_name = "keyword"
                    elif item.string in dir(builtins):
                        tag_name = "builtin"
                    previous_keyword = item.string if keyword.iskeyword(item.string) else ""
                elif item.type == token.STRING:
                    tag_name = "string"
                elif item.type == token.COMMENT:
                    tag_name = "comment"
                elif item.type == token.NUMBER:
                    tag_name = "number"
                elif item.type == token.OP:
                    tag_name = "operator"
                elif item.type not in {tokenize.NL, token.NEWLINE, token.INDENT, token.DEDENT}:
                    previous_keyword = ""
                if tag_name:
                    self.editor.tag_add(tag_name, start, end)
        except (IndentationError, tokenize.TokenError):
            pass

    def _populate_symbols(self, report: AnalysisReport) -> None:
        self.tree.delete(*self.tree.get_children())
        self._symbols.clear()
        self._symbols_by_name.clear()
        self._hovered_item = ""
        self._hovered_code_name = ""
        for node in build_symbol_tree(report):
            self._insert_symbol("", node)
        if report.errors:
            self._show_message("Analysis error", report.errors[0])
        elif not self._symbols:
            self._show_message("No symbols", "Add a variable, function, or class to inspect it here.")
        else:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self._show_symbol(self._symbols[first])

    def _insert_symbol(self, parent: str, node: SymbolNode) -> None:
        item = self.tree.insert(parent, END, text=node.name, values=(node.kind, node.type_name))
        self._symbols[item] = node
        self._symbols_by_name.setdefault(node.name.lstrip("*"), []).append(node)
        for child in node.children:
            self._insert_symbol(item, child)

    def _editor_hover(self, event: tk.Event[tk.Misc]) -> None:
        index = self.editor.index(f"@{event.x},{event.y}")
        word = self.editor.get(f"{index} wordstart", f"{index} wordend")
        if not word.isidentifier():
            self._editor_leave(event)
            return
        matches = self._symbols_by_name.get(word)
        if not matches:
            self._editor_leave(event)
            return
        self.editor.configure(cursor="hand2")
        if word == self._hovered_code_name:
            return
        self._hovered_code_name = word
        self._show_symbol(matches[0])

    def _editor_leave(self, _event: tk.Event[tk.Misc]) -> None:
        self._hovered_code_name = ""
        self.editor.configure(cursor="xterm")

    def _tree_hover(self, event: tk.Event[tk.Misc]) -> None:
        item = self.tree.identify_row(event.y)
        if not item or item == self._hovered_item:
            return
        self._hovered_item = item
        self.tree.configure(cursor="hand2")
        self.tree.selection_set(item)
        self._show_symbol(self._symbols[item])

    def _tree_select(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self.tree.selection()
        if selection:
            self._show_symbol(self._symbols[selection[0]])

    def _tree_open(self, event: tk.Event[tk.Misc]) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        symbol = self._symbols[item]
        if symbol.line is not None:
            self.editor.see(f"{symbol.line}.0")
            self.editor.mark_set("insert", f"{symbol.line}.0")
            self.editor.focus_set()

    def _show_symbol(self, symbol: SymbolNode) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", END)
        for key, value in symbol.properties:
            self.details.insert(END, key.upper() + "\n", "key")
            self.details.insert(END, value + "\n", "value")
        self.details.configure(state="disabled")
        self.editor.tag_remove("symbol_focus", "1.0", END)
        bare_name = symbol.name.lstrip("*")
        for start, end in self._name_ranges.get(bare_name, []):
            self.editor.tag_add("symbol_focus", start, end)

    def _show_message(self, heading: str, message: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", END)
        self.details.insert(END, heading.upper() + "\n", "key")
        self.details.insert(END, message, "value")
        self.details.configure(state="disabled")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyanalyzer-gui",
        description="Open the syntax-highlighted pyanalyzer symbol inspector.",
    )
    parser.add_argument("path", nargs="?", type=Path, help="Python file to open")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.path is not None and not args.path.is_file():
        build_parser().error(f"file not found: {args.path}")
    try:
        root = Tk()
    except TclError as exc:
        print(f"pyanalyzer-gui: unable to start Tk: {exc}")
        return 1
    AnalyzerGUI(root, args.path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
