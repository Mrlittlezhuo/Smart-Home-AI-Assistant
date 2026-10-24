# 全屋智能 Agent(灯具控制首个领域)

按 [全屋智能Agent技术架构设计.md](全屋智能Agent技术架构设计.md) 与
[灯具控制与多指令Agent架构设计.md](灯具控制与多指令Agent架构设计.md) 实现的
阶段一~五核心链路:

> 自然语言 → LLM 单次结构化规划(候选计划)→ 确定性校验器 → 任务执行器(顺序/并行/条件/延迟/部分失败)→ 基于真实结果的反馈

## 当前能力

- 灯具开关、亮度、色温、颜色;单灯 / 房间灯组 / 全屋
- 一句话多指令:顺序(depends_on)、并行、条件(condition)、延迟(delay_seconds)
- 状态查询(实时工具查询,不用历史记忆)
- 闲聊(与设备控制互不触发)
- 澄清(目标缺失/歧义/冲突)与拒绝(非法参数/未注册类型/不支持能力)
- 延迟任务:PostgreSQL 任务表 + 独立 Worker,行锁领取、幂等键去重
- 会话记忆:LangGraph PostgreSQL Checkpointer(线程状态)+ 每轮单轮字段重置
- 个人偏好(阶段四):仅“记住/以后默认”触发写入;设备/房间/用户三级作用域;
  按用户隔离;补全未说明的参数并记录来源;当前指令始终覆盖偏好;
  查询/删除/总开关 + REST 接口(`/v1/preferences*`)
- 高风险确认(阶段四):全屋控制、场景激活、一小时以后的定时任务 → LangGraph `interrupt`
  暂停,`POST /v1/plans/{id}/confirm`(带计划哈希)恢复同一计划,不重新规划
- 多领域(阶段五):lighting / curtain / scene 注册表驱动,校验器与执行器不认识
  具体领域;新增领域只需注册插件(任务类型、能力校验、处理器)
- 跨领域场景:`scene.activate`(内置“离家模式”= 全屋关灯 + 全屋关窗帘),
  执行时展开为各领域已注册动作,统一走确认与工具链
- 事件驱动自动化:设备事件 → `event_id` 去重 → 设备状态表更新 → 已启用规则
  确定性条件检查 → 幂等执行(事件 ID 编入幂等键,重复事件不重复触发)
- 设备适配器:Mock(故障注入/事件上报)+ Home Assistant(REST,`light.*`/`cover.*`
  实体映射,`unavailable` → 离线);两者通过同一套契约测试
  (`tests/contract/`,HA 用 `httpx.MockTransport` 假服务器)
- 星火终端前端(阶段零):浏览器打开 `http://localhost:8000/` 即可 —
  星火模式(canvas 粒子动画,八种终端状态配色/节奏)+ 聊天记录模式
  (消息列表 + 任务进度芯片);文字输入 + 麦克风语音输入(Chrome/Edge
  Web Speech API)+ 语音播报;确认操作弹出确认条(确认/取消);
  无操作自动回到星火模式,确认/失败切到聊天记录模式

## 快速开始

```bash
# 1. 依赖(Python 3.12+)
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 2. Postgres(业务表 + LangGraph 检查点)
docker compose up -d          # 端口 5433

# 3. 配置
cp .env.example .env          # 填入 LLM_AUTH_TOKEN
# 真实设备:DEVICE_ADAPTER=home_assistant + HA_BASE_URL + HA_TOKEN(默认 mock)

# 4. 启动 API 与 Worker
.venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m app.worker.scheduler

# 5. 打开前端
浏览器访问 http://localhost:8000/(推荐 Chrome,语音输入需要麦克风)

# 6. 测试 / 演示(注意:测试会重建数据库表,清掉开发数据)
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/demo_e2e.py       # 基础控制 + 多指令
.venv/bin/python scripts/demo_phase4.py    # 偏好 + 确认流
.venv/bin/python scripts/demo_phase5.py    # 窗帘 + 离家模式场景 + 事件自动化
```

对话接口:`POST /v1/chat {session_id, message, fixed_plan?}`;
`fixed_plan` 可跳过 LLM,直接验证主图链路(阶段一验收路径)。
会话历史:`GET /v1/sessions/{session_id}/messages`(聊天记录模式数据源)。
故障注入(仅 Mock):`POST /v1/debug/device-failure {device_id, mode}`;
事件注入(仅 Mock):`POST /v1/debug/device-event {device_id, event_type, event_id}`。

## 结构(对应架构文档 §17)

```
app/
├── graph/          # 主图:context → planner → validator → (approval) → executor → responder
├── domains/        # 领域插件注册表:lighting / curtain / scene
├── capabilities/   # 标准能力模型与参数规则
├── tools/          # 工具注册(副作用/超时/幂等元数据)
├── device_gateway/ # 统一网关 + DeviceAdapter 契约 + Mock / HomeAssistant 适配器
├── automations/    # 事件管道:去重 → 状态更新 → 规则触发
├── llm/            # LLM 网关(Anthropic 兼容接口,供应商可替换)
├── db/             # SQLAlchemy 业务表(plans/scenes/device_events/automations/...)
└── worker/         # 延迟任务 Worker
```

安全边界(两份设计文档的核心):LLM 只产出候选计划;设备存在性、能力、
参数、权限、冲突全部由程序校验;工具结果三级状态 `accepted/applied/verified`,
回复只能基于落库的执行事实。

## 尚未实现(按文档阶段推进)

- 阶段六:身份认证、OpenTelemetry、限流告警
- Alembic 迁移(当前 create_all)、星火/聊天记录前端(阶段零)
- 说明:偏好以业务表为唯一事实源(未另接 LangGraph Store,避免双写不一致);
  会话临时要求(优先级第二级)暂未实现,当前为 明确指令 > 偏好 > 默认
