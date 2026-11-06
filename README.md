# CNC-MES Smart Factory V4.0

面向 1–20 台 CNC、20–50 名员工的私有化制造执行系统工程包。V4.0 在 V1/V2/V3 的生产执行、质量、库存、刀具、工艺、设备联网、报警和 OEE 基础上，新增启发式 APS 排程、可解释生产分析 Agent、基于工厂配置阈值的预测性维护趋势基线，以及工单/工序/设备/员工/材料批次/刀具追溯事件账本。

> **重要：**这是可运行的试点基线，不是未经现场验证即可直接用于生产的商业 MES。所有 CNC/PLC 连接保持只读，不包含程序下载、远程启停、复位、刀补修改或其他设备控制能力。

## 1. 快速部署

### 1.1 准备配置

```bash
cp .env.example .env
```

修改 `.env` 中所有占位符。至少需要设置：

- `POSTGRES_PASSWORD`：数据库强密码，并同步修改 `DATABASE_URL` 中的密码。
- `TOKEN_SECRET`：至少 32 位随机值，用于用户 Bearer Token 签名。
- `MES_ADMIN_PASSWORD`：首次管理员密码，至少 12 位。
- `EDGE_INGEST_TOKEN`：至少 32 位随机值，边缘网关到 API 的独立共享密钥。
- `PLANT_TIMEZONE`：工厂 IANA 时区，APS 班次日历与 V2 排程共用，例如 `Asia/Tokyo` / `Asia/Shanghai`。

可用 Python 生成随机密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

不要将 `.env`、真实密码或边缘密钥提交到版本库或发入工单。

### 1.2 启动 MES 核心服务

```bash
docker compose up --build -d
```

若在局域网内通过 MES 服务器 IP 打开 Web，请把 `.env` 中 `CORS_ORIGINS` 增加为实际浏览器来源，例如 `http://192.168.1.50:5173`。

访问：

- Web：`http://<MES服务器IP>:5173`
- API 文档：`http://<MES服务器IP>:8000/docs`
- 健康检查：`http://<MES服务器IP>:8000/health`

首次启动会创建管理员账号（由 `MES_ADMIN_USER` 和 `MES_ADMIN_PASSWORD` 提供），数据库迁移会在 API 启动前执行。首次登录后创建具名用户并按岗位分配角色。

### 1.3 启动模拟边缘网关

默认 `edge_gateway/config.yaml` 使用模拟 CNC 数据源，可在没有真实机床的情况下验证整条链路：

```bash
docker compose --profile edge up --build -d
```

模拟网关每 5 秒发送状态、主轴负载、转速、周期时间、理想节拍和累计合格/不合格计数器。边缘 Outbox 存在 Docker 命名卷 `edgeoutbox`，API 暂时不可用时消息会保留并重试。进入 Web 的“实时设备监控”“设备 OEE”“设备报警事件”“边缘网关状态”模块检查数据。

若要更换采集设备，编辑 `edge_gateway/config.yaml`；保留 `.env` 中相同的 `EDGE_INGEST_TOKEN`。修改后重启：

```bash
docker compose --profile edge up -d --build edge-gateway
```

## 2. V3.0 设备接入模式

| 模式 | `adapter` | 说明 |
|---|---|---|
| 模拟数据 | `mock` | 默认，开发/测试/演示使用 |
| OPC UA | `opcua` | 通过 endpoint 和 NodeId 映射读取变量，需要厂商开放只读账户 |
| MTConnect | `mtconnect` | 读取 Agent 的 `/current` XML，需按 Probe 中的 dataItemId 映射 |

配置模板见 `edge_gateway/config.example.yaml`。OPC UA NodeId、MTConnect Agent URL、状态枚举和点位映射都必须依据真实控制器/Agent 的版本及数据字典确认。当前 OPC UA 客户端还未包含客户端证书与 SignAndEncrypt 自动配置，正式 OT 接入前必须完成安全通道配置和验证；不能直接将示例 IP 或 NodeId 用于生产。

## 3. V3.0 核心能力

- **边缘采集：**独立 Python 服务，主动向 MES API 发送采集数据；支持轮询、时间戳、唯一消息 ID、重复消息去重和网关心跳；API 中断时先写入本地 SQLite Outbox，恢复后按原消息 ID 顺序补传。
- **设备状态：**将常见状态归一化为 `running / idle / setup / alarm / maintenance / planned_stop / offline / unknown`，保留状态时间段。
- **设备点位：**将标准点位保存到既有 `device_readings` 表，支持最近值与历史读数查询。
- **报警引擎：**阈值规则、设备范围、严重程度、冷却时间、确认和关闭；业务操作写入审计日志。
- **OEE：**时间开动率、性能效率、质量合格率和综合 OEE；返回窗口计数器增量、理想节拍和数据完整性标识。
- **网关健康：**心跳和最后在线时间，辅助识别采集进程不可用。
- **Web：**增加实时设备监控、OEE、设备报警和边缘网关页面。

## 4. OEE 计算口径

- Availability = 设备运行时间 / 计划生产时间。
- Performance = 理想节拍 × 总产量 / 运行时间。
- Quality = 合格数量 / 总产量。
- OEE = Availability × min(Performance, 100%) × Quality。

`planned_stop` 和 `maintenance` 从计划生产时间分母扣除。尚未结束的状态段最多延续到最后一条有效设备数据接收时间 + 90 秒，防止采集断线后无限累计运行时间。产量依据 `good_count_total`、`reject_count_total` 两个累计计数器在 OEE 时间窗内的增量计算。

计数器清零、回绕、换班清零和补传数据需要结合机床实测策略处理。缺少状态历史、计数器或理想节拍时，接口会将 `data_complete` 标为 `false`。该字段仅代表采集要素齐备，默认口径尚未配置班次/休息日历（`shift_calendar_applied=false`），正式 OEE KPI 上线前必须先校准班次日历和非生产时段规则；未校准前不得将模拟数据用于薪酬、产能承诺或正式 KPI。

**可选班次日历口径（V4.0 新增）：** `GET /api/v3/oee/machines/{id}` 支持查询参数 `shift_start`、`shift_end`、`break_start`、`break_end`（`HH:MM`）与 `workdays`（0=周一…6=周日，可多次传入）。四段班次时间必须成组提供，`shift_start < shift_end`、休息时段包含于班内、`workdays` 取值 0–6，任一参数缺失或非法返回 400。启用后计划生产时间 = 查询窗口 ∩ 工作日班次时段（扣除休息时间），按 `.env` 的 `PLANT_TIMEZONE` 解释本地时间；`shift_calendar_applied=true`，且班次口径下 `data_complete` 要求计划时间 > 0。示例：

```
GET /api/v3/oee/machines/1?shift_start=08:00&shift_end=17:00&break_start=12:00&break_end=13:00&workdays=0&workdays=1&workdays=2&workdays=3&workdays=4&workdays=5
```

## 5. 主要 API

| 模块 | 路径 | 权限/说明 |
|---|---|---|
| 设备采集 | `POST /api/v3/edge/ingest` | 仅验证 `X-Edge-Token`，边缘服务调用 |
| 网关心跳 | `POST /api/v3/edge/heartbeat` | 仅验证 `X-Edge-Token` |
| 网关列表 | `GET /api/v3/edge/gateways` | 登录后，管理/维修/只读角色 |
| 设备总览 | `GET /api/v3/monitor/overview` | 登录后 |
| 点位历史 | `GET /api/v3/monitor/machines/{id}/readings` | 登录后 |
| OEE | `GET /api/v3/oee/machines/{id}` | 登录后；默认最近 8 小时；可选班次日历参数见第 4 节 |
| 报警规则 | `GET/POST /api/v3/alarms/rules` | 查询/管理员或维修人员新增 |
| 报警事件 | `GET /api/v3/alarms/events` | 登录后 |
| 报警确认 | `POST /api/v3/alarms/events/{id}/acknowledge` | 管理/维修/操作员 |
| 报警关闭 | `POST /api/v3/alarms/events/{id}/resolve` | 管理/维修 |

旧版 `/api/v1`、`/api/v2` 接口保持兼容。V1 API 默认也要求 Bearer Token，且 `viewer`/`gateway` 角色对写操作（POST/PUT/DELETE/PATCH）返回 403。登录接口有内存限流：同一用户名连续失败 5 次后 15 分钟内返回 429，成功登录自动清除计数。

## 6. 数据库迁移

- V1 -> V2：`0002_v2_mes_modules`
- V2 -> V3：`0003_v3_edge_oee`
- V3 -> V4：`0004_v4_aps_ai_trace`
- 新部署从 V1 baseline 顺序升级至 head。
- 迁移脚本的 downgrade 不执行破坏性删除；回滚前必须备份并另行批准。

## 7. 项目目录

```text
CNC-MES-Smart-Factory-V4.0/
├── backend/
│   ├── app/                 # FastAPI，V1/V2/V3/V4 模型与 API
│   ├── migrations/          # Alembic 迁移
│   └── tests/               # 冒烟测试
├── edge_gateway/
│   ├── adapters/            # mock / OPC UA / MTConnect 只读适配器
│   ├── config.example.yaml
│   └── Dockerfile
├── frontend/                # Vue 3 + Vite
├── docs/                    # 需求、架构、数据字典、部署、V3/V4设计与验收
├── agents/                  # APS Planner / Production Analysis Agent 角色规范
└── docker-compose.yml
```

## 8. 已执行验证与未验证项目

当前工程完成的本地验证：

可在开发环境按以下命令重跑集成冒烟测试：

```bash
cd backend
pip install -r requirements-test.txt
PYTHONPATH=. python tests/smoke_test_v3.py
PYTHONPATH=. python tests/smoke_test_v4.py
```


- Python 后端及边缘服务 `compileall` 语法检查。
- SQLite 全新安装迁移 V1 -> V2 -> V3。
- 已有 V2 数据库从 `0002` 升级到 `0003`。
- FastAPI TestClient 冒烟测试：管理员登录、设备创建、报警规则、边缘消息接收与去重、同 ID 内容冲突拒绝、坏质量数据不触发阈值报警、网关心跳、监控总览、OEE 计算、未授权边缘访问拒绝。
- Outbox 测试：SQLite 队列持久化、模拟 API 中断时保留消息、模拟恢复后成功确认并删除队列项。
- OEE 数据质量保护：未关闭状态段在最后有效数据接收时间 + 90 秒后停止累计。
- V1 既有数据库升级 V1 -> V2 -> V3 -> V4，并验证旧设备/工单记录保留；V2 既有数据库升级 V2 -> V3 -> V4，并验证员工主数据保留。
- V4 冒烟测试命令：`PYTHONPATH=. python tests/smoke_test_v4.py`；迁移保留测试还需单独执行项目维护者提供的迁移测试脚本。
- V4 APS 测试：工单内工序顺序、同一任务防重复排程、草案审批/驳回回队列、设备时间占用和工厂时区转换。
- APS 回归测试：排程窗口内已存在已审批 SchedulePlan 时，`POST /api/v4/aps/generate` 正常返回 200 且草案避开占用时段（修复此前因未定义函数 `_utc_naive_to_plant` 导致的 500；占用窗口现与 V2 排程一致按工厂本地墙钟直接比较）。
- 写路径行锁已在 SQLite 冒烟环境回归验证（PostgreSQL 上 `FOR UPDATE` 生效，序列化报工/刀具消耗/出库/排程审批/APS 审批/边缘接入）。
- V4 维护评估测试：阈值新增/更新、趋势样本和 24 小时外推风险分级。
- V4 AI 生产分析与追溯事件新增、查询。
- 示例 OEE 测试输入 10 分钟运行、10 合格、1 不合格、30 秒理想节拍，输出 A=100%、P=55%、Q=90.91%、OEE=50%。

V4.0 本机已完成的运行验证（2026-11）：

- Docker Compose 全栈构建并启动（PostgreSQL 16 + API + 前端生产构建 + 模拟边缘网关）；网关持续上报，`outbox_pending=0`。
- 前端生产构建（`vite build` + `vite preview`）已装入镜像并对外提供 5173。
- 实库 E2E：登录、演示数据初始化、监控总览、OEE（默认口径与班次口径）、APS 任务→生成草案→审批写入正式排程→重新生成不重复占用的完整链路。
- 边缘 Outbox：API 可用时按序投递并确认删除；持久性 4xx 与超限重试转入死信（冒烟测试覆盖）。

仍未在此环境完成：PostgreSQL 实库高并发与性能压测、真实 OPC UA/MTConnect 设备联调、断网长时缓存与补传耐久性测试、现场 OEE 校准及 OT 安全验收。不要把这些事项理解为已通过。

## 9. V4.0 新增模块

### APS 智能排程

- `GET/POST /api/v4/aps/jobs`：录入已由工艺/计划人员确认的工序排程任务。
- `POST /api/v4/aps/generate`：按优先级、交期、工序顺序和合格设备集合生成草案；支持班次、休息时间和工作日。
- `GET /api/v4/aps/runs`、`GET /api/v4/aps/runs/{id}/assignments`：查看批次与分配。
- `POST /api/v4/aps/assignments/{id}/approve`：人工审批时二次检查设备冲突并创建正式 V2 SchedulePlan。
- `POST /api/v4/aps/assignments/{id}/reject`：驳回草案并释放任务回队列；待审批草案会阻止其他批次产生重叠建议。

当前算法是可解释的贪心/最早完工启发式，不是全局最优 APS。未自动建模夹具、刀具、材料齐套、人员技能、换型、维护窗口及多班次节假日；必须由计划员核实设备适配和工艺约束后审批。V4 APS 通过 `.env` 的 `PLANT_TIMEZONE`（IANA 时区）解释工厂班次；无时区 API 值按工厂本地时间解释，有时区值按偏移转换，APS 与 V2 SchedulePlan 统一保存工厂本地无时区 wall-clock，便于共用设备占用日历。历史上由旧版 V2 API 以带时区值写入并保存为 UTC naive 的排程记录，应先核对时区后再启用 APS。部署时必须配置真实工厂时区，例如 `Asia/Tokyo` 或 `Asia/Shanghai`。

### AI 生产分析 Agent

- `POST /api/v4/ai/production-analysis/run?days=7`：生成生产统计、良率、人工工时、NCR、报警和状态停机规则分析。
- `GET /api/v4/ai/production-analysis/latest`：读取报告。

`explainable_rules_v1` 是规则分析，不是 LLM 或训练好的机器学习模型；98% 良率线为示例规则，需要工厂按产品族校准。

### 预测性维护趋势基线

- `GET/POST/PUT /api/v4/maintenance/thresholds`：配置设备/点位阈值。
- `GET /api/v4/maintenance/predictions`：读取最近传感器样本，计算线性趋势并外推 24 小时（纯只读，不落库）。
- `POST /api/v4/maintenance/purge`：仅管理员；按 `days`（默认 30）清理过期设备读数、边缘消息与已关闭状态段，返回各表清理数量与截止时间，用于定期数据生命周期管理。

阈值须依据设备手册和健康基线配置。线性外推不是故障概率或经验证的故障预测模型。

### 生产全流程追溯

- `POST /api/v4/traceability/events`：写入工单、工序、设备、员工、材料批次、刀具和数量事件。
- `GET /api/v4/traceability/work-orders/{id}`：按工单追溯。
- `GET /api/v4/traceability/material-lots/{lot}`：按材料批次反查。

V4 当前提供事件账本接口，还没有自动从所有既有报工/领料/检验/刀具更换流程生成追溯事件，正式上线前需增加事务内事件/outbox 集成。

## 10. V4.0 验证

```bash
cd backend
PYTHONPATH=. python tests/smoke_test_v4.py
```

## 11. 生产上线前必做

1. 变更 `.env` 所有占位密钥；将 `PLANT_TIMEZONE` 设置为工厂 IANA 时区（如 `Asia/Tokyo` / `Asia/Shanghai`）；启用 TLS，限制 API 与前端访问网段。
2. 将边缘网关放置于经批准的 OT DMZ/工业边缘区，控制器专用只读身份，防火墙白名单。
3. 完成备份、恢复演练、日志留存、监控告警、密钥轮换与补丁管理；定期监控边缘 Outbox 堆积量与最老消息时间。
4. 针对计数器重置、控制器时钟、网络断开、状态映射、班次日历、计划停机口径进行现场验证。
5. 由生产、设备、质量、IT/OT 安全负责人共同签署单机试点验收后再扩展。
