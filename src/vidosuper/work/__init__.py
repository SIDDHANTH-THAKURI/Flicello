"""Cello's hands and mind for real work: Claude Code agent sessions on the person's own plan.

Cello's voice (Gemini Live) is quick: it listens, talks and does the instant things. Anything that needs thinking or
making (several steps, anything creative, anything there is no button for) it hands to a work session:

- `sessions`: a Claude Code agent, run headless in a folder of its own for the film, with Flicello at its hands (the
  `fl` command), allowed to read and write only its folder and to run only Flicello's own commands. It reports
  progress, asks a question when something truly matters (and is resumed with the answer), and ends with what Cello
  should tell the person. One session per film carries the whole conversation about it.
- `fl`: the agent's hands: every one of Cello's tools, plus progress, the person's choices, waiting for a step to
  finish, and designing a scene into the film.
- `asks`: questions put to the person (by the agent, or by Flicello itself, like the shape of a film once its clips
  are in), shown as a card of options beside Cello and answered by voice or a tap.
"""
