import os
import tempfile

# Eigene, leere Test-Datenbank und kein Hintergrund-Scheduler während der Tests
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-test-"))
os.environ["JARVIS_NO_SCHEDULER"] = "1"
os.environ.pop("DATABASE_URL", None)
