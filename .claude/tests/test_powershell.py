"""Runs the Pester suite for the daily work agent's PowerShell (tests/pester). Skipped when PowerShell 7 or Pester is not installed."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]              # the repo that owns this .claude folder
PWSH = os.environ.get("CODEATLAS_PWSH") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell 7 (pwsh) is not installed")

SCRIPT = (
    "if (-not (Get-Module -ListAvailable Pester)) { exit 77 }; Import-Module Pester; "
    "$r = Invoke-Pester -Path .claude/tests/pester -PassThru -Output Detailed; exit [int]$r.FailedCount"
)


def test_pester_suite():
    run = subprocess.run([PWSH, "-NoProfile", "-Command", SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=900)
    if run.returncode == 77:
        pytest.skip("the Pester module is not installed")
    assert run.returncode == 0, run.stdout[-6000:] + run.stderr[-2000:]
