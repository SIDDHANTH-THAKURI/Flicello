from __future__ import annotations

from vidosuper.learning import POSTMORTEMS, add_postmortem, critic_bias, log_rating, read_ratings, summary


def test_ratings_log_and_critic_bias(tmp_path):
    path = tmp_path / "ratings.jsonl"
    log_rating({"version": "draft", "user": 3.0, "critics": 6.9}, path)
    log_rating({"version": "cut 2", "user": 4.8, "critics": None, "music": 5.0}, path)
    log_rating({"version": "cut 3", "user": 5.5, "critics": 6.0}, path)
    ratings = read_ratings(path)
    assert [r["user"] for r in ratings] == [3.0, 4.8, 5.5] and all("date" in r for r in ratings)
    assert critic_bias(ratings) == (2.2, 2)  # (3.9 + 0.5) / 2
    text = summary(ratings)
    assert "too generous" in text and "music 5" in text and "best 5.5" in text


def test_postmortem_goes_on_top(tmp_path):
    path = tmp_path / "lessons.md"
    path.write_text(f"# Lessons\n\n{POSTMORTEMS}\n- **old** — first lesson\n", encoding="utf-8")
    add_postmortem("Trip v2", "4.8. Polish without an idea plateaus.", path, day="2026-10-08")
    lines = path.read_text(encoding="utf-8").splitlines()
    i = lines.index(POSTMORTEMS)
    assert lines[i + 1].startswith("- **2026-10-08 Trip v2** — 4.8.") and lines[i + 2] == "- **old** — first lesson"
