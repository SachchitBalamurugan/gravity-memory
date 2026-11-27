"""Click to open a persistent local workspace, or pass standard CLI arguments."""
import os
from pathlib import Path
import sys

from gravity_memory.__main__ import main


if __name__ == "__main__":
    if len(sys.argv) == 1:
        data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GravityMemory" / "workspace.sqlite"
        sys.argv.extend(["--db", str(data), "studio"])
        print("Gravity Memory runs locally. Close this console to stop the workspace.", flush=True)
    raise SystemExit(main())
