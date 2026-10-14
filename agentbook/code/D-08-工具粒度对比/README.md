# D-08 工具粒度对比（第 7 章配套 demo）

同一业务任务（20 个订单项折扣重算＋2 笔例外退款＋收尾），三种工具粒度的三本账：**步数账、token 账、恢复账**。零依赖、全程离线（`--mock` 口径，无需网络与 API key）。

## 用法

```bash
python run.py --strategy atomic      # 12 原子工具逐步编排
python run.py --strategy aggregate   # 3 业务聚合工具
python run.py --strategy code        # run_python + SDK 文档
python run.py --all                  # 三口径并跑对照表
python run.py --all --inject-fail    # 第 14 次调用注入失败，看恢复成本
```

## 实测读数（2026-10-14，本机 `--all --inject-fail`）

| 粒度 | 工具数 | 调用数 | 端到端成功 | 累计输入 tok | 定义税 tok |
| --- | --- | --- | --- | --- | --- |
| atomic | 12 | 89 | 16.6% | 558,564 | 96 |
| aggregate | 3 | 3 | 94.1% | 3,183 | 41 |
| code | 1 | 1 | 98.0% | 1,007 | 107 |

恢复账：atomic 需重放 60 个单元（三个逐行循环段整体重来）；aggregate/code 各重放 1 个幂等单元。

## ⚠️ 免责口径（同 D-07，QWEN.md 铁律三）

**不调用任何模型。** 选择器是脚本化替身（按预置剧本走最短序列），单步错误率 p=0.02 为 mock 设定值。本 demo 演示的是三种粒度的**结构性差异**（调用数是自变量、成功随步数指数衰减、token 呈平方形状、幂等边界决定恢复成本），不代表任何真实模型的选型行为。真实对比请用你的工具注册表与生产日志重放。

## 接真模型

把 `run.py` 的 `_script()` 换成 LLM 循环：注册表经 `code/llm.py` 薄适配层注入 function schema，剧本顺序改由模型自主决定——三本账的统计代码原样复用。预期观察点不变：atomic 路线的调用数与恢复成本会显著放大模型间的选择质量差异。

## 文件

- `tools.py` —— 契约即数据：三种粒度的工具注册表（含 side_effect / max_response_tokens 一等字段）
- `run.py` —— 三本账模拟器与对照表
- `requirements.txt` —— 空（零第三方依赖）
