# D-13 拓扑对比：Handoff vs Supervisor（第 12 章）

**验证日期**：2026-10-15｜**验证环境**：Python 3.11，Linux，`--mock` 离线模式

## 运行
```bash
python topology.py --mock        # 零依赖、零网络、无 API key
```

## 预期输出
同一批 5 个客服请求分别经两种拓扑处理，打印逐请求轨迹与调用计数对照
（handoff 调用=8，supervisor 派发=9）。

## 观察什么（对应 12.6）
1. **控制权落点**：handoff 转交后上游退场；supervisor 每轮收回控制权再派发。
2. **汇总税**：supervisor 多出的那次调用＝中心汇总的代价。
3. **失败半径**：handoff 路径上没有任何节点能审计"接管后发生了什么"；
   supervisor 的可观测钩子天然在调度点。
4. mock 只演示机制与相对形状，不代表任何真实模型的准确率。

## 最低可跑供应商组合
仅需 Python ≥3.11 标准库。真实模式走 `../llm.py` 薄适配层：设置
`LLM_ENDPOINT` / `LLM_MODEL` / `LLM_API_KEY`（密钥只从环境变量读取，代码中无硬编码）。
