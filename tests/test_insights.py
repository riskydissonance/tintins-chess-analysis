"""Tests for the time-windowed insights aggregate (history.insights)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from server import config
from server.core import history


def _rec(game_id: str, date: str | None, *, accuracy: float = 80.0, result: str = "win",
         motifs: list[str] | None = None, analyzed_at: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "game_id": game_id,
        "reviewed_side": "white",
        "analyzed_at": analyzed_at or "2024-01-01T00:00:00Z",
        "player_id": "alice",
        "platform": "lichess",
        "player_name": "alice",
        "date": date,
        "white": "alice",
        "black": "bob",
        "result": "1-0",
        "player_result": result,
        "opening": "Petrov Defense",
        "speed": "blitz",
        "accuracy": accuracy,
        "counts": {"inaccuracy": 0, "mistake": 1, "blunder": 1},
        "phase_loss": {"opening": 0.0, "middlegame": 20.0, "endgame": 0.0},
        "mistakes": [{"motifs": motifs or ["hung_piece"]}],
    }


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USERNAME", "alice")
    monkeypatch.setattr(config, "USERNAME_ALIASES", [])
    path = tmp_path / "history"
    path.mkdir()
    return str(tmp_path)


def _write(records: list[dict], data_dir: str) -> None:
    p = os.path.join(data_dir, "history", "games.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _day(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_insights_all_time(data_dir):
    _write([_rec("g1", _day(1)), _rec("g2", _day(40), accuracy=60.0, result="loss")], data_dir)
    out = history.insights(None, data_dir)
    assert out["games"] == 2
    assert out["results"] == {"win": 1, "loss": 1, "draw": 0}
    assert out["avg_accuracy"] == 70.0
    # Motifs carry human labels for the frontend.
    assert out["top_motifs"][0]["motif"] == "hung_piece"
    assert "hanging pieces" in out["top_motifs"][0]["label"]
    assert out["weakest_phase"] == "middlegame"


def test_insights_window_filters_by_played_date(data_dir):
    _write([_rec("g1", _day(1)), _rec("g2", _day(40))], data_dir)
    out = history.insights(7, data_dir)
    assert out["games"] == 1
    assert out["days"] == 7


def test_insights_falls_back_to_analyzed_date(data_dir):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _write([_rec("g1", None, analyzed_at=now)], data_dir)
    assert history.insights(7, data_dir)["games"] == 1


def test_insights_only_counts_my_games(data_dir):
    other = _rec("g9", _day(1))
    other["player_id"] = "someone_else"
    other["player_name"] = "someone_else"
    _write([_rec("g1", _day(1)), other], data_dir)
    assert history.insights(None, data_dir)["games"] == 1


def test_insights_without_identity_counts_everything(data_dir, monkeypatch):
    # No configured username (paste-only user) -> aggregate all analyzed games.
    monkeypatch.setattr(config, "USERNAME", "")
    other = _rec("g9", _day(1))
    other["player_id"] = "someone_else"
    other["player_name"] = "someone_else"
    _write([_rec("g1", _day(1)), other], data_dir)
    assert history.insights(None, data_dir)["games"] == 2


def test_insights_empty_history(data_dir):
    _write([], data_dir)
    out = history.insights(30, data_dir)
    assert out["games"] == 0


def test_history_rows_ordered_by_played_date_then_import_time(data_dir):
    # Played date wins over import (analyzed_at) order; no played date falls back to import day;
    # same-day games tiebreak on import time.
    recent_played = _rec("g-recent", _day(1), analyzed_at="2024-01-01T00:00:00Z")
    old_played_new_import = _rec("g-old", _day(40), analyzed_at="2099-01-01T00:00:00Z")
    undated = _rec("g-undated", None, analyzed_at=_day(2) + "T12:00:00Z")
    _write([old_played_new_import, undated, recent_played], data_dir)
    rows = history.history_rows(data_dir=data_dir)
    assert [r["game_id"] for r in rows] == ["g-recent", "g-undated", "g-old"]


def test_opening_accuracy_ignores_games_without_accuracy(data_dir):
    # Two Petrov games, only one with an accuracy: the average is that game's 80%, not 40%.
    no_acc = _rec("g2", _day(1))
    no_acc["accuracy"] = None
    _write([_rec("g1", _day(1)), no_acc], data_dir)
    (petrov,) = history.insights(None, data_dir)["openings"]
    assert petrov["games"] == 2
    assert petrov["avg_accuracy"] == 80.0


def _rep_rec(
    game_id: str,
    side: str,
    *,
    eco: str = "B90",
    opening: str = "Sicilian Defense: Najdorf Variation",
    result: str = "win",
    accuracy: float = 80.0,
    opening_loss: float = 5.0,
    book_ply: int | None = 10,
    mistakes: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "game_id": game_id,
        "reviewed_side": side,
        "analyzed_at": "2024-01-01T00:00:00Z",
        "player_id": "alice",
        "platform": "lichess",
        "player_name": "alice",
        "date": _day(1),
        "white": "alice" if side == "white" else "bob",
        "black": "bob" if side == "white" else "alice",
        "result": "1-0",
        "player_result": result,
        "eco": eco,
        "opening": opening,
        "book_ply": book_ply,
        "speed": "blitz",
        "accuracy": accuracy,
        "counts": {"inaccuracy": 0, "mistake": 0, "blunder": 0},
        "phase_loss": {"opening": opening_loss, "middlegame": 0.0, "endgame": 0.0},
        "mistakes": mistakes or [],
    }


def test_repertoire_groups_by_side_and_computes_score(data_dir):
    _write(
        [
            _rep_rec("g1", "white", result="win", opening_loss=4.0),
            _rep_rec("g2", "white", result="loss", opening_loss=6.0),
            _rep_rec("g3", "black", eco="C50", opening="Italian Game", result="draw"),
        ],
        data_dir,
    )
    rep = history.insights(None, data_dir)["repertoire"]
    assert len(rep["white"]) == 1
    white = rep["white"][0]
    assert white["side"] == "white"
    assert white["games"] == 2
    assert white["score"] == {"win": 1, "loss": 1, "draw": 0}
    assert white["opening_loss_per_game"] == 5.0
    assert len(rep["black"]) == 1
    assert rep["black"][0]["opening"] == "Italian Game"


def test_repertoire_worst_requires_min_three_games(data_dir):
    # Only 2 games in this opening/side -> excluded from "worst" despite a high loss rate.
    _write(
        [
            _rep_rec("g1", "white", opening_loss=20.0),
            _rep_rec("g2", "white", opening_loss=20.0),
        ],
        data_dir,
    )
    rep = history.insights(None, data_dir)["repertoire"]
    assert rep["worst"] == []


def test_repertoire_worst_sorted_by_loss_per_game(data_dir):
    _write(
        [
            _rep_rec("g1", "white", eco="B90", opening_loss=2.0),
            _rep_rec("g2", "white", eco="B90", opening_loss=2.0),
            _rep_rec("g3", "white", eco="B90", opening_loss=2.0),
            _rep_rec("g4", "black", eco="C50", opening="Italian Game", opening_loss=15.0),
            _rep_rec("g5", "black", eco="C50", opening="Italian Game", opening_loss=15.0),
            _rep_rec("g6", "black", eco="C50", opening="Italian Game", opening_loss=15.0),
        ],
        data_dir,
    )
    rep = history.insights(None, data_dir)["repertoire"]
    assert rep["worst"][0]["eco"] == "C50"
    assert rep["worst"][0]["opening_loss_per_game"] == 15.0


def test_repertoire_blunders_opening_and_book_ply(data_dir):
    mistakes = [
        {"phase": "opening", "classification": "blunder"},
        {"phase": "middlegame", "classification": "blunder"},
        {"phase": "opening", "classification": "mistake"},
    ]
    _write(
        [
            _rep_rec("g1", "white", book_ply=8, mistakes=mistakes),
            _rep_rec("g2", "white", book_ply=12),
            _rep_rec("g3", "white", book_ply=None),
        ],
        data_dir,
    )
    white = history.insights(None, data_dir)["repertoire"]["white"][0]
    assert white["blunders_opening"] == 1
    # Median of the two known book_ply values (8, 12) -> 10; the None record is ignored.
    assert white["book_ply"] == 10


def test_insights_trend_is_chronological_and_capped(data_dir):
    # Oldest→newest by played day, one entry per game, with the fields the trend chart reads.
    _write(
        [
            _rec("g1", _day(3), accuracy=70.0),
            _rec("g2", _day(1), accuracy=90.0),
            _rec("g3", _day(2), accuracy=80.0, result="loss"),
        ],
        data_dir,
    )
    out = history.insights(None, data_dir)
    assert [t["accuracy"] for t in out["trend"]] == [70.0, 80.0, 90.0]
    assert out["trend"][1]["result"] == "loss"
    assert set(out["trend"][0]) == {"date", "accuracy", "result", "opening"}
