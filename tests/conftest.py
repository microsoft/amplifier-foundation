import sys
from pathlib import Path

# Add tools/ directory to path so tests can import dot_docs package
_tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
