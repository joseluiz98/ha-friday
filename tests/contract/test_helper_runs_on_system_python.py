"""The satellite helper runs as root with the system Python (/usr/bin/python3), which has no aiohttp: what it imports
from the release (the migrations runner) must load without the agent's dependencies.

14/11 rehearsal: `activate v0.3.0` failed with ModuleNotFoundError because importing friday_agent_core.ota pulled the
package __init__, which imported the agent server (aiohttp). The suites run where aiohttp exists, so nothing caught it.
"""

import subprocess
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "agents" / "common"
NO_AIOHTTP = "import sys; sys.modules['aiohttp'] = None; sys.path.insert(0, %r); " % str(COMMON)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-c", NO_AIOHTTP + code], capture_output=True, text=True)


def test_the_migrations_runner_imports_without_aiohttp():
    r = _run("from friday_agent_core.ota import migrations as mg; print(mg.run_pending.__name__)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "run_pending"


def test_the_package_still_exports_the_agent_core():
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); "
                        "from friday_agent_core import AgentCore, PROTOCOL_VERSION, diagnostics; "
                        "print(AgentCore.__name__, type(PROTOCOL_VERSION).__name__)" % str(COMMON)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == "AgentCore"
