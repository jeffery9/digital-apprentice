# D-09 情景记忆最小实现

对应书稿第 8 章（记忆系统）。演示四组件骨架的**机制形状**：显著性打分写入 → 词面重叠＋新鲜度加权检索 → 后台整理（衰减/归档/墓碑）。

## 运行

```bash
python3 run.py --mock        # 全程离线，零第三方依赖，无需 API key
rm -f memory.jsonl           # 重置记忆库后重跑可复现全部输出
```

## 结构

| 文件 | 书中对应 | 说明 |
| --- | --- | --- |
| `run.py::score_salience` | 8.3 显著性判定 | 类型权重 + 承诺/纠正加成，阈值 0.35 |
| `run.py::MemoryStore` | 8.2 分层记忆 | append-only JSONL，字段含 kind/salience/last_hit/dead |
| `run.py::retrieve` | 8.4 检索策略 | 词面重叠是嵌入的替身；新鲜度乘子 exp(-age/τ) |
| `run.py::consolidate` | 8.3 后台整理卡 | 合并、陈旧 tombstone、容量归档 |
| `memory.jsonl` | — | 运行产物，可删可再生 |

## 实测要点（书 8.8 引用）

1. 两条 chitchat 在写入侧被丢弃——存储成本只是表象，检索噪声才是主因。
2. 探针 Q1/Q2 返回"无召回"——**未入池的事实查不到，写入侧决定检索上限**（本章标题论点的实验形态）。
3. "SF33 物流单号"被整理器 tombstone——任务态数据不该住进长期记忆（8.7 外存-记忆相变判据）。

## 接真模型路线

- `score_salience` → 让模型对每轮打 importance 分（接口签名不变，返回仍是 float）。
- 词面重叠 → 嵌入相似度（推荐混合检索：向量 top-k ∪ BM25 top-k，再重排）。
- `consolidate` 的合并/冲突消解 → 模型批处理任务（夜间跑，加心跳监控）。
- memory-as-tool（书 8.5）：把 store 的四个操作包成 ToolSpec 注册进 `code/llm.py` 的循环即可。

⚠️ 铁律三：本 demo 不调用任何模型，脚本化启发式只演示机制形状，不承诺机制智力。
