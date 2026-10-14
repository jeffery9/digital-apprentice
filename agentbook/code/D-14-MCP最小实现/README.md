# D-14 MCP 最小实现（第 9 章）

**验证日期**：2026-10-15｜**验证环境**：Python 3.11，Linux，`--mock` 离线模式

## 运行
```bash
python mcp_min.py --mock      # 同进程演示 initialize → tools/list → tools/call 全往返
python mcp_min.py --serve     # 真 stdio JSON-RPC server（echo 请求行 | 本脚本 可对接外部 client）
```

## 预期输出
协议握手、两工具清单、两次调用结果、一次缺参调用的 KeyError——末行给出"观察什么"。

## 观察什么（对应 9.4）
1. **能力发现在协议层**：agent 不硬编码工具清单，换 server 不改代码。
2. **写操作确认门**挂在 content 语义上（refund_apply 返回"待人工确认"）。
3. **schema 校验缺口**：必填参数缺失只在 server 侧炸出 KeyError——协议本身不强制校验，这正是第 9 章说的坑。
4. mock 只演示机制形状，不代表任何真实 server 的行为。

## 最低可跑供应商组合
仅需 Python ≥3.11 标准库（json 即可，模型通道在本 demo 中是纯协议往返）。密钥经环境变量，代码零硬编码。
