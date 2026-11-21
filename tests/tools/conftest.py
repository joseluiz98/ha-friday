import pytest


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    """The install-script tests run a local HTTP server; the HA plugin blocks sockets."""
    yield


import importlib.util  # noqa: E402
import pathlib  # noqa: E402
import subprocess  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def ref():
    """The frozen sweep.py of 1b79c9f: the reference every optimisation must match."""
    return _load("sweep_reference", _ROOT / "tests" / "tools" / "sweep_reference.py")


@pytest.fixture(scope="session")
def new():
    """The sweep.py under test."""
    return _load("sweep_new", _ROOT / "scripts" / "sweep.py")


def _git(repo, *args, check=True):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": __import__("os").environ["PATH"], "HOME": str(repo)}
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True, env=env).stdout.decode().strip()


def _commit(repo, files, msg):
    repo = pathlib.Path(repo)
    for rel, content in files.items():
        p = repo / rel
        if content is None:
            if p.exists():
                p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)


@pytest.fixture
def make_repo(tmp_path):
    """make_repo(commits) -> repo path: one commit per dict (path -> str/bytes content; None deletes)."""
    def make(commits, name="repo"):
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        for i, files in enumerate(commits, 1):
            _commit(repo, files, f"c{i}")
        return str(repo)
    return make


@pytest.fixture
def branch():
    """branch(repo, name, files, base=None) -> sha of a new branch with one commit on top of `base` (default HEAD);
    leaves the repo on the branch it started from."""
    def make(repo, name, files, base=None):
        start = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        _git(repo, "checkout", "-q", "-b", name, *([base] if base else []))
        _commit(repo, files, f"on {name}")
        sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", start)
        return sha
    return make
