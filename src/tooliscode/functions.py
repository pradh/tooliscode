"""Utilities for generating Python stubs from OpenAI Responses function tools."""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from typing import Any, Iterable

__all__ = ["ToolFunctionEmitter"]


@dataclass
class _ToolParameter:
    name: str
    original_name: str
    annotation: str
    default: str | None
    field_expr: str
    description: str | None
    optional: bool
    has_alias: bool


class ToolFunctionEmitter:
    """Translate OpenAI Responses function tools into Python function stubs."""

    _TYPE_MAP = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
    }

    def __init__(self, session_id: str, tools: Iterable[dict]):
        self._tools = [
            tool["function"]
            for tool in tools
            if isinstance(tool, dict) and tool.get("type") == "function" and "function" in tool
        ]
        self._session_id = session_id
        self._typing_imports: set[str] = set()
        self._uses_field = False
        self._uses_config_dict = False

    def render(self) -> str:
        if not self._tools:
            return ""

        body_lines: list[str] = []
        for idx, function_tool in enumerate(self._tools):
            if idx:
                body_lines.append("")
            body_lines.extend(self._emit_tool(function_tool))
        import_lines = self._build_import_block()
        return "\n".join(import_lines + [""] + body_lines) + "\n"

    def _build_import_block(self) -> list[str]:
        lines = ["from __future__ import annotations"]

        typing_imports = sorted(self._typing_imports)
        if typing_imports:
            lines.append(f"from typing import {', '.join(typing_imports)}")

        pydantic_parts = ["BaseModel"]
        if self._uses_config_dict:
            pydantic_parts.append("ConfigDict")
        if self._uses_field:
            pydantic_parts.append("Field")
        lines.append(f"from pydantic import {', '.join(pydantic_parts)}")
        lines.append("from tooliscode.runtime import tool_call")
        return lines

    def _emit_tool(self, tool: dict) -> list[str]:
        name = tool.get("name") or "tool"
        fn_name = self._to_identifier(name)
        model_name = f"{self._to_pascal_case(fn_name)}Args"
        description = tool.get("description")
        parameters_schema = tool.get("parameters") or {}

        parameters = self._parse_parameters(parameters_schema)

        lines: list[str] = []
        lines.extend(self._emit_model(model_name, fn_name, parameters))
        lines.append("")
        lines.extend(self._emit_function(fn_name, name, model_name, description, parameters))
        return lines

    def _emit_model(self, model_name: str, fn_name: str, parameters: list[_ToolParameter]) -> list[str]:
        lines = [f"class {model_name}(BaseModel):"]
        doc = f"Pydantic model for `{fn_name}` arguments."
        lines.extend(self._format_docstring(doc, level=1))

        has_alias = any(param.has_alias for param in parameters)
        if has_alias:
            lines.append("    model_config = ConfigDict(populate_by_name=True)")
            if parameters:
                lines.append("")

        if not parameters:
            lines.append("    pass")
            return lines

        for param in parameters:
            field_line = f"    {param.name}: {param.annotation}"
            if param.field_expr:
                field_line += f" = {param.field_expr}"
            lines.append(field_line)
        return lines

    def _emit_function(
        self,
        fn_name: str,
        tool_name: str,
        model_name: str,
        description: str | None,
        parameters: list[_ToolParameter],
    ) -> list[str]:
        sig_parts = []
        for param in parameters:
            annotation = param.annotation
            if param.optional and not (param.default and param.default != "None"):
                if not annotation.startswith("Optional["):
                    annotation = f"Optional[{annotation}]"
                    self._typing_imports.add("Optional")
            if param.default is not None:
                sig_parts.append(f"{param.name}: {annotation} = {param.default}")
            else:
                sig_parts.append(f"{param.name}: {annotation}")
        signature = ", ".join(sig_parts)
        self._typing_imports.add("Any")
        lines = [f"def {fn_name}({signature}) -> Any:" if signature else f"def {fn_name}() -> Any:"]

        doc_lines: list[str] = []
        if description:
            doc_lines.append(description)
        arg_docs = []
        for param in parameters:
            desc = param.description or "No description provided."
            if param.original_name != param.name:
                desc = f"{desc} (alias: `{param.original_name}`)"
            arg_docs.append(f"{param.name}: {desc}")
        if arg_docs:
            if doc_lines:
                doc_lines.append("")
            doc_lines.append("Args:")
            doc_lines.extend(f"    {line}" for line in arg_docs)
        if doc_lines:
            lines.extend(self._format_docstring(doc_lines, level=1))

        init_args = ", ".join(f"{param.name}={param.name}" for param in parameters)
        lines.append(f"    args = {model_name}({init_args})" if init_args else f"    args = {model_name}()")
        session_literal = self._repr(self._session_id)
        lines.append(f"    return tool_call({session_literal}, {self._repr(tool_name)}, args)")
        return lines

    def _parse_parameters(self, schema: dict) -> list[_ToolParameter]:
        properties = schema.get("properties") or {}
        required = schema.get("required") or []

        ordered_names = list(dict.fromkeys(list(required) + list(properties.keys())))
        parameters: list[_ToolParameter] = []
        for original_name in ordered_names:
            prop_schema = properties.get(original_name, {})
            parameters.append(self._build_parameter(original_name, prop_schema, original_name in required))
        return parameters

    def _build_parameter(self, original_name: str, schema: dict, is_required: bool) -> _ToolParameter:
        py_name = self._to_identifier(original_name)
        schema_type = schema.get("type")
        allows_null = False
        schema_for_annotation = schema
        if isinstance(schema_type, list):
            allows_null = "null" in schema_type
            non_null_types = [t for t in schema_type if t != "null"]
            schema_for_annotation = dict(schema)
            schema_for_annotation["type"] = non_null_types[0] if non_null_types else None
        annotation = self._annotation_from_schema(schema_for_annotation)
        default_expr: str | None = None
        field_expr: str = ""

        description = schema.get("description")
        default = schema.get("default")

        field_kwargs: list[str] = []
        if description:
            field_kwargs.append(f"description={self._repr(description)}")

        if is_required:
            field_default = "..."
        else:
            if default is not None:
                default_expr = self._repr(default)
                field_default = default_expr
            else:
                default_expr = "None"
                field_default = "None"

        needs_field = bool(field_kwargs) or not is_required or default is not None or py_name != original_name
        if py_name != original_name:
            field_kwargs.append(f"alias={self._repr(original_name)}")
            needs_field = True
            self._uses_config_dict = True

        if needs_field:
            self._uses_field = True
            args = [field_default] if field_default not in ("...",) else ["..."]
            args.extend(field_kwargs)
            field_expr = f"Field({', '.join(args)})"

        optional = not is_required or allows_null
        return _ToolParameter(
            name=py_name,
            original_name=original_name,
            annotation=annotation,
            default=default_expr if default_expr != "None" or not is_required else None,
            field_expr=field_expr,
            description=description,
            optional=optional,
            has_alias=py_name != original_name,
        )

    def _annotation_from_schema(self, schema: dict) -> str:
        if "enum" in schema:
            values = schema["enum"]
            self._typing_imports.add("Literal")
            literal_values = ", ".join(self._repr(value) for value in values)
            return f"Literal[{literal_values}]"

        schema_type = schema.get("type")
        if schema_type in self._TYPE_MAP:
            return self._TYPE_MAP[schema_type]

        if schema_type == "array":
            items = schema.get("items") or {}
            inner = self._annotation_from_schema(items)
            self._typing_imports.add("List")
            return f"List[{inner}]"

        if schema_type == "object":
            self._typing_imports.update({"Dict", "Any"})
            return "Dict[str, Any]"

        self._typing_imports.add("Any")
        return "Any"

    @staticmethod
    def _repr(value: Any) -> str:
        return repr(value)

    @staticmethod
    def _to_identifier(name: str) -> str:
        name = re.sub(r"\W+", "_", name).strip("_")
        if not name:
            name = "tool"
        name = name.lower()
        if name[0].isdigit():
            name = f"tool_{name}"
        if keyword.iskeyword(name):
            name = f"{name}_"
        return name

    @staticmethod
    def _to_pascal_case(name: str) -> str:
        parts = re.split(r"[_\-\s]+", name)
        candidate = "".join(part.capitalize() for part in parts if part)
        if not candidate:
            candidate = "Tool"
        if candidate[0].isdigit():
            candidate = f"Tool{candidate}"
        if keyword.iskeyword(candidate.lower()):
            candidate = f"{candidate}Model"
        return candidate

    @staticmethod
    def _format_docstring(text: str | list[str], level: int = 0) -> list[str]:
        indent = "    " * level
        if isinstance(text, list):
            lines = text
        else:
            lines = [text]
        safe_lines = [line.replace('"""', '\\"""') for line in lines]
        if len(safe_lines) == 1:
            return [f'{indent}"""{safe_lines[0]}"""']
        result = [f'{indent}"""']
        result.extend(f"{indent}{line}" for line in safe_lines)
        result.append(f'{indent}"""')
        return result
