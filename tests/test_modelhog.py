from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

from modelhog import cli, scan
from modelhog.scan import AI, CACHE, PROJECT

MB = 2**20
OLD = time.time() - 200 * 86400


def blob(path: Path, size: int, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.truncate(size)  # sparse: big on paper, nothing on disk
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def age(path: Path, mtime: float):
    for p in [path, *path.rglob("*")]:
        os.utime(p, (mtime, mtime), follow_symlinks=False) if os.utime in os.supports_follow_symlinks else \
            os.utime(p, (mtime, mtime))


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    for var in ("HF_HOME", "HF_HUB_CACHE", "OLLAMA_MODELS", "XDG_CACHE_HOME", "UV_CACHE_DIR", "LLAMA_CACHE",
                "TORCH_HOME", "HF_DATASETS_CACHE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(scan, "mac", lambda: False)
    monkeypatch.setattr(scan, "win", lambda: False)
    hub = h / ".cache" / "huggingface" / "hub"
    # a Hugging Face model: blobs + a snapshot that symlinks to them (must count once)
    m = hub / "models--meta-llama--Llama-3.1-8B"
    b = blob(m / "blobs" / "abc123", 300 * MB)
    snap = m / "snapshots" / "deadbeef"
    snap.mkdir(parents=True)
    try:
        (snap / "model.safetensors").symlink_to(b)
    except OSError:  # Windows without symlink rights
        pass
    age(m, OLD)
    blob(hub / "datasets--squad" / "blobs" / "x", 20 * MB)
    # Ollama: two tags sharing one 400 MB layer
    om = h / ".ollama" / "models"
    for tag, extra in (("8b", "sha256:aaa"), ("latest", "sha256:bbb")):
        mf = om / "manifests" / "registry.ollama.ai" / "library" / "llama3" / tag
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"config": {"digest": extra, "size": 1000},
                                  "layers": [{"digest": "sha256:shared", "size": 400 * MB}]}))
    # LM Studio publisher/model folders
    blob(h / ".lmstudio" / "models" / "lmstudio-community" / "Qwen2.5-7B-GGUF" / "q4.gguf", 120 * MB)
    blob(h / ".lmstudio" / "models" / "tiny" / "small-model" / "x.gguf", 1 * MB)  # too small to list
    # dev caches
    blob(h / ".cache" / "pip" / "http" / "a", 90 * MB)
    blob(h / ".cache" / "uv" / "wheels" / "b", 10 * MB)  # below the threshold
    # project junk
    proj = h / "code" / "shop"
    (proj).mkdir(parents=True)
    (proj / "package.json").write_text("{}")
    blob(proj / "node_modules" / "big" / "index.js", 60 * MB)
    blob(h / "code" / "notes" / "build" / "x.bin", 60 * MB)  # "build" outside a project isn't junk
    return h


def test_du_counts_hardlinks_once(tmp_path):
    a = blob(tmp_path / "d" / "a", 5 * MB)
    try:
        os.link(a, tmp_path / "d" / "b")
    except OSError:
        pytest.skip("no hard links here")
    assert scan.du(tmp_path / "d")[0] == 5 * MB


def test_scan(home):
    items = scan.scan(projects=[home / "code"])
    by = {(i.source, i.name): i for i in items}
    hf = by[("Hugging Face", "meta-llama/Llama-3.1-8B")]
    assert hf.size == 300 * MB and hf.group == AI and hf.last_used < time.time() - 100 * 86400
    assert ("Hugging Face", "squad (dataset)") in by
    o = [i for i in items if i.source == "Ollama"]
    assert sorted(i.name for i in o) == ["llama3:8b", "llama3:latest"] and o[0].how_to_remove.startswith("ollama rm")
    assert scan.total(o) == 400 * MB + 2000  # the shared layer counts once
    assert ("LM Studio", "Qwen2.5-7B-GGUF") in by and not any(i.name == "small-model" for i in items)
    assert by[("pip", str(home / ".cache" / "pip").replace(str(home), "~"))].group == CACHE
    assert not any(i.source == "uv" for i in items)
    junk = [i for i in items if i.group == PROJECT]
    assert [i.source for i in junk] == ["node_modules"]
    assert items == sorted(items, key=lambda i: -i.size)


def test_render(home):
    items = scan.scan()
    from rich.console import Console
    c = Console(width=160, record=True, file=io.StringIO())
    cli.render(items, c)
    out = c.export_text()
    assert "of AI models and caches" in out and "meta-llama/Llama-3.1-8B" in out and "uvx modelhog" in out
    assert "hasn't been touched in 90 days" in out


def test_clean(home):
    items = scan.scan(projects=[home / "code"])
    from rich.console import Console
    c = Console(file=io.StringIO(), width=120, record=True)
    hf = next(n for n, i in enumerate(items, 1) if i.source == "Hugging Face" and "Llama" in i.name)
    assert cli.clean(items, [hf], None, None, yes=False, dry=True, console=c) == 0
    assert (home / ".cache" / "huggingface" / "hub" / "models--meta-llama--Llama-3.1-8B").exists()
    assert cli.clean(items, [hf], None, None, yes=False, dry=False, console=c, ask=lambda _: "n") == 1
    assert cli.clean(items, [hf], None, None, yes=False, dry=False, console=c, ask=lambda _: "y") == 0
    assert not (home / ".cache" / "huggingface" / "hub" / "models--meta-llama--Llama-3.1-8B").exists()
    assert "Freed 300 MB" in c.export_text()
    assert cli.clean(items, [999], None, None, True, False, c) == 2


def test_clean_older_than_and_command_items(home, monkeypatch):
    items = scan.scan()
    from rich.console import Console
    c = Console(file=io.StringIO(), width=120, record=True)
    # only the Hugging Face model is older than 90 days
    assert cli.clean(items, [], 90 * 86400, None, yes=True, dry=False, console=c) == 0
    assert "meta-llama" in c.export_text()
    # Ollama models are removed with `ollama rm`, never by deleting shared blobs
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    o = next(n for n, i in enumerate(items, 1) if i.source == "Ollama")
    assert cli.clean(items, [o], None, None, yes=True, dry=False, console=c) == 1
    assert "run ollama rm" in c.export_text()


def test_main_json(home, capsys):
    assert cli.main(["--json", "--only", "ai"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {d["source"] for d in data} == {"Hugging Face", "Ollama", "LM Studio"}
    assert cli.main(["clean"]) == 1


def test_human_and_ago():
    assert [scan.human(x) for x in (512, 300 * MB, 1.5 * 2**30, 250 * 2**30)] == ["512 B", "300 MB", "1.5 GB", "250 GB"]
    assert scan.ago(time.time() - 3 * 86400) == "3d ago" and scan.ago(None) == "?"
