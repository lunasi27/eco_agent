from __future__ import annotations

import os


DEFAULT_DB_PATH = os.path.join("runs", "checkpoints.sqlite")
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"
MEM_DB_URL = "sqlite:///:memory:"
