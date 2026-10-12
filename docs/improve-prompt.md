You are running the studio's improvement routine for the owner, unattended. Read docs/improvement.md first and follow it
exactly (the loop and the rules the routine never breaks).

Today's digest is the newest file in %LOCALAPPDATA%\VidoSuper\studio\improve\ (digest-*.md). Read it and the last
entries of bench.jsonl in the same folder. Then:

1. Choose at most two root causes. Say which and why, in one line each.
2. Reproduce each on the stored project; fix as code with a test; run `python -m pytest -q`.
3. Run `python -m vidosuper studio bench` and compare with the previous commit's numbers.
4. Commit on branch improve/<today's date> and push that branch. Never push main.
5. Run `python -m vidosuper studio notify "<what you changed, what is checked, what is only a guess>"`.

If the digest shows nothing worth fixing, run `vido studio notify "Nothing to fix today: <one line>"` and stop. If
anything is unclear or risky, stop and leave the question in the note instead of guessing.
