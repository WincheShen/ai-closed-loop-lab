"""把 src/ 加到 sys.path，使得 pytest 不依赖 `pip install -e .`。"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
