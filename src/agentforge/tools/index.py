"""Bounded, syntax-aware repository symbol indexing."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass

from agentforge.tools.repository import RepositoryInspector, RepositoryToolError


@dataclass(frozen=True, slots=True)
class PythonSymbol:
    """A compact source location for one Python class, function, or method."""

    name: str
    qualified_name: str
    kind: str
    path: str
    line: int
    end_line: int
    parameters: tuple[str, ...] = ()


class PythonSymbolIndex:
    """Parse permitted Python files once and answer bounded symbol searches."""

    ALLOWED_KINDS = frozenset({"class", "function", "method"})

    def __init__(
        self,
        inspector: RepositoryInspector,
        *,
        maximum_symbols: int = 10_000,
        maximum_results: int | None = None,
    ) -> None:
        if maximum_symbols <= 0:
            raise ValueError("Maximum symbol count must be greater than zero")
        selected_results = (
            inspector.maximum_search_results
            if maximum_results is None
            else maximum_results
        )
        if selected_results <= 0:
            raise ValueError("Maximum symbol results must be greater than zero")
        self._inspector = inspector
        self.maximum_symbols = maximum_symbols
        self.maximum_results = selected_results
        self._symbols_by_scope: dict[str, tuple[PythonSymbol, ...]] = {}

    def find(
        self, query: str, *, kind: str | None = None, path: str = "."
    ) -> list[dict[str, object]]:
        query = query.strip()
        if not query:
            raise RepositoryToolError("Symbol query cannot be empty")
        normalized_kind = kind.casefold().strip() if kind is not None else None
        if normalized_kind is not None and normalized_kind not in self.ALLOWED_KINDS:
            raise RepositoryToolError(
                "Symbol kind must be one of: " + ", ".join(sorted(self.ALLOWED_KINDS))
            )

        symbols = self._symbols_by_scope.get(path)
        if symbols is None:
            symbols = self._build(path)
            self._symbols_by_scope[path] = symbols
        folded_query = query.casefold()
        matches = [
            symbol
            for symbol in symbols
            if folded_query in symbol.name.casefold()
            and (normalized_kind is None or symbol.kind == normalized_kind)
        ]
        matches.sort(
            key=lambda symbol: (
                symbol.name.casefold() != folded_query,
                symbol.name.casefold(),
                symbol.path,
                symbol.line,
            )
        )
        return [asdict(symbol) for symbol in matches[: self.maximum_results]]

    def refresh(self) -> None:
        """Discard cached syntax trees after repository content changes."""

        self._symbols_by_scope.clear()

    def _build(self, path: str) -> tuple[PythonSymbol, ...]:
        symbols: list[PythonSymbol] = []
        for relative_path in self._inspector.list_files(path):
            if not relative_path.casefold().endswith(".py"):
                continue
            try:
                source = self._inspector.read_file(relative_path)
                tree = ast.parse(source, filename=relative_path)
            except (RepositoryToolError, SyntaxError):
                continue
            visitor = _PythonSymbolVisitor(relative_path)
            visitor.visit(tree)
            symbols.extend(visitor.symbols)
            if len(symbols) > self.maximum_symbols:
                raise RepositoryToolError(
                    f"Python index exceeds the {self.maximum_symbols}-symbol limit"
                )
        return tuple(symbols)


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.symbols: list[PythonSymbol] = []
        self._scope: list[tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add(node, "class")
        self._scope.append((node.name, "class"))
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self._scope and self._scope[-1][1] == "class" else "function"
        self._add(node, kind, self._parameters(node.args))
        self._scope.append((node.name, kind))
        self.generic_visit(node)
        self._scope.pop()

    def _add(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
        parameters: tuple[str, ...] = (),
    ) -> None:
        qualified_parts = [name for name, _ in self._scope]
        qualified_parts.append(node.name)
        self.symbols.append(
            PythonSymbol(
                name=node.name,
                qualified_name=".".join(qualified_parts),
                kind=kind,
                path=self.path,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                parameters=parameters,
            )
        )

    @staticmethod
    def _parameters(arguments: ast.arguments) -> tuple[str, ...]:
        names = [argument.arg for argument in arguments.posonlyargs]
        names.extend(argument.arg for argument in arguments.args)
        if arguments.vararg is not None:
            names.append("*" + arguments.vararg.arg)
        names.extend(argument.arg for argument in arguments.kwonlyargs)
        if arguments.kwarg is not None:
            names.append("**" + arguments.kwarg.arg)
        return tuple(names)
