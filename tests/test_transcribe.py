from __future__ import annotations

from types import SimpleNamespace as NS

from vidosuper.perceive.transcribe import build_transcript


def _seg(start, end, text, word_probs, no_speech=0.1, logprob=-0.4, compression=1.2):
    words = [NS(start=start + i * 0.2, end=start + i * 0.2 + 0.15, word=f"w{i}", probability=p)
             for i, p in enumerate(word_probs)]
    return NS(start=start, end=end, text=text, words=words, no_speech_prob=no_speech, avg_logprob=logprob,
              compression_ratio=compression)


def test_clear_speech_is_confident():
    segs = [_seg(1.0, 3.0, " Look at the bridge, it's huge!", [0.9, 0.95, 0.9, 0.88, 0.93, 0.9])]
    t = build_transcript("c", "small", segs, NS(language="en", language_probability=0.97))
    assert t.has_speech and t.utterances[0].text == "Look at the bridge, it's huge!"


def test_noise_misheard_as_short_words_is_flagged():
    # Real case: tram brakes hissing came back as "Shush!" three times, language only 50% certain.
    segs = [_seg(8.05, 8.33, " Shush!", [0.104]), _seg(8.47, 8.51, " Shush!", [0.763]),
            _seg(10.46, 10.94, " Shush!", [0.704]), _seg(11.02, 11.5, " Are you?", [0.16, 0.977])]
    t = build_transcript("c", "small", segs, NS(language="en", language_probability=0.505))
    assert not t.has_speech
    assert all("(unsure)" in line for line in t.as_lines())


def test_low_word_confidence_line_is_flagged_even_in_confident_language():
    segs = [_seg(1.0, 3.0, " Real sentence here today", [0.9, 0.92, 0.95, 0.9]), _seg(5.0, 5.4, " Hmm", [0.2])]
    t = build_transcript("c", "small", segs, NS(language="en", language_probability=0.95))
    assert [u.confident for u in t.utterances] == [True, False]
