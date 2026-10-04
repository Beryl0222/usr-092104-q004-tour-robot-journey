# 伴游设备旅程托管 · 领域说明

本文描述景区游客服务平台「旅程托管服务」的领域词汇、事件契约与不变量。
设备端与业务系统之间只通过领域事件交换；事件是**幂等入口**，可重放、可补齐。

- 事件目录与稳定枚举的单一事实源：`src/contracts.py`
- 信封/载荷校验：`src/validator.py`
- 事件流折叠与不变量：`src/policies.py`
- JSON Schema（由代码生成）：`contracts/domain.schema.json`
- 联调样例：`data/sample.json`、`data/scenarios/`

## 1. 聚合

| 聚合 | 含义 |
| --- | --- |
| `rental_order` | 一次租借单，绑定设备、同行组与租用人 |
| `tour_device` | 伴游设备本体：发放、途中状态、归还检验、清除、隔离与放行 |
| `journey_party` | 同行组：成员、监护关系、摄录/定位意愿 |
| `route_advisory` | 路线服务：约束发布、缓存失效、路线版本 |
| `media_retention` | 媒体留存：采集、分类、导出、删除、证据保全/解除 |
| `assistance_device` | 助力设备：适配检查、加装、升级 |

## 2. 事件目录（32 个，原有 5 个全部保留）

**租借生命周期**：`RENTAL_OPENED` → `DEVICE_HANDED_OUT` → … → `RENTAL_CLOSED`
→ `RETURN_INSPECTED` → `DEVICE_CLEARED` / `DEVICE_CLEAR_FAILED`
→ `DEVICE_QUARANTINED`（失败时）→ `DEVICE_RELEASED_FOR_RENTAL`

**同行成员与同意**：`PARTY_MEMBER_JOINED` / `PARTY_MEMBER_LEFT`、
`GUARDIANSHIP_DECLARED`、`CONSENT_RECORDED`、`CONSENT_UPDATED`

**路线与途中**：`ROUTE_CONSTRAINT_PUBLISHED`、`ROUTE_CACHE_INVALIDATED`、
`ROUTE_REVISED`、`DEVICE_ALERT_RAISED`、`MANUAL_REROUTE_LOGGED`、
`DEVICE_STATUS_REPORTED`、`OFFLINE_EVENTS_RESYNCED`

**媒体与证据**：`MEDIA_CAPTURED`、`MEDIA_CLASSIFIED`、`MEDIA_EXPORTED`、
`MEDIA_DELETION_REQUESTED`、`MEDIA_DELETED`、`EVIDENCE_HELD`、`EVIDENCE_RELEASED`

**异常处理**：`INCIDENT_REVIEW_OPENED`、`INCIDENT_REVIEW_CLOSED`

**助力设备**：`ASSISTANCE_FIT_CHECKED`、`ASSISTANCE_DEVICE_ATTACHED`、
`ASSISTANCE_ESCALATED`（原有）

各事件的必填/选填载荷与枚举见 `src/contracts.py` 的 `EVENT_SPECS`。
事件只能追加，不得改名或变更所属聚合。

## 3. 信封与两条时间线

```jsonc
{
  "event_id": "evt-008",                 // 全局唯一，幂等键
  "event_type": "DEVICE_ALERT_RAISED",
  "aggregate_type": "tour_device",
  "aggregate_id": "dev-002",
  "occurred_at": "2026-10-04T14:00:00+08:00",  // 真实发生时间
  "received_at": "2026-10-04T17:40:00+08:00",  // 平台收到时间（断网补齐时晚于发生时间）
  "version": 6,                          // 聚合内按到达序单调递增
  "buffered": true,                      // 断网缓冲标记
  "device_sequence": 7,                  // 设备本地序列号（真实时间序）
  "source": "device",
  "summary": "跌倒告警（断网缓冲）",
  "payload": { /* 按 event_type 区分，见 EVENT_SPECS */ }
}
```

- **幂等**：同一 `event_id` 重放且负载一致 → 只生效一次；同号不同体 →
  `IDEMPOTENT_CONFLICT`。
- **领域顺序**按 `occurred_at`（真实时间）折叠，所以断网期间先发生、晚收到的
  告警/人工改道/状态仍落在正确的历史位置（例如告警时设备确实在租）。
- **聚合 `version`** 按平台**到达序**分配并校验唯一、单调；晚到的离线事件携带
  较高版本号，不构成版本回退。设备本地先后由 `device_sequence` 承担。
- 补齐由 `OFFLINE_EVENTS_RESYNCED` 声明序列号区间与条数；区间内序列缺失
  → `RESYNC_SEQUENCE_GAP`，条数与跨度不符 → `RESYNC_COUNT_MISMATCH`。

## 4. 设备状态机与再出租闸门

```
available ──DEVICE_HANDED_OUT──▶ rented
rented ──RETURN_INSPECTED(passed)──▶ inspected_pending_wipe
rented ──RETURN_INSPECTED(failed)──▶ quarantine_required ──▶ quarantined
inspected_pending_wipe ──DEVICE_CLEARED──▶ cleared_ready
inspected_pending_wipe ──DEVICE_CLEAR_FAILED──▶ quarantine_required ──▶ quarantined
quarantined ──复检通过/重清除成功（视隔离原因）──▶ inspected_pending_wipe / cleared_ready
cleared_ready ──DEVICE_RELEASED_FOR_RENTAL──▶ available
```

闸门规则（`FoldResult.rental_gate` 与放行处理器）：

- **只有归还检验通过且数据清除成功，设备才可再次出租**；库存新设备（从未发放）
  无需先清除。
- 检验失败或清除失败必须登记 `DEVICE_QUARANTINED`；失败却未隔离 →
  `QUARANTINE_NOT_PERFORMED`。
- 现场人员通过 `quarantine_view` 看到的隔离信息只有设备号、状态与**隔离原因**
  （`QuarantineReason`：wipe_failed / residual_media / inspection_failed /
  physical_damage / incident_hold / overdue_lock），不含任何行程内容。
- 隔离解除按原因加码：
  - 检验类（inspection_failed、physical_damage）→ 隔离后**复检通过**；
  - 数据类（wipe_failed、residual_media）→ 隔离后**重新清除成功**；
  - incident_hold → **复盘关闭**后才可放行。
- 任何条件不满足的放行事件本身被记录为违例且不翻转设备状态。

## 5. 同行成员、监护与意愿

- 成员分 `adult` / `child`；意愿分摄录 `recording` 与定位 `positioning`，
  决定为 `granted` / `denied`，**途中可随时变更**：首次用 `CONSENT_RECORDED`，
  变更用 `CONSENT_UPDATED`（对无记录的成员误用更新 →
  `CONSENT_UPDATE_WITHOUT_RECORD`）。
- 成年成员意愿只能本人表达（`basis=self` 且 `actor_member_id` 为本人）；
  **租用人替另一位成年人同意 → `CONSENT_PROXY_FORBIDDEN`**。
- 未成年成员只能由已声明监护关系（`GUARDIANSHIP_DECLARED`：成年监护人、
  未成年被监护人，服务台核验）的监护人代为表达；无监护关系代同意 →
  `CONSENT_NO_GUARDIANSHIP`，代同意人不符 → `CONSENT_GUARDIAN_MISMATCH`。
- 意愿变更对后续采集即时生效（见下节 `consent_denied_person`）。

## 6. 媒体留存：三类人不能同处理，证据按合法范围保全

`MEDIA_CLASSIFIED` 把素材主体分为四类，允许的处理方式：

| subject_class | 允许 treatment |
| --- | --- |
| `ordinary` 普通同行游客 | retain / anonymize / exclude_capture / purge |
| `child` 儿童 | retain / anonymize / exclude_capture / purge（监护人意愿下可正常留存） |
| `incidental_passerby` 偶然入镜者 | anonymize / **exclude_capture / purge**（不得 retain） |
| `consent_denied_person` 明确拒绝者 | **exclude_capture / purge**（不得留存、不得导出） |

越界分类 → `MEDIA_TREATMENT_FORBIDDEN`。

- **普通游记**：仅 ordinary/child 且 retain 的素材可 `MEDIA_EXPORTED`；
  游客可请求删除（`MEDIA_DELETION_REQUESTED` → `MEDIA_DELETED`）。
- **事故证据**：`EVIDENCE_HELD` 携带合法依据、授权人、事故号与保全清单。
  保全期间导出 → `MEDIA_EXPORT_EVIDENCE_HELD`，删除 →
  `MEDIA_DELETE_EVIDENCE_HELD`；保全解除（`EVIDENCE_RELEASED`）后才可删除。
  保全的是证据库副本，设备本体仍按清除流程清空，不占用设备。
- **最小授权复盘**：`INCIDENT_REVIEW_OPENED` 引用的媒体必须是按**本事故**
  合法保全过的素材，否则 → `REVIEW_SCOPE_EXCESS`；安全负责人只能看到
  `review_view`（事故号、该事故证据 ID、设备日志引用），取不到无关行程。

## 7. 路线：硬约束压过偏好，旧缓存不得带路

- 约束 `kind`：construction（施工）、temporary_control（临时管制）、
  accessibility_restriction（无障碍限制）、closure。
- `severity=hard` 的生效约束必须出现在路线修订的 `considered_constraint_ids`
  中，个性偏好不得压过它 → 否则 `PREFERENCE_OVER_HARD_CONSTRAINT`。
- 硬约束发布后，必须先有覆盖该设备的 `ROUTE_CACHE_INVALIDATED`
  （scope：device / party / all_devices），随后的 `ROUTE_REVISED` 才合法；
  否则 → `STALE_ROUTE_CACHE`。
- 路线版本按设备单调递增；现场人工改道 `MANUAL_REROUTE_LOGGED` 只能发生在
  在租期间、只能升级版本、且登记的约束须为硬约束；平台随后补发同版本
  `ROUTE_REVISED` 与现场对齐。断网期间的改道按真实时间补齐。

## 8. 老年游客与助力设备

- 加装前必须有 `ASSISTANCE_FIT_CHECKED`（适配结论 passed/failed/conditional、
  检查人、**责任人 responsible_staff_id**）。
- `ASSISTANCE_DEVICE_ATTACHED` 必须引用对应成员的适配检查；无记录 →
  `ATTACH_WITHOUT_FIT_CHECK`，适配不合格仍加装 → `ATTACH_FIT_FAILED`。
- 途中异常由 `ASSISTANCE_ESCALATED` 升级给责任人。

## 9. 违例码

违例由 `fold()` 折叠后以 `Violation(code, message, event_id)` 返回，
现场端、安全端与集成测试共用同一套判定：

| 码 | 触发要点 |
| --- | --- |
| `IDEMPOTENT_CONFLICT` | 同 event_id 重放负载不一致 |
| `AGGREGATE_VERSION_DUPLICATE / _REGRESSED` | 聚合版本按到达序重复/回退 |
| `DEVICE_SEQUENCE_DUPLICATE` | 设备序列号重复 |
| `RESYNC_SEQUENCE_GAP / _COUNT_MISMATCH` | 断网补齐区间缺序列/条数不符 |
| `RENTAL_DEVICE_BUSY / RENTAL_GATE_BLOCKED / HANDOUT_GATE_BLOCKED` | 开单/发放闸门 |
| `HANDOUT_WITHOUT_RENTAL / HANDOUT_DEVICE_MISMATCH` | 发放与租约不一致 |
| `CONSENT_PROXY_FORBIDDEN / _NO_GUARDIANSHIP / _GUARDIAN_MISMATCH / _UPDATE_WITHOUT_RECORD` | 同意代理与变更 |
| `GUARDIAN_MUST_BE_ADULT / _WARD_MUST_BE_CHILD` | 监护声明合法性 |
| `PREFERENCE_OVER_HARD_CONSTRAINT / STALE_ROUTE_CACHE` | 路线硬约束与缓存 |
| `ROUTE_VERSION_NOT_MONOTONIC / MANUAL_REROUTE_*` | 路线版本与人工改道 |
| `ALERT_OUTSIDE_RENTAL / MANUAL_REROUTE_OUTSIDE_RENTAL` | 途中事件时序 |
| `MEDIA_TREATMENT_FORBIDDEN / _EXPORT_* / _DELETE_EVIDENCE_HELD / _UNKNOWN / _DUPLICATE_ID` | 媒体分类、导出、删除 |
| `EVIDENCE_UNKNOWN_MEDIA / _MEDIA_ALREADY_DELETED / _DUPLICATE_HOLD / _UNKNOWN_HOLD` | 证据保全 |
| `CLEAR_BEFORE_INSPECTION_PASSED / QUARANTINE_NOT_PERFORMED / QUARANTINE_*` | 清除与隔离 |
| `RELEASE_WITHOUT_REINSPECTION / _WITHOUT_SUCCESSFUL_CLEAR / _INCIDENT_REVIEW_OPEN / _WHILE_RENTAL_OPEN / _GATE_BLOCKED / _WITHOUT_QUARANTINE` | 放行闸门 |
| `REVIEW_SCOPE_EXCESS / _UNKNOWN_RENTAL / _CLOSE_WITHOUT_OPEN / _LEAD_MISMATCH` | 复盘范围与归属 |
| `ATTACH_WITHOUT_FIT_CHECK / _FIT_FAILED / _FIT_MEMBER_MISMATCH / FIT_UNKNOWN_MEMBER / ESCALATE_UNKNOWN_ASSISTANCE` | 助力设备 |

## 10. 本地检查

```bash
python3 -m src.schema          # 由 contracts 重新生成 JSON Schema
python3 data/build_samples.py  # 重新生成联调样例
python3 -m unittest discover -s tests
```

变更事件目录（`EVENT_SPECS`/枚举）后须依次执行前两步；测试会校验落盘
schema、样例与代码三者一致。
