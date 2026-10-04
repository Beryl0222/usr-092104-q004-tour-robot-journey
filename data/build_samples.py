"""生成 data/ 与 data/scenarios/ 下的联调样例。

样例按真实发生时间（occurred_at）升序构造；离线事件用 buffered + received_at
表达“断网时发生、稍后补齐”。运行：

    python3 data/build_samples.py

测试会复跑本脚本并比对落盘内容，防止样例与事件目录漂移。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.contracts import EVENT_AGGREGATE  # noqa: E402

DATA = ROOT / "data"
SCEN = DATA / "scenarios"


class Builder:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._versions: dict[str, int] = {}

    def add(self, event_type: str, aggregate_id: str, occurred_at: str,
            summary: str, payload: dict, *, source: str = "platform",
            received_at: str | None = None, buffered: bool | None = None,
            device_sequence: int | None = None,
            correlation_id: str | None = None,
            event_id: str | None = None,
            version: int | None = None) -> dict:
        aggregate_type = EVENT_AGGREGATE[event_type]
        key = f"{aggregate_type}:{aggregate_id}"
        if version is None:
            version = self._versions.get(key, 0) + 1
        self._versions[key] = version
        eid = event_id or f"evt-{len(self.events) + 1:03d}"
        event = {
            "event_id": eid,
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": occurred_at,
            "version": version,
            "summary": summary,
            "source": source,
            "payload": payload,
        }
        if received_at:
            event["received_at"] = received_at
        if buffered is not None:
            event["buffered"] = buffered
        if device_sequence is not None:
            event["device_sequence"] = device_sequence
        if correlation_id:
            event["correlation_id"] = correlation_id
        self.events.append(event)
        return event

    def clone(self) -> "Builder":
        b = Builder()
        b.events = [dict(e) for e in self.events]
        b._versions = dict(self._versions)
        return b

    def renumber_versions(self) -> "Builder":
        """按平台到达序（received_at 缺省回退 occurred_at）重排聚合版本号。

        断网缓冲事件真实发生得早、到达得晚，其聚合版本号应反映落库顺序；
        设备本地先后由 device_sequence 承担。
        """
        groups: dict[str, list[tuple[tuple[str, int], int]]] = {}
        for idx, e in enumerate(self.events):
            key = f"{e['aggregate_type']}:{e['aggregate_id']}"
            arrival = e.get("received_at") or e["occurred_at"]
            groups.setdefault(key, []).append(((arrival, idx), idx))
        for key, items in groups.items():
            for version, (_sort_key, idx) in enumerate(sorted(items), start=1):
                self.events[idx]["version"] = version
        return self


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 公共小片段 ---------------------------------------------------------------


def family_checkout(b: Builder, *, day: str, rental: str, device: str,
                    party: str, renter: str, members: list[tuple[str, str]],
                    guardian: tuple[str, str] | None,
                    handover_staff: str = "s-01",
                    site: str = "site-lakeside",
                    accessibility_needs: list[str] | None = None,
                    assistance: list[str] | None = None) -> None:
    """开单—成员—监护—同意—发放的标准开场。members: [(member_id, adult|child)]。"""
    h, m = f"{day}T08:30", f"{day}T08:31"
    open_payload = {
        "device_id": device, "party_id": party, "renter_member_id": renter,
        "site_id": site, "planned_return_at": f"{day}T17:00:00+08:00",
    }
    if accessibility_needs:
        open_payload["accessibility_needs"] = accessibility_needs
    b.add("RENTAL_OPENED", rental, f"{h}:00+08:00", "开出租借单", open_payload)
    for i, (mid, category) in enumerate(members):
        b.add("PARTY_MEMBER_JOINED", party, f"{m}:{i:02d}+08:00",
              f"成员 {mid} 加入同行",
              {"member_id": mid, "member_category": category,
               "display_alias": f"游客{mid[-2:]}"})
    if guardian:
        g, w = guardian
        b.add("GUARDIANSHIP_DECLARED", party, f"{day}T08:32:00+08:00",
              f"声明 {g} 对 {w} 的监护关系",
              {"guardian_member_id": g, "ward_member_id": w,
               "relation": "监护人", "verified_by_staff_id": "s-01",
               "verification_ref": f"idv-{w}"})
    for i, (mid, category) in enumerate(members):
        if category == "adult":
            basis, actor, guard = "self", mid, None
        else:
            basis, actor, guard = "guardian", guardian[0], guardian[0]
        for j, scope in enumerate(("recording", "positioning")):
            payload = {
                "member_id": mid, "scope": scope, "decision": "granted",
                "basis": basis, "actor_member_id": actor,
                "note": "出发前在服务台登记",
            }
            if guard:
                payload["guardian_member_id"] = guard
            b.add("CONSENT_RECORDED", party,
                  f"{day}T08:33:{i * 2 + j:02d}+08:00",
                  f"{mid} 的{('摄录', '定位')[j]}意愿", payload)
    handout = {
        "rental_id": rental, "party_id": party,
        "handover_staff_id": handover_staff, "site_id": site,
    }
    if assistance:
        handout["assistance_device_ids"] = assistance
    b.add("DEVICE_HANDED_OUT", device, f"{day}T08:35:00+08:00",
          "伴游设备发放", handout, source="staff")


def normal_return(b: Builder, *, day: str, rental: str, device: str,
                  result: str = "completed") -> None:
    b.add("RENTAL_CLOSED", rental, f"{day}T17:00:00+08:00", "租约关闭",
          {"rental_id": rental, "result": result, "closed_by_staff_id": "s-01"})
    b.add("RETURN_INSPECTED", device, f"{day}T17:05:00+08:00",
          "归还外观与功能检验",
          {"rental_id": rental, "inspecting_staff_id": "s-01",
           "result": "passed", "findings": "外观完好，电量正常"},
          source="staff")
    b.add("DEVICE_CLEARED", device, f"{day}T17:10:00+08:00",
          "旅程数据安全清除并验证",
          {"rental_id": rental, "wipe_method": "secure_erase",
           "verified_by": "s-02"},
          source="staff")
    b.add("DEVICE_RELEASED_FOR_RENTAL", device, f"{day}T17:15:00+08:00",
          "检验与清除均成功，放行再出租",
          {"rental_id": rental, "released_by_staff_id": "s-02"},
          source="staff")


# 场景 1：正常全旅程，设备清除后立刻交给下一位游客 ---------------------------


def scenario_happy() -> list[dict]:
    b = Builder()
    day = "2026-10-04"
    family_checkout(
        b, day=day, rental="ren-001", device="dev-001", party="pty-001",
        renter="m-01",
        members=[("m-01", "adult"), ("m-02", "adult"), ("m-03", "child")],
        guardian=("m-01", "m-03"),
    )
    b.add("MEDIA_CAPTURED", "med-01", f"{day}T10:15:00+08:00",
          "湖边合影",
          {"media_id": "med-01", "rental_id": "ren-001", "party_id": "pty-001",
           "area_ref": "A-03", "media_kind": "photo"},
          source="device")
    b.add("MEDIA_CLASSIFIED", "med-01", f"{day}T10:15:05+08:00",
          "普通游记素材",
          {"media_id": "med-01", "subject_class": "ordinary",
           "treatment": "retain", "classified_by": "device-auto"})
    b.add("MEDIA_EXPORTED", "med-01", f"{day}T16:40:00+08:00",
          "游客导出本人游记",
          {"media_id": "med-01", "requested_by_member_id": "m-01",
           "purpose": "personal_travelogue", "export_ref": "exp-01"})
    normal_return(b, day=day, rental="ren-001", device="dev-001")

    # 下一位游客（次日）：上一位的日志已随清除不可见
    family_checkout(
        b, day="2026-10-05", rental="ren-002", device="dev-001",
        party="pty-002",
        renter="m-10", members=[("m-10", "adult")], guardian=None,
    )
    return b.renumber_versions().events


# 场景 2：清除失败隔离 + 事故证据保全 + 最小授权复盘 -------------------------


def scenario_quarantine_review() -> list[dict]:
    b = Builder()
    day = "2026-10-04"
    family_checkout(
        b, day=day, rental="ren-101", device="dev-002", party="pty-101",
        renter="m-101", members=[("m-101", "adult")], guardian=None,
    )
    b.add("MEDIA_CAPTURED", "med-201", f"{day}T10:20:00+08:00",
          "事故路段影像",
          {"media_id": "med-201", "rental_id": "ren-101",
           "party_id": "pty-101", "area_ref": "A-12", "media_kind": "video"},
          source="device")
    b.add("MEDIA_CLASSIFIED", "med-201", f"{day}T10:20:10+08:00",
          "普通游记素材（事故后转为证据保全）",
          {"media_id": "med-201", "subject_class": "ordinary",
           "treatment": "retain", "classified_by": "device-auto"})

    # 断网期间的真实事件（设备序列号 7-10），按真实时间补齐
    b.add("DEVICE_ALERT_RAISED", "dev-002", f"{day}T14:00:00+08:00",
          "跌倒告警（断网缓冲）",
          {"alert_code": "fall_detected", "severity": "critical",
           "area_ref": "A-12", "detail": "疑似跌倒，等待人工确认"},
          source="device", received_at=f"{day}T17:40:00+08:00",
          buffered=True, device_sequence=7)
    b.add("DEVICE_STATUS_REPORTED", "dev-002", f"{day}T14:05:00+08:00",
          "设备状态（断网缓冲）",
          {"status_code": "assist_needed", "battery_pct": 63,
           "detail": "游客已起身，现场人员前往"},
          source="device", received_at=f"{day}T17:40:00+08:00",
          buffered=True, device_sequence=8)
    b.add("MANUAL_REROUTE_LOGGED", "dev-002", f"{day}T14:10:00+08:00",
          "现场人工改道至救护点",
          {"staff_id": "s-09", "from_route_version": 2,
           "to_route_version": 3, "constraint_id": "con-301",
           "reason": "护送受伤游客走无障碍通道", "area_ref": "A-12"},
          source="staff", received_at=f"{day}T17:40:00+08:00",
          buffered=True, device_sequence=9)
    b.add("DEVICE_STATUS_REPORTED", "dev-002", f"{day}T14:15:00+08:00",
          "设备状态（断网缓冲）",
          {"status_code": "enroute_first_aid", "battery_pct": 58},
          source="device", received_at=f"{day}T17:40:00+08:00",
          buffered=True, device_sequence=10)

    b.add("RENTAL_CLOSED", "ren-101", f"{day}T17:30:00+08:00",
          "事故租约关闭",
          {"rental_id": "ren-101", "result": "terminated_incident",
           "closed_by_staff_id": "s-09"})
    b.add("RETURN_INSPECTED", "dev-002", f"{day}T17:35:00+08:00",
          "归还检验通过",
          {"rental_id": "ren-101", "inspecting_staff_id": "s-01",
           "result": "passed", "findings": "外观完好"},
          source="staff")

    # 证据按合法范围保全到证据库（不随设备清除而消失）
    b.add("EVIDENCE_HELD", "hold-inc-501", f"{day}T17:45:00+08:00",
          "事故证据依法保全",
          {"hold_id": "hold-inc-501", "incident_id": "inc-501",
           "legal_basis": "生产安全事故配合调查",
           "authorized_by": "l-01", "media_ids": ["med-201"],
           "hold_until": f"{day}T23:59:59+08:00",
           "note": "仅限事故调查成员访问"},
          source="authority")
    b.add("INCIDENT_REVIEW_OPENED", "dev-002", f"{day}T17:50:00+08:00",
          "安全负责人开启复盘（仅取本事故证据与设备日志引用）",
          {"incident_id": "inc-501", "rental_id": "ren-101",
           "safety_lead_id": "l-01", "media_ids": ["med-201"],
           "device_log_refs": ["dev-002#seq7", "dev-002#seq8",
                               "dev-002#seq9", "dev-002#seq10"],
           "note": "不调取该游客其他行程素材"},
          source="staff")

    # 断网事件按真实时间补齐
    b.add("OFFLINE_EVENTS_RESYNCED", "dev-002", f"{day}T17:40:30+08:00",
          "离线缓冲 4 条事件补齐，序列 7-10 连续",
          {"device_sequence_from": 7, "device_sequence_to": 10,
           "event_count": 4, "resynced_by_staff_id": "s-09"},
          source="device")

    # 首次清除失败：检出残留媒体 → 隔离，现场可见原因
    b.add("DEVICE_CLEAR_FAILED", "dev-002", f"{day}T18:00:00+08:00",
          "清除验证失败：设备仍有残留媒体",
          {"rental_id": "ren-101", "attempt_ref": "wipe-att-1",
           "reason": "residual_media",
           "detail": "缓存分区残留 med-201 设备端副本", "staff_id": "s-02"},
          source="device")
    b.add("DEVICE_QUARANTINED", "dev-002", f"{day}T18:05:00+08:00",
          "设备隔离：残留媒体，禁止再出租",
          {"reason": "residual_media", "rental_id": "ren-101",
           "detail": "现场看板展示隔离原因与处理指引"},
          source="staff")

    # 重新清除成功后放行（证据副本已在证据库，设备本体清空）
    b.add("DEVICE_CLEARED", "dev-002", f"{day}T18:20:00+08:00",
          "二次清除成功并验证",
          {"rental_id": "ren-101", "wipe_method": "cryptographic_erase",
           "verified_by": "s-02"},
          source="staff")
    b.add("DEVICE_RELEASED_FOR_RENTAL", "dev-002", f"{day}T18:25:00+08:00",
          "重新清除成功，隔离解除放行",
          {"rental_id": "ren-101", "released_by_staff_id": "s-02",
           "note": "事故复盘依据证据库副本继续，不占用设备"},
          source="staff")

    b.add("INCIDENT_REVIEW_CLOSED", "dev-002", f"{day}T19:00:00+08:00",
          "复盘结束：设备助力延迟，转交维保",
          {"incident_id": "inc-501", "outcome": "fault_confirmed",
           "safety_lead_id": "l-01",
           "note": "形成整改单，未接触无关行程"},
          source="staff")
    b.add("EVIDENCE_RELEASED", "hold-inc-501", f"{day}T19:05:00+08:00",
          "保全范围到期解除",
          {"hold_id": "hold-inc-501", "released_by": "l-01",
           "reason": "调查结束，保全期限届满"},
          source="authority")
    b.add("MEDIA_DELETED", "med-201", f"{day}T19:10:00+08:00",
          "保全解除后按留存策略删除",
          {"media_id": "med-201", "wipe_method": "secure_erase",
           "verified_by": "l-01"},
          source="platform")
    return b.renumber_versions().events


# 场景 3：硬约束压过偏好 + 缓存失效 + 断网补齐 ------------------------------


def scenario_route_offline() -> list[dict]:
    b = Builder()
    day = "2026-10-04"
    family_checkout(
        b, day=day, rental="ren-201", device="dev-003", party="pty-201",
        renter="m-201", members=[("m-201", "adult")], guardian=None,
        accessibility_needs=["wheelchair"],
    )
    adv = "adv-site-lakeside"
    b.add("ROUTE_CONSTRAINT_PUBLISHED", adv, f"{day}T10:55:00+08:00",
          "A-12 栈道施工（硬约束）",
          {"constraint_id": "con-301", "kind": "construction",
           "severity": "hard", "area_ref": "A-12",
           "effective_from": f"{day}T11:00:00+08:00",
           "issued_by": "park-ops", "description": "栈道封闭施工"},
          source="authority")
    b.add("ROUTE_CACHE_INVALIDATED", adv, f"{day}T10:55:10+08:00",
          "施工约束发布，使在途设备旧路线缓存失效",
          {"scope": "all_devices", "reason": "construction con-301",
           "constraint_id": "con-301"},
          source="platform")
    b.add("ROUTE_REVISED", adv, f"{day}T10:56:00+08:00",
          "改走环湖东路（压过游客的临湖偏好）",
          {"device_id": "dev-003", "route_version": 1,
           "area_ref": "A-12",
           "considered_constraint_ids": ["con-301"],
           "preference_applied": "false",
           "change_reason": "硬约束：施工封闭"},
          source="platform")

    b.add("ROUTE_CONSTRAINT_PUBLISHED", adv, f"{day}T12:50:00+08:00",
          "A-15 无障碍通道临时管制（硬约束）",
          {"constraint_id": "con-302",
           "kind": "accessibility_restriction", "severity": "hard",
           "area_ref": "A-15",
           "effective_from": f"{day}T13:00:00+08:00",
           "issued_by": "park-ops",
           "description": "无障碍电梯检修，暂停使用"},
          source="authority")
    b.add("ROUTE_CACHE_INVALIDATED", adv, f"{day}T12:50:10+08:00",
          "管制生效前刷新全部设备缓存",
          {"scope": "all_devices",
           "reason": "accessibility restriction con-302",
           "constraint_id": "con-302"},
          source="platform")
    b.add("ROUTE_REVISED", adv, f"{day}T12:51:00+08:00",
          "为轮椅游客切换缓坡替代线",
          {"device_id": "dev-003", "route_version": 2,
           "area_ref": "A-15",
           "considered_constraint_ids": ["con-301", "con-302"],
           "preference_applied": "false",
           "change_reason": "硬约束：无障碍通道管制"},
          source="platform")

    # 断网：告警与人工改道先在设备端发生
    b.add("DEVICE_ALERT_RAISED", "dev-003", f"{day}T13:20:00+08:00",
          "偏离路线告警（断网缓冲）",
          {"alert_code": "route_deviation", "severity": "warning",
           "area_ref": "A-15", "detail": "游客走向管制通道"},
          source="device", received_at=f"{day}T15:10:00+08:00",
          buffered=True, device_sequence=12)
    b.add("MANUAL_REROUTE_LOGGED", "dev-003", f"{day}T13:25:00+08:00",
          "现场人工引导改道缓坡线（断网缓冲）",
          {"staff_id": "s-09", "from_route_version": 2,
           "to_route_version": 3, "constraint_id": "con-302",
           "reason": "现场拦截后人工改道", "area_ref": "A-15"},
          source="staff", received_at=f"{day}T15:10:00+08:00",
          buffered=True, device_sequence=13)
    b.add("DEVICE_STATUS_REPORTED", "dev-003", f"{day}T13:30:00+08:00",
          "设备状态（断网缓冲）",
          {"status_code": "back_on_route", "battery_pct": 71},
          source="device", received_at=f"{day}T15:10:00+08:00",
          buffered=True, device_sequence=14)
    b.add("OFFLINE_EVENTS_RESYNCED", "dev-003", f"{day}T15:10:20+08:00",
          "网络恢复，序列 12-14 按真实时间补齐",
          {"device_sequence_from": 12, "device_sequence_to": 14,
           "event_count": 3, "resynced_by_staff_id": "s-09"},
          source="device")
    b.add("ROUTE_REVISED", adv, f"{day}T15:12:00+08:00",
          "平台确认人工改道并补发路线版本",
          {"device_id": "dev-003", "route_version": 3,
           "area_ref": "A-15",
           "considered_constraint_ids": ["con-301", "con-302"],
           "preference_applied": "false",
           "change_reason": "与现场人工改道对齐"},
          source="platform")

    normal_return(b, day=day, rental="ren-201", device="dev-003")
    return b.renumber_versions().events


# 场景 4：老年游客助力设备 + 途中变更意愿 ------------------------------------


def scenario_assistance() -> list[dict]:
    b = Builder()
    day = "2026-10-04"
    family_checkout(
        b, day=day, rental="ren-301", device="dev-004", party="pty-301",
        renter="m-301",
        members=[("m-301", "adult"), ("m-303", "child")],
        guardian=("m-301", "m-303"),
        accessibility_needs=["mobility_support"],
        assistance=["ast-007"],
    )
    b.add("ASSISTANCE_FIT_CHECKED", "ast-007", f"{day}T08:32:30+08:00",
          "动力助力装置适配检查（加装前留痕）",
          {"assistance_device_id": "ast-007", "member_id": "m-301",
           "kind": "power_assist", "result": "passed",
           "checker_staff_id": "s-03", "responsible_staff_id": "s-07",
           "note": "握力与制动距离合格，已告知使用边界"},
          source="staff")
    fit_event = b.events[-1]
    b.add("ASSISTANCE_DEVICE_ATTACHED", "ast-007", f"{day}T08:36:00+08:00",
          "助力装置加装到伴游设备",
          {"assistance_device_id": "ast-007", "member_id": "m-301",
           "rental_id": "ren-301",
           "fit_check_event_id": fit_event["event_id"],
           "attached_by_staff_id": "s-03"},
          source="staff")

    # 途中变更意愿：本人撤回定位；监护人撤回儿童摄录
    b.add("CONSENT_UPDATED", "pty-301", f"{day}T11:00:00+08:00",
          "老年游客途中关闭定位意愿（告警等安全功能改以最小定位处理）",
          {"member_id": "m-301", "scope": "positioning",
           "decision": "denied", "basis": "self",
           "actor_member_id": "m-301",
           "previous_decision": "granted",
           "reason": "游客自助端变更，即时生效"})
    b.add("CONSENT_UPDATED", "pty-301", f"{day}T11:05:00+08:00",
          "监护人途中撤回儿童摄录意愿",
          {"member_id": "m-303", "scope": "recording",
           "decision": "denied", "basis": "guardian",
           "actor_member_id": "m-301", "guardian_member_id": "m-301",
           "previous_decision": "granted",
           "reason": "儿童不愿继续出镜"})
    b.add("MEDIA_CAPTURED", "med-401", f"{day}T11:20:00+08:00",
          "后续视频自动识别到拒绝出镜儿童",
          {"media_id": "med-401", "rental_id": "ren-301",
           "party_id": "pty-301", "area_ref": "A-21", "media_kind": "video"},
          source="device")
    b.add("MEDIA_CLASSIFIED", "med-401", f"{day}T11:20:08+08:00",
          "明确拒绝者：拍摄环节排除",
          {"media_id": "med-401",
           "subject_class": "consent_denied_person",
           "treatment": "exclude_capture",
           "classified_by": "device-auto",
           "note": "实时遮挡 m-303 人脸，不留存可识别片段"})

    b.add("ASSISTANCE_ESCALATED", "dev-004", f"{day}T13:30:00+08:00",
          "助力装置异常，升级给责任人",
          {"assistance_device_id": "ast-007", "member_id": "m-301",
           "reason": "助力间歇失效", "escalated_to_staff_id": "s-07",
           "severity": "warning", "detail": "现场更换备用装置"},
          source="device")

    normal_return(b, day=day, rental="ren-301", device="dev-004")
    return b.renumber_versions().events


# 反例集合：结构合法、违反领域不变量 ---------------------------------------


def _minimal_party(b: Builder, *, day="2026-10-04", rental="ren-x",
                   device="dev-x", party="pty-x", renter="m-x") -> None:
    family_checkout(
        b, day=day, rental=rental, device=device, party=party,
        renter=renter, members=[(renter, "adult")], guardian=None)


def negative_cases() -> dict:
    day = "2026-10-04"
    cases: dict[str, dict] = {}

    # 1. 租用人替另一位成年人同意摄录
    b = Builder()
    family_checkout(
        b, day=day, rental="ren-a", device="dev-a", party="pty-a",
        renter="m-a1", members=[("m-a1", "adult"), ("m-a2", "adult")],
        guardian=None)
    b.add("CONSENT_RECORDED", "pty-a", f"{day}T09:00:00+08:00",
          "租用人替同伴同意（非法代理）",
          {"member_id": "m-a2", "scope": "recording", "decision": "granted",
           "basis": "self", "actor_member_id": "m-a1"})
    cases["proxy_consent_for_adult"] = {
        "expected": ["CONSENT_PROXY_FORBIDDEN"], "events": b.events}

    # 2. 无监护关系代儿童同意
    b = Builder()
    b.add("RENTAL_OPENED", "ren-b", f"{day}T08:30:00+08:00", "开单",
          {"device_id": "dev-b", "party_id": "pty-b",
           "renter_member_id": "m-b1", "site_id": "site-lakeside",
           "planned_return_at": f"{day}T17:00:00+08:00"})
    b.add("PARTY_MEMBER_JOINED", "pty-b", f"{day}T08:31:00+08:00", "成人",
          {"member_id": "m-b1", "member_category": "adult"})
    b.add("PARTY_MEMBER_JOINED", "pty-b", f"{day}T08:31:01+08:00", "儿童",
          {"member_id": "m-b2", "member_category": "child"})
    b.add("CONSENT_RECORDED", "pty-b", f"{day}T08:33:00+08:00",
          "未登记监护即代同意",
          {"member_id": "m-b2", "scope": "recording", "decision": "granted",
           "basis": "guardian", "actor_member_id": "m-b1",
           "guardian_member_id": "m-b1"})
    cases["guardian_consent_without_declaration"] = {
        "expected": ["CONSENT_NO_GUARDIANSHIP"], "events": b.events}

    # 3. 首次意愿误用更新事件（新加入、尚未登记任何意愿的成员）
    b = Builder()
    _minimal_party(b, rental="ren-c", device="dev-c", party="pty-c",
                   renter="m-c1")
    b.add("PARTY_MEMBER_JOINED", "pty-c", f"{day}T08:50:00+08:00",
          "途中加入的新成员",
          {"member_id": "m-c2", "member_category": "adult"})
    b.add("CONSENT_UPDATED", "pty-c", f"{day}T09:00:00+08:00",
          "首次意愿误用 UPDATED",
          {"member_id": "m-c2", "scope": "recording", "decision": "denied",
           "basis": "self", "actor_member_id": "m-c2",
           "previous_decision": "granted"})
    cases["consent_update_without_record"] = {
        "expected": ["CONSENT_UPDATE_WITHOUT_RECORD"], "events": b.events}

    # 4. 媒体分类与导出：拒绝者/偶然入镜者不能等同普通游记
    b = Builder()
    _minimal_party(b, rental="ren-d", device="dev-d", party="pty-d",
                   renter="m-d1")
    b.add("MEDIA_CAPTURED", "med-d1", f"{day}T10:00:00+08:00", "影像",
          {"media_id": "med-d1", "rental_id": "ren-d", "party_id": "pty-d",
           "media_kind": "video"}, source="device")
    b.add("MEDIA_CLASSIFIED", "med-d1", f"{day}T10:00:05+08:00",
          "偶然入镜者被错误地正常留存",
          {"media_id": "med-d1", "subject_class": "incidental_passerby",
           "treatment": "retain", "classified_by": "device-auto"})
    b.add("MEDIA_CAPTURED", "med-d2", f"{day}T10:01:00+08:00", "影像",
          {"media_id": "med-d2", "rental_id": "ren-d", "party_id": "pty-d",
           "media_kind": "video"}, source="device")
    b.add("MEDIA_CLASSIFIED", "med-d2", f"{day}T10:01:05+08:00",
          "明确拒绝者仍尝试导出",
          {"media_id": "med-d2", "subject_class": "consent_denied_person",
           "treatment": "purge", "classified_by": "device-auto"})
    b.add("MEDIA_EXPORTED", "med-d2", f"{day}T10:05:00+08:00",
          "导出拒绝者素材",
          {"media_id": "med-d2", "requested_by_member_id": "m-d1",
           "purpose": "personal_travelogue"})
    cases["media_classification_and_export"] = {
        "expected": ["MEDIA_TREATMENT_FORBIDDEN",
                     "MEDIA_EXPORT_NOT_TRAVELOGUE"],
        "events": b.events}

    # 5. 证据保全期间导出与删除都被阻止
    b = Builder()
    _minimal_party(b, rental="ren-e", device="dev-e", party="pty-e",
                   renter="m-e1")
    b.add("MEDIA_CAPTURED", "med-e1", f"{day}T10:00:00+08:00", "影像",
          {"media_id": "med-e1", "rental_id": "ren-e", "party_id": "pty-e",
           "media_kind": "video"}, source="device")
    b.add("MEDIA_CLASSIFIED", "med-e1", f"{day}T10:00:05+08:00", "普通素材",
          {"media_id": "med-e1", "subject_class": "ordinary",
           "treatment": "retain", "classified_by": "device-auto"})
    b.add("EVIDENCE_HELD", "hold-e1", f"{day}T11:00:00+08:00", "依法保全",
          {"hold_id": "hold-e1", "incident_id": "inc-e1",
           "legal_basis": "事故调查", "authorized_by": "l-01",
           "media_ids": ["med-e1"]}, source="authority")
    b.add("MEDIA_EXPORTED", "med-e1", f"{day}T11:10:00+08:00",
          "保全期内尝试导出",
          {"media_id": "med-e1", "requested_by_member_id": "m-e1",
           "purpose": "personal_travelogue"})
    b.add("MEDIA_DELETED", "med-e1", f"{day}T11:15:00+08:00",
          "保全期内尝试删除",
          {"media_id": "med-e1", "wipe_method": "secure_erase",
           "verified_by": "s-02"})
    cases["evidence_blocks_export_and_delete"] = {
        "expected": ["MEDIA_EXPORT_EVIDENCE_HELD",
                     "MEDIA_DELETE_EVIDENCE_HELD"],
        "events": b.events}

    # 6. 硬约束未失效缓存 / 被偏好压过
    b = Builder()
    _minimal_party(b, rental="ren-f", device="dev-f", party="pty-f",
                   renter="m-f1")
    b.add("ROUTE_CONSTRAINT_PUBLISHED", "adv-f", f"{day}T10:00:00+08:00",
          "临时管制",
          {"constraint_id": "con-f1", "kind": "temporary_control",
           "severity": "hard", "area_ref": "A-f",
           "effective_from": f"{day}T10:05:00+08:00",
           "issued_by": "park-ops"}, source="authority")
    # 故意不发 ROUTE_CACHE_INVALIDATED，且改线不考虑该约束
    b.add("ROUTE_REVISED", "adv-f", f"{day}T10:10:00+08:00",
          "沿用旧缓存且按观景偏好带路",
          {"device_id": "dev-f", "route_version": 1, "area_ref": "A-f",
           "considered_constraint_ids": [],
           "preference_applied": "true",
           "change_reason": "游客偏好湖景"})
    cases["hard_constraint_ignored"] = {
        "expected": ["PREFERENCE_OVER_HARD_CONSTRAINT", "STALE_ROUTE_CACHE"],
        "events": b.events}

    # 7. 未完成清除即放行
    b = Builder()
    _minimal_party(b, rental="ren-g", device="dev-g", party="pty-g",
                   renter="m-g1")
    b.add("RENTAL_CLOSED", "ren-g", f"{day}T17:00:00+08:00", "关单",
          {"rental_id": "ren-g", "result": "completed",
           "closed_by_staff_id": "s-01"})
    b.add("RETURN_INSPECTED", "dev-g", f"{day}T17:05:00+08:00", "检验通过",
          {"rental_id": "ren-g", "inspecting_staff_id": "s-01",
           "result": "passed"}, source="staff")
    b.add("DEVICE_RELEASED_FOR_RENTAL", "dev-g", f"{day}T17:06:00+08:00",
          "未清除即放行",
          {"rental_id": "ren-g", "released_by_staff_id": "s-02"},
          source="staff")
    cases["release_without_clear"] = {
        "expected": ["RELEASE_GATE_BLOCKED"], "events": b.events}

    # 8. 检验失败却未隔离
    b = Builder()
    _minimal_party(b, rental="ren-h", device="dev-h", party="pty-h",
                   renter="m-h1")
    b.add("RENTAL_CLOSED", "ren-h", f"{day}T17:00:00+08:00", "关单",
          {"rental_id": "ren-h", "result": "terminated_other",
           "closed_by_staff_id": "s-01"})
    b.add("RETURN_INSPECTED", "dev-h", f"{day}T17:05:00+08:00", "检验失败",
          {"rental_id": "ren-h", "inspecting_staff_id": "s-01",
           "result": "failed", "findings": "外壳破损"}, source="staff")
    cases["failed_without_quarantine"] = {
        "expected": ["QUARANTINE_NOT_PERFORMED"], "events": b.events}

    # 9. 助力设备适配未过/未做即加装
    b = Builder()
    _minimal_party(b, rental="ren-i", device="dev-i", party="pty-i",
                   renter="m-i1")
    b.add("ASSISTANCE_FIT_CHECKED", "ast-i1", f"{day}T08:40:00+08:00",
          "适配不合格",
          {"assistance_device_id": "ast-i1", "member_id": "m-i1",
           "kind": "power_assist", "result": "failed",
           "checker_staff_id": "s-03", "responsible_staff_id": "s-07"},
          source="staff")
    fit_fail = b.events[-1]["event_id"]
    b.add("ASSISTANCE_DEVICE_ATTACHED", "ast-i1", f"{day}T08:41:00+08:00",
          "适配不合格仍加装",
          {"assistance_device_id": "ast-i1", "member_id": "m-i1",
           "rental_id": "ren-i", "fit_check_event_id": fit_fail},
          source="staff")
    b.add("ASSISTANCE_DEVICE_ATTACHED", "ast-i2", f"{day}T08:42:00+08:00",
          "完全未做适配即加装",
          {"assistance_device_id": "ast-i2", "member_id": "m-i1",
           "rental_id": "ren-i", "fit_check_event_id": "evt-fit-none"},
          source="staff")
    cases["assistance_without_valid_fit"] = {
        "expected": ["ATTACH_FIT_FAILED", "ATTACH_WITHOUT_FIT_CHECK"],
        "events": b.events}

    # 10. 断网补齐序列有缺口
    b = Builder()
    _minimal_party(b, rental="ren-j", device="dev-j", party="pty-j",
                   renter="m-j1")
    b.add("DEVICE_ALERT_RAISED", "dev-j", f"{day}T13:00:00+08:00",
          "缓冲告警 seq5",
          {"alert_code": "route_deviation", "severity": "warning"},
          source="device", received_at=f"{day}T15:00:00+08:00",
          buffered=True, device_sequence=5)
    b.add("OFFLINE_EVENTS_RESYNCED", "dev-j", f"{day}T15:00:10+08:00",
          "声明补齐 5-7，但 6、7 从未到达",
          {"device_sequence_from": 5, "device_sequence_to": 7,
           "event_count": 3}, source="device")
    cases["resync_sequence_gap"] = {
        "expected": ["RESYNC_SEQUENCE_GAP"], "events": b.events}

    # 11. 幂等：同 event_id 同负载重放忽略；负载冲突报错
    b = Builder()
    _minimal_party(b, rental="ren-k", device="dev-k", party="pty-k",
                   renter="m-k1")
    dup = dict(b.events[0])
    b.events.append(dict(dup))  # 完全相同的重放
    tampered = dict(dup)
    tampered["payload"] = dict(dup["payload"])
    tampered["payload"]["planned_return_at"] = f"{day}T18:00:00+08:00"
    tampered["event_id"] = dup["event_id"]  # 同号不同体
    b.events.append(tampered)
    cases["idempotent_replay"] = {
        "expected": ["IDEMPOTENT_CONFLICT"], "events": b.events,
        "expect_duplicates": 1}

    # 12. 复盘越权：引用未按本事故保全的媒体
    b = Builder()
    _minimal_party(b, rental="ren-l", device="dev-l", party="pty-l",
                   renter="m-l1")
    b.add("MEDIA_CAPTURED", "med-l1", f"{day}T10:00:00+08:00",
          "无关行程素材",
          {"media_id": "med-l1", "rental_id": "ren-l", "party_id": "pty-l",
           "media_kind": "photo"}, source="device")
    b.add("MEDIA_CLASSIFIED", "med-l1", f"{day}T10:00:05+08:00", "普通素材",
          {"media_id": "med-l1", "subject_class": "ordinary",
           "treatment": "retain", "classified_by": "device-auto"})
    b.add("INCIDENT_REVIEW_OPENED", "dev-l", f"{day}T15:00:00+08:00",
          "复盘引用了无关且未保全的素材",
          {"incident_id": "inc-l1", "rental_id": "ren-l",
           "safety_lead_id": "l-01", "media_ids": ["med-l1"],
           "device_log_refs": ["dev-l#seq3"]}, source="staff")
    cases["review_scope_excess"] = {
        "expected": ["REVIEW_SCOPE_EXCESS"], "events": b.events}

    return cases


def main() -> None:
    handout = next(e for e in scenario_happy()
                   if e["event_type"] == "DEVICE_HANDED_OUT")
    _write(DATA / "sample.json", handout)
    _write(SCEN / "01_happy_rental.json", scenario_happy())
    _write(SCEN / "02_clear_failure_quarantine_review.json",
           scenario_quarantine_review())
    _write(SCEN / "03_route_hard_constraint_offline.json",
           scenario_route_offline())
    _write(SCEN / "04_assistance_fit_consent_change.json",
           scenario_assistance())
    _write(SCEN / "negative_cases.json", negative_cases())
    print("samples written to", DATA)


if __name__ == "__main__":
    main()
