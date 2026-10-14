"""向后兼容 shim：网关基座已提升为 `code/gateway.py`（M2 交付判据，作者拍板 2026-10-15）。

历史引用「code/D-04-模型网关与快照钉扎/gateway.py」经本文件转发，行为不变。
实现见上一级目录的 gateway.py；用法：python ../gateway.py --mock --calls 60
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gateway import *  # noqa: F401,F403,E402
from gateway import main  # noqa: F401,E402

if __name__ == "__main__":
    main()
