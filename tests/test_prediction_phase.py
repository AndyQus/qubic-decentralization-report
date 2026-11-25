"""The core's prediction phase (v1.314.0, epoch 241): replies of messages 82/83
and 84/85 as laid out in core src/network_messages/prediction_phase.h."""
import struct

import pytest

from qdr import antnode


def test_phase_training():
    p = antnode.parse_prediction_phase(struct.pack("<6I", 0, 0, 87, 3504, 1800, 0))
    assert p["phase"] == "training"
    assert p["switch_tick"] is None and p["first_hour"] is None
    assert p["computors_qualified"] == 87 and p["computors_needed"] == 226
    assert p["threshold"] == 3504 and p["deadline_s"] == 1800 and p["min_correct"] == 12


def test_phase_prediction():
    p = antnode.parse_prediction_phase(struct.pack("<6I", 92300000, 40510, 226, 3504, 1800, 0xABCD))
    assert p["phase"] == "prediction"
    assert p["switch_tick"] == 92300000 and p["first_hour"] == 40510
    assert p["folded_digest"] == 0xABCD


def test_phase_short_reply():
    with pytest.raises(ValueError):
        antnode.parse_prediction_phase(b"\0" * 20)


def test_identity_layout():
    answers = bytes([1, 0, 2] + [2] * 165)
    raw = struct.pack("<6I", 40510, 24, 18, 92310000, 7, 40489) + answers
    assert len(raw) == antnode.PREDICTION_IDENTITY_SIZE == 192
    r = antnode.parse_prediction_identity(raw)
    assert r["qualified"] and r["first_hour"] == 40510
    assert r["hours"] == 24 and r["correct"] == 18 and r["accuracy"] == 0.75
    assert r["ranked_by_accuracy"]
    assert r["solution"] == {"tick": 92310000, "index": 7}
    assert r["base_hour"] == 40489 and r["answers"].startswith("010002")


def test_identity_not_qualified():
    r = antnode.parse_prediction_identity(struct.pack("<6I", 0, 0, 0, 0, 0, 40489) + bytes([2] * 168))
    assert not r["qualified"] and r["accuracy"] is None and r["solution"] is None
    assert not r["ranked_by_accuracy"]
