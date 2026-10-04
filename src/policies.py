"""事件流折叠与领域不变量。

设备端与业务系统把领域事件作为幂等入口：同一 ``event_id`` 重放不产生新效果。
``fold`` 按事件的真实发生时间（``occurred_at``）排序——离线缓冲事件晚收到也按
真实时间归位——再逐条折叠，输出设备/租约/成员/媒体/路线等当前态以及不变量违例。

违例以代码形式返回，供现场端（隔离原因）、安全端（复盘范围）与集成测试共用。
"""

from dataclasses import dataclass, field
from datetime import datetime

from .contracts import AggregateType, EventType


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    event_id: str | None = None


# ---------------------------------------------------------------- 投影数据


@dataclass
class DeviceRuntime:
    device_id: str
    state: str = "available"                 # 见 contracts.DeviceState
    ever_used: bool = False                  # 库存新设备无须先清除
    rental_id: str | None = None
    last_inspection: str | None = None       # passed / failed
    last_inspection_at: datetime | None = None
    cleared: bool = False
    clear_count: int = 0
    last_clear_at: datetime | None = None
    last_failure_event_id: str | None = None
    last_clear_failure_reason: str | None = None
    quarantine_reason: str | None = None
    quarantine_event_id: str | None = None
    quarantine_at: datetime | None = None
    quarantine_incident_id: str | None = None
    sequences: set[int] = field(default_factory=set)
    resynced_ranges: list[tuple[int, int]] = field(default_factory=list)
    route_versions: list[int] = field(default_factory=list)
    cache_invalidated_at: datetime | None = None


@dataclass
class MediaRecord:
    media_id: str
    rental_id: str
    party_id: str
    subject_class: str | None = None
    treatment: str | None = None
    deleted: bool = False
    holds: set[str] = field(default_factory=set)


@dataclass
class FoldResult:
    violations: list[Violation] = field(default_factory=list)
    devices: dict[str, DeviceRuntime] = field(default_factory=dict)
    rentals: dict[str, dict] = field(default_factory=dict)
    members: dict[str, dict] = field(default_factory=dict)        # member_id -> 行
    guardians: dict[str, str] = field(default_factory=dict)       # ward -> guardian
    consents: dict[tuple[str, str], dict] = field(default_factory=dict)
    media: dict[str, MediaRecord] = field(default_factory=dict)
    holds: dict[str, dict] = field(default_factory=dict)
    incidents: dict[str, dict] = field(default_factory=dict)
    constraints: dict[str, dict] = field(default_factory=dict)
    assistance: dict[str, dict] = field(default_factory=dict)
    event_times: dict[str, datetime] = field(default_factory=dict)
    applied_events: int = 0
    replayed_duplicates: int = 0

    def error(self, code: str, message: str, event_id: str | None = None) -> None:
        self.violations.append(Violation(code, message, event_id))

    # -- 供现场/安全端使用的最小投影 --

    def rental_gate(self, device_id: str) -> tuple[bool, list[str]]:
        """返回（可否出租，阻断原因列表）。再出租闸门：检验与清除都成功才放行。

        库存新设备（从未发放）无需先清除；一旦发放过，再次出租前必须完成
        “检验通过 + 清除成功”。
        """
        d = self.devices.get(device_id)
        if d is None:
            return True, []
        reasons: list[str] = []
        if d.state == "rented":
            reasons.append("设备仍在租约中")
        if d.state in ("quarantine_required", "quarantined"):
            reasons.append(f"设备隔离中：{d.quarantine_reason or '原因待登记'}")
        if d.state == "inspected_pending_wipe":
            reasons.append("归还已检验，数据尚未清除")
        if d.state == "cleared_ready":
            reasons.append("检验与清除已完成，等待放行复核")
        if d.ever_used and d.state == "available":
            if d.last_inspection != "passed":
                reasons.append("最近一次归还检验未通过")
            if not d.cleared:
                reasons.append("设备数据未完成清除验证")
        return (not reasons, reasons)

    def quarantine_view(self, device_id: str) -> dict:
        """现场人员可见的隔离信息（不含任何行程内容）。"""
        d = self.devices[device_id]
        return {
            "device_id": device_id,
            "state": d.state,
            "quarantine_reason": d.quarantine_reason,
            "detail_event_id": d.quarantine_event_id,
        }

    def review_view(self, incident_id: str) -> dict:
        """安全负责人复盘视图：仅限该事故合法保全过的证据与设备日志引用。

        保全解除后素材本体可能已删除，这里仍保留证据 ID 作为复盘档案引用，
        不暴露任何无关行程。
        """
        incident = self.incidents[incident_id]
        held = {
            media_id
            for hold in self.holds.values()
            if hold["incident_id"] == incident_id
            for media_id in hold["media_ids"]
        }
        return {
            "incident_id": incident_id,
            "rental_id": incident["rental_id"],
            "media_ids": sorted(held & set(incident["media_ids"])),
            "device_log_refs": list(incident["device_log_refs"]),
            "opened_at": incident["opened_at"].isoformat(),
            "closed_at": incident["closed_at"].isoformat()
            if incident["closed_at"] else None,
            "outcome": incident["outcome"],
        }


# ---------------------------------------------------------------- 折叠


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def fold(events: list[dict]) -> FoldResult:
    result = FoldResult()

    # 幂等入口：同一 event_id 只生效一次；重复负载不一致即违例。
    seen: dict[str, dict] = {}
    ordered: list[dict] = []
    for raw in events:
        eid = raw.get("event_id")
        if eid in seen:
            if raw == seen[eid]:
                result.replayed_duplicates += 1
                continue
            result.error("IDEMPOTENT_CONFLICT",
                         f"event_id={eid} 重放负载与首次不一致", eid)
            continue
        seen[eid] = raw
        ordered.append(raw)

    # 聚合版本是平台落库序号：按“到达序”（received_at 缺省回退 occurred_at）
    # 检查唯一与单调；断网晚到事件携带较高版本号，不影响真实时间序。
    _check_versions_by_arrival(result, ordered)

    # 断网补齐：领域折叠按真实发生时间归位，同刻再以设备序列号决断先后。
    ordered.sort(key=lambda e: (_dt(e["occurred_at"]),
                                e.get("device_sequence", 0)))

    for event in ordered:
        at = _dt(event["occurred_at"])
        result.applied_events += 1
        result.event_times[event["event_id"]] = at
        _collect_sequence(result, event)
        handler = _HANDLERS.get(event["event_type"])
        if handler is not None:
            handler(result, event)

    _final_checks(result)
    return result


def _check_versions_by_arrival(r: FoldResult, events: list[dict]) -> None:
    histories: dict[str, list[tuple[int, str]]] = {}
    arrival = lambda e: e.get("received_at") or e["occurred_at"]
    for e in sorted(events, key=lambda x: arrival(x)):
        key = f"{e['aggregate_type']}:{e['aggregate_id']}"
        history = histories.setdefault(key, [])
        version = e.get("version")
        if history:
            prev_version, prev_id = history[-1]
            if version == prev_version:
                r.error("AGGREGATE_VERSION_DUPLICATE",
                        f"聚合 {key} 的 version={version} 已被事件 {prev_id} 占用",
                        e["event_id"])
            elif version < prev_version:
                r.error("AGGREGATE_VERSION_REGRESSED",
                        f"聚合 {key} 的 version 从 {prev_version} 回退到 {version}"
                        f"（到达事件 {e['event_id']}）",
                        e["event_id"])
        history.append((version, e["event_id"]))


def check_stream(events: list[dict]) -> list[Violation]:
    return fold(events).violations


def _collect_sequence(r: FoldResult, e: dict) -> None:
    seq = e.get("device_sequence")
    if e.get("aggregate_type") != AggregateType.TOUR_DEVICE.value or seq is None:
        return
    device = _device(r, e["aggregate_id"])
    if seq in device.sequences:
        r.error("DEVICE_SEQUENCE_DUPLICATE",
                f"设备序列号 {seq} 重复（真实时间：{e['occurred_at']}）",
                e["event_id"])
    device.sequences.add(seq)


# ---------------------------------------------------------------- 工具


def _device(result: FoldResult, device_id: str) -> DeviceRuntime:
    return result.devices.setdefault(device_id, DeviceRuntime(device_id))


def _payload(event: dict) -> dict:
    return event.get("payload") or {}


def _hard_constraints_active(result: FoldResult, area_ref: str,
                             at: datetime) -> list[dict]:
    return [
        c for c in result.constraints.values()
        if c.get("area_ref") == area_ref
        and c["severity"] == "hard"
        and c["effective_from"] <= at
        and (c["effective_to"] is None or at <= c["effective_to"])
    ]


def _invalidation_covers(result: FoldResult, device_id: str, party_id: str | None,
                         after: datetime, before: datetime) -> bool:
    """在 (after, before] 之间是否存在覆盖该设备的缓存失效。"""
    for c in result.constraints.values():
        inv = c.get("invalidation")
        if inv is None or not (after < inv["at"] <= before):
            continue
        scope = inv["scope"]
        if scope == "all_devices":
            return True
        if scope == "device" and device_id in inv.get("device_ids", ()):
            return True
        if scope == "party" and party_id and party_id == inv.get("party_id"):
            return True
    return False


# ---------------------------------------------------------------- 租借


def _on_rental_opened(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    rental_id = e["aggregate_id"]
    before = len(r.violations)
    if any(x["device_id"] == p["device_id"] and not x["closed"]
           for x in r.rentals.values()):
        r.error("RENTAL_DEVICE_BUSY",
                f"设备 {p['device_id']} 已有未关闭租约", e["event_id"])
    gate_ok, reasons = r.rental_gate(p["device_id"])
    if not gate_ok:
        r.error("RENTAL_GATE_BLOCKED",
                f"设备 {p['device_id']} 不可出租：{'; '.join(reasons)}",
                e["event_id"])
    if len(r.violations) > before:
        return  # 非法开单不落事实
    r.rentals[rental_id] = {
        "rental_id": rental_id,
        "device_id": p["device_id"],
        "party_id": p["party_id"],
        "renter_member_id": p["renter_member_id"],
        "closed": False,
        "result": None,
        "opened_at": _dt(e["occurred_at"]),
        "closed_at": None,
    }


def _on_handed_out(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    device = _device(r, e["aggregate_id"])
    before = len(r.violations)
    rental = r.rentals.get(p["rental_id"])
    if rental is None:
        r.error("HANDOUT_WITHOUT_RENTAL",
                f"发放设备但租约 {p['rental_id']} 不存在", e["event_id"])
    elif rental["device_id"] != device.device_id:
        r.error("HANDOUT_DEVICE_MISMATCH",
                "发放设备与租约登记设备不一致", e["event_id"])
    gate_ok, reasons = r.rental_gate(device.device_id)
    if not gate_ok:
        r.error("HANDOUT_GATE_BLOCKED",
                f"设备 {device.device_id} 不能发放：{'; '.join(reasons)}",
                e["event_id"])
    if len(r.violations) > before:
        return  # 非法发放不落事实
    device.state = "rented"
    device.ever_used = True
    device.rental_id = p["rental_id"]
    device.cleared = False
    device.clear_count = 0
    device.last_clear_at = None
    device.last_inspection = None
    device.last_inspection_at = None
    device.quarantine_reason = None
    device.quarantine_incident_id = None


def _on_rental_closed(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    rental = r.rentals.get(e["aggregate_id"])
    if rental is None:
        r.error("CLOSE_UNKNOWN_RENTAL", "关闭不存在的租约", e["event_id"])
        return
    rental["closed"] = True
    rental["result"] = p["result"]
    rental["closed_at"] = _dt(e["occurred_at"])


# ---------------------------------------------------------------- 成员与同意


def _on_member_joined(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    r.members[p["member_id"]] = {
        "member_id": p["member_id"],
        "category": p["member_category"],
        "party_id": e["aggregate_id"],
        "left_at": None,
    }


def _on_member_left(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    member = r.members.get(p["member_id"])
    if member is None:
        r.error("PARTY_UNKNOWN_MEMBER", "成员未加入即离开", e["event_id"])
        return
    member["left_at"] = _dt(e["occurred_at"])


def _on_guardianship(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    guardian = r.members.get(p["guardian_member_id"])
    ward = r.members.get(p["ward_member_id"])
    if guardian is None or ward is None:
        r.error("GUARDIAN_UNKNOWN_MEMBER",
                "监护声明涉及未登记成员", e["event_id"])
        return
    if guardian["category"] != "adult":
        r.error("GUARDIAN_MUST_BE_ADULT",
                "监护人必须是成年成员", e["event_id"])
    if ward["category"] != "child":
        r.error("GUARDIAN_WARD_MUST_BE_CHILD",
                "监护代理只适用于未成年被监护人；成年成员须自行表达意愿",
                e["event_id"])
        return
    r.guardians[p["ward_member_id"]] = p["guardian_member_id"]


def _on_consent(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    is_update = e["event_type"] == EventType.CONSENT_UPDATED.value
    member = r.members.get(p["member_id"])
    if member is None:
        r.error("CONSENT_UNKNOWN_MEMBER",
                f"成员 {p['member_id']} 未登记", e["event_id"])
        return

    key = (p["member_id"], p["scope"])
    if is_update and key not in r.consents:
        r.error("CONSENT_UPDATE_WITHOUT_RECORD",
                "首次意愿必须用 CONSENT_RECORDED，途中变更才用 CONSENT_UPDATED",
                e["event_id"])

    if p["basis"] == "self":
        if p["actor_member_id"] != p["member_id"]:
            r.error("CONSENT_PROXY_FORBIDDEN",
                    "成年成员的摄录/定位意愿只能由本人表达，租用人不得代为同意",
                    e["event_id"])
    else:  # guardian：仅限已登记监护关系中的未成年被监护人
        declared_guardian = r.guardians.get(p["member_id"])
        if declared_guardian is None:
            r.error("CONSENT_NO_GUARDIANSHIP",
                    f"不存在对成员 {p['member_id']} 的监护关系，不能代为同意",
                    e["event_id"])
        elif declared_guardian != p.get("guardian_member_id") \
                or p["actor_member_id"] != declared_guardian:
            r.error("CONSENT_GUARDIAN_MISMATCH",
                    "代同意人必须是已登记的监护人本人", e["event_id"])

    r.consents[key] = {
        "decision": p["decision"],
        "basis": p["basis"],
        "actor": p["actor_member_id"],
        "at": _dt(e["occurred_at"]),
    }


# ---------------------------------------------------------------- 路线与途中


def _on_constraint_published(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    r.constraints[p["constraint_id"]] = {
        "constraint_id": p["constraint_id"],
        "kind": p["kind"],
        "severity": p["severity"],
        "area_ref": p["area_ref"],
        "published_at": _dt(e["occurred_at"]),
        "effective_from": _dt(p["effective_from"]),
        "effective_to": _dt(p["effective_to"]) if p.get("effective_to") else None,
        "invalidation": None,
    }


def _on_route_revised(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    at = _dt(e["occurred_at"])
    device = _device(r, p["device_id"])
    version = p["route_version"]
    if device.route_versions and version <= device.route_versions[-1]:
        r.error("ROUTE_VERSION_NOT_MONOTONIC",
                f"路线版本必须递增：{version} <= {device.route_versions[-1]}",
                e["event_id"])
    device.route_versions.append(version)

    party_id = r.rentals.get(device.rental_id or "", {}).get("party_id")
    considered = set(p.get("considered_constraint_ids", ()))
    for c in _hard_constraints_active(r, p["area_ref"], at):
        if c["constraint_id"] not in considered:
            r.error("PREFERENCE_OVER_HARD_CONSTRAINT",
                    f"区域 {p['area_ref']} 存在硬约束 {c['constraint_id']}"
                    f"（{c['kind']}），个性偏好不得压过它",
                    e["event_id"])
        if not _invalidation_covers(r, p["device_id"], party_id,
                                    c["published_at"], at):
            r.error("STALE_ROUTE_CACHE",
                    f"硬约束 {c['constraint_id']} 发布后未先使设备缓存失效，"
                    "旧缓存不得继续带路",
                    e["event_id"])


def _on_cache_invalidated(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    marker = {
        "at": _dt(e["occurred_at"]),
        "scope": p["scope"],
        "device_ids": tuple(p.get("device_ids", ())),
        "party_id": p.get("party_id"),
    }
    target = p.get("constraint_id")
    if target and target in r.constraints:
        r.constraints[target]["invalidation"] = marker
    else:
        r.constraints[f"__invalidation__{e['event_id']}"] = {
            "area_ref": "", "severity": "soft",
            "published_at": marker["at"],
            "effective_from": marker["at"], "effective_to": marker["at"],
            "invalidation": marker,
        }
    if p["scope"] == "device":
        for did in p.get("device_ids", ()):
            _device(r, did).cache_invalidated_at = marker["at"]


def _on_alert(r: FoldResult, e: dict) -> None:
    device = _device(r, e["aggregate_id"])
    if device.state != "rented" and e.get("source") in (None, "device"):
        r.error("ALERT_OUTSIDE_RENTAL",
                "非在租设备上报途中告警，请核查来源", e["event_id"])


def _on_manual_reroute(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    device = _device(r, e["aggregate_id"])
    if p["from_route_version"] > p["to_route_version"]:
        r.error("MANUAL_REROUTE_VERSION_BACK",
                "人工改道不能把路线版本改回旧版", e["event_id"])
    constraint_id = p.get("constraint_id")
    if constraint_id and constraint_id in r.constraints:
        c = r.constraints[constraint_id]
        if c["severity"] != "hard":
            r.error("MANUAL_REROUTE_SOFT_ONLY",
                    "人工改道登记的约束不是硬约束，请核对管制来源", e["event_id"])
    if device.state != "rented":
        r.error("MANUAL_REROUTE_OUTSIDE_RENTAL",
                "人工改道只能发生在旅程中", e["event_id"])


def _on_status(r: FoldResult, e: dict) -> None:
    _device(r, e["aggregate_id"])


def _on_resync(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    device = _device(r, e["aggregate_id"])
    lo, hi, count = (p["device_sequence_from"], p["device_sequence_to"],
                     p["event_count"])
    if lo > hi:
        r.error("RESYNC_RANGE_REVERSED", "补齐序列区间颠倒", e["event_id"])
        return
    missing = [s for s in range(lo, hi + 1) if s not in device.sequences]
    if missing:
        r.error("RESYNC_SEQUENCE_GAP",
                f"补齐区间 [{lo},{hi}] 缺序列：{missing}", e["event_id"])
    if count != hi - lo + 1:
        r.error("RESYNC_COUNT_MISMATCH",
                f"声明事件数 {count} 与序列跨度 {hi - lo + 1} 不符",
                e["event_id"])
    device.resynced_ranges.append((lo, hi))


# ---------------------------------------------------------------- 媒体与证据


def _on_media_captured(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    if p["media_id"] in r.media:
        r.error("MEDIA_DUPLICATE_ID", "媒体标识重复", e["event_id"])
        return
    r.media[p["media_id"]] = MediaRecord(
        media_id=p["media_id"], rental_id=p["rental_id"], party_id=p["party_id"])


_ALLOWED_TREATMENT = {
    "ordinary": {"retain", "anonymize", "exclude_capture", "purge"},
    "child": {"retain", "anonymize", "exclude_capture", "purge"},
    "incidental_passerby": {"anonymize", "exclude_capture", "purge"},
    "consent_denied_person": {"exclude_capture", "purge"},
}


def _on_media_classified(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    media = r.media.get(p["media_id"])
    if media is None:
        r.error("MEDIA_UNKNOWN", "对未采集媒体做分类", e["event_id"])
        return
    subject, treatment = p["subject_class"], p["treatment"]
    if treatment not in _ALLOWED_TREATMENT[subject]:
        r.error("MEDIA_TREATMENT_FORBIDDEN",
                f"{subject} 不允许处理方式 {treatment}：儿童、偶然入镜者与"
                "明确拒绝者不能采用同一处理方式",
                e["event_id"])
    media.subject_class = subject
    media.treatment = treatment


def _media_held(media: MediaRecord) -> bool:
    return bool(media.holds)


def _on_media_exported(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    media = r.media.get(p["media_id"])
    if media is None:
        r.error("MEDIA_UNKNOWN", "导出未采集媒体", e["event_id"])
        return
    if media.deleted:
        r.error("MEDIA_EXPORT_AFTER_DELETE", "不能导出已删除媒体", e["event_id"])
    if _media_held(media):
        r.error("MEDIA_EXPORT_EVIDENCE_HELD",
                "事故证据保全期间不得按普通游记导出", e["event_id"])
    if media.subject_class not in ("ordinary", "child") or media.treatment != "retain":
        r.error("MEDIA_EXPORT_NOT_TRAVELOGUE",
                "仅普通游记（本人或受监护儿童、正常留存）可导出；"
                "偶然入镜者与明确拒绝者素材不得导出",
                e["event_id"])


def _on_media_delete_requested(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    if p["media_id"] not in r.media:
        r.error("MEDIA_UNKNOWN", "删除请求针对未采集媒体", e["event_id"])


def _on_media_deleted(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    media = r.media.get(p["media_id"])
    if media is None:
        r.error("MEDIA_UNKNOWN", "删除未采集媒体", e["event_id"])
        return
    if media.holds:
        r.error("MEDIA_DELETE_EVIDENCE_HELD",
                f"媒体处于事故证据保全（holds={sorted(media.holds)}），"
                "仅可在合法保全范围结束后删除",
                e["event_id"])
        return
    media.deleted = True


def _on_evidence_held(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    for media_id in p["media_ids"]:
        media = r.media.get(media_id)
        if media is None:
            r.error("EVIDENCE_UNKNOWN_MEDIA",
                    f"保全清单含未采集媒体 {media_id}", e["event_id"])
            continue
        if media.deleted:
            r.error("EVIDENCE_MEDIA_ALREADY_DELETED",
                    f"媒体 {media_id} 已删除，无法保全", e["event_id"])
        media.holds.add(p["hold_id"])
    if p["hold_id"] in r.holds:
        r.error("EVIDENCE_DUPLICATE_HOLD", "保全编号重复", e["event_id"])
    r.holds[p["hold_id"]] = {
        "hold_id": p["hold_id"],
        "incident_id": p["incident_id"],
        "legal_basis": p["legal_basis"],
        "authorized_by": p["authorized_by"],
        "media_ids": list(p["media_ids"]),
        "active": True,
    }


def _on_evidence_released(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    hold = r.holds.get(p["hold_id"])
    if hold is None:
        r.error("EVIDENCE_UNKNOWN_HOLD", "解除不存在的保全", e["event_id"])
        return
    hold["active"] = False
    for media_id in hold["media_ids"]:
        media = r.media.get(media_id)
        if media:
            media.holds.discard(p["hold_id"])


# ---------------------------------------------------------------- 归还/清除/隔离


def _on_return_inspected(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    device = _device(r, e["aggregate_id"])
    # quarantined 态下该事件表示隔离后的复检。
    if device.state not in ("rented", "quarantined"):
        r.error("INSPECTION_OUTSIDE_RENTAL",
                "归还检验必须发生在设备在租或隔离复检期间", e["event_id"])
        return
    device.last_inspection = p["result"]
    device.last_inspection_at = _dt(e["occurred_at"])
    if p["result"] == "passed":
        if device.state == "quarantined":
            # 复检通过：若隔离后也已重新清除成功则可放行，否则回到待清除。
            re_cleared = (
                device.cleared and device.last_clear_at
                and device.quarantine_at
                and device.last_clear_at >= device.quarantine_at)
            device.state = ("cleared_ready" if re_cleared
                            else "inspected_pending_wipe")
        else:
            device.state = "inspected_pending_wipe"
    elif device.state == "quarantined":
        # 隔离后复检仍不合格：继续隔离，等待维修/再次复检。
        device.last_failure_event_id = e["event_id"]
    else:
        device.state = "quarantine_required"
        device.last_failure_event_id = e["event_id"]


def _on_device_cleared(r: FoldResult, e: dict) -> None:
    device = _device(r, e["aggregate_id"])
    if device.last_inspection != "passed":
        r.error("CLEAR_BEFORE_INSPECTION_PASSED",
                "数据清除前归还检验必须已通过", e["event_id"])
        return
    device.cleared = True
    device.clear_count += 1
    device.last_clear_at = _dt(e["occurred_at"])
    device.last_clear_failure_reason = None
    if device.state in ("quarantined", "inspected_pending_wipe",
                        "quarantine_required"):
        device.state = "cleared_ready"


def _on_device_clear_failed(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    device = _device(r, e["aggregate_id"])
    device.cleared = False
    device.last_failure_event_id = e["event_id"]
    device.last_clear_failure_reason = p["reason"]
    device.state = "quarantine_required"


def _on_quarantined(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    device = _device(r, e["aggregate_id"])
    if device.state == "rented" and p["reason"] != "incident_hold":
        r.error("QUARANTINE_WHILE_RENTED",
                "在租设备仅可因事故暂扣隔离", e["event_id"])
    if p["reason"] == "incident_hold" and not p.get("related_incident_id"):
        r.error("QUARANTINE_INCIDENT_WITHOUT_REF",
                "事故暂扣隔离必须关联 incident_id", e["event_id"])
    device.state = "quarantined"
    device.quarantine_reason = p["reason"]
    device.quarantine_event_id = e["event_id"]
    device.quarantine_at = _dt(e["occurred_at"])
    device.quarantine_incident_id = p.get("related_incident_id")


def _on_released(r: FoldResult, e: dict) -> None:
    device = _device(r, e["aggregate_id"])
    q_at = device.quarantine_at

    if device.state == "rented":
        r.error("RELEASE_WHILE_RENTAL_OPEN",
                "设备仍在租约中，不能放行再出租", e["event_id"])
    if device.state == "quarantine_required":
        r.error("RELEASE_WITHOUT_QUARANTINE",
                "设备刚被判定失败，必须先登记隔离再处理", e["event_id"])
    if device.state == "inspected_pending_wipe":
        r.error("RELEASE_GATE_BLOCKED",
                "归还检验已通过但数据清除未成功，设备不可放行", e["event_id"])

    if device.rental_id:
        rental = r.rentals.get(device.rental_id)
        if rental and not rental["closed"]:
            r.error("RELEASE_WHILE_RENTAL_OPEN",
                    "租约未关闭，设备不能放行再出租", e["event_id"])

    # 隔离态放行：必须先解除导致隔离的具体原因。
    if device.state == "quarantined" and q_at is not None:
        reason = device.quarantine_reason
        if reason in ("inspection_failed", "physical_damage"):
            if not (device.last_inspection == "passed"
                    and device.last_inspection_at
                    and device.last_inspection_at >= q_at):
                r.error("RELEASE_WITHOUT_REINSPECTION",
                        f"因 {reason} 隔离后未复检通过即放行", e["event_id"])
        if reason in ("wipe_failed", "residual_media"):
            if not (device.last_clear_at and device.last_clear_at >= q_at):
                r.error("RELEASE_WITHOUT_SUCCESSFUL_CLEAR",
                        f"因 {reason} 隔离后未重新清除成功即放行", e["event_id"])

    # 事故暂扣即使已复检、已清除（进入 cleared_ready），
    # 仍须等复盘结束才能放行。
    if device.quarantine_incident_id:
        incident = r.incidents.get(device.quarantine_incident_id)
        if incident is None or not incident.get("closed_at"):
            r.error("RELEASE_INCIDENT_REVIEW_OPEN",
                    "事故复盘未结束，暂扣设备不得放行再出租", e["event_id"])

    # 通用双成功闸门：无论是否走过隔离，检验与清除都必须成功。
    if device.last_inspection != "passed":
        r.error("RELEASE_GATE_BLOCKED",
                "归还检验未通过，设备不可放行再出租", e["event_id"])
    if not device.cleared:
        r.error("RELEASE_GATE_BLOCKED",
                "数据未完成清除验证，设备不可放行再出租", e["event_id"])

    # 本事件产生任何阻断性违例：设备保持原态（继续隔离/待清除）。
    if any(v.event_id == e["event_id"] for v in r.violations):
        return

    device.state = "available"
    device.rental_id = None
    device.quarantine_reason = None
    device.quarantine_event_id = None
    device.quarantine_at = None
    device.quarantine_incident_id = None


# ---------------------------------------------------------------- 复盘


def _on_review_opened(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    held_for_incident = {
        mid
        for hold in r.holds.values()
        if hold["incident_id"] == p["incident_id"]
        for mid in hold["media_ids"]
    }
    for mid in p["media_ids"]:
        if mid not in held_for_incident:
            r.error("REVIEW_SCOPE_EXCESS",
                    f"安全复盘引用了未按本事故合法保全的媒体 {mid}（无关行程）",
                    e["event_id"])
    rental = r.rentals.get(p["rental_id"])
    if rental is None:
        r.error("REVIEW_UNKNOWN_RENTAL", "复盘关联了不存在的租约", e["event_id"])
    r.incidents[p["incident_id"]] = {
        "incident_id": p["incident_id"],
        "rental_id": p["rental_id"],
        "safety_lead_id": p["safety_lead_id"],
        "media_ids": list(p["media_ids"]),
        "device_log_refs": list(p["device_log_refs"]),
        "opened_at": _dt(e["occurred_at"]),
        "closed_at": None,
        "outcome": None,
        "quarantine_device": rental["device_id"] if rental else None,
    }


def _on_review_closed(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    incident = r.incidents.get(p["incident_id"])
    if incident is None:
        r.error("REVIEW_CLOSE_WITHOUT_OPEN", "关闭未开启的复盘", e["event_id"])
        return
    if p["safety_lead_id"] != incident["safety_lead_id"]:
        r.error("REVIEW_LEAD_MISMATCH",
                "复盘必须由开启它的安全负责人关闭", e["event_id"])
    incident["closed_at"] = _dt(e["occurred_at"])
    incident["outcome"] = p["outcome"]


# ---------------------------------------------------------------- 助力设备


def _on_fit_checked(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    if p["member_id"] not in r.members:
        r.error("FIT_UNKNOWN_MEMBER", "适配检查涉及未登记成员", e["event_id"])
    r.assistance[p["assistance_device_id"]] = {
        "assistance_device_id": p["assistance_device_id"],
        "member_id": p["member_id"],
        "kind": p["kind"],
        "result": p["result"],
        "checker_staff_id": p["checker_staff_id"],
        "responsible_staff_id": p["responsible_staff_id"],
        "at": _dt(e["occurred_at"]),
        "attached": False,
    }


def _on_assistance_attached(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    aid = p["assistance_device_id"]
    fit = r.assistance.get(aid)
    if fit is None:
        r.error("ATTACH_WITHOUT_FIT_CHECK",
                "加装助力设备前必须留下适配检查记录", e["event_id"])
        return
    if fit["member_id"] != p["member_id"]:
        r.error("ATTACH_FIT_MEMBER_MISMATCH",
                "适配检查的成员与加装成员不一致", e["event_id"])
    if fit["result"] == "failed":
        r.error("ATTACH_FIT_FAILED",
                f"适配检查未通过（责任人 {fit['responsible_staff_id']}），不得加装",
                e["event_id"])
    fit["attached"] = True
    fit["fit_check_event_id"] = p["fit_check_event_id"]


def _on_assistance_escalated(r: FoldResult, e: dict) -> None:
    p = _payload(e)
    if p["assistance_device_id"] not in r.assistance:
        r.error("ESCALATE_UNKNOWN_ASSISTANCE",
                "升级了未登记的助力设备", e["event_id"])


# ---------------------------------------------------------------- 事件表与收尾


_HANDLERS = {
    EventType.RENTAL_OPENED.value: _on_rental_opened,
    EventType.DEVICE_HANDED_OUT.value: _on_handed_out,
    EventType.RENTAL_CLOSED.value: _on_rental_closed,
    EventType.PARTY_MEMBER_JOINED.value: _on_member_joined,
    EventType.PARTY_MEMBER_LEFT.value: _on_member_left,
    EventType.GUARDIANSHIP_DECLARED.value: _on_guardianship,
    EventType.CONSENT_RECORDED.value: _on_consent,
    EventType.CONSENT_UPDATED.value: _on_consent,
    EventType.ROUTE_CONSTRAINT_PUBLISHED.value: _on_constraint_published,
    EventType.ROUTE_REVISED.value: _on_route_revised,
    EventType.ROUTE_CACHE_INVALIDATED.value: _on_cache_invalidated,
    EventType.DEVICE_ALERT_RAISED.value: _on_alert,
    EventType.MANUAL_REROUTE_LOGGED.value: _on_manual_reroute,
    EventType.DEVICE_STATUS_REPORTED.value: _on_status,
    EventType.OFFLINE_EVENTS_RESYNCED.value: _on_resync,
    EventType.MEDIA_CAPTURED.value: _on_media_captured,
    EventType.MEDIA_CLASSIFIED.value: _on_media_classified,
    EventType.MEDIA_EXPORTED.value: _on_media_exported,
    EventType.MEDIA_DELETION_REQUESTED.value: _on_media_delete_requested,
    EventType.MEDIA_DELETED.value: _on_media_deleted,
    EventType.EVIDENCE_HELD.value: _on_evidence_held,
    EventType.EVIDENCE_RELEASED.value: _on_evidence_released,
    EventType.RETURN_INSPECTED.value: _on_return_inspected,
    EventType.DEVICE_CLEARED.value: _on_device_cleared,
    EventType.DEVICE_CLEAR_FAILED.value: _on_device_clear_failed,
    EventType.DEVICE_QUARANTINED.value: _on_quarantined,
    EventType.DEVICE_RELEASED_FOR_RENTAL.value: _on_released,
    EventType.INCIDENT_REVIEW_OPENED.value: _on_review_opened,
    EventType.INCIDENT_REVIEW_CLOSED.value: _on_review_closed,
    EventType.ASSISTANCE_FIT_CHECKED.value: _on_fit_checked,
    EventType.ASSISTANCE_DEVICE_ATTACHED.value: _on_assistance_attached,
    EventType.ASSISTANCE_ESCALATED.value: _on_assistance_escalated,
}


def _final_checks(r: FoldResult) -> None:
    for device in r.devices.values():
        if device.state == "quarantine_required":
            r.error("QUARANTINE_NOT_PERFORMED",
                    f"设备 {device.device_id} 检验或清除已失败但尚未隔离，"
                    "现场必须看到隔离原因且设备不可再出租",
                    device.last_failure_event_id)
