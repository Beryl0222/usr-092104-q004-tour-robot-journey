"""由 src.contracts 生成 JSON Schema（draft 2020-12）。

``contracts/domain.schema.json`` 是本函数输出的落盘副本；
测试会校验落盘文件与本函数一致，变更事件目录后请运行：

    python3 -m src.schema
"""

import json
from pathlib import Path

from .contracts import (
    AGGREGATES,
    ENVELOPE_OPTIONAL,
    ENVELOPE_REQUIRED,
    EVENT_SPECS,
    EVENT_TYPES,
    PAYLOAD_INT_FIELDS,
    PAYLOAD_LIST_FIELDS,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "domain.schema.json"


def _payload_schema(required: tuple[str, ...], optional: tuple[str, ...],
                    enums: dict) -> dict:
    props = {}
    for name in (*required, *optional):
        if name in enums:
            props[name] = {"type": "string", "enum": [v.value for v in enums[name]]}
        elif name in PAYLOAD_LIST_FIELDS:
            props[name] = {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1 if PAYLOAD_LIST_FIELDS[name] else 0,
            }
        elif name in PAYLOAD_INT_FIELDS:
            field_schema = {"type": "integer"}
            if name in ("route_version", "from_route_version",
                        "to_route_version", "supersede_route_version"):
                field_schema["minimum"] = 1
            props[name] = field_schema
        else:
            props[name] = {"type": "string"}
    return {
        "type": "object",
        "required": list(required),
        "properties": props,
        "additionalProperties": True,
    }


def build_schema() -> dict:
    payload_defs = {}
    for event, (_aggregate, required, optional, enums) in EVENT_SPECS.items():
        payload_defs[event] = _payload_schema(required, optional, enums)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "伴游设备旅程托管领域事件",
        "type": "object",
        "required": list(ENVELOPE_REQUIRED),
        "properties": {
            "event_id": {"type": "string", "minLength": 1},
            "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
            "aggregate_type": {"type": "string", "enum": list(AGGREGATES)},
            "aggregate_id": {"type": "string", "minLength": 1},
            "occurred_at": {"type": "string", "format": "date-time"},
            "received_at": {"type": "string", "format": "date-time"},
            "version": {"type": "integer", "minimum": 1},
            "summary": {"type": "string", "minLength": 1},
            "source": {"type": "string"},
            "buffered": {"type": "boolean"},
            "device_sequence": {"type": "integer", "minimum": 1},
            "correlation_id": {"type": "string"},
            "causation_id": {"type": "string"},
            "site_id": {"type": "string"},
            "payload": {"$ref": "#/$defs/payloads_by_event"},
        },
        "additionalProperties": True,
        "$defs": {
            "payloads_by_event": {
                "type": "object",
                "additionalProperties": True,
            },
            **payload_defs,
        },
        "$comment": (
            "信封校验由本 schema 承担；按事件区分的 payload 结构见 $defs 中"
            "与 event_type 同名的定义，由 src.validator 强制执行。"
        ),
        "x-payload-definitions": list(payload_defs),
        "x-envelope-optional": list(ENVELOPE_OPTIONAL),
    }


def write_schema(path: Path = SCHEMA_PATH) -> Path:
    path.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    write_schema()
    print(f"schema written: {SCHEMA_PATH}")
