"""Keep executable code on F: and private runtime data on the user's NTFS disk."""

from pathlib import Path
import json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = Path("F:/chatbot-runtime/AstrBot")
_root_config = PROJECT_ROOT / "config" / "runtime_root.local.json"
_logical_root = Path.home() / "AppData" / "Local" / "CompanionCore" / "runtime"
_config_anchor = _logical_root / "data" / "cmd_config.json"
RUNTIME_ROOT = (
    Path(json.loads(_root_config.read_text(encoding="utf-8"))["runtime_root"])
    if _root_config.exists()
    else (
        _config_anchor.resolve().parent.parent
        if _config_anchor.exists()
        else _logical_root
    )
)
DATA = RUNTIME_ROOT / "data"
PLUGIN_DATA = DATA / "plugin_data" / "companion_core"
