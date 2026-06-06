# 0001. 用 worker 异步状态机替代同步 services 业务函数

**日期**:2026-06-06
**状态**:Accepted

## 背景

项目最初是按"同步阻塞业务函数"思路写的:

- `services/vps_register.py::register_vps()` —— 一进来就 SSH 探测 → 装 xray →
  启动 → 端口审计 → 返回 dict
- `services/ip_register.py::register_ip()` —— 一进来就挑 VPS → 挂 inbound →
  内外 ping → 返回 dict

每个业务函数=一次性脚本:同步阻塞地跑完所有步骤,失败就抛 status 给上层。
最初设想把这些函数包成 MCP 工具,对外暴露 `rgvps` / `rgip`。

**关键认知拐点**(2026-06-06 那次对话):

用户意识到这些业务函数本质是"一次性脚本",没有以下能力:

1. **失败重试** —— 装机途中 GitHub 拉不下来,直接抛错给用户,不会自动等几分钟再试
2. **熔断** —— 连续失败 N 次不自停,会持续浪费资源
3. **资源协调** —— 两条业务线(VPS 装机 + IP 挂代理)抢同一台 VPS 时没有裁判
4. **状态可见性** —— agent 不知道任务跑到哪一步,只能等结果
5. **状态持久化** —— 进程被杀重启后,正在跑的任务无法续上
6. **agent 干涉风险** —— 如果对外暴露内部步骤(SSH/装包/开端口),agent 可能越权,
   后端管控能力被掏空

这些能力在"常驻后端服务"形态下是基础设施,在"一次性脚本"形态下完全缺失。
把脚本简单包成 MCP 工具不能填补这个缺口。

## 决策

引入 **worker 异步状态机架构**,新业务编排层用 `workers/`,旧 `services/`
保留作对照不删。核心改动:

1. **工人(worker) = 新业务层** —— 主动决策、抢锁、写表、扫 task。
   旧 `services/*.py` 同步阻塞业务函数被工人取代。

2. **任务表(task) = 异步协调媒介** —— rgvps/rgip 入口写一行 task 立刻返回,
   worker 后台轮询消费;状态机字段:pending / in_progress / pending_retry /
   done / failed / circuit_broken。

3. **工具箱 = xray/manager.py::XrayManager** —— 本项目就 xray 一个软件领域,
   不拆 `kits/`,直接复用现有 `xray/manager.py`。XrayManager 是对象式工具箱,
   被工人 import 实例化使用。新方法直接写在类里,不再绕 service.py / config.py。
   旧 `xray/service.py` + `xray/config.py` 作为代码片段参照保留,
   **新代码完工 + 真机验证后整体删除**(见本项目代码组织策略)。

4. **资源池协调** —— 一台 VPS 同时只能被 1 个 worker 持锁(task.locked_until
   软锁,worker 抢到 task = 抢到 task.vps_id 那台机的操作权),避免装机和挂代理
   争抢同一台。

5. **MCP 暴露三类工具,绝不暴露内部子动作**:
   - 写入意图(rgvps / rgip):提交意图立刻返回
   - 状态查询(get_vps_registration_status / get_ip_registration_status):
     agent 看后端干啥的唯一窗口
   - 数据查询(get_available_proxy_nodes):纯只读

6. **行为故事先于实现** —— 每个 worker 在 `tests_behavior/<worker>/spec.md`
   有产品视角的验收标准(用户口述原话金标准),实现按 spec 写。

## 备选方案

### 方案 A:保持同步 + 在 MCP 层做退避(被否决)

把 `register_vps()` 包成 MCP 工具,失败让 agent 重试。
**否决理由**:
- agent 不该懂后端重试逻辑(熔断、退避、并发限制等),这些是后端职责
- 失败时 agent 看到的是 raw error,无法判断"是临时抖动还是参数错"
- 资源争抢无法协调:两个 agent 并发调 rgvps 装同一台机,后端会乱

### 方案 B:用消息队列(Redis / RabbitMQ / Kafka)替代 task 表(被否决)

**否决理由**:
- 项目当前规模(个人/小团队、SQLite 持久层)上消息队列是过度工程
- SQLite + 轮询完全够用,且无新依赖
- 未来真有性能瓶颈再换不迟

### 方案 C:每个业务一个独立进程(被否决)

每个 worker 独立进程,通过 task 表通信。
**否决理由**:
- 单进程多 worker 在 Python 用 asyncio 也能完成,够用了
- 多进程引入进程协调、日志聚合、信号处理等复杂度,YAGNI

## 后果

### 好处

- agent 调一次 rgvps 立刻返回 task_id,3 秒内拿到反馈
- 失败自愈:网络抖动自动重试,熔断防止资源浪费
- 状态可观测:agent 通过查询工具看到"正在装 xray / 等下次重试"
- 资源争抢被裁判:VPS 同时只 1 worker 操作
- 内部步骤完全藏在后端,agent 无法越权
- 旧 services/ 留着作对照,新代码崩了能 fallback 看老逻辑
- 旧 `xray/service.py` + `xray/config.py` 同样留着作片段参照,**完工后整体删除**
- worker / kit / task 体系是可复用模式:加新软件 = 加新 kit,加新业务 = 加新 worker,
  加新工具 = 加新 tools/<name>.py

### 引入的新约束

- 必须建 `task` 表(及配套 ORM 模型)
- 必须实现轮询循环(可放主进程的后台 asyncio task)
- 必须实现软锁机制(locked_until + worker_id 字段)
- 必须为每个 worker 写 `spec.md`(行为规约)
- 必须改造 MCP 入口:rgvps / rgip 工具拿到入参后只入库 + 建 task 立刻返回,
  不再阻塞跑完业务
- 旧 services/ 暂不删保留对照(占空间但不污染新代码)

### 风险

- 第一次跑这套架构,可能在锁、轮询、状态机分支上踩坑(测试覆盖要严)
- spec.md 维护成本(但 single source of truth 比多处复制好)
- 工人之间"接力"逻辑(谁建谁的 task)要在 spec 里明确,否则会有遗漏

## 用户口述原话(关键节选)

> "我突然幡然醒悟,我的 rgvps 和 rgIP 目前还是还只是一次性脚本,虽然有业务编排层
> 但是缺少了自循环的能力,我的初衷是对外只展示 rgvps 和 rgip 这两个 mcp 工具入口
> 就给这两个工具和若干查询工具,但是目前的程序还不是这个形态,所以你要在
> CLAUDEmd 中写入设计实现的时候应该往 mcp 注册的角度想;就是业务编排层他还要能
> 支持失败了自己调其他工具重试而不是失败了直接停下,就像登记 vps 要先连接服务器,
> 如果服务器拒绝连接或者因为服务器厂商速率限制而不给连,应该先在表里登记状态等到
> 一定时间再回去自己循环一次"

> "我应该站在一台服务器的全生命周期管理上面的角度去思考问题。我先不管外部传进来
> 是服务器 IP 还是 IP 的出口 IP。VPS 服务器始终都是作为那个被操作的对象。"

> "诶,前面我们说的那个业务是有一个安全策略组问题的吧。安全策略组的问题只有外部
> 服务商那边能设置,然后我们安全检查就需要进来。"

> "现在已经有两个完整业务线了"

> "其实就跟爬虫一样了;main 从 vps 和 IP 来判断要启动哪个工人;就像 xhs 启动 xhs 爬虫,
> dy 启动 dy 爬虫,后面不同的是会抢同一个 vps 对象"

## 后续

本决策落地需要以下配套(各自单独 task / spec):

- `CLAUDE.local.md` 追加"业务编排:worker / kit / task 体系"一大节
- 每个 worker 写 `tests_behavior/<worker>/spec.md`(行为规约)
- `db/models.py` 新增 `Task` 表
- 实现 4 个工人:SSHWorker / XrayWorker / IPProbeWorker / ProxyDeployWorker
- 重写 `xray/manager.py::XrayManager`:新方法全部直接在类里写实现,
  不 import `xray/service.py` 也不 import `xray/config.py`
- MCP 入口工具改造:rgvps / rgip 从同步执行改成"建 task 立刻返回"
- 新增状态查询 MCP 工具:`get_vps_registration_status` /
  `get_ip_registration_status`
- 旧 `services/` 不删,作对照参考
- 封存 worker:HealthCheckWorker / ExpiryWorker / CleanupWorker(后续单独 ADR)
