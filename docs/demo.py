"""Fill a temp home folder with made-up (sparse, so they take no real space) models and caches, then run modelhog.
Used to record the README GIF."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

H = Path(tempfile.mkdtemp(prefix="modelhog-demo-"))
GB, MB = 2**30, 2**20
NOW = time.time()


def put(path, size, days_ago):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(int(size))
    t = NOW - days_ago * 86400
    for p in [path, *path.parents]:
        if p == H:
            break
        os.utime(p, (t, t))


hub = H / ".cache" / "huggingface" / "hub"
for name, size, days in [("meta-llama--Llama-3.1-8B-Instruct", 16.1 * GB, 240), ("mistralai--Mistral-7B-v0.3", 14.5 * GB, 310),
                         ("stabilityai--stable-diffusion-xl-base-1.0", 6.9 * GB, 400), ("openai--whisper-large-v3", 3.1 * GB, 35),
                         ("sentence-transformers--all-MiniLM-L6-v2", 91 * MB, 3), ("Qwen--Qwen2.5-Coder-7B", 15.2 * GB, 120)]:
    put(hub / f"models--{name}" / "blobs" / "weights", size, days)
for model, tag, size, days in [("llama3.1", "8b", 4.9 * GB, 200), ("qwen2.5-coder", "32b", 19.9 * GB, 95),
                               ("deepseek-r1", "14b", 9.0 * GB, 150), ("nomic-embed-text", "latest", 274 * MB, 6)]:
    mf = H / ".ollama" / "models" / "manifests" / "registry.ollama.ai" / "library" / model / tag
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps({"config": {"digest": f"c-{model}", "size": 500},
                              "layers": [{"digest": f"l-{model}", "size": int(size)}]}))
    os.utime(mf, (NOW - days * 86400,) * 2)
put(H / ".lmstudio" / "models" / "lmstudio-community" / "gemma-3-27b-it-GGUF" / "q4.gguf", 16.5 * GB, 180)
put(H / ".cache" / "pip" / "http" / "x", 2.4 * GB, 1)
put(H / ".npm" / "_cacache" / "x", 3.6 * GB, 2)
put(H / ".cache" / "ms-playwright" / "chromium" / "x", 1.1 * GB, 70)
put(H / ".cargo" / "registry" / "x", 2.2 * GB, 20)
for proj, days in [("old-hackathon-app", 420), ("client-dashboard", 160), ("storefront", 1)]:
    (H / "code" / proj).mkdir(parents=True, exist_ok=True)
    (H / "code" / proj / "package.json").write_text("{}")
    os.utime(H / "code" / proj / "package.json", (NOW - days * 86400,) * 2)
    put(H / "code" / proj / "node_modules" / "x", 0.9 * GB, days)
env = dict(os.environ, HOME=str(H), USERPROFILE=str(H))
for v in ("HF_HOME", "XDG_CACHE_HOME", "OLLAMA_MODELS"):
    env.pop(v, None)
args = sys.argv[1:] or ["--projects", str(H / "code")]
sys.exit(subprocess.call(["modelhog", *args], env=env))
