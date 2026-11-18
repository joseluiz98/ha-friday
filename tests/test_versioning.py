"""Versioning guard (docs/releasing.md): the manifest carries a plain SemVer core, and a tag push must match it."""
import json
import os
import re
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "custom_components" / "friday" / "manifest.json"
TAG = re.compile(r"^v\d+\.\d+\.\d+$")


def _version() -> str:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def test_manifest_version_is_plain_semver_core():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version()), "no 'v', no pre-release suffix: the channel is the GitHub flag"


def test_tag_push_matches_manifest():
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        return  # only a tag push has something to compare
    tag = os.environ.get("GITHUB_REF_NAME", "")
    assert TAG.match(tag), f"tag {tag!r} is not vMAJOR.MINOR.PATCH; OTA would ignore it"
    assert tag == f"v{_version()}", f"tag {tag} does not match manifest.json {_version()}"
