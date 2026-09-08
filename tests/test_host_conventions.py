"""The image must fit the host it is actually deployed to.

That host already runs qubic_spotlight and qubic_doge_stats: each bind-mounts a
root-owned directory (/root/<service>/data:/data) and each container runs as
root. This image shipped `USER qdr` (uid 10001) instead, so it could not write
the directory the admin had set up the same way as every other service — the
store failed to open, every store-backed endpoint answered 500, and the report
stayed empty while looking merely "still building".

The remedy is not to ask the admin to treat this one service differently. It is
for the image to follow the convention already in place. These tests pin that,
because a well-meaning "run as non-root" change would silently break the
deployment again.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")


def test_image_does_not_drop_to_a_non_root_user():
    """USER qdr cannot write the host's root-owned bind mount."""
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, re.M)

    assert not users, (
        f"Dockerfile sets USER {users!r}. The deployment bind-mounts a root-owned "
        "host directory, which a non-root container cannot write — that is what "
        "kept the live report empty. Match qubic_spotlight/qubic_doge_stats and "
        "stay root, or coordinate a chown with whoever administers the host."
    )


def test_the_root_tradeoff_is_documented():
    """Running as root is a deliberate call and must not look accidental."""
    assert "as root" in DOCKERFILE, (
        "the reason for running as root belongs next to the decision"
    )


def test_compose_bind_mounts_like_the_sibling_services():
    """One predictable directory per service under /root, as on the host."""
    assert "/root/qdr/data:/data" in COMPOSE, (
        "compose should mirror how the host actually mounts data for its other "
        "services (/root/<service>/data:/data)"
    )
    assert "qdr-data:/data" not in COMPOSE, "the named volume was replaced"
    assert not re.search(r"^volumes:", COMPOSE, re.M), (
        "no top-level volumes: block is needed for a bind mount"
    )


def test_both_services_share_one_data_directory():
    """The API reads exactly what the ingest writes."""
    mounts = re.findall(r"-\s+(/root/qdr/data:/data)", COMPOSE)
    assert len(mounts) == 2, f"expected both services to mount it, found {mounts}"
