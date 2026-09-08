"""`docker run <image>` alone must produce a working report.

This is the failure that cost days: the Dockerfile's entrypoint started only
uvicorn, and the ingest worker existed solely as a second service in
docker-compose.yaml. A deployment that pulls the image and runs one container —
which is how this actually gets deployed — therefore had a healthy API in front
of a store nothing would ever fill, and the dashboard said "building" forever.

These are static checks on the shipped files: they do not need Docker, but they
fail the moment the image stops being self-sufficient.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "scripts" / "entrypoint.sh").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")


def test_dockerfile_entrypoint_is_not_bare_uvicorn():
    m = re.search(r'^ENTRYPOINT\s+(\[.*\])', DOCKERFILE, re.M)
    assert m, "Dockerfile has no ENTRYPOINT"
    entry = json.loads(m.group(1))

    assert "uvicorn" not in entry[0], (
        "a bare uvicorn entrypoint serves the API without ever running the "
        "ingest, which leaves a plain `docker run` deployment permanently empty"
    )
    assert "entrypoint.sh" in " ".join(entry)


def test_entrypoint_defaults_to_running_both_roles():
    assert 'ROLE="${QDR_ROLE:-both}"' in ENTRYPOINT, (
        "the default must run the ingest too; anything else reintroduces the bug"
    )
    for role in ("api", "ingest", "both"):
        assert f"  {role})" in ENTRYPOINT, f"QDR_ROLE={role} must be handled"


def test_entrypoint_keeps_the_api_alive_if_the_worker_dies():
    both = ENTRYPOINT.split("both)")[1]
    assert "worker.sh &" in both, (
        "the worker must be backgrounded so its failure cannot take the API down"
    )


def test_compose_still_splits_the_two_roles():
    """The split is better where compose is available; it must stay explicit."""
    assert "QDR_ROLE=api" in COMPOSE
    assert "QDR_ROLE=ingest" in COMPOSE
    assert COMPOSE.count("QDR_ROLE") == 2


def test_worker_is_shipped_in_the_image():
    assert "COPY scripts/" in DOCKERFILE or "COPY scripts" in DOCKERFILE
    assert (ROOT / "scripts" / "worker.sh").exists()
