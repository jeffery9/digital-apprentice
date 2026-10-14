这个判断对一半：

**从 LLM 视角看，agent 最终确实只是不断组装 prompt。**  
模型能看到的只有上下文：系统提示、历史消息、工具定义、检索结果、状态、错误反馈、用户目标等。

但从工程视角看，agent 不只是“拼 prompt”，而是：

> **设计一个循环：感知环境 → 组装上下文 → 让模型决策 → 执行动作 → 把结果塞回上下文 → 继续循环。**

所以 agent 的核心不是 prompt 本身，而是：

1. **上下文如何产生**
2. **什么时候调用模型**
3. **模型能调用什么工具**
4. **执行结果如何反馈**
5. **状态如何保存和裁剪**
6. **什么时候停止**
7. **如何约束模型不跑偏**

---

# 一、Agent Loop 的本质

最抽象的 agent loop：

```python
context = init_context(goal, tools, memory, state)

while not done:
    prompt = assemble_prompt(
        system=system_prompt,
        goal=goal,
        tools=tool_schemas,
        history=history,
        memory=memory,
        observations=observations,
        constraints=policies
    )

    response = llm(prompt)

    if response.has_tool_call:
        result = execute_tool(response.tool_call)
        observations.append(result)
    else:
        done = True

    history.append(response)
    state.update(response, observations)
```

模型只是其中一个决策函数。

真正决定 agent 质量的是：

```text
prompt = f(goal, state, memory, tools, observations, constraints, budget)
```

这里的 `f` 才是 agent 框架真正干的事。

---

# 二、Prompt 组装到底在组装什么

一个成熟 agent 的 prompt 通常包含这些层：

| 层级 | 内容 | 作用 |
|---|---|---|
| System Prompt | 角色、目标、边界、风格 | 定义行为基线 |
| Developer Policy | 工具使用规则、安全策略 | 防止乱来 |
| User Goal | 用户任务 | 当前目标 |
| Tool Schema | 工具名、参数、描述 | 让模型知道能做什么 |
| History | 历史对话和动作 | 保持连续性 |
| Observations | 工具执行结果 | 环境反馈 |
| Retrieved Knowledge | RAG 检索内容 | 补充事实 |
| Memory | 长期记忆、偏好、经验 | 跨会话能力 |
| Scratchpad | 中间推理、计划、TODO | 外置工作记忆 |
| Error Feedback | 失败原因、异常栈 | 自我修正 |
| Output Format | JSON、tool call、代码格式 | 保证可解析 |
| Guardrails | 权限、禁止项、审批要求 | 安全控制 |
| Context Budget | 截断、摘要、压缩结果 | 控制 token |

所以 agent 工程很多时候是在做：

> **上下文工程 + 控制流工程 + 工具工程 + 状态工程。**

---

# 三、AI Agent 的主要 Design Pattern

下面按几个维度总结。

---

# 1. 单 Agent 循环模式

这是最基础的一类：一个模型 + 工具 + 反馈循环。

---

## 1.1 ReAct

核心思想：

```text
Reason + Act
```

模型交替进行：

```text
思考 → 调用工具 → 观察结果 → 再思考 → 再调用工具
```

典型流程：

```text
User: 查一下北京今天天气，然后写一句出行建议

Thought: 我需要先调用天气工具
Action: get_weather(city="Beijing")
Observation: 晴，12℃，风大
Thought: 可以给出建议
Final: 北京今天晴但风大，建议穿外套。
```

适用：

- 搜索
- 查询
- 简单工具调用
- 多步信息收集

问题：

- 容易绕圈
- 长上下文后推理质量下降
- 没有强计划时容易短视

本质：

> 把模型的推理过程和行动结果不断塞回 prompt。

---

## 1.2 Plan-and-Execute

核心思想：

```text
先做计划，再执行。
```

结构：

```text
Planner: 生成步骤
Executor: 逐步执行
Replanner: 根据结果修改计划
```

例子：

```text
Goal: 分析竞品并写报告

Plan:
1. 搜索竞品列表
2. 抓取官网信息
3. 提取定价
4. 对比功能
5. 生成报告
```

适用：

- 长任务
- 多步骤研究
- 报告生成
- 自动化工作流

问题：

- 计划可能一开始就错
- 需要 replan 机制
- 对模型规划能力要求高

和 ReAct 的区别：

```text
ReAct: 边走边想
Plan-and-Execute: 先想路线再走
```

---

## 1.3 Reflexion / Self-Reflection

核心思想：

```text
执行后让模型自己反思，把反思结果写回上下文。
```

流程：

```text
Act → Evaluate → Reflect → Improve → Act again
```

例子：

```text
第一次生成代码失败：
Error: KeyError: 'name'

Reflection:
我假设返回结果一定有 name 字段，应该先检查结构。

第二次生成：
增加字段检查和默认值。
```

适用：

- 代码生成
- 数学推理
- 写作修改
- 复杂问题求解

问题：

- 自我评价不一定可靠
- 没有外部验证时容易自信地错
- 需要测试、评估器或环境反馈

关键：

> reflection 要有外部信号，不然容易变成自我催眠。

---

## 1.4 Evaluator-Optimizer

核心思想：

```text
一个模型生成，一个模型评估。
```

结构：

```text
Generator → Output
Evaluator → Score/Critique
Generator → Revised Output
```

可以用同一个模型不同 prompt，也可以不同模型。

适用：

- 文案优化
- 代码审查
- 答案质检
- 长文生成
- 合规检查

问题：

- 成本翻倍
- 评估器如果弱，优化方向会跑偏
- 容易过拟合评估器偏好

本质：

> 把“批评”也变成 prompt 的一部分。

---

## 1.5 Tool Router

核心思想：

```text
根据用户意图选择工具，而不是让模型自由发挥。
```

结构：

```text
User Input → Intent Classifier → Tool Selector → Tool Execution → Response
```

例子：

```text
用户：查订单 12345
路由：order_lookup
执行：get_order(order_id="12345")
回答：订单状态是已发货。
```

适用：

- 客服系统
- 企业内部工具
- API 编排
- 垂直业务 agent

优点：

- 可控
- 成本低
- 易评估
- 安全性好

问题：

- 灵活性差
- 工具多了路由难做
- 多步任务需要额外编排

很多所谓 agent，生产上其实用 router 就够了。

---

## 1.6 CodeAct / Code-as-Action

核心思想：

```text
模型不直接调用零散工具，而是生成代码来调用工具。
```

例如：

```python
result = search("AI agent design patterns")
summary = summarize(result[:5])
save_note(summary)
```

适用：

- 数据分析
- 自动化脚本
- 复杂工具组合
- 多 API 编排
- 计算任务

优点：

- 表达力强
- 可以用循环、条件、变量
- 比 function calling 更灵活

问题：

- 安全风险高
- 需要沙箱
- 错误调试复杂
- 对代码模型能力要求高

本质：

> 把工具调用变成程序生成。

---

## 1.7 Computer Use Agent

核心思想：

```text
模型操作图形界面：截图、点击、输入、滚动。
```

输入：

```text
屏幕截图 + 元素树 + 操作历史
```

输出：

```text
click(x, y)
type("hello")
scroll(down)
```

适用：

- 操作网页
- 操作桌面软件
- 自动化测试
- 无 API 系统

问题：

- 慢
- 贵
- 不稳定
- 界面变化敏感
- 安全风险高

本质：

> 把 GUI 状态转成 prompt，把模型输出转成操作事件。

---

# 2. 工作流与控制流模式

Agent 不一定越自由越好。很多时候应该用确定性流程。

---

## 2.1 Workflow / DAG

核心思想：

```text
任务拆成固定节点，按图执行。
```

例子：

```text
输入 → 意图识别 → 检索 → 排序 → 生成 → 审核 → 输出
```

适用：

- 生产系统
- RAG 管线
- 客服机器人
- 内容生成流水线

优点：

- 可控
- 可监控
- 可回归测试
- 成本低

问题：

- 不灵活
- 难以处理开放任务
- 分支太多会变复杂

生产环境优先考虑：

```text
能 workflow，不要 agent loop。
```

---

## 2.2 State Machine

核心思想：

```text
agent 在不同状态之间跳转。
```

例如：

```text
COLLECT_REQUIREMENTS
→ SEARCH_INFO
→ DRAFT
→ REVIEW
→ APPROVE
→ DONE
```

每个状态有自己的 prompt 和可用工具。

适用：

- 表单填写
- 审批流程
- 多轮任务
- 合规流程

优点：

- 状态清晰
- 容易恢复
- 容易审计

问题：

- 设计复杂
- 状态爆炸

本质：

> 把 agent 的自由度限制在状态机里。

---

## 2.3 Graph Agent

核心思想：

```text
用图结构表达节点、边、条件分支、循环。
```

类似：

```text
Node: planner
Node: researcher
Node: writer
Edge: if missing_info → researcher
Edge: if draft_ready → writer
```

适用：

- 复杂 agent 编排
- 需要条件分支和循环
- 多角色协作

代表思路：LangGraph、状态图、流程图。

优点：

- 比纯 prompt 更可控
- 比固定 workflow 更灵活
- 可以持久化状态

问题：

- 图设计不好会变成屎山
- 调试成本高

---

## 2.4 Router Pattern

核心思想：

```text
一个入口模型/分类器决定后续路径。
```

例如：

```text
用户问题
├── 简单问答 → 直接 LLM
├── 代码问题 → code agent
├── 搜索问题 → search agent
├── 数据分析 → data agent
└── 违规请求 → reject
```

适用：

- 多能力助手
- 企业 AI 平台
- 客服系统

优点：

- 每个子链路可以独立优化
- 成本和延迟可控

问题：

- 路由错误会级联
- 多意图任务难处理

---

## 2.5 Hierarchical Planner

核心思想：

```text
高层计划器拆目标，低层执行器完成子任务。
```

结构：

```text
High-level Planner:
  把大任务拆成子任务

Low-level Executor:
  完成每个子任务

Monitor:
  检查是否完成，必要时重规划
```

适用：

- 自动化研究
- 软件开发
- 复杂分析
- 长周期任务

问题：

- 层级越多越难调试
- 高层计划容易空泛
- 需要强状态管理

---

# 3. 多 Agent 模式

多 agent 不一定更好。很多场景单 agent 加工具就够。

但任务复杂、角色差异大、需要并行时，多 agent 有价值。

---

## 3.1 Supervisor / Orchestrator-Workers

核心思想：

```text
一个主管 agent 拆任务，分给多个 worker agent。
```

结构：

```text
Supervisor
├── Researcher
├── Coder
├── Analyst
└── Writer
```

例子：

```text
Supervisor:
1. Researcher 找资料
2. Analyst 做对比
3. Writer 写报告
```

适用：

- 复杂研究
- 报告生成
- 软件开发
- 多工具协作

优点：

- 职责清晰
- 可并行
- 每个 worker prompt 更专注

问题：

- supervisor 是瓶颈
- 上下文传递成本高
- 容易出现信息丢失

---

## 3.2 Multi-Agent Debate

核心思想：

```text
多个 agent 对同一问题给出不同答案，然后辩论，最后收敛。
```

结构：

```text
Agent A: 答案 1
Agent B: 答案 2
Agent C: 评估两者
Final: 综合答案
```

适用：

- 数学推理
- 事实核查
- 复杂判断
- 需要降低偏见时

优点：

- 提高某些任务准确率
- 能发现单模型盲点

问题：

- 成本高
- 延迟高
- 如果模型同质化，辩论收益有限
- 容易集体幻觉

---

## 3.3 Actor-Critic

核心思想：

```text
Actor 做事，Critic 挑错。
```

结构：

```text
Actor: 生成方案/代码/答案
Critic: 指出问题
Actor: 修改
```

适用：

- 代码生成
- 写作优化
- 策略设计
- 安全审查

关键：

> critic 最好有真实反馈，而不是纯靠模型嘴硬。

例如代码场景：

```text
Actor 写代码
Critic 跑测试
测试失败信息回传
Actor 修代码
```

---

## 3.4 Generator-Discriminator

和 Actor-Critic 类似，但更像生成对抗思路：

```text
Generator 生成内容
Discriminator 判断真假/质量
```

适用：

- 数据合成
- 文本质量筛选
- 图像/内容评估
- 模拟用户反馈

问题：

- 判别器弱则生成器骗过它
- 需要持续校准

---

## 3.5 Role-Based Team

核心思想：

```text
给不同 agent 分配角色：产品经理、架构师、工程师、测试。
```

例子：

```text
PM: 拆需求
Architect: 设计接口
Engineer: 写代码
Tester: 写测试
Reviewer: 审查
```

适用：

- 软件开发
- 模拟团队协作
- 复杂内容生产

问题：

- 容易戏太多
- 角色 prompt 过长
- 真实收益不一定比单 agent + tools 高

很多 role-play 多 agent 是演示好看，生产难用。

---

## 3.6 Swarm / Handoff

核心思想：

```text
多个 agent 之间互相转交控制权。
```

例如：

```text
Frontdesk Agent
→ Sales Agent
→ Support Agent
→ Refund Agent
```

每个 agent 可以决定是否 handoff 给另一个 agent。

适用：

- 客服
- 销售
- 多业务线助手

优点：

- 模块化
- 每个 agent 权限和工具隔离
- 便于扩展

问题：

- 转交条件难设计
- 容易循环转交
- 需要全局状态

---

## 3.7 Blackboard Pattern

核心思想：

```text
所有 agent 读写一个共享状态区。
```

结构：

```text
Blackboard:
  goal
  facts
  hypotheses
  evidence
  partial_results

Agents:
  各自观察 blackboard，贡献自己的结果
```

适用：

- 复杂推理
- 多专家系统
- 不确定问题求解

优点：

- 松耦合
- 适合异构能力

问题：

- 并发控制难
- 状态污染
- 难以追踪责任

---

# 4. 记忆模式

Agent 要跨轮次、跨任务，就绕不开记忆。

---

## 4.1 Working Memory

工作记忆：

```text
当前 prompt 里的内容。
```

包括：

- 当前目标
- 最近几轮对话
- 最近工具结果
- 临时变量

限制：

- token 有限
- 越长越贵
- 越长越容易噪声干扰

工程重点：

> 不是记得越多越好，而是当前上下文越准越好。

---

## 4.2 Episodic Memory

情节记忆：

```text
过去发生过什么。
```

例子：

```text
用户上次让我用 Python 写。
用户拒绝过某种方案。
上次任务失败在权限问题。
```

存储方式：

- 向量库
- 结构化日志
- 会话摘要

适用：

- 长期助手
- 个性化服务
- 经验复用

问题：

- 检索不准会引入噪声
- 旧记忆可能过时
- 需要时间衰减和置信度

---

## 4.3 Semantic Memory

语义记忆：

```text
事实知识和用户画像。
```

例子：

```text
用户是公司财务。
用户偏好简洁回答。
项目使用 PostgreSQL。
```

适合结构化存储：

```text
user_profile
preferences
project_facts
domain_knowledge
```

优点：

- 比向量检索更稳定
- 可审计
- 可更新

---

## 4.4 Procedural Memory

程序记忆：

```text
怎么做事。
```

例子：

```text
发布流程：
1. 跑测试
2. 构建镜像
3. 部署 staging
4. 冒烟测试
5. 上线
```

可以存成：

- SOP 文档
- 工具模板
- 代码片段
- prompt 模板
- 成功轨迹

高级形态：

> agent 从成功经验中提炼可复用技能。

---

## 4.5 Summarization Memory

核心思想：

```text
长历史压缩成摘要。
```

方式：

```text
最近 N 轮保留原文
更早历史压缩摘要
关键事实单独抽取
```

适用：

- 长对话
- 长任务
- 多轮客服

问题：

- 摘要可能丢关键细节
- 需要保留可回溯原文

---

## 4.6 Retrieval-Augmented Memory

核心思想：

```text
不把记忆全塞 prompt，而是按需检索。
```

流程：

```text
当前问题 → 检索相关记忆 → 只塞 top-k
```

适用：

- 长期助手
- 企业知识库
- 大量历史工单

问题：

- 检索质量决定上限
- embedding 不等于理解
- 需要 rerank 和过滤

---

# 5. RAG 与知识型 Agent 模式

RAG 本身也是 prompt 组装的一部分。

---

## 5.1 Naive RAG

流程：

```text
Query → Embedding → Vector Search → Top-k Docs → LLM
```

问题：

- 检索不准
- 文档切片烂
- 没有引用
- 无法多跳推理

---

## 5.2 Advanced RAG

包括：

```text
Query Rewrite
HyDE
Multi-query
Semantic Chunking
Hybrid Search
Rerank
Citation
Self-ask
Iterative Retrieval
```

适用：

- 企业知识库
- 法律/医疗/金融资料
- 技术文档问答

本质：

> 检索结果也是 prompt 的一部分，检索质量就是上下文质量。

---

## 5.3 Agentic RAG

核心思想：

```text
让 agent 自己决定何时检索、检索什么、是否需要再检索。
```

流程：

```text
模型判断信息不足
→ 生成搜索 query
→ 检索
→ 阅读结果
→ 发现不够
→ 再搜索
→ 综合回答
```

适用：

- 深度研究
- 多来源对比
- 开放问题

问题：

- 容易搜太多
- 需要去重和可信度判断
- 需要停止条件

---

# 6. 工具使用模式

工具是 agent 的手脚。

---

## 6.1 Function Calling

结构：

```json
{
  "name": "search",
  "arguments": {
    "query": "agent design patterns"
  }
}
```

优点：

- 结构化
- 易解析
- 生产友好

问题：

- 工具太多时模型选择困难
- 参数描述质量很关键
- 多工具组合能力有限

---

## 6.2 MCP / Tool Registry

核心思想：

```text
把工具、资源、prompt 标准化注册。
```

模型通过统一协议发现和使用工具。

适用：

- 企业工具集成
- 多系统连接
- 插件生态

关键问题：

- 权限
- 版本
- 描述质量
- 工具污染
- 安全边界

---

## 6.3 Tool Selection + Tool Execution + Tool Verification

成熟工具调用不是只 call 一下，而是：

```text
选择工具
→ 校验参数
→ 权限检查
→ 执行
→ 捕获异常
→ 结果摘要
→ 写回上下文
```

很多 agent 失败不是模型不会选工具，而是工具返回太长、太脏、不可靠。

---

# 7. 安全与治理模式

生产 agent 必须有边界。

---

## 7.1 Guardrails

核心思想：

```text
输入输出都要检查。
```

包括：

- 敏感词
- 注入攻击
- 越权请求
- 隐私信息
- 非法操作
- 格式校验

结构：

```text
User Input
→ Input Guardrail
→ Agent
→ Output Guardrail
→ User
```

---

## 7.2 Permission Gate

核心思想：

```text
危险操作必须授权。
```

例如：

```text
删除文件
转账
发邮件
修改生产配置
执行 shell
```

必须：

```text
展示操作
→ 用户确认
→ 执行
→ 记录日志
```

---

## 7.3 Human-in-the-Loop

核心思想：

```text
关键节点让人审批。
```

适用：

- 金融
- 医疗
- 法务
- 生产部署
- 数据删除

模式：

```text
Agent 生成方案
→ Human approve/reject
→ Agent 继续执行
```

---

## 7.4 Sandboxing

核心思想：

```text
代码执行、文件操作、网络访问必须隔离。
```

适用于：

- CodeAct
- Computer Use
- 自动化脚本
- 浏览器 agent

没有沙箱的 code agent 不能上生产。

---

## 7.5 Audit Trail

核心思想：

```text
每一步都要可回放。
```

记录：

- prompt
- model version
- tool call
- tool result
- decision
- user approval
- final output

用途：

- 调试
- 合规
- 追责
- 评估
- 回归测试

---

# 8. 评估与自进化模式

Agent 最难的不是 demo，是稳定变好。

---

## 8.1 Offline Evaluation

离线评估：

```text
准备测试集
→ 跑 agent
→ 评估结果
→ 对比版本
```

评估对象：

- 最终答案
- 工具选择
- 参数正确性
- 步骤数
- 成本
- 延迟
- 安全性

---

## 8.2 Trajectory Evaluation

不只看最终答案，还看过程：

```text
是否调用正确工具？
是否多余搜索？
是否误删数据？
是否遗漏关键步骤？
```

适合：

- agent 回归测试
- 失败分析
- 行为约束评估

---

## 8.3 LLM-as-Judge

用模型评估模型：

```text
Candidate Answer → Judge Model → Score/Critique
```

优点：

- 快
- 便宜
- 可扩展

问题：

- 偏好偏差
- 位置偏差
- 冗长偏差
- 需要人工校准

---

## 8.4 Online Telemetry

线上收集：

```text
用户点赞/点踩
重试率
中断率
人工接管率
任务完成率
成本
延迟
```

真正有用的指标不是“模型聪不聪明”，而是：

```text
任务有没有完成。
用户有没有返工。
系统有没有失控。
```

---

## 8.5 Self-Improvement / Skill Distillation

高级形态：

```text
成功轨迹 → 提炼模板 → 存入 procedural memory → 下次复用
```

例子：

```text
agent 多次成功完成退款流程后，
自动沉淀一个 refund_sop 工具或 prompt 模板。
```

风险：

- 错误经验也会被固化
- 需要审核机制

---

# 9. 常见 Agent 架构分层

一个完整 agent 系统通常有这些层：

```text
1. Interface Layer
   用户输入、API、消息通道

2. Policy Layer
   权限、安全、合规、路由

3. Planning Layer
   目标拆解、计划、重规划

4. Context Layer
   历史、记忆、检索、状态压缩

5. Tool Layer
   工具注册、调用、沙箱、结果处理

6. Execution Layer
   代码执行、浏览器、文件系统、外部 API

7. Memory Layer
   短期记忆、长期记忆、结构化知识

8. Evaluation Layer
   日志、追踪、评估、回放

9. Governance Layer
   审批、审计、限流、熔断
```

很多所谓 agent 框架，本质就是帮你管理这些层。

---

# 10. 从简单到复杂的演进路径

实际工程不要一上来就多 agent。

推荐路径：

```text
Level 0: 单次 LLM 调用
Level 1: Prompt 模板 + 结构化输出
Level 2: RAG
Level 3: Function Calling
Level 4: 单 Agent Loop
Level 5: Workflow / State Machine
Level 6: Planner + Executor
Level 7: Memory + Reflection
Level 8: Multi-Agent
Level 9: Self-Improvement
```

很多业务到 Level 5 就够了。

---

# 11. 各种模式的选择原则

| 场景 | 推荐模式 |
|---|---|
| 简单问答 | 单轮 LLM |
| 企业知识问答 | RAG / Advanced RAG |
| 固定流程任务 | Workflow / DAG |
| 客服多业务路由 | Router + Handoff |
| 需要工具调用 | Function Calling |
| 多步搜索研究 | ReAct / Agentic RAG |
| 长任务 | Plan-and-Execute |
| 代码生成 | CodeAct + Sandbox + Tests |
| 高质量写作 | Evaluator-Optimizer |
| 复杂推理 | Debate / Self-Consistency |
| 自动化办公 | Computer Use / Browser Agent |
| 复杂项目 | Supervisor + Workers |
| 生产系统 | Guardrails + Audit + HITL |

---

# 12. 最重要的几个设计判断

## 1. 上下文不是越多越好

很多 agent 效果差，不是因为模型笨，而是 prompt 太脏：

```text
无关历史太多
工具结果太长
检索内容噪声大
错误堆栈重复塞入
记忆互相冲突
```

好的上下文应该：

```text
目标清楚
状态明确
工具可用
证据可靠
噪声最低
```

---

## 2. 工具描述就是 prompt 的一部分

工具描述写不好，模型就不会用。

差的工具描述：

```text
search: 搜索
```

好的工具描述：

```text
search:
用于查询公开网络信息。
适合事实性问题、新闻、文档查找。
不适合访问私有数据库。
返回结果包含 title、url、snippet。
如果问题需要最新数据，请使用此工具。
```

---

## 3. Agent 的停止条件和开始条件一样重要

没有停止条件，agent 会：

- 无限搜索
- 无限反思
- 无限修代码
- 无限转交
- 成本爆炸

必须有：

```text
最大步数
最大 token
最大工具调用次数
最大重试次数
超时
人工接管
```

---

## 4. 可观测性优先于智能性

生产 agent 必须能看见：

```text
当前 goal 是什么？
当前 state 是什么？
为什么调用这个工具？
工具返回了什么？
哪一步导致失败？
prompt 是什么？
哪个版本模型？
```

没有 trace，agent 就是黑盒。

---

## 5. 多 Agent 不是默认答案

多 agent 常见问题：

```text
上下文重复传递
信息衰减
责任不清
成本上升
调试困难
```

优先顺序：

```text
确定性流程 > 单 agent + tools > planner/executor > multi-agent
```

---

# 13. 一句话总结各类 pattern

| Pattern | 一句话 |
|---|---|
| ReAct | 边想边做，把观察塞回 prompt |
| Plan-and-Execute | 先做计划，再逐步执行 |
| Reflexion | 失败后自我总结，再重试 |
| Evaluator-Optimizer | 一个生成，一个批评，循环改进 |
| Router | 根据意图分发到不同处理链路 |
| Workflow | 用固定流程替代自由发挥 |
| State Machine | 用状态约束 agent 行为 |
| Graph Agent | 用图结构表达分支、循环和状态 |
| Supervisor-Workers | 主管拆任务，工人执行 |
| Debate | 多个 agent 辩论收敛 |
| Actor-Critic | 一个做事，一个挑错 |
| Handoff/Swarm | agent 之间转交控制权 |
| Blackboard | 多 agent 共享状态板协作 |
| RAG | 检索外部知识进入 prompt |
| Agentic RAG | 模型自主决定检索和再检索 |
| CodeAct | 用代码表达复杂工具组合 |
| Computer Use | 用截图和操作事件控制界面 |
| Memory | 把长期信息按需带回上下文 |
| Guardrails | 输入输出加安全边界 |
| HITL | 关键动作让人审批 |
| Eval Loop | 用评估驱动 agent 迭代 |

---

最后回到你的判断：

```text
AI agent 的表层是 prompt 组装。
AI agent 的底层是控制系统。
```

模型只看到 prompt，但 prompt 长什么样，是由你的工具、状态、记忆、检索、流程、权限、评估共同决定的。

所以真正的设计问题不是：

```text
怎么写 prompt？
```

而是：

```text
怎么设计一个系统，让每次送给模型的上下文都尽可能正确、充分、干净、可执行、可回滚？
```