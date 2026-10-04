"""策略折叠不变量测试：四个正向场景零违例，反例逐一命中预期违例码。"""

import json
import random
import unittest
from pathlib import Path

from src.policies import check_stream, fold
from src.validator import validate_event
from data.build_samples import (  # type: ignore
    Builder,
    family_checkout,
    negative_cases,
    normal_return,
    scenario_assistance,
    scenario_happy,
    scenario_quarantine_review,
    scenario_route_offline,
)

ROOT = Path(__file__).parents[1]
DAY = "2026-10-04"


class PositiveScenariosTest(unittest.TestCase):
    def test_happy_rental_is_clean_and_re_rentable(self) -> None:
        events = scenario_happy()
        result = fold(events)
        self.assertEqual([], result.violations)
        device = result.devices["dev-001"]

        # 全程结束时设备已交给次日下一位游客
        self.assertEqual(device.state, "rented")
        self.assertEqual(device.rental_id, "ren-002")
        self.assertIn("ren-001", result.rentals)

        # 第一程单独折叠：清除并放行成功、再出租闸门开放
        first_leg = [
            e for e in events
            if e["occurred_at"].startswith("2026-10-04")]
        first = fold(first_leg)
        self.assertEqual([], first.violations)
        d1 = first.devices["dev-001"]
        self.assertEqual(d1.state, "available")
        self.assertTrue(d1.cleared)
        gate_ok, _ = first.rental_gate("dev-001")
        self.assertTrue(gate_ok)

    def test_clear_failure_quarantine_and_scoped_review(self) -> None:
        events = scenario_quarantine_review()
        result = fold(events)
        self.assertEqual([], result.violations)

        device = result.devices["dev-002"]
        self.assertEqual(device.state, "available")
        self.assertTrue(device.cleared)
        self.assertEqual(device.clear_count, 1)  # 首次清除失败不计成功次数
        # 成功清除发生在隔离之后
        quarantine_event = next(
            e for e in events if e["event_type"] == "DEVICE_QUARANTINED")
        q_at = quarantine_event["occurred_at"]
        self.assertGreater(device.last_clear_at.isoformat(), q_at)

        # 隔离期间现场能看到隔离原因（不含行程内容）
        quarantine_event = next(
            e for e in events if e["event_type"] == "DEVICE_QUARANTINED")
        view = result.quarantine_view("dev-002")  # 终态视图
        self.assertEqual(view["state"], "available")
        self.assertEqual(
            quarantine_event["payload"]["reason"], "residual_media")

        # 复盘视图只暴露本事故合法保全的证据
        review = result.review_view("inc-501")
        self.assertEqual(review["media_ids"], ["med-201"])
        self.assertEqual(4, len(review["device_log_refs"]))
        self.assertEqual(review["outcome"], "fault_confirmed")
        self.assertTrue(result.media["med-201"].deleted)
        self.assertFalse(result.holds["hold-inc-501"]["active"])

    def test_hard_constraints_and_offline_resync(self) -> None:
        events = scenario_route_offline()
        result = fold(events)
        self.assertEqual([], result.violations)
        device = result.devices["dev-003"]
        self.assertEqual(device.route_versions, [1, 2, 3])
        self.assertEqual(device.resynced_ranges, [(12, 14)])
        self.assertEqual(device.state, "available")
        self.assertTrue(device.cleared)

    def test_assistance_fit_and_consent_changes(self) -> None:
        events = scenario_assistance()
        result = fold(events)
        self.assertEqual([], result.violations)
        self.assertTrue(result.assistance["ast-007"]["attached"])
        self.assertEqual(
            result.consents[("m-301", "positioning")]["decision"], "denied")
        self.assertEqual(
            result.consents[("m-303", "recording")]["decision"], "denied")

    def test_events_are_structurally_valid(self) -> None:
        for events in (scenario_happy(), scenario_quarantine_review(),
                       scenario_route_offline(), scenario_assistance()):
            for event in events:
                self.assertEqual([], validate_event(event))


class NegativeCasesTest(unittest.TestCase):
    def test_every_negative_case_hits_expected_codes(self) -> None:
        cases = negative_cases()
        for name, case in cases.items():
            with self.subTest(case=name):
                for event in case["events"]:
                    self.assertEqual([], validate_event(event))
                codes = {v.code for v in check_stream(case["events"])}
                self.assertGreaterEqual(
                    codes, set(case["expected"]),
                    f"{name} 缺少违例：{set(case['expected']) - codes}")
                if case.get("expect_duplicates"):
                    result = fold(case["events"])
                    self.assertEqual(
                        case["expect_duplicates"], result.replayed_duplicates)

    def test_negative_artifacts_on_disk_match(self) -> None:
        doc = json.loads(
            (ROOT / "data" / "scenarios" / "negative_cases.json").read_text(
                encoding="utf-8"))
        for name, case in doc.items():
            with self.subTest(case=name):
                codes = {v.code for v in check_stream(case["events"])}
                self.assertTrue(set(case["expected"]) <= codes)


class GateAndStateMachineTest(unittest.TestCase):
    def _round_trip(self) -> list[dict]:
        b = Builder()
        family_checkout(
            b, day=DAY, rental="ren-q", device="dev-q", party="pty-q",
            renter="m-q1", members=[("m-q1", "adult")], guardian=None)
        b.add("RENTAL_CLOSED", "ren-q", f"{DAY}T17:00:00+08:00", "关单",
              {"rental_id": "ren-q", "result": "completed",
               "closed_by_staff_id": "s-01"})
        return b.events

    def test_inspection_failed_requires_reinspection_before_release(self) -> None:
        b = Builder()
        for e in self._round_trip():
            b.events.append(e)
        b.add("RETURN_INSPECTED", "dev-q", f"{DAY}T17:05:00+08:00",
              "检验失败",
              {"rental_id": "ren-q", "inspecting_staff_id": "s-01",
               "result": "failed", "findings": "外壳破损"}, source="staff")
        b.add("DEVICE_QUARANTINED", "dev-q", f"{DAY}T17:06:00+08:00",
              "物理损坏隔离",
              {"reason": "physical_damage", "rental_id": "ren-q"},
              source="staff")
        # 只清除、未复检：不能放行
        b.add("RETURN_INSPECTED", "dev-q", f"{DAY}T17:20:00+08:00",
              "复检仍不合格",
              {"rental_id": "ren-q", "inspecting_staff_id": "s-01",
               "result": "failed"}, source="staff")
        b.add("DEVICE_RELEASED_FOR_RENTAL", "dev-q",
              f"{DAY}T17:25:00+08:00", "违规放行",
              {"rental_id": "ren-q", "released_by_staff_id": "s-02"},
              source="staff")
        b.renumber_versions()
        result = fold(b.events)
        codes = {v.code for v in result.violations}
        self.assertIn("RELEASE_WITHOUT_REINSPECTION", codes)
        self.assertIn("RELEASE_GATE_BLOCKED", codes)
        self.assertEqual("quarantined", result.devices["dev-q"].state)

    def test_reinspection_then_clear_then_release_succeeds(self) -> None:
        b = Builder()
        for e in self._round_trip():
            b.events.append(e)
        b.add("RETURN_INSPECTED", "dev-q", f"{DAY}T17:05:00+08:00",
              "首次检验失败",
              {"rental_id": "ren-q", "inspecting_staff_id": "s-01",
               "result": "failed"}, source="staff")
        b.add("DEVICE_QUARANTINED", "dev-q", f"{DAY}T17:06:00+08:00",
              "检验失败隔离",
              {"reason": "inspection_failed", "rental_id": "ren-q"},
              source="staff")
        b.add("RETURN_INSPECTED", "dev-q", f"{DAY}T17:20:00+08:00",
              "复检通过",
              {"rental_id": "ren-q", "inspecting_staff_id": "s-01",
               "result": "passed"}, source="staff")
        b.add("DEVICE_CLEARED", "dev-q", f"{DAY}T17:25:00+08:00",
              "清除成功",
              {"rental_id": "ren-q", "wipe_method": "secure_erase",
               "verified_by": "s-02"}, source="staff")
        b.add("DEVICE_RELEASED_FOR_RENTAL", "dev-q",
              f"{DAY}T17:30:00+08:00", "正常放行",
              {"rental_id": "ren-q", "released_by_staff_id": "s-02"},
              source="staff")
        b.renumber_versions()
        result = fold(b.events)
        self.assertEqual([], result.violations)
        self.assertEqual("available", result.devices["dev-q"].state)
        gate_ok, _ = result.rental_gate("dev-q")
        self.assertTrue(gate_ok)

    def test_incident_hold_release_blocked_until_review_closed(self) -> None:
        b = Builder()
        family_checkout(
            b, day=DAY, rental="ren-p", device="dev-p", party="pty-p",
            renter="m-p1", members=[("m-p1", "adult")], guardian=None)
        b.add("DEVICE_QUARANTINED", "dev-p", f"{DAY}T12:00:00+08:00",
              "事故暂扣",
              {"reason": "incident_hold", "rental_id": "ren-p",
               "related_incident_id": "inc-p1"}, source="staff")
        b.add("EVIDENCE_HELD", "hold-p1", f"{DAY}T12:05:00+08:00",
              "证据保全",
              {"hold_id": "hold-p1", "incident_id": "inc-p1",
               "legal_basis": "事故调查", "authorized_by": "l-01",
               "media_ids": []}, source="authority")
        b.add("INCIDENT_REVIEW_OPENED", "dev-p", f"{DAY}T12:10:00+08:00",
              "开启复盘",
              {"incident_id": "inc-p1", "rental_id": "ren-p",
               "safety_lead_id": "l-01", "media_ids": [],
               "device_log_refs": ["dev-p#seq1"]}, source="staff")
        b.add("RENTAL_CLOSED", "ren-p", f"{DAY}T16:00:00+08:00", "关单",
              {"rental_id": "ren-p", "result": "terminated_incident",
               "closed_by_staff_id": "s-01"})
        b.add("RETURN_INSPECTED", "dev-p", f"{DAY}T16:05:00+08:00",
              "检验通过",
              {"rental_id": "ren-p", "inspecting_staff_id": "s-01",
               "result": "passed"}, source="staff")
        b.add("DEVICE_CLEARED", "dev-p", f"{DAY}T16:10:00+08:00",
              "清除成功",
              {"rental_id": "ren-p", "wipe_method": "secure_erase",
               "verified_by": "s-02"}, source="staff")
        # 复盘未结束：放行必须被拦
        b.add("DEVICE_RELEASED_FOR_RENTAL", "dev-p",
              f"{DAY}T16:15:00+08:00", "复盘未结束即放行",
              {"rental_id": "ren-p", "released_by_staff_id": "s-02"},
              source="staff")
        blocked = fold(b.events)
        self.assertIn("RELEASE_INCIDENT_REVIEW_OPEN",
                      {v.code for v in blocked.violations})

        b.add("INCIDENT_REVIEW_CLOSED", "dev-p", f"{DAY}T17:00:00+08:00",
              "复盘结束",
              {"incident_id": "inc-p1", "outcome": "no_fault",
               "safety_lead_id": "l-01"}, source="staff")
        b.add("DEVICE_RELEASED_FOR_RENTAL", "dev-p",
              f"{DAY}T17:05:00+08:00", "复盘结束后放行",
              {"rental_id": "ren-p", "released_by_staff_id": "s-02"},
              source="staff")
        result = fold(b.events)
        self.assertNotIn("RELEASE_INCIDENT_REVIEW_OPEN",
                         {v.code for v in result.violations
                          if v.event_id == b.events[-1]["event_id"]})
        self.assertEqual("available", result.devices["dev-p"].state)


class IdempotencyAndOrderingTest(unittest.TestCase):
    def test_replay_same_event_is_idempotent(self) -> None:
        events = scenario_happy()
        once = fold(events)
        twice = fold(events + events[:5])  # 前 5 条重放
        self.assertEqual(5, twice.replayed_duplicates)
        self.assertEqual(
            [v.code for v in once.violations],
            [v.code for v in twice.violations])

    def test_conflicting_replay_is_rejected(self) -> None:
        events = scenario_happy()
        tampered = dict(events[0])
        tampered["payload"] = dict(tampered["payload"])
        tampered["payload"]["summary_note"] = "被篡改"
        result = fold(events + [tampered])
        self.assertIn("IDEMPOTENT_CONFLICT",
                      {v.code for v in result.violations})

    def test_fold_is_order_independent(self) -> None:
        events = scenario_quarantine_review()
        canonical = fold(events).violations
        shuffled = events[:]
        random.Random(7).shuffle(shuffled)
        self.assertEqual(
            [(v.code, v.event_id) for v in canonical],
            [(v.code, v.event_id) for v in fold(shuffled).violations])

    def test_offline_events_fold_by_true_time(self) -> None:
        # 到达序：先收到平台晚间事件，再收到设备 14 点的缓冲；
        # 折叠必须按真实时间判定告警发生时设备仍在租。
        b = Builder()
        family_checkout(
            b, day=DAY, rental="ren-o", device="dev-o", party="pty-o",
            renter="m-o1", members=[("m-o1", "adult")], guardian=None)
        b.add("DEVICE_ALERT_RAISED", "dev-o", f"{DAY}T14:00:00+08:00",
              "断网告警",
              {"alert_code": "route_deviation", "severity": "warning"},
              source="device", received_at=f"{DAY}T18:00:00+08:00",
              buffered=True, device_sequence=3)
        b.add("RENTAL_CLOSED", "ren-o", f"{DAY}T17:00:00+08:00", "关单",
              {"rental_id": "ren-o", "result": "completed",
               "closed_by_staff_id": "s-01"})
        normal_return(b, day=DAY, rental="ren-o", device="dev-o")
        result = fold(b.events)
        self.assertNotIn("ALERT_OUTSIDE_RENTAL",
                         {v.code for v in result.violations})

    def test_duplicate_device_sequence_is_flagged(self) -> None:
        b = Builder()
        family_checkout(
            b, day=DAY, rental="ren-s", device="dev-s", party="pty-s",
            renter="m-s1", members=[("m-s1", "adult")], guardian=None)
        payload = {"alert_code": "route_deviation", "severity": "warning"}
        b.add("DEVICE_ALERT_RAISED", "dev-s", f"{DAY}T10:00:00+08:00",
              "seq1", payload, source="device", device_sequence=1)
        b.add("DEVICE_ALERT_RAISED", "dev-s", f"{DAY}T10:05:00+08:00",
              "seq1 重复", payload, source="device", device_sequence=1)
        codes = {v.code for v in check_stream(b.events)}
        self.assertIn("DEVICE_SEQUENCE_DUPLICATE", codes)


class MediaAndEvidenceTest(unittest.TestCase):
    def _stream_with_media(self, subject_class: str, treatment: str):
        b = Builder()
        family_checkout(
            b, day=DAY, rental="ren-m", device="dev-m", party="pty-m",
            renter="m-m1",
            members=[("m-m1", "adult"), ("m-m3", "child")],
            guardian=("m-m1", "m-m3"))
        b.add("MEDIA_CAPTURED", "med-m1", f"{DAY}T10:00:00+08:00", "影像",
              {"media_id": "med-m1", "rental_id": "ren-m",
               "party_id": "pty-m", "media_kind": "photo"}, source="device")
        b.add("MEDIA_CLASSIFIED", "med-m1", f"{DAY}T10:00:05+08:00", "分类",
              {"media_id": "med-m1", "subject_class": subject_class,
               "treatment": treatment, "classified_by": "device-auto"})
        return b

    def test_child_retained_can_be_exported_by_guardian_renter(self) -> None:
        b = self._stream_with_media("child", "retain")
        b.add("MEDIA_EXPORTED", "med-m1", f"{DAY}T10:10:00+08:00",
              "监护人导出游记",
              {"media_id": "med-m1", "requested_by_member_id": "m-m1",
               "purpose": "family_travelogue"})
        self.assertEqual([], check_stream(b.events))

    def test_incidental_passerby_cannot_be_retained_or_exported(self) -> None:
        b = self._stream_with_media("incidental_passerby", "anonymize")
        b.add("MEDIA_EXPORTED", "med-m1", f"{DAY}T10:10:00+08:00",
              "尝试导出偶然入镜素材",
              {"media_id": "med-m1", "requested_by_member_id": "m-m1",
               "purpose": "personal"})
        codes = {v.code for v in check_stream(b.events)}
        self.assertIn("MEDIA_EXPORT_NOT_TRAVELOGUE", codes)

    def test_evidence_released_allows_deletion(self) -> None:
        b = self._stream_with_media("ordinary", "retain")
        b.add("EVIDENCE_HELD", "hold-m1", f"{DAY}T11:00:00+08:00", "保全",
              {"hold_id": "hold-m1", "incident_id": "inc-m1",
               "legal_basis": "事故调查", "authorized_by": "l-01",
               "media_ids": ["med-m1"]}, source="authority")
        b.add("EVIDENCE_RELEASED", "hold-m1", f"{DAY}T15:00:00+08:00",
              "保全解除",
              {"hold_id": "hold-m1", "released_by": "l-01"},
              source="authority")
        b.add("MEDIA_DELETED", "med-m1", f"{DAY}T15:05:00+08:00",
              "解除后删除",
              {"media_id": "med-m1", "wipe_method": "secure_erase",
               "verified_by": "s-02"}, source="platform")
        result = fold(b.events)
        self.assertEqual([], result.violations)
        self.assertTrue(result.media["med-m1"].deleted)


class ConsentTest(unittest.TestCase):
    def test_guardian_can_consent_for_declared_ward(self) -> None:
        b = Builder()
        family_checkout(
            b, day=DAY, rental="ren-g", device="dev-g", party="pty-g",
            renter="m-g1",
            members=[("m-g1", "adult"), ("m-g2", "child")],
            guardian=("m-g1", "m-g2"))
        self.assertEqual([], check_stream(b.events))

    def test_adult_self_consent_wrong_actor_is_rejected(self) -> None:
        b = Builder()
        family_checkout(
            b, day=DAY, rental="ren-w", device="dev-w", party="pty-w",
            renter="m-w1",
            members=[("m-w1", "adult"), ("m-w2", "adult")], guardian=None)
        b.add("CONSENT_UPDATED", "pty-w", f"{DAY}T10:00:00+08:00",
              "租用人替同伴关闭定位",
              {"member_id": "m-w2", "scope": "positioning",
               "decision": "denied", "basis": "self",
               "actor_member_id": "m-w1",
               "previous_decision": "granted"})
        codes = {v.code for v in check_stream(b.events)}
        self.assertIn("CONSENT_PROXY_FORBIDDEN", codes)


if __name__ == "__main__":
    unittest.main()
