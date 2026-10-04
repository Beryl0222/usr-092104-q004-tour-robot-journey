"""领域事件信封与载荷的基础校验。

只做结构与取值域校验（可在事件入口同步执行）；
跨事件的状态不变量（再出租闸门、同意代理、证据保全等）见 ``src.policies``。
"""

from datetime import datetime

from .contracts import (
    ENVELOPE_REQUIRED,
    EVENT_AGGREGATE,
    EVENT_SPECS,
    AGGREGATES,
    PAYLOAD_INT_FIELDS,
    PAYLOAD_LIST_FIELDS,
)


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def validate_event(record: dict) -> list[str]:
    """返回错误信息列表；空列表表示通过。保持纯函数、无副作用。"""
    errors = [f"缺少字段：{name}" for name in ENVELOPE_REQUIRED if name not in record]
    if errors:
        return errors

    event_type = record.get("event_type")
    if event_type not in EVENT_SPECS:
        errors.append(f"未知 event_type：{event_type}")
    if record.get("aggregate_type") not in AGGREGATES:
        errors.append(f"未知 aggregate_type：{record.get('aggregate_type')}")

    version = record.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("version 必须是正整数")

    occurred = _parse_dt(record.get("occurred_at"))
    if occurred is None:
        errors.append("occurred_at 必须是 ISO 8601 日期时间")
    received = _parse_dt(record.get("received_at")) if record.get("received_at") else None
    if "received_at" in record and received is None:
        errors.append("received_at 必须是 ISO 8601 日期时间")
    if occurred and received and occurred > received:
        errors.append("occurred_at（真实发生时间）不得晚于 received_at（平台收到时间）")

    if event_type in EVENT_SPECS:
        expected_aggregate = EVENT_AGGREGATE[event_type]
        if record.get("aggregate_type") != expected_aggregate:
            errors.append(
                f"{event_type} 的 aggregate_type 必须是 {expected_aggregate}")
        errors.extend(_validate_payload(event_type, record.get("payload")))

    if "buffered" in record and not isinstance(record["buffered"], bool):
        errors.append("buffered 必须是布尔值")
    if "device_sequence" in record and (
        not isinstance(record["device_sequence"], int)
        or isinstance(record["device_sequence"], bool)
        or record["device_sequence"] < 1
    ):
        errors.append("device_sequence 必须是正整数")

    return errors


def _validate_payload(event_type: str, payload: object) -> list[str]:
    if payload is None:
        return ["缺少 payload"]
    if not isinstance(payload, dict):
        return ["payload 必须是对象"]

    _aggregate, required, optional, enums = EVENT_SPECS[event_type]
    known = (*required, *optional)
    errors = [f"payload 缺少字段：{name}" for name in required if name not in payload]

    for name, value in payload.items():
        if name not in known:
            continue  # additionalProperties 允许扩展字段
        if name in enums and value not in tuple(v.value for v in enums[name]):
            errors.append(
                f"payload.{name} 取值非法：{value!r}，"
                f"允许：{[v.value for v in enums[name]]}")
        elif name not in enums:
            errors.extend(_validate_plain_field(name, value))

    # 条件字段：监护代理必须指明监护人
    if event_type in ("CONSENT_RECORDED", "CONSENT_UPDATED"):
        if payload.get("basis") == "guardian" and not payload.get("guardian_member_id"):
            errors.append("basis=guardian 时 payload.guardian_member_id 必填")
        if payload.get("basis") == "self" and payload.get("guardian_member_id"):
            errors.append("basis=self 时不得携带 guardian_member_id")

    return errors


def _validate_plain_field(name: str, value: object) -> list[str]:
    if name in PAYLOAD_LIST_FIELDS:
        if not isinstance(value, list) or not all(
            isinstance(x, str) and x for x in value
        ):
            return [f"payload.{name} 必须是非空字符串数组"]
        if PAYLOAD_LIST_FIELDS[name] and not value:
            return [f"payload.{name} 至少包含一项"]
        return []
    if name in PAYLOAD_INT_FIELDS:
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"payload.{name} 必须是整数"]
        if name in ("route_version", "from_route_version",
                    "to_route_version", "supersede_route_version") and value < 1:
            return [f"payload.{name} 必须是正整数"]
        return []
    if not isinstance(value, str):
        return [f"payload.{name} 必须是字符串"]
    if not value:
        return [f"payload.{name} 不得为空串"]
    return []
