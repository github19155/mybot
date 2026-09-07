# 非阻塞 Subagent 控制与动态角色——实施计划

日期: 2026-09-08

## 目标

按已批准的 `docs/superpowers/specs/2026-09-08-subagent-control-design.md`，将现有
`spawn` 子代理机制替换为统一的 `subagent` 工具，使主会话默认可以继续收发消息，
并允许主线程控制任务、选择模型/思考强度、动态管理角色。完成后部署到已存在的 Linux
服务器；部署前必须重建 Docker 镜像。

## 约束

- 保持进程内 `asyncio.Task` 架构，不新增 worker、队列服务或依赖。
- 删除旧 `spawn` 工具和所有兼容提示词，不保留别名。
- 默认后台运行，只有显式 `wait=true` 才阻塞当前主线程。
- 每个主会话最多 8 个运行中子代理，实例最多 16 个；超出后排队。
- 角色权限只能取父会话当前允许工具的交集；禁止嵌套 subagent。
- 保留用户已有 `.gitignore` 修改，不暂存、不覆盖。
- 运行任务使用启动时角色/模型/思考配置快照；nanobot 重启不恢复子任务。

## 执行步骤

### 1. 配置、角色与运行时解析（先测试后实现）

先扩展配置与模型管理测试，覆盖动态角色字段、名称/工具/模型互斥校验、
builtin 覆盖/禁用/删除/重置、自定义角色 CRUD、原子失败，以及 per-run 字段高于
角色/预设/父运行时的优先级。再实现：

- `SubagentRoleConfig` 与 `Config.subagent_roles` 的动态角色字段和 camelCase 别名。
- `subagent_roles.py` 的 builtin 模板、角色解析、工具权限映射与名称校验。
- `SubagentRoleStore`，复用现有配置锁和原子写入路径。
- `ModelManagement` 的模型、preset、`thinking -> reasoning_effort`、温度/超时解析。

验证：角色/解析相关 pytest 与 `basedpyright`。

### 2. Manager 调度与生命周期（先测试后实现）

先为 manager 添加失败测试：后台 `run` 立即返回、`wait=true` 返回结果，单会话 8/全局
16 限制排队且按可用性公平 admission，任务状态流转、终态保留 50 条、steer/stop、
超时/异常隔离、关闭时取消，以及完成通知在父会话忙时不抢占。补充 `fork` 历史快照和
`fresh` 隔离、父工具权限交集、Linux shell 进程组清理测试（平台不可用时标记条件跳过）。

再修改 `SubagentManager`：

- 将立即超限改为有界排队与 admission pass，分别维护全局和 session 运行计数。
- 每个任务固定 role/model/thinking/context 快照，支持 queued/running/completed/failed/stopped。
- 为 `steer` 复用已有 bounded injection queue；queued 任务追加 launch brief。
- stop queued/running 统一记录 `stopped`，释放槽位并等待取消传播。
- 完成事件仅投递一次，交由已有 session pending queue/锁在主 turn 结束后处理。
- 新增 fork 历史只读快照和 portable message sanitization，禁止子代理再创建 subagent。

验证：subagent lifecycle/fleet/loop-save-turn 相关测试，随后全量 backend tests。

### 3. 统一 `subagent` 工具和提示词（先测试后实现）

先更新工具测试，确保 `spawn` 不在 registry、工具契约支持 `run/status/steer/stop` 和
全部 `role.*` action，校验错误在 admission 前返回，并覆盖 wait/background、权限、
模型/思考参数。然后：

- 删除 `nanobot/agent/tools/spawn.py`，新增 `subagent.py`。
- 在工具注册/加载路径接入单一 `subagent` 工具。
- 更新 `tool_contract.md`、`subagent_system.md` 和相关模板，移除所有 `spawn` 文本，
  只在主提示词公布角色名和简述。
- 角色 CRUD 使用 manager/store 的原子接口；状态/控制只允许当前父 session 的任务。

验证：工具、提示词、注册相关 pytest 与 `rg` 确认生产提示词和注册代码没有旧 `spawn`。

### 4. 请求上下文、接口与 FleetView（先测试后实现）

先增加 loop/API/WebUI 测试，确认父会话工具集合和 fork 历史注入、完成通知幂等，
FleetView 能显示 queued/running/completed/failed/stopped 及 role/model/thinking、当前
工具、耗时、token usage。

实现：

- 扩展 `RequestContext`，在主 turn 构造允许工具名和只读历史快照。
- 保持 `/api/subagents` 只读、实例级聚合；补充统一状态字段，不增加 WebUI 控制按钮或
  角色编辑器。
- 更新 `webui/FleetView.tsx` 和对应测试。

验证：WebUI test/build，API/loop 测试和 `ruff`。

### 5. 集成验证与提交

按项目命令运行：

```text
uv run --no-sync pytest
uv run --no-sync ruff check nanobot/ tests/
uv run --no-sync basedpyright
cd webui && bun run test && bun run build
```

若环境缺少可选依赖，记录实际阻塞并运行可用的针对性测试。检查 git diff，确保只提交
本功能文件和实施计划，不包含 `.gitignore`。提交实现。

### 6. 服务器部署

服务器为 `ubuntu@165.154.62.99`，远端项目 `~/nanobot`，当前分支
`feature/subagent-fleet`，无 Git remote。使用当前分支生成 `git bundle`，通过已有
SSH key 上传，服务器 `git fetch` 后 `git merge --ff-only`，不覆盖服务器配置。

进入远端项目后必须先重建镜像：

```bash
cd ~/nanobot
NANOBOT_CHANNELS=weixin docker compose -f ~/nanobot/docker-compose.yml build nanobot-gateway
NANOBOT_CHANNELS=weixin docker compose -f ~/nanobot/docker-compose.yml up -d --force-recreate nanobot-gateway
```

确认容器、日志和健康检查：

```bash
curl -fsS http://127.0.0.1:18790/health
docker inspect -f '{{.Config.Image}} {{.State.Status}}' nanobot-gateway
docker logs nanobot-gateway --since 5m
```

不得在最终回复中回显服务器文档中的 WebUI 密钥。

## 完成标准

- 主会话在后台子代理运行、排队、完成、steer 或 stop 时仍可继续处理消息。
- `wait=true` 是唯一显式阻塞当前父 turn 的 subagent 路径。
- 动态角色和 per-run 模型/思考参数可用且权限不越界。
- 旧 `spawn` 已从注册、提示词、测试和用户可见示例移除。
- 相关测试、lint、类型检查和 WebUI 构建通过，服务器镜像已重建并健康运行。
