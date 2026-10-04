"""信封与载荷校验的单元测试。"""

import unittest

from src.contracts import EventType
from src.validator import validate_event


def _event(**overrides) -> dict:
    event = {
        "event_id": "e-1",
        "event_type": EventType.DEVICE_HANDED_OUT.value,
        "aggregate_type": "tour_device",
        "aggregate_id": "dev-1",
        "occurred_at": "2026-10-04T08:35:00+08:00",
        "version": 1,
        "summary": "发放",
        "payload": {
            "rental_id": "ren-1",
            "party_id": "pty-1",
            "handover_staff_id": "s-1",
        },
    }
    event.update(overrides)
    return event


class ValidatorTest(unittest.TestCase):
    def test_valid_event(self) -> None:
        self.assertEqual([], validate_event(_event()))

    def test_missing_envelope_fields(self) -> None:
        errors = validate_event({"event_id": "x"})
        self.assertTrue(any("event_type" in e for e in errors))
        self.assertTrue(any("version" in e for e in errors))

    def test_unknown_enums(self) -> None:
        errors = validate_event(_event(
            event_type="NOT_REAL", aggregate_type="tour_device"))
        self.assertTrue(any("未知 event_type" in e for e in errors))

    def test_event_aggregate_mismatch(self) -> None:
        errors = validate_event(_event(aggregate_type="rental_order"))
        self.assertTrue(any("aggregate_type 必须是" in e for e in errors))

    def test_missing_payload_field(self) -> None:
        bad = _event()
        del bad["payload"]["rental_id"]
        self.assertTrue(any("rental_id" in e
                            for e in validate_event(bad)))

    def test_enum_payload_value(self) -> None:
        event = {
            "event_id": "e-2",
            "event_type": EventType.RETURN_INSPECTED.value,
            "aggregate_type": "tour_device",
            "aggregate_id": "dev-1",
            "occurred_at": "2026-10-04T17:05:00+08:00",
            "version": 2,
            "summary": "检验",
            "payload": {"rental_id": "ren-1",
                        "inspecting_staff_id": "s-1",
                        "result": "maybe"},
        }
        self.assertTrue(any("result 取值非法" in e
                            for e in validate_event(event)))

    def test_guardian_basis_requires_guardian_id(self) -> None:
        event = {
            "event_id": "e-3",
            "event_type": EventType.CONSENT_RECORDED.value,
            "aggregate_type": "journey_party",
            "aggregate_id": "pty-1",
            "occurred_at": "2026-10-04T08:33:00+08:00",
            "version": 1,
            "summary": "同意",
            "payload": {"member_id": "m-2", "scope": "recording",
                        "decision": "granted", "basis": "guardian",
                        "actor_member_id": "m-1"},
        }
        self.assertTrue(any("guardian_member_id 必填" in e
                            for e in validate_event(event)))

    def test_occurred_after_received_rejected(self) -> None:
        errors = validate_event(_event(
            occurred_at="2026-10-04T18:00:00+08:00",
            received_at="2026-10-04T17:00:00+08:00"))
        self.assertTrue(any("不得晚于" in e for e in errors))

    def test_bad_datetime_and_version(self) -> None:
        self.assertTrue(validate_event(_event(occurred_at="not-a-date")))
        self.assertTrue(validate_event(_event(version=0)))
        self.assertTrue(validate_event(_event(version=True)))

    def test_list_and_int_payload_fields(self) -> None:
        event = {
            "event_id": "e-4",
            "event_type": EventType.OFFLINE_EVENTS_RESYNCED.value,
            "aggregate_type": "tour_device",
            "aggregate_id": "dev-1",
            "occurred_at": "2026-10-04T18:00:00+08:00",
            "version": 3,
            "summary": "补齐",
            "payload": {"device_sequence_from": "7",
                        "device_sequence_to": 10, "event_count": 4},
        }
        self.assertTrue(any("必须是整数" in e for e in validate_event(event)))


if __name__ == "__main__":
    unittest.main()
