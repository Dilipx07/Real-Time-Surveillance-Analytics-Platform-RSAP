from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_cpu_orchestration_import_does_not_eagerly_import_ml_backends() -> None:
    root = Path(__file__).resolve().parents[3]
    script = (
        "import sys; "
        "import app.orchestration; "
        "assert 'torch' not in sys.modules; "
        "assert 'ultralytics' not in sys.modules; "
        "print('cpu-only lazy import successful')"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "apps" / "desktop-backend"), str(root / "packages" / "cv-engine")]
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert "successful" in completed.stdout
