"""Test setup for the application. Some component tests import the evaluation scripts in
06_Workspace_Tools, at the top of the repository, under the name they were written with: tools."""
import sys
import types
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "06_Workspace_Tools"
if TOOLS.is_dir() and "tools" not in sys.modules:
    package = types.ModuleType("tools")
    package.__path__ = [str(TOOLS)]
    sys.modules["tools"] = package
