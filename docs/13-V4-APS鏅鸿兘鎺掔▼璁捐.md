# V4.0 APS 智能排程设计

## 1. 目标与边界

V4.0 提供面向 1–20 台 CNC 的可解释启发式 APS 排程原型。它将已由工艺/计划人员确认的 APSJob 纳入滚动排程，输出草案分配；只有计划员/管理员审批后，才写入 V2 `schedule_plans` 正式排程表。V4 不自动推断机床加工能力，不替代工艺工程师的设备适配确认。

## 2. 任务输入

`POST /api/v4/aps/jobs`

字段：`job_no`、`work_order_id`、`operation_id`、`priority(1..10)`、`due_at`、`release_at`、`required_minutes`、`eligible_machine_codes`。合格设备列表必须由有权限的计划/工艺人员显式提供；若工序在 V1 `operations.machine_id` 中已指定设备，调度器将其视为硬约束。

## 3. 排程策略

当前 strategy=`priority_edd`，实现规则：

1. 以 APSJob 的最高优先级、最早交期对工单组排序。
2. 同一工单内按 MES 工序 `sequence` 顺序排程，确保被纳入本批次的前序工序先于后序工序。
3. 对每个任务的合格设备计算最早完工时间，选择最早完工的设备；同时间按设备编码稳定排序。
4. 尊重已审批/执行中的 V2 SchedulePlan 时间占用，以及本次草案中的临时占用。
5. 支持工作日、班次开始/结束和一个午休窗口。默认周一至周六 08:00–17:00，12:00–13:00 休息。
6. 生成结果仅是草案；草案任务会进入 `drafted` 状态，避免被重复排程；未审批草案作为软占用，避免另一批次生成重叠建议。审批端点会重新检查正式排程冲突，并核对已指定设备约束；驳回后任务回到 `queued`。

## 4. API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v4/aps/jobs` | 查看任务，可按 status 筛选 |
| POST | `/api/v4/aps/jobs` | 创建待排任务 |
| POST | `/api/v4/aps/generate` | 生成排程批次与草案分配 |
| GET | `/api/v4/aps/runs` | 查看批次 |
| GET | `/api/v4/aps/runs/{run_id}/assignments` | 查看批次分配 |
| POST | `/api/v4/aps/assignments/{id}/approve` | 审批单条分配并生成正式 SchedulePlan |
| POST | `/api/v4/aps/assignments/{id}/reject` | 驳回草案并把任务退回队列 |

## 5. 当前限制

- 当前是启发式排程，不是数学规划/遗传算法/强化学习求得的全局最优解。
- 不自动计算工装、夹具、刀具、材料齐套、换型矩阵、人员资质、维护窗口及工序外协约束；需要把这些能力作为后续资源约束建模。
- 同工单顺序保证仅适用于同一批次中已录入的 APSJob；如前序工序没有建任务，系统无法判断其未完成状态。
- 时间通过 `PLANT_TIMEZONE`（IANA 时区）解释：无时区 API 值按工厂本地时间解释，有时区值按自身偏移转换；排程相关表（APSJob、APSRun、APSAssignment、SchedulePlan）统一保存工厂本地无时区 wall-clock，V2排程接口也按该规则解释时间。部署前必须设置工厂真实时区，并统一客户端时区约定。历史上由旧版 V2 API 以带时区值写入、并已保存为 UTC naive 的排程记录，应先核对时区后再启用 APS。
- 周期休息目前只支持一个休息时间段；多班次、夜班跨日和节假日例外日历需后续扩展。
- 排程结果需计划员核实后审批，不建议无人值守自动审批。
