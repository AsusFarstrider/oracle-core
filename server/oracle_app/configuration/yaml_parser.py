from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from ruamel.yaml.tokens import AliasToken, AnchorToken, DirectiveToken, TagToken


_TAG_PREFIX = "tag:yaml.org,2002:"
_MAP_TAG = f"{_TAG_PREFIX}map"
_SEQ_TAG = f"{_TAG_PREFIX}seq"
_STR_TAG = f"{_TAG_PREFIX}str"
_BOOL_TAG = f"{_TAG_PREFIX}bool"
_NULL_TAG = f"{_TAG_PREFIX}null"
_INT_TAG = f"{_TAG_PREFIX}int"
_FLOAT_TAG = f"{_TAG_PREFIX}float"
_ALLOWED_TAGS = {_MAP_TAG, _SEQ_TAG, _STR_TAG, _BOOL_TAG, _NULL_TAG, _INT_TAG, _FLOAT_TAG}
_INTEGER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class ConfigurationSyntaxError(ValueError):
    def __init__(self, code: str, message: str, *, line: int | None = None, column: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.line = line
        self.column = column


@dataclass(frozen=True)
class ParsedYamlDocument:
    round_trip: CommentedMap
    primitive: dict[str, Any]


def _mark(node: Node) -> tuple[int | None, int | None]:
    mark = getattr(node, "start_mark", None)
    if mark is None:
        return None, None
    return int(mark.line) + 1, int(mark.column) + 1


def _raise_for_node(code: str, message: str, node: Node) -> None:
    line, column = _mark(node)
    raise ConfigurationSyntaxError(code, message, line=line, column=column)


class RestrictedYamlParser:
    """Parse the ratified non-executable YAML 1.2 subset."""

    @staticmethod
    def _new_yaml() -> YAML:
        yaml = YAML(typ="rt")
        yaml.version = (1, 2)
        yaml.allow_duplicate_keys = False
        yaml.preserve_quotes = True
        return yaml

    def parse(self, text: str) -> ParsedYamlDocument:
        if not isinstance(text, str):
            raise TypeError("Configuration YAML must be decoded UTF-8 text.")
        if text.startswith("\ufeff"):
            raise ConfigurationSyntaxError("config.yaml.bom", "A UTF-8 byte-order mark is not allowed.")

        self._validate_tokens(text)
        documents = list(self._new_yaml().compose_all(text))
        if len(documents) != 1 or documents[0] is None:
            raise ConfigurationSyntaxError(
                "config.yaml.document_count",
                "Configuration files must contain exactly one non-empty YAML document.",
            )
        self._validate_node(documents[0])

        try:
            loaded = self._new_yaml().load(text)
        except ConfigurationSyntaxError:
            raise
        except Exception as exc:
            mark = getattr(exc, "problem_mark", None)
            raise ConfigurationSyntaxError(
                "config.yaml.parse",
                str(exc),
                line=(int(mark.line) + 1) if mark is not None else None,
                column=(int(mark.column) + 1) if mark is not None else None,
            ) from exc
        if not isinstance(loaded, CommentedMap):
            raise ConfigurationSyntaxError(
                "config.yaml.root_type",
                "A configuration file root must be a mapping.",
            )
        primitive = self._to_primitive(loaded)
        return ParsedYamlDocument(round_trip=loaded, primitive=primitive)

    def _validate_tokens(self, text: str) -> None:
        try:
            tokens = self._new_yaml().scan(text)
            for token in tokens:
                if isinstance(token, (AnchorToken, AliasToken)):
                    raise ConfigurationSyntaxError(
                        "config.yaml.alias",
                        "YAML anchors and aliases are forbidden.",
                        line=int(token.start_mark.line) + 1,
                        column=int(token.start_mark.column) + 1,
                    )
                if isinstance(token, TagToken):
                    raise ConfigurationSyntaxError(
                        "config.yaml.tag",
                        "Explicit YAML tags are forbidden.",
                        line=int(token.start_mark.line) + 1,
                        column=int(token.start_mark.column) + 1,
                    )
                if isinstance(token, DirectiveToken):
                    raise ConfigurationSyntaxError(
                        "config.yaml.directive",
                        "YAML directives are forbidden.",
                        line=int(token.start_mark.line) + 1,
                        column=int(token.start_mark.column) + 1,
                    )
        except ConfigurationSyntaxError:
            raise
        except Exception as exc:
            mark = getattr(exc, "problem_mark", None)
            raise ConfigurationSyntaxError(
                "config.yaml.scan",
                str(exc),
                line=(int(mark.line) + 1) if mark is not None else None,
                column=(int(mark.column) + 1) if mark is not None else None,
            ) from exc

    def _validate_node(self, node: Node) -> None:
        if node.tag not in _ALLOWED_TAGS:
            _raise_for_node("config.yaml.type", f"YAML type {node.tag!r} is forbidden.", node)
        if isinstance(node, MappingNode):
            for key, value in node.value:
                if not isinstance(key, ScalarNode) or key.tag != _STR_TAG:
                    _raise_for_node("config.yaml.mapping_key", "Mapping keys must be strings.", key)
                if key.value == "<<":
                    _raise_for_node("config.yaml.merge", "YAML merge keys are forbidden.", key)
                self._validate_node(key)
                self._validate_node(value)
            return
        if isinstance(node, SequenceNode):
            for value in node.value:
                self._validate_node(value)
            return
        if not isinstance(node, ScalarNode):
            _raise_for_node("config.yaml.type", "Unsupported YAML node type.", node)
        if node.tag == _BOOL_TAG and node.value not in {"true", "false"}:
            _raise_for_node("config.yaml.boolean", "Booleans must be exactly true or false.", node)
        if node.tag == _NULL_TAG and node.value != "null":
            _raise_for_node("config.yaml.null", "Null must be written exactly as null.", node)
        if node.tag == _INT_TAG and _INTEGER_PATTERN.fullmatch(node.value) is None:
            _raise_for_node("config.yaml.integer", "Integers must use JSON decimal syntax.", node)
        if node.tag == _FLOAT_TAG and _NUMBER_PATTERN.fullmatch(node.value) is None:
            _raise_for_node("config.yaml.number", "Numbers must use finite JSON decimal syntax.", node)

    def _to_primitive(self, value: Any) -> Any:
        if isinstance(value, CommentedMap):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ConfigurationSyntaxError("config.yaml.mapping_key", "Mapping keys must be strings.")
                result[key] = self._to_primitive(item)
            return result
        if isinstance(value, CommentedSeq):
            return [self._to_primitive(item) for item in value]
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ConfigurationSyntaxError("config.yaml.number", "Numbers must be finite.")
            return value
        raise ConfigurationSyntaxError(
            "config.yaml.type",
            f"Unsupported YAML value type: {type(value).__name__}.",
        )
