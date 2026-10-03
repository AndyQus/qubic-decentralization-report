"""The task description follows the network across core releases without an edit.

What is pinned here is what made the hard-coded version go wrong: v1.306 changed the
task and the page kept describing v1.304's. So:

  * the description is read from the core ref whose task file carries the live hash,
    including a release tag when `main` has already moved on to the next epoch;
  * until that lookup succeeds the page gets the hash and nothing else -- never an
    earlier task's numbers;
  * a hash nothing matches is not looked up on every 30 s poll;
  * whether the task asks about hours nobody knows yet is read from the file.

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

LIVE = "e835130d" * 8
PREVIOUS = "979cdc22" * 8

ROLLING_SETTINGS = """
static constexpr unsigned long long BPP9000_NUMBER_OF_INPUT_NEURONS = 18;
static constexpr unsigned long long BPP9000_SEQUENCE_LENGTH = 24 * 365 + 24 * 7;
static constexpr unsigned long long BPP9000_WINDOW_WIDTH = 24 * 365;
// How far the frame may slide: production predicts one week ahead.
static constexpr unsigned long long BPP9000_SHIFT_CAP = 24 * 7;
static constexpr unsigned int BPP9000_SOLUTION_THRESHOLD_DEFAULT = 4350;
"""
WINDOW_SETTINGS = """
static constexpr unsigned long long BPP9000_SEQUENCE_LENGTH = 24 * 365;
static constexpr unsigned long long BPP9000_WINDOW_WIDTH = 24 * 28;
static constexpr unsigned int BPP9000_SOLUTION_THRESHOLD_DEFAULT = 4000;
"""


def _pack(trits: list[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(trits), 5):
        chunk = trits[i:i + 5] + [0] * (5 - len(trits[i:i + 5]))
        out.append(sum(t * 3 ** k for k, t in enumerate(chunk)))
    return bytes(out)


def task_file(data_hash: str, targets: list[int], n_in: int = 18) -> bytes:
    """A task file in the core's format: header, empty topology, packed rows."""
    population, neighbors = 0, 0
    header = struct.pack("<IIIIQII", antask.TASK_MAGIC, 1, n_in, 1, len(targets), population, neighbors)
    header += b"\0" * 32 + bytes.fromhex(data_hash)
    assert len(header) == antask.HEADER_SIZE
    topology = b"\0" * ((n_in + 1 + 1 + population * neighbors) * 4)
    rows = b"".join(_pack([1] * n_in) + _pack([t]) for t in targets)
    return header + topology + rows


def github(files: dict[str, bytes], tags: list[str]):
    """A fetch() that serves `files` (keyed ref/path) and the tag list, and logs calls."""
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


def rolling_targets(tail: list[int] | None = None) -> list[int]:
    seq = 24 * 365 + 24 * 7
    body = [i % 2 for i in range(24 * 365)]
    tail = tail if tail is not None else [1, 0] * (24 * 7 // 2)
    return body + tail + [0]   # the core's file carries one row past the sequence


def test_settings_expressions_are_evaluated():
    s = antask.parse_settings(ROLLING_SETTINGS)
    assert s["BPP9000_SEQUENCE_LENGTH"] == 8928
    assert s["BPP9000_WINDOW_WIDTH"] == 8760
    assert s["BPP9000_SHIFT_CAP"] == 168


def test_rolling_frame_is_described_from_the_file():
    task = antask.parse_task_file(task_file(LIVE, rolling_targets()))
    d = antask.describe(task, antask.parse_settings(ROLLING_SETTINGS), "main")

    assert d["scoring"] == "rolling-frame"
    assert d["score_max"] == 8760          # error count inside one frame
    assert d["advance_gate"] == 2920       # a third of the frame
    assert d["shift_cap"] == 168
    assert d["inputs_used"] is False
    # Only the first SEQUENCE_LENGTH rows are graded; the extra row is not counted.
    assert d["target_zeros"] + d["target_ones"] + d["target_unknown"] == 8928
    assert d["tail_rows"] == 168 and d["tail_unknown"] == 0


def test_open_hours_in_the_frame_tail_are_reported():
    """UNKNOWN targets where the frame slides to would mean grading unknown hours."""
    tail = [1, 0] * 80 + [2] * 8
    task = antask.parse_task_file(task_file(LIVE, rolling_targets(tail)))
    d = antask.describe(task, antask.parse_settings(ROLLING_SETTINGS), "main")
    assert d["tail_unknown"] == 8


def test_sliding_windows_model_still_reads_correctly():
    targets = [i % 2 for i in range(24 * 365 + 1)]
    task = antask.parse_task_file(task_file(PREVIOUS, targets))
    d = antask.describe(task, antask.parse_settings(WINDOW_SETTINGS), "v1.305.0")
    assert d["scoring"] == "sliding-windows"
    assert d["score_max"] == 8088
    assert d["shift_cap"] is None


def test_epoch_switch_finds_the_release_tag_when_main_moved_on():
    """main already holds the next task; the network still mines the previous one."""
    fetch = github({
        "main/" + antask.TASK_PATH: task_file(LIVE, rolling_targets()),
        "main/" + antask.SETTINGS_PATH: ROLLING_SETTINGS.encode(),
        "v1.306.0/" + antask.TASK_PATH: task_file(LIVE, rolling_targets()),
        "v1.305.0/" + antask.TASK_PATH: task_file(PREVIOUS, [i % 2 for i in range(8761)]),
        "v1.305.0/" + antask.SETTINGS_PATH: WINDOW_SETTINGS.encode(),
    }, tags=["v1.306.0", "v1.305.0"])

    d = antask.find_task(PREVIOUS, fetch)
    assert d["source_ref"] == "v1.305.0"
    assert d["window_width"] == 672
    # main and v1.306.0 carry the same file: the second copy is not parsed again,
    # and settings are only fetched for the ref that matched.
    assert not any(u.endswith("main/" + antask.SETTINGS_PATH) for u in fetch.calls)


def test_unresolved_hash_gives_the_hash_and_no_numbers(tmp_path):
    fetch = github({}, tags=[])
    r = antask.TaskResolver(cache_file=tmp_path / "t.json", fetch=fetch, background=False)
    block = antask.task_block(LIVE, r)
    assert block == {"data_hash": LIVE, "hash_verified": None}


def test_a_failed_lookup_is_not_repeated_every_poll(tmp_path):
    fetch = github({}, tags=[])
    r = antask.TaskResolver(cache_file=tmp_path / "t.json", fetch=fetch, background=False)
    r.get(LIVE)
    n = len(fetch.calls)
    for _ in range(5):
        r.get(LIVE)
    assert len(fetch.calls) == n


def test_resolved_task_survives_a_restart_without_github(tmp_path):
    files = {
        "main/" + antask.TASK_PATH: task_file(LIVE, rolling_targets()),
        "main/" + antask.SETTINGS_PATH: ROLLING_SETTINGS.encode(),
    }
    cache = tmp_path / "t.json"
    first = antask.TaskResolver(cache_file=cache, fetch=github(files, []), background=False)
    assert antask.task_block(LIVE, first)["hash_verified"] is True

    def offline(url):
        raise OSError("no network")

    second = antask.TaskResolver(cache_file=cache, fetch=offline, background=False)
    block = antask.task_block(LIVE.upper(), second)   # hash case must not matter
    assert block["hash_verified"] is True
    assert block["source_ref"] == "main"
    assert block["score_max"] == 8760
