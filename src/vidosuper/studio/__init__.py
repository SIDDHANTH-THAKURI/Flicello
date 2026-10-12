"""Flicello: the web app that lets invited people make a video themselves.

Upload clips and a song, pick one of three story ideas, approve a short sample, get the finished video in
9:16 and/or 16:9, and rate it; the system learns each person's style from that feedback. The studio wraps
the same pipeline the command line uses (ingest, perceive, design, render, check); the judgement steps
that a person or Claude Code did by hand are made by a "brain" (the Claude API, headless Claude Code for
the owner, or a local no-AI fallback).
"""
