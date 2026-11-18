"""The mining task is read from the network itself, and checked there.

Since core v1.309 (live from epoch 240) there is no task file: the targets are the
hourly BTC direction the nodes store through the Price oracle, served by
REQUEST_BPP9000_TASK_DATA (80); the feed's state by REQUEST_PRICE_SERIES (78).
What is pinned here:

  * the wire formats parse as the core defines them (doc/ant_colony_mining.md
    2.7d/2.7e), and a node's `valid = 0` is "no task", not an empty one;
  * the task counts as verified only when K12 of its first frame equals the
    node's dataHash -- a mismatch is reported, never shown as numbers;
  * prices never leave the parser;
  * the constants a node does not report come from the release tag of the
    node's own version, and a version nothing matches is not looked up on every
    poll.

No test here touches the network: GitHub is a dict of URL -> bytes.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import antask  # noqa: E402

SETTINGS = """
static constexpr unsigned long long BPP9000_WINDOW_WIDTH = 24 * 365;
// How far the frame may slide: production predicts one week ahead.
static constexpr unsigned long long BPP9000_SHIFT_CAP = 24 * 7;
static constexpr unsigned int BPP9000_ADVANCE_THRESHOLD = 3504;
static constexpr unsigned int BPP9000_SOLUTION_THRESHOLD_DEFAULT = 4350;
"""
W = 24 * 365
BASE = 40316


def targets(live: list[int]) -> bytes:
    return bytes([i % 2 for i in range(W)] + live)


def task_payload(tg: bytes, shift_limit: int, valid: int = 1, base: int = BASE,
                 tick: int = 91_911_000, epoch: int = 240) -> bytes:
    head = struct.pack("<IIHBBII", tick, tick - 3, epoch, valid, 0, base, shift_limit)
    return head + (tg if valid else b"")


def series_payload(points: list[tuple[int, int, int, int]], next_hour: int,
                   failed: int = 0, base: int = BASE) -> bytes:
    head = struct.pack("<IHBBIIIqq32sQQQQQQII", 91_911_000, 240, 1, 0, 1, next_hour, failed,
                       0, 0, b"\x11" * 32, 4, 0, 0, 0, 0, 0, base, len(points))
    return head + b"".join(struct.pack("<IIIq", *p) for p in points)


def github(files: dict[str, bytes], tags: list[str]):
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.startswith("https://api.github.com/"):
            return json.dumps([{"name": t} for t in tags]).encode()
        key = url.split("/qubic/core/", 1)[1]
        if key not in files:
            raise OSError("404 " + url)
        return files[key]

    fetch.calls = calls
    return fetch


def resolver(tmp_path, files=None, tags=("v1.313.0", "v1.312.0")):
    files = files if files is not None else {"v1.313.0/src/public_settings.h": SETTINGS.encode()}
    return antask.SettingsResolver(cache_file=tmp_path / "s.json",
                                   fetch=github(files, list(tags)), background=False)


# -- wire formats ------------------------------------------------------------

def test_task_data_parses_and_invalid_means_none():
    tg = targets([0, 1, 1])
    got = antask.parse_task_data(task_payload(tg, 3))
    assert got["base_hour"] == BASE and got["shift_limit"] == 3 and got["targets"] == tg
    assert antask.parse_task_data(task_payload(b"", 0, valid=0)) is None


def test_price_series_drops_every_price():
    p = antask.parse_price_series(series_payload([(40317, 91910016, 0, 6468533000000)], 40318))
    assert p["next_hour"] == 40318 and p["points"] == [{"hour": 40317, "append_tick": 91910016, "flags": 0}]
    assert "price" not in json.dumps(p).lower()


def test_hour_zero_is_the_documented_epoch():
    # doc 2.3a: hour H is H hours after 2022-04-13 12:00:00 UTC; baseHour 40316
    # was measured live as 2026-11-18 08:00 UTC.
    assert antask.hour_at(40316) == 1794988800


# -- verification -------------------------------------------------------------

def test_verified_only_when_k12_of_the_first_frame_matches(tmp_path):
    tg = targets([0, 0, 1])
    task = antask.parse_task_data(task_payload(tg, 3))
    good = antask.k12_hex(tg[:W])
    out = antask.task_block(good, task, version=313, resolver=resolver(tmp_path))
    assert out["hash_verified"] is True
    assert out["window_width"] == W and out["shift_limit"] == 3 and out["shift_cap"] == 168
    assert out["advance_threshold"] == 3504 and out["source_ref"] == "v1.313.0"
    assert out["target_ones"] == sum(tg)

    bad = antask.task_block("00" * 32, task, version=313, resolver=resolver(tmp_path))
    assert bad["hash_verified"] is False


def test_live_hours_are_marked_and_carry_their_tick(tmp_path):
    tg = targets([0, 1])
    task = antask.parse_task_data(task_payload(tg, 2))
    series = antask.parse_price_series(series_payload(
        [(BASE + 1, 500, 0, 1), (BASE + 2, 600, antask.FLAG_FILLED, 1)], BASE + 3))
    hours = antask.hours_block(task, series)
    assert len(hours) == antask.RECENT_FRAME_HOURS + 2
    live = [h for h in hours if h["live"]]
    assert [(h["hour"], h["up"], h["append_tick"], h["filled"]) for h in live] == [
        (BASE + 1, False, 500, False), (BASE + 2, True, 600, True)]
    assert hours[antask.RECENT_FRAME_HOURS - 1]["hour"] == BASE    # frame ends at baseHour


def test_no_task_data_gives_the_hash_and_no_numbers(tmp_path):
    out = antask.task_block("ab" * 32, None, version=313, resolver=resolver(tmp_path))
    assert out["hash_verified"] is None and "window_width" not in out
    assert antask.hours_block(None, None) == []


def test_a_frame_of_the_wrong_width_is_not_trusted(tmp_path):
    tg = bytes(100)
    task = antask.parse_task_data(task_payload(tg, 0))
    out = antask.task_block(antask.k12_hex(tg), task, version=313, resolver=resolver(tmp_path))
    assert out["hash_verified"] is False


# -- settings -----------------------------------------------------------------

def test_settings_come_from_the_nodes_own_release(tmp_path):
    files = {"v1.312.0/src/public_settings.h": SETTINGS.replace("3504", "9999").encode(),
             "v1.313.0/src/public_settings.h": SETTINGS.encode(),
             "main/src/public_settings.h": SETTINGS.replace("3504", "1").encode()}
    r = resolver(tmp_path, files)
    assert r.get(313)["ref"] == "v1.313.0"
    assert r.get(312)["constants"]["BPP9000_ADVANCE_THRESHOLD"] == 9999


def test_a_version_without_a_tag_falls_back_to_main(tmp_path):
    r = resolver(tmp_path, {"main/src/public_settings.h": SETTINGS.encode()})
    assert r.get(314)["ref"] == "main"


def test_a_failed_lookup_is_not_repeated_every_poll(tmp_path):
    r = resolver(tmp_path, files={})
    assert r.get(313) is None
    n = len(r.fetch.calls)
    assert r.get(313) is None
    assert len(r.fetch.calls) == n


def test_resolved_settings_survive_a_restart_without_github(tmp_path):
    assert resolver(tmp_path).get(313) is not None
    offline = antask.SettingsResolver(cache_file=tmp_path / "s.json",
                                      fetch=github({}, []), background=False)
    assert offline.get(313)["ref"] == "v1.313.0"
    assert offline.fetch.calls == []


def test_settings_expressions_are_evaluated():
    s = antask.parse_settings(SETTINGS)
    assert s["BPP9000_WINDOW_WIDTH"] == 8760 and s["BPP9000_SHIFT_CAP"] == 168
