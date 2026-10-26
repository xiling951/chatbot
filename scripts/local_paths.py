"""Keep executable code on F: and private runtime data on the user's NTFS disk."""

from pathlib import Path

CODE_ROOT = Path("F:/chatbot-runtime/AstrBot")
RUNTIME_ROOT = Path.home() / "AppData" / "Local" / "CompanionCore" / "runtime"
DATA = RUNTIME_ROOT / "data"
PLUGIN_DATA = DATA / "plugin_data" / "companion_core"
