"""Tests for long-transcript map-reduce summarization and JSON-fallback (P1.2).

The map-reduce + JSON-normalize behaviour now lives in ``run_skill`` (driven by a
skill). These exercise it via the built-in json ``quick-note`` skill."""
import asyncio

import app
import skills


def _quick_note():
    return skills.get_skill("quick-note")


def test_chunk_text_respects_size_and_words():
    text = " ".join(f"word{i}" for i in range(5000))  # well over CHUNK_CHARS
    chunks = app._chunk_text(text, size=app.CHUNK_CHARS)
    assert len(chunks) > 1
    assert all(len(c) <= app.CHUNK_CHARS for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")  # no data lost


def test_short_transcript_is_single_shot(monkeypatch):
    calls = []

    async def fake_gen(model, prompt, fmt=None, images=None, timeout=300.0):
        calls.append(fmt)
        return '{"summary":"s","action_items":["a"],"tags":["t"]}'

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)

    result = asyncio.run(app.run_skill(_quick_note(), "short note", "m"))
    assert (result["summary"], result["action_items"], result["tags"]) == ("s", ["a"], ["t"])
    assert calls == ["json"]  # exactly one (reduce) call, no map step


def test_long_transcript_uses_map_reduce(monkeypatch):
    calls = []

    async def fake_gen(model, prompt, fmt=None, images=None, timeout=300.0):
        calls.append(fmt)
        if fmt == "json":
            return '{"summary":"combined","action_items":[],"tags":["x"]}'
        return "partial summary"  # map step (plain text)

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)

    long_text = "x " * (app.MAP_REDUCE_CHARS)  # exceeds the threshold
    result = asyncio.run(app.run_skill(_quick_note(), long_text, "m"))
    assert result["summary"] == "combined"
    assert calls.count(None) >= 2   # multiple map calls
    assert calls.count("json") == 1  # one reduce call


def test_bad_json_falls_back(monkeypatch):
    async def fake_gen(model, prompt, fmt=None, images=None, timeout=300.0):
        return "I'm sorry, here is your summary in prose."  # not JSON

    monkeypatch.setattr(app, "_ollama_generate", fake_gen)

    result = asyncio.run(app.run_skill(_quick_note(), "note", "m"))
    assert result["summary"] == "" and result["action_items"] == [] and result["tags"] == []
