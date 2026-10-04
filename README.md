# 伴游设备旅程托管

本仓库保存伴游设备旅程托管服务的领域词汇、事件契约、联调样例与不变量校验，
供设备端与业务系统统一对象身份、事件顺序、版本语义与责任边界。

## 目录

- `docs/domain.md`：领域说明（聚合、事件目录、状态机、幂等/断网语义、违例码）
- `src/contracts.py`：聚合、32 个事件与稳定枚举的**单一事实源**
- `src/validator.py`：事件信封与载荷校验（入口同步可用的纯函数）
- `src/policies.py`：事件流折叠与领域不变量（再出租闸门、同意代理、
  三类入镜者差异处理、证据保全、硬约束压偏好、缓存失效、断网补齐、
  助力适配前置、最小授权复盘）
- `src/schema.py`：由 `contracts.py` 生成 `contracts/domain.schema.json`
- `contracts/domain.schema.json`：落盘 JSON Schema（draft 2020-12）
- `data/sample.json`、`data/scenarios/`：单条样例与四个正向场景 + 反例集
- `data/build_samples.py`：样例生成器（同时是测试夹具来源）
- `tests/`：契约一致性、校验器、策略折叠、幂等与断网时序测试

初次提交的 5 个事件（DEVICE_HANDED_OUT、CONSENT_RECORDED、ROUTE_REVISED、
ASSISTANCE_ESCALATED、DEVICE_CLEARED）与 4 个聚合全部保留，新增事件仅追加。

## 核心规则摘要

1. 只有**归还检验通过且数据清除成功**，设备才可再次出租；失败必须隔离，
   现场看到隔离原因，看不到任何行程内容。
2. 摄录/定位意愿**途中可变更**：成年人只能本人表达；儿童由已核验监护人代表达；
   租用人不得替其他成年人同意。
3. 儿童、偶然入镜者、明确拒绝者的素材处理方式各不相同；普通游记可导出/删除，
   事故证据按合法范围保全，安全复盘只能取本事故保全的证据与设备日志引用。
4. 施工、临时管制、无障碍限制等硬约束压过个性偏好；旧缓存在约束发布后必须
   先失效才能继续带路。
5. 断网期间的告警、人工改道、设备状态按**真实发生时间**归位，聚合版本按
   **平台到达序**管理，序列号区间连续才算补齐成功。
6. 老年游客加用助力设备前必须留下适配检查与责任人，不合格/未检查不得加装。

## 本地检查

```bash
python3 -m src.schema          # 由 contracts 重新生成 JSON Schema
python3 data/build_samples.py  # 重新生成联调样例
python3 -m unittest discover -s tests
```
