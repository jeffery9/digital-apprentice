# D-01　最小 agent 循环

对应书中：**第 1 章 1.7 节**。演示"agent 是循环，不是魔法"。

## 一句话

用不到 40 行核心代码（`run_agent`）实现第 1 章 1.3 节的四要素循环——模型、工具、上下文、停止条件——直调模型接口，**不依赖任何 agent 框架**。全书后续 demo 都是往这段代码上加东西。

## 验证信息

| 项 | 值 |
| --- | --- |
| 验证日期 | 2026-10-13 |
| 验证环境 | Python 3.11+，Linux，**无网络、无 API key** |
| 验证方式 | `python agent_loop.py --mock` 及下表全部变体，逐条核对输出 |
| 依赖 | 无第三方依赖（见 `requirements.txt`） |
| 随机种子 | 默认 `--seed 20261013`，输出可复现 |

## 最低可跑组合

- **离线（推荐，书中所有截图与数字都基于此）**：`--mock`。零依赖、零网络、零密钥。
- **真实调用**：任意 **OpenAI 兼容** 的 `/chat/completions` 端点即可，需支持 function calling。
  环境变量：`LLM_ENDPOINT`、`LLM_MODEL`（须为 `../llm.py` 的 `MODEL_POOL` 中已登记画像的快照 ID）、`LLM_API_KEY`。
  **最低可跑供应商组合：1 家**（本 demo 不演示故障转移；那是 D-04）。
- **密钥处理方式**：只从环境变量读取。示例代码与 README 中不出现任何真实密钥形态；
  请勿把 key 写进代码或提交到仓库（`prepub_check.py` 的 F6 会扫）。

## 怎么跑

```bash
python agent_loop.py --mock                          # 基线：4 个工具各调一次后收尾
python agent_loop.py --mock --max-steps 20 --tool-fail-rate 0.9   # ① 观察 token 的超线性增长
python agent_loop.py --mock --tool-fail-rate 0.5 --token-budget 3000   # ② 观察预算熔断先触发
python agent_loop.py --mock --no-summarize           # ③ 对比上下文摘要的作用
python agent_loop.py --mock --dump-trace trace.json  # ④ 落盘轨迹，逐条读一遍
```

## 预期输出（`--mock`，2026-10-13 实测）

```
模型：mock-flagship-1｜模式：mock 离线
停止条件：步数上限 8｜token 预算 48,000｜摘要：开

  第  1 轮｜本轮输入     255 tok｜累计      303 tok｜动作 search_kb
  第  2 轮｜本轮输入     739 tok｜累计    1,090 tok｜动作 get_order
  第  3 轮｜本轮输入     805 tok｜累计    1,943 tok｜动作 get_logistics
  第  4 轮｜本轮输入     883 tok｜累计    2,874 tok｜动作 search_history
  第  5 轮｜本轮输入     963 tok｜累计    3,847 tok｜动作 —

结果：status=done｜步数=5｜累计 token=3,847
```

各变体的实测结果（同一环境、同一随机种子）：

| 命令 | 结果 | 说明 |
| --- | --- | --- |
| `--mock` | `done`，5 步，3,847 tok | 基线 |
| `--mock --no-summarize` | `done`，5 步，**5,935 tok** | 摘要省约 35% token（第 6 章预告） |
| `--mock --tool-fail-rate 0.5 --token-budget 3000` | **`budget_exceeded`**，5 步，3,905 tok | 预算熔断先于"模型说完成"触发 |
| `--mock --max-steps 20 --tool-fail-rate 0.9 --token-budget 200000` | `done`，17 步，21,987 tok | 步数翻 3.4 倍，token 翻 5.7 倍 |

退出码：`0`＝正常完成；`2`＝触达步数或预算上限（便于接入 CI）。

## 观察什么（书中 1.7 节的四条）

1. **步数与 token 的关系**：对比第 1 轮与第 5 轮的"本轮输入"——255 → 963，因为每轮都要重发前几轮的全部产物。这就是 1.3.3 那条 `T(n) ≈ 400n² + 800n` 曲线的微观形态。注意 demo 的绝对数字与该式不同口径（demo 用 `llm.py::_estimate_tokens` 的离线估算，式子假设每轮固定 800 token 的工具结果），**只看增长形状，不要对齐数值**。
2. **停止条件谁先触发**：默认配置下模型在第 5 轮声明完成；把 `--tool-fail-rate` 调到 0.5、预算调到 3000，会看到 `budget_exceeded` 先触发。这是"模型无法自我约束"（1.3.2）的实证——**代码持有的那两个停止条件才是真的停止条件**。
3. **摘要的作用**：`--no-summarize` 重跑，token 从 3,847 涨到 5,935。这是第 6 章上下文预算的最小演示；`summarize()` 里那句 `[已截断 N 字符…]` 的留痕比截断本身更重要——**模型不知道自己失忆了**（1.3.3 后果二）。
4. **轨迹**：`--dump-trace` 落盘后打开读一遍，你会看到每轮的动作、token、finish_reason，以及完整消息列表。第 18 章会说：没有这个文件，故障就只能靠猜。

## 刻意不做的事

重试、流式、并行工具调用、持久化、路由、网关——分别在第 4、10、15 章加进来。D-01 的职责是**确立基线**：后面每一章的复杂度，都要能指回这段代码里的某一行。

## 代码地图

| 位置 | 对应书中 |
| --- | --- |
| `run_agent()` | 1.7 节列出的骨干片段（逐字对应） |
| 三个 `return Result(...)` | 1.3.5 的三个停止条件 ＋ "触达上限之后怎么办" |
| `Tool.invoke()` 的错误整形 | 1.3.4 结论③：给模型看的错误消息质量要求更高 |
| `summarize()` | 1.3.3 后果一的缓解手段（第 6 章展开） |
| `side_effect == "irreversible"` 分支 | 1.2.2 第三判据：副作用可逆性（第 15、17 章展开） |
| `../llm.py` | 能力画像、快照固定、多模态通道、故障转移钩子（第 3、4、13 章展开） |
