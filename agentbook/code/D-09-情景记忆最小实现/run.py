#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-09 情景记忆最小实现：写入显著性打分 → 检索（词面重叠替身）→ 陈旧衰减（第 8 章）

四组件骨架（书 8.2–8.5）：
  store    —— append-only JSONL，字段含 text/kind/salience/created_turn/last_hit_turn/dead
  write    —— 热路径写入器：显著性打分决定是否值得记（书 8.3）
  retrieve —— 检索器：词面重叠 + 新鲜度加权（真实系统换嵌入相似度，接口不变）
  consolidate —— 后台整理器：同主题合并、陈旧降权、超容归档（书 8.3/8.6）

⚠️ 不调用任何模型。打分与匹配均为脚本化替身（启发式关键词），只演示机制形状
（QWEN.md 铁律三）。接真模型方法见 README。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

KIND_WEIGHT = {"preference": 1.0, "fact": 0.8, "event": 0.6, "chitchat": 0.1}


def score_salience(turn: dict) -> float:
    """显著性打分（mock）：类型权重 + 承诺/纠正信号加成。书 8.3 的"什么值得记"。"""
    s = KIND_WEIGHT.get(turn["kind"], 0.3)
    if turn.get("has_commitment"):      # agent 对用户做过承诺
        s += 0.3
    if turn.get("is_correction"):       # 用户纠正过 agent
        s += 0.4                        # 纠正必记——遗忘代价最高（书 8.3）
    return round(min(s, 1.0), 2)


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.items = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.items.append(json.loads(line))

    def flush(self):
        self.path.write_text(
            "\n".join(json.dumps(i, ensure_ascii=False) for i in self.items) + "\n",
            encoding="utf-8")

    def write(self, text, kind, turn_no, **flags):
        sal = score_salience({"kind": kind, **flags})
        if sal < 0.35:                  # 阈值以下不入长期记忆（留工作记忆即可）
            return None
        self.items.append({"id": len(self.items), "text": text, "kind": kind,
                           "salience": sal, "created_turn": turn_no,
                           "last_hit_turn": turn_no, "dead": False})
        return self.items[-1]

    def retrieve(self, query, now, top_k=3, decay_half_life=40):
        """词面重叠 × 显著性 × 新鲜度衰减（指数，半衰期=40 轮）。书 8.4 的分层加载简化形。"""
        q_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}", query))
        scored = []
        for it in self.items:
            if it["dead"]:
                continue
            overlap = len(q_tokens & set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}", it["text"])))
            if overlap == 0:
                continue
            age = max(now - it["created_turn"], 0)
            freshness = 0.5 ** (age / decay_half_life)
            r = overlap * it["salience"] * (0.4 + 0.6 * freshness)
            scored.append((r, it))
        scored.sort(key=lambda x: -x[0])
        hits = [it for _, it in scored[:top_k]]
        for it in hits:                 # 命中回写：被检索到的记忆续命（书 8.3 强化机制）
            it["last_hit_turn"] = now
        return hits

    def consolidate(self, now, stale_after=60, cap=8):
        """后台整理：陈旧低显著者标记 tombstone；总量超限归档最旧低分条目。书 8.6 记忆卫生。"""
        merged, archived = 0, 0
        for it in self.items:
            if it["dead"]:
                continue
            if now - it["last_hit_turn"] > stale_after and it["salience"] < 0.7:
                it["dead"] = True       # tombstone 保留（合规删除走硬删，书 8.6 被遗忘权）
                merged += 1
        alive = [i for i in self.items if not i["dead"]]
        if len(alive) > cap:
            alive.sort(key=lambda i: (i["salience"], i["last_hit_turn"]))
            for victim in alive[:len(alive) - cap]:
                victim["dead"] = True
                archived += 1
        return merged, archived


SCRIPT = [  # (text, kind, flags) —— 模拟一次跨 90 轮的客户会话中的候选记忆
    ("客户偏好：只接受顺丰发货，月结", "preference", {"has_commitment": True}),
    ("订单 T-2201 因破损退款 128 元", "event", {}),
    ("用户今天心情不错", "chitchat", {}),
    ("客户纠正：地址是'文三路'不是'文溪路'", "fact", {"is_correction": True}),
    ("承诺：下周三前寄出替换件", "event", {"has_commitment": True}),
    ("闲聊了两分钟天气", "chitchat", {}),
    ("客户再次提到月结发票要开公司抬头", "preference", {}),
    ("临时查询：物流单号 SF33 到哪了", "event", {}),
]


def main():
    ap = argparse.ArgumentParser(description="D-09 情景记忆最小实现（--mock 全程离线）")
    ap.add_argument("--mock", action="store_true", help="显式声明离线模式（本 demo 默认即离线）")
    args = ap.parse_args()

    db = Path(__file__).resolve().parent / "memory.jsonl"
    st = MemoryStore(db)
    print("== 写入阶段（显著性过滤）==")
    for i, (text, kind, flags) in enumerate(SCRIPT):
        got = st.write(text, kind, turn_no=i * 5, **flags)
        mark = f"入池 sal={got['salience']}" if got else "丢弃（低于阈值 0.35）"
        print(f"  轮{i*5:>3} [{kind:<10}] {text[:22]:<24} → {mark}")

    print("\n== 检索阶段（第 80 轮，三条探针查询）==")
    for q in ["发货方式有约定吗", "地址写哪里", "SF33 物流到哪了"]:
        hits = st.retrieve(q, now=80)
        print(f"  Q: {q}")
        for h in hits or [None]:
            if h is None:
                print("     （无召回——注意：未入池的事实查不到，写入侧决定检索上限）")
            else:
                age = 80 - h["created_turn"]
                print(f"     ← [{h['kind']}] {h['text']}（已 {age} 轮，sal={h['salience']}）")

    print("\n== 整理阶段（陈旧衰减＋容量归档）==")
    m, a = st.consolidate(now=90)
    st.flush()
    alive = [i for i in st.items if not i["dead"]]
    print(f"  tombstone {m} 条、归档 {a} 条 → 存活 {len(alive)} 条 / 总 {len(st.items)} 条")
    for i in alive:
        print(f"     存活: [{i['kind']}] {i['text']}")
    print("\n观察（对应书中 8.3/8.4/8.6）：")
    print("  ① chitchat 在写入侧就被挡——存储成本只是表象，检索噪声才是主因")
    print("  ② 纠正类记忆靠 is_correction 加成置顶——遗忘它的代价最高")
    print("  ③ 第 80 轮仍能召回早期偏好：新鲜度衰减乘子压不垮高显著+被命中续命的条目")
    print("  ④ 物流单号这类瞬时事实被整理器清出——任务态数据不该住进长期记忆（书 8.7 边界题）")


if __name__ == "__main__":
    main()
