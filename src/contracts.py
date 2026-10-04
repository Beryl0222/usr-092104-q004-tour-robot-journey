"""伴游设备旅程托管——领域词汇与事件目录（单一事实源）。

设备端与业务系统以领域事件作为幂等入口，本模块登记：

- 聚合类型 ``AGGREGATES``
- 事件类型 ``EVENT_TYPES`` 及其所属聚合 ``EVENT_AGGREGATE``
- 每个事件的载荷必填/选填字段与枚举字段 ``EVENT_SPECS``
- 跨事件复用的稳定枚举

信封字段见 ``ENVELOPE_REQUIRED`` / ``ENVELOPE_OPTIONAL``。
``contracts/domain.schema.json`` 必须与本文件保持一致（由测试守护）。
新增事件只能追加，不得调整既有事件的名称与所属聚合。
"""

from enum import Enum

# ---------------------------------------------------------------- 信封

ENVELOPE_REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)

# 离线缓冲事件用 occurred_at 记录“真实发生时间”，received_at 记录“平台收到时间”。
ENVELOPE_OPTIONAL = (
    "payload",
    "source",
    "received_at",
    "buffered",
    "device_sequence",
    "correlation_id",
    "causation_id",
    "site_id",
)


class StrEnum(str, Enum):
    """字符串枚举，JSON 序列化即其值。"""

    def __str__(self) -> str:  # pragma: no cover - 便利方法
        return self.value


class AggregateType(StrEnum):
    TOUR_DEVICE = "tour_device"
    RENTAL_ORDER = "rental_order"
    JOURNEY_PARTY = "journey_party"
    ROUTE_ADVISORY = "route_advisory"
    MEDIA_RETENTION = "media_retention"
    ASSISTANCE_DEVICE = "assistance_device"


AGGREGATES = tuple(a.value for a in AggregateType)


class EventType(StrEnum):
    # 租借生命周期
    RENTAL_OPENED = "RENTAL_OPENED"
    DEVICE_HANDED_OUT = "DEVICE_HANDED_OUT"
    RENTAL_CLOSED = "RENTAL_CLOSED"
    # 同行成员与同意
    PARTY_MEMBER_JOINED = "PARTY_MEMBER_JOINED"
    PARTY_MEMBER_LEFT = "PARTY_MEMBER_LEFT"
    GUARDIANSHIP_DECLARED = "GUARDIANSHIP_DECLARED"
    CONSENT_RECORDED = "CONSENT_RECORDED"
    CONSENT_UPDATED = "CONSENT_UPDATED"
    # 路线与途中约束
    ROUTE_CONSTRAINT_PUBLISHED = "ROUTE_CONSTRAINT_PUBLISHED"
    ROUTE_REVISED = "ROUTE_REVISED"
    ROUTE_CACHE_INVALIDATED = "ROUTE_CACHE_INVALIDATED"
    DEVICE_ALERT_RAISED = "DEVICE_ALERT_RAISED"
    MANUAL_REROUTE_LOGGED = "MANUAL_REROUTE_LOGGED"
    DEVICE_STATUS_REPORTED = "DEVICE_STATUS_REPORTED"
    OFFLINE_EVENTS_RESYNCED = "OFFLINE_EVENTS_RESYNCED"
    # 媒体留存与证据
    MEDIA_CAPTURED = "MEDIA_CAPTURED"
    MEDIA_CLASSIFIED = "MEDIA_CLASSIFIED"
    MEDIA_EXPORTED = "MEDIA_EXPORTED"
    MEDIA_DELETION_REQUESTED = "MEDIA_DELETION_REQUESTED"
    MEDIA_DELETED = "MEDIA_DELETED"
    EVIDENCE_HELD = "EVIDENCE_HELD"
    EVIDENCE_RELEASED = "EVIDENCE_RELEASED"
    # 归还、清除、隔离与复盘
    RETURN_INSPECTED = "RETURN_INSPECTED"
    DEVICE_CLEARED = "DEVICE_CLEARED"
    DEVICE_CLEAR_FAILED = "DEVICE_CLEAR_FAILED"
    DEVICE_QUARANTINED = "DEVICE_QUARANTINED"
    DEVICE_RELEASED_FOR_RENTAL = "DEVICE_RELEASED_FOR_RENTAL"
    INCIDENT_REVIEW_OPENED = "INCIDENT_REVIEW_OPENED"
    INCIDENT_REVIEW_CLOSED = "INCIDENT_REVIEW_CLOSED"
    # 助力设备
    ASSISTANCE_FIT_CHECKED = "ASSISTANCE_FIT_CHECKED"
    ASSISTANCE_DEVICE_ATTACHED = "ASSISTANCE_DEVICE_ATTACHED"
    ASSISTANCE_ESCALATED = "ASSISTANCE_ESCALATED"


EVENT_TYPES = tuple(e.value for e in EventType)

# ---------------------------------------------------------------- 稳定枚举


class EventSource(StrEnum):
    DEVICE = "device"            # 设备自动产生
    STAFF = "staff"              # 现场人员人工录入
    VISITOR = "visitor"          # 游客自助端
    PLATFORM = "platform"        # 平台/业务系统
    AUTHORITY = "authority"      # 园区或外部主管单位


class RentalResult(StrEnum):
    COMPLETED = "completed"
    TERMINATED_INCIDENT = "terminated_incident"
    TERMINATED_OTHER = "terminated_other"


class MemberCategory(StrEnum):
    ADULT = "adult"
    CHILD = "child"


class ConsentScope(StrEnum):
    RECORDING = "recording"      # 摄录
    POSITIONING = "positioning"  # 定位


class ConsentDecision(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"


class ConsentBasis(StrEnum):
    SELF = "self"            # 本人
    GUARDIAN = "guardian"    # 监护人代未成年被监护人


class ConstraintKind(StrEnum):
    CONSTRUCTION = "construction"                   # 施工
    TEMPORARY_CONTROL = "temporary_control"         # 园区临时管制
    ACCESSIBILITY_RESTRICTION = "accessibility_restriction"  # 无障碍限制
    CLOSURE = "closure"


class ConstraintSeverity(StrEnum):
    HARD = "hard"  # 压过一切个性偏好
    SOFT = "soft"


class CacheInvalidationScope(StrEnum):
    DEVICE = "device"
    PARTY = "party"
    ALL_DEVICES = "all_devices"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MediaKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    TRACK_LOG = "track_log"


class MediaSubjectClass(StrEnum):
    ORDINARY = "ordinary"                          # 普通同行游客
    CHILD = "child"                                # 儿童
    INCIDENTAL_PASSERBY = "incidental_passerby"    # 偶然入镜者
    CONSENT_DENIED_PERSON = "consent_denied_person"  # 明确拒绝者


class MediaTreatment(StrEnum):
    RETAIN = "retain"                    # 正常留存（可导出/删除）
    ANONYMIZE = "anonymize"              # 去标识化后留存
    EXCLUDE_CAPTURE = "exclude_capture"  # 拍摄环节即排除
    PURGE = "purge"                      # 清除


class DeletionReason(StrEnum):
    VISITOR_REQUEST = "visitor_request"
    RETENTION_POLICY = "retention_policy"
    CONSENT_DENIED = "consent_denied"
    INCIDENTAL_CLEANUP = "incidental_cleanup"


class WipeMethod(StrEnum):
    SECURE_ERASE = "secure_erase"
    CRYPTOGRAPHIC_ERASE = "cryptographic_erase"


class InspectionResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class ClearFailureReason(StrEnum):
    WIPE_ERROR = "wipe_error"
    RESIDUAL_MEDIA = "residual_media"
    HARDWARE_FAULT = "hardware_fault"
    MEDIA_LOCKED = "media_locked"


class QuarantineReason(StrEnum):
    WIPE_FAILED = "wipe_failed"
    INSPECTION_FAILED = "inspection_failed"
    PHYSICAL_DAMAGE = "physical_damage"
    RESIDUAL_MEDIA = "residual_media"
    INCIDENT_HOLD = "incident_hold"
    OVERDUE_LOCK = "overdue_lock"


class ReviewOutcome(StrEnum):
    NO_FAULT = "no_fault"
    FAULT_CONFIRMED = "fault_confirmed"
    HANDED_TO_AUTHORITY = "handed_to_authority"


class AssistanceKind(StrEnum):
    POWER_ASSIST = "power_assist"      # 动力助力
    MOBILITY_AID = "mobility_aid"      # 行动辅具
    NAVIGATION_AID = "navigation_aid"  # 导航辅助


class FitCheckResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CONDITIONAL = "conditional"


# ---------------------------------------------------------------- 事件规格

# (事件, 所属聚合, 必填载荷, 选填载荷, 载荷枚举字段)
_Spec = tuple[str, str, tuple[str, ...], tuple[str, ...], dict[str, type[Enum]]]

EVENT_SPECS: dict[str, _Spec] = {
    # 租借
    EventType.RENTAL_OPENED.value: (
        AggregateType.RENTAL_ORDER.value,
        ("device_id", "party_id", "renter_member_id", "site_id", "planned_return_at"),
        ("accessibility_needs",),
        {},
    ),
    EventType.DEVICE_HANDED_OUT.value: (
        AggregateType.TOUR_DEVICE.value,
        ("rental_id", "party_id", "handover_staff_id"),
        ("site_id", "assistance_device_ids"),
        {},
    ),
    EventType.RENTAL_CLOSED.value: (
        AggregateType.RENTAL_ORDER.value,
        ("rental_id", "result"),
        ("closed_by_staff_id", "note"),
        {"result": RentalResult},
    ),
    # 成员与同意
    EventType.PARTY_MEMBER_JOINED.value: (
        AggregateType.JOURNEY_PARTY.value,
        ("member_id", "member_category"),
        ("display_alias", "age_band"),
        {"member_category": MemberCategory},
    ),
    EventType.PARTY_MEMBER_LEFT.value: (
        AggregateType.JOURNEY_PARTY.value,
        ("member_id",),
        ("reason",),
        {},
    ),
    EventType.GUARDIANSHIP_DECLARED.value: (
        AggregateType.JOURNEY_PARTY.value,
        ("guardian_member_id", "ward_member_id", "relation"),
        ("verified_by_staff_id", "verification_ref"),
        {},
    ),
    EventType.CONSENT_RECORDED.value: (
        AggregateType.JOURNEY_PARTY.value,
        ("member_id", "scope", "decision", "basis", "actor_member_id"),
        ("guardian_member_id", "note"),
        {"scope": ConsentScope, "decision": ConsentDecision, "basis": ConsentBasis},
    ),
    EventType.CONSENT_UPDATED.value: (
        AggregateType.JOURNEY_PARTY.value,
        ("member_id", "scope", "decision", "basis", "actor_member_id"),
        ("guardian_member_id", "previous_decision", "reason"),
        {
            "scope": ConsentScope,
            "decision": ConsentDecision,
            "basis": ConsentBasis,
            "previous_decision": ConsentDecision,
        },
    ),
    # 路线与途中
    EventType.ROUTE_CONSTRAINT_PUBLISHED.value: (
        AggregateType.ROUTE_ADVISORY.value,
        ("constraint_id", "kind", "severity", "area_ref", "effective_from"),
        ("effective_to", "issued_by", "description"),
        {"kind": ConstraintKind, "severity": ConstraintSeverity},
    ),
    EventType.ROUTE_REVISED.value: (
        AggregateType.ROUTE_ADVISORY.value,
        ("device_id", "route_version", "area_ref"),
        ("considered_constraint_ids", "preference_applied", "change_reason",
         "supersede_route_version"),
        {},
    ),
    EventType.ROUTE_CACHE_INVALIDATED.value: (
        AggregateType.ROUTE_ADVISORY.value,
        ("scope", "reason"),
        ("device_ids", "party_id", "constraint_id"),
        {"scope": CacheInvalidationScope},
    ),
    EventType.DEVICE_ALERT_RAISED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("alert_code", "severity"),
        ("area_ref", "detail"),
        {"severity": AlertSeverity},
    ),
    EventType.MANUAL_REROUTE_LOGGED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("staff_id", "from_route_version", "to_route_version"),
        ("constraint_id", "reason", "area_ref"),
        {},
    ),
    EventType.DEVICE_STATUS_REPORTED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("status_code",),
        ("battery_pct", "detail"),
        {},
    ),
    EventType.OFFLINE_EVENTS_RESYNCED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("device_sequence_from", "device_sequence_to", "event_count"),
        ("resynced_by_staff_id",),
        {},
    ),
    # 媒体
    EventType.MEDIA_CAPTURED.value: (
        AggregateType.MEDIA_RETENTION.value,
        ("media_id", "rental_id", "party_id"),
        ("area_ref", "media_kind"),
        {"media_kind": MediaKind},
    ),
    EventType.MEDIA_CLASSIFIED.value: (
        AggregateType.MEDIA_RETENTION.value,
        ("media_id", "subject_class", "treatment"),
        ("classified_by", "note"),
        {"subject_class": MediaSubjectClass, "treatment": MediaTreatment},
    ),
    EventType.MEDIA_EXPORTED.value: (
        AggregateType.MEDIA_RETENTION.value,
        ("media_id", "requested_by_member_id", "purpose"),
        ("export_ref",),
        {},
    ),
    EventType.MEDIA_DELETION_REQUESTED.value: (
        AggregateType.MEDIA_RETENTION.value,
        ("media_id", "requested_by", "reason"),
        (),
        {"reason": DeletionReason},
    ),
    EventType.MEDIA_DELETED.value: (
        AggregateType.MEDIA_RETENTION.value,
        ("media_id", "wipe_method", "verified_by"),
        (),
        {"wipe_method": WipeMethod},
    ),
    EventType.EVIDENCE_HELD.value: (
        AggregateType.MEDIA_RETENTION.value,
        ("hold_id", "incident_id", "legal_basis", "authorized_by", "media_ids"),
        ("hold_until", "note"),
        {},
    ),
    EventType.EVIDENCE_RELEASED.value: (
        AggregateType.MEDIA_RETENTION.value,
        ("hold_id", "released_by"),
        ("reason",),
        {},
    ),
    # 归还/清除/隔离/复盘
    EventType.RETURN_INSPECTED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("rental_id", "inspecting_staff_id", "result"),
        ("findings",),
        {"result": InspectionResult},
    ),
    EventType.DEVICE_CLEARED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("rental_id", "wipe_method", "verified_by"),
        (),
        {"wipe_method": WipeMethod},
    ),
    EventType.DEVICE_CLEAR_FAILED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("rental_id", "attempt_ref", "reason"),
        ("detail", "staff_id"),
        {"reason": ClearFailureReason},
    ),
    EventType.DEVICE_QUARANTINED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("reason",),
        ("rental_id", "detail", "related_incident_id"),
        {"reason": QuarantineReason},
    ),
    EventType.DEVICE_RELEASED_FOR_RENTAL.value: (
        AggregateType.TOUR_DEVICE.value,
        ("rental_id", "released_by_staff_id"),
        ("note",),
        {},
    ),
    EventType.INCIDENT_REVIEW_OPENED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("incident_id", "rental_id", "safety_lead_id", "media_ids", "device_log_refs"),
        ("note",),
        {},
    ),
    EventType.INCIDENT_REVIEW_CLOSED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("incident_id", "outcome", "safety_lead_id"),
        ("note",),
        {"outcome": ReviewOutcome},
    ),
    # 助力设备
    EventType.ASSISTANCE_FIT_CHECKED.value: (
        AggregateType.ASSISTANCE_DEVICE.value,
        ("assistance_device_id", "member_id", "kind", "result",
         "checker_staff_id", "responsible_staff_id"),
        ("note",),
        {"kind": AssistanceKind, "result": FitCheckResult},
    ),
    EventType.ASSISTANCE_DEVICE_ATTACHED.value: (
        AggregateType.ASSISTANCE_DEVICE.value,
        ("assistance_device_id", "member_id", "rental_id", "fit_check_event_id"),
        ("attached_by_staff_id",),
        {},
    ),
    EventType.ASSISTANCE_ESCALATED.value: (
        AggregateType.TOUR_DEVICE.value,
        ("assistance_device_id", "member_id", "reason", "escalated_to_staff_id"),
        ("severity", "detail"),
        {"severity": AlertSeverity},
    ),
}

EVENT_AGGREGATE = {event: spec[0] for event, spec in EVENT_SPECS.items()}

# 载荷中跨事件复用的结构字段（validator 与 schema 生成器共用）。
PAYLOAD_LIST_FIELDS = {
    "media_ids": False,                   # True 表示至少一项
    "device_log_refs": True,
    "device_ids": False,
    "considered_constraint_ids": False,
    "assistance_device_ids": False,
    "accessibility_needs": False,
}

PAYLOAD_INT_FIELDS = (
    "route_version",
    "from_route_version",
    "to_route_version",
    "supersede_route_version",
    "battery_pct",
    "event_count",
    "device_sequence_from",
    "device_sequence_to",
)

# 状态机：设备可再出租的闸门由 src.policies 折叠判定，这里仅登记状态词汇。


class DeviceState(StrEnum):
    AVAILABLE = "available"                # 可出租
    RENTED = "rented"                      # 已发放，旅程中
    INSPECTED_PENDING_WIPE = "inspected_pending_wipe"  # 归还检验通过，待清除
    QUARANTINE_REQUIRED = "quarantine_required"        # 已失败，待隔离
    QUARANTINED = "quarantined"            # 已隔离，现场可见原因
    CLEARED_READY = "cleared_ready"        # 检验与清除均成功，待放行


def spec_for(event_type: str) -> _Spec:
    return EVENT_SPECS[event_type]


def events_for(aggregate_type: str) -> tuple[str, ...]:
    return tuple(e for e, a in EVENT_AGGREGATE.items() if a == aggregate_type)
