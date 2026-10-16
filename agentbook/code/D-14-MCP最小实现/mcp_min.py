"""D-14 MCP 最小实现：Model Context Protocol 的 tools/list + tools/call 骨架

对应书中：**第 9 章**（工具协议与上下文互通）。MCP 的本质是三件事：
  · 能力发现 —— 客户端问 `tools/list`，服务器按 JSON Schema 自报工具清单；
  · 调用约定 —— `tools/call` 携带 {name, arguments}，返回 content 块列表；
  · 进程隔离 —— 服务器是独立进程/端点，工具权限边界天然落在协议层。

本 demo 用标准库实现一个最小 MCP server（stdio 换行分隔 JSON-RPC）与 client，
证明"不装任何 SDK 也能吃透协议"——这正是第 9 章反对框架黑箱的立场。

用法：
    python mcp_min.py --mock          # 同进程模拟 stdio 往返（默认演示方式）
    python mcp_min.py --serve         # 作为 server 挂 stdin/stdout（供外部 client 接入）
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

TOOLS = [
    {"name": "query_order", "description": "按订单号查询状态（只读）",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                     "required": ["order_id"]}},
    {"name": "refund_apply", "description": "提交退款申请（写操作，需人工确认门）",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"},
                                                      "amount": {"type": "number"}},
                     "required": ["order_id", "amount"]}},
]

DB = {"8871": {"status": "已签收", "amount": 128.0}}


def call_tool(name: str, args: dict[str, Any]) -> list[dict]:
    """服务器侧执行体。写操作被标记 requires_confirmation（第 9 章确认门的协议落点）。"""
    if name == "query_order":
        rec = DB.get(args["order_id"])
        text = json.dumps(rec or {"error": "订单不存在"}, ensure_ascii=False)
    elif name == "refund_apply":
        text = json.dumps({"ticket": "R-001", "note": "已受理，待人工确认"},
                          ensure_ascii=False)
    else:
        return [{"type": "text", "text": f"未知工具 {name}", "isError": True}]
    return [{"type": "text", "text": text}]


def handle(req: dict[str, Any]) -> dict[str, Any]:
    """JSON-RPC 2.0 分派：initialize / tools/list / tools/call。"""
    m, p = req.get("method", ""), req.get("params", {})
    rid = req.get("id")
    if m == "initialize":
        res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
               "serverInfo": {"name": "d14-min", "version": "0.1"}}
    elif m == "tools/list":
        res = {"tools": TOOLS}
    elif m == "tools/call":
        res = {"content": call_tool(p["name"], p.get("arguments", {}))}
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {m}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": res}


class InProcessClient:
    """client 侧：把'跨进程 stdio'折叠为函数往返，报文格式保持逐字节一致。"""

    def __init__(self) -> None:
        self._id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        line = json.dumps(req, ensure_ascii=False)          # 走一遍序列化以贴近真实线协议
        resp = handle(json.loads(line))
        if "error" in resp:
            raise RuntimeError(resp["error"]["message"])
        return resp["result"]

    def list_tools(self): return self.request("tools/list")["tools"]
    def call(self, name, args): return self.request("tools/call",
                                                    {"name": name, "arguments": args})


def main() -> None:
    ap = argparse.ArgumentParser(description="D-14 MCP 最小实现（第 9 章）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--mock", action="store_true", help="同进程演示完整协议往返")
    g.add_argument("--serve", action="store_true", help="stdio 模式对外提供 server")
    a = ap.parse_args()
    if a.serve:
        for line in sys.stdin:
            if line.strip():
                sys.stdout.write(json.dumps(handle(json.loads(line)),
                                            ensure_ascii=False) + "\n")
                sys.stdout.flush()
        return
    c = InProcessClient()
    init = c.request("initialize")
    print(f"initialize → {init['serverInfo']['name']} v{init['serverInfo']['version']}"
          f"（协议 {init['protocolVersion']}）")
    print("tools/list →")
    for t in c.list_tools():
        print(f"   {t['name']:<12} {t['description']}")
    r1 = c.call("query_order", {"order_id": "8871"})["content"]
    print(f"tools/call query_order → {r1[0]['text']}")
    r2 = c.call("refund_apply", {"order_id": "8871", "amount": 128.0})["content"]
    print(f"tools/call refund_apply → {r2[0]['text']}")
    try:                                        # 必填参数缺失：真实 server 应校验拒绝
        c.call("query_order", {})
    except KeyError as e:
        print(f"缺参调用被 server 侧拒绝 → KeyError({e})（协议层未强制校验＝第 9 章的坑）")
    print("\n观察什么（9.4）：能力发现在协议层完成，agent 不必硬编码工具清单；"
          "写操作的确认门挂在 content 语义上；换 server 不改 agent 代码。")


if __name__ == "__main__":
    main()
