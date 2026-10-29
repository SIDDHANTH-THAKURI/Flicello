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


def test_summary_shows_each_ratings_scale(tmp_path):
    path = tmp_path / "ratings.jsonl"
    log_rating({"version": "old cut", "user": 7.1}, path)
    log_rating({"version": "new cut", "user": 7.2, "out_of": 20}, path)
    text = summary(read_ratings(path))
    assert "7.1/10" in text and "7.2/20" in text and "best 7.2" in text


def test_postmortem_goes_on_top(tmp_path):
    path = tmp_path / "lessons.md"
    path.write_text(f"# Lessons\n\n{POSTMORTEMS}\n- **old** — first lesson\n", encoding="utf-8")
    add_postmortem("Trip v2", "4.8. Polish without an idea plateaus.", path, day="2026-10-08")
    lines = path.read_text(encoding="utf-8").splitlines()
    i = lines.index(POSTMORTEMS)
    assert lines[i + 1].startswith("- **2026-10-08 Trip v2** — 4.8.") and lines[i + 2] == "- **old** — first lesson"


def test_a_fresh_copy_reads_the_empty_starters_and_writes_its_own_files(tmp_path, monkeypatch):
    from vidosuper import learning

    starter_r, starter_l = tmp_path / "ratings.jsonl", tmp_path / "lessons.md"
    starter_r.write_text("", encoding="utf-8")
    starter_l.write_text(f"# Lessons\n\n## 1. Your scale\n\n{POSTMORTEMS}\n", encoding="utf-8")
    mine = tmp_path / "mine"
    monkeypatch.setattr(learning, "STARTER_RATINGS", starter_r)
    monkeypatch.setattr(learning, "STARTER_LESSONS", starter_l)
    monkeypatch.setattr(learning, "RATINGS", mine / "ratings.jsonl")
    monkeypatch.setattr(learning, "LESSONS", mine / "lessons.md")
    assert learning.read_ratings() == [] and learning.summary(learning.read_ratings()) == "No ratings yet."
    assert learning.lessons_path() == starter_l  # nothing of their own yet: the agents read the starter
    learning.log_rating({"version": "first cut", "user": 6.0}, learning.RATINGS)
    learning.add_postmortem("Trip", "6/20. The idea carried it.", learning.LESSONS, day="2026-10-29")
    assert [r["user"] for r in learning.read_ratings()] == [6.0]
    own = (mine / "lessons.md").read_text(encoding="utf-8")
    assert own.startswith("# Lessons") and "- **2026-10-29 Trip** — 6/20." in own
    assert learning.lessons_path() == mine / "lessons.md"
    assert starter_r.read_text(encoding="utf-8") == "" and "Trip" not in starter_l.read_text(encoding="utf-8")


def test_the_shipped_starters_are_empty_and_ready():
    from vidosuper import learning

    assert learning.STARTER_RATINGS.read_text(encoding="utf-8").strip() == ""
    starter = learning.STARTER_LESSONS.read_text(encoding="utf-8")
    assert POSTMORTEMS in starter and "Wollongong" not in starter and "Sydney" not in starter
