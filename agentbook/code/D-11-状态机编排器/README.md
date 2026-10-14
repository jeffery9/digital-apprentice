# D-11 状态机编排器：机制层（第 10 章）

**验证日期**：2026-10-15｜**验证环境**：Python 3.11，Linux，`--mock` 离线模式

## 运行（四剧本）
```bash
python orchestrator.py --mock                      # 正常路径：审批点挂起（设计如此）
python orchestrator.py --mock --human-reply 同意   # 批复先到：五节点一次跑完
python orchestrator.py --mock --crash-at 2         # 第 3 节点提交后模拟 SIGKILL
python orchestrator.py --mock --resume             # 从检查点续跑
python orchestrator.py --no-checkpoint --mock      # 对照组：无检查点，崩溃即从头重跑
```

## 预期输出
逐节点执行轨迹、检查点文件 checkpoint.json 的落盘快照、挂起提示与恢复命令；
resume 剧本显示续跑模型调用数＝剩余节点数。

## 观察什么（对应 10.4–10.8）
1. **节点＝纯函数、边＝显式转移、状态＝唯一数据通道**——编排的最小内核形状。
2. 打点位置在"节点提交后"；状态里只存引用不存聊天历史（体积曲线可见）。
3. 发送节点幂等键防二次发送；审批等待用 return 不用 sleep——进程可以为等待而死。
4. 与 D-18 分工：**本 demo 演示机制，D-18 演示治理**。

## 最低可跑供应商组合
仅需 Python ≥3.11 标准库。mock 全程不调模型（确定性桩），真实系统把桩换成 ../llm.py 调用即可。
