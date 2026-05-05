"""Static route-map helpers for monolith and extracted Flask modules."""

from __future__ import annotations

import ast
from pathlib import Path


def _literal_methods(value: ast.AST | None) -> set[str]:
    if isinstance(value, (ast.List, ast.Tuple)):
        methods = {
            str(elt.value)
            for elt in value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
        if methods:
            return methods
    return {"GET"}


def endpoint_map_from_source(src: str) -> dict[str, set[str]]:
    tree = ast.parse(src)
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if not (isinstance(func, ast.Attribute) and func.attr == "route"):
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                path = dec.args[0].value
                if not isinstance(path, str):
                    continue
                methods = {"GET"}
                for kw in dec.keywords or []:
                    if kw.arg == "methods":
                        methods = _literal_methods(kw.value)
                out[path] = methods
        if isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "add_url_rule"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            path = node.args[0].value
            if not isinstance(path, str):
                continue
            methods = {"GET"}
            for kw in node.keywords or []:
                if kw.arg == "methods":
                    methods = _literal_methods(kw.value)
            out[path] = methods
    return out


def endpoint_map_from_files(paths: list[Path]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for path in paths:
        if not path.exists():
            continue
        for route, methods in endpoint_map_from_source(
            path.read_text(encoding="utf-8")
        ).items():
            merged[route] = methods
    return merged
