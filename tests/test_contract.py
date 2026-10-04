"""契约一致性：信封、样例、落盘 schema 与事件目录保持同步。"""

import json
import unittest
from pathlib import Path

from src.contracts import (
    AGGREGATES,
    EVENT_AGGREGATE,
    EVENT_SPECS,
    EVENT_TYPES,
)
from src.schema import build_schema
from src.validator import validate_event
from data.build_samples import (  # type: ignore
    negative_cases,
    scenario_assistance,
    scenario_happy,
    scenario_quarantine_review,
    scenario_route_offline,
)

ROOT = Path(__file__).parents[1]


def _load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        self.assertEqual(validate_event(_load("data/sample.json")), [])

    def test_every_registered_event_has_spec_and_aggregate(self) -> None:
        self.assertEqual(set(EVENT_TYPES), set(EVENT_SPECS))
        for event_type, aggregate in EVENT_AGGREGATE.items():
            self.assertIn(aggregate, AGGREGATES)

    def test_legacy_events_remain_compatible(self) -> None:
        # 初次提交登记的 5 个事件必须继续存在且所属聚合不变。
        legacy = {
            "DEVICE_HANDED_OUT": "tour_device",
            "CONSENT_RECORDED": "journey_party",
            "ROUTE_REVISED": "route_advisory",
            "ASSISTANCE_ESCALATED": "tour_device",
            "DEVICE_CLEARED": "tour_device",
        }
        for event_type, aggregate in legacy.items():
            self.assertIn(event_type, EVENT_TYPES)
            self.assertEqual(EVENT_AGGREGATE[event_type], aggregate)

    def test_schema_file_matches_generated(self) -> None:
        on_disk = _load("contracts/domain.schema.json")
        self.assertEqual(on_disk, build_schema())

    def test_all_scenario_events_pass_envelope_validation(self) -> None:
        for path in sorted((ROOT / "data" / "scenarios").glob("*.json")):
            doc = _load(str(path.relative_to(ROOT)))
            if isinstance(doc, list):
                streams = [doc]
            else:  # negative_cases.json
                streams = [case["events"] for case in doc.values()]
            for stream in streams:
                for event in stream:
                    self.assertEqual(
                        validate_event(event), [],
                        f"{path.name} / {event.get('event_id')} 校验失败")

    def test_on_disk_samples_match_generator(self) -> None:
        # 落盘样例必须与生成器输出一致，防止改了事件目录后遗漏重生成。
        expected = {
            "01_happy_rental.json": scenario_happy(),
            "02_clear_failure_quarantine_review.json":
                scenario_quarantine_review(),
            "03_route_hard_constraint_offline.json": scenario_route_offline(),
            "04_assistance_fit_consent_change.json": scenario_assistance(),
            "negative_cases.json": negative_cases(),
        }
        for name, value in expected.items():
            self.assertEqual(
                value, _load(f"data/scenarios/{name}"),
                f"{name} 与生成器不一致，请运行 python3 data/build_samples.py")
        handout = next(e for e in scenario_happy()
                       if e["event_type"] == "DEVICE_HANDED_OUT")
        self.assertEqual(handout, _load("data/sample.json"))


if __name__ == "__main__":
    unittest.main()
