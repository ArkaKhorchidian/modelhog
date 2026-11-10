"""Find the AI models and developer caches eating your disk. Everything listed can be downloaded again."""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

AI, CACHE, PROJECT = "AI models", "Dev caches", "Project junk"


@dataclass
class Item:
    group: str  # AI / CACHE / PROJECT
    source: str  # "Hugging Face", "Ollama", "pip cache"...
    name: str  # model name or folder
    path: Path
    size: int = 0
    last_used: float | None = None
    how_to_remove: str = ""  # a command to run instead of deleting files, when deleting files would be wrong
    note: str = ""
    shared: dict = field(default_factory=dict)  # Ollama layers: digest -> size (shared between models)


def home() -> Path:
    return Path.home()


def mac() -> bool:
    return sys.platform == "darwin"


def win() -> bool:
    return sys.platform == "win32"


def du(path: Path, seen: set | None = None) -> tuple[int, float]:
    """(bytes, newest mtime) of a file or folder. Symlinks aren't followed; hard links count once."""
    seen = seen if seen is not None else set()
    total, newest, newest_dir = 0, 0.0, 0.0
    stack = [path]
    while stack:
        p = stack.pop()
        try:
            st = p.lstat()
        except OSError:
            continue
        if os.path.islink(p):  # e.g. Hugging Face snapshots pointing at blobs: the blob's own time is what counts
            continue
        if p.is_dir():
            # folder times change for reasons that have nothing to do with use (and Windows is lax about them),
            # so they only count when there are no files at all
            newest_dir = max(newest_dir, st.st_mtime)
            try:
                stack.extend(Path(e.path) for e in os.scandir(p))
            except OSError:
                pass
            continue
        key = (st.st_dev, st.st_ino)
        if st.st_nlink > 1:
            if key in seen:
                continue
            seen.add(key)
        total += st.st_size
        newest = max(newest, st.st_mtime)
    return total, newest or newest_dir


def env_path(var: str, default: Path) -> Path:
    v = os.environ.get(var)
    return Path(v).expanduser() if v else default


def cache_home() -> Path:
    if mac():
        return home() / "Library" / "Caches"
    if win():
        return Path(os.environ.get("LOCALAPPDATA", home() / "AppData" / "Local"))
    return env_path("XDG_CACHE_HOME", home() / ".cache")


# ---------------------------------------------------------------------------
# AI models
# ---------------------------------------------------------------------------
def huggingface() -> list[Item]:
    hub = env_path("HF_HUB_CACHE", env_path("HF_HOME", home() / ".cache" / "huggingface") / "hub")
    out = []
    if not hub.is_dir():
        return out
    for d in sorted(hub.iterdir()):
        if not d.is_dir() or "--" not in d.name:
            continue
        kind, _, name = d.name.partition("--")
        name = name.replace("--", "/")
        size, newest = du(d)
        label = name if kind == "models" else f"{name} ({kind[:-1]})"
        out.append(Item(AI, "Hugging Face", label, d, size, newest))
    return out


def ollama() -> list[Item]:
    root = env_path("OLLAMA_MODELS", home() / ".ollama" / "models")
    manifests = root / "manifests"
    out = []
    if not manifests.is_dir():
        return out
    for mf in manifests.rglob("*"):
        if not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text())
        except (OSError, ValueError):
            continue
        rel = mf.relative_to(manifests).parts  # registry.ollama.ai/library/llama3/8b
        model = "/".join(rel[2:-1]) if rel[1] == "library" else "/".join(rel[1:-1])
        name = f"{model}:{rel[-1]}"
        layers = list(data.get("layers") or []) + ([data["config"]] if isinstance(data.get("config"), dict) else [])
        shared = {l.get("digest"): int(l.get("size") or 0) for l in layers if l.get("digest")}
        out.append(Item(AI, "Ollama", name, mf, sum(shared.values()), mf.stat().st_mtime,
                        how_to_remove=f"ollama rm {name}", shared=shared))
    return out


def gguf_dirs() -> list[Item]:
    """LM Studio, llama.cpp, GPT4All, Jan: folders of model files, one item per model folder or file."""
    places = [
        ("LM Studio", home() / ".lmstudio" / "models"),
        ("LM Studio", home() / ".cache" / "lm-studio" / "models"),
        ("llama.cpp", env_path("LLAMA_CACHE", cache_home() / "llama.cpp")),
        ("GPT4All", home() / "Library" / "Application Support" / "nomic.ai" / "GPT4All" if mac()
         else home() / ".local" / "share" / "nomic.ai" / "GPT4All"),
        ("Jan", home() / "jan" / "models"),
        ("Whisper", home() / ".cache" / "whisper"),
        ("PyTorch Hub", env_path("TORCH_HOME", home() / ".cache" / "torch") / "hub"),
        ("Keras", home() / ".keras" / "models"),
        ("CLIP", home() / ".cache" / "clip"),
    ]
    out = []
    for source, root in places:
        if not root.is_dir():
            continue
        entries = sorted(root.iterdir())
        # LM Studio nests publisher/model; flatten one level so each model is its own line
        if source == "LM Studio":
            entries = [m for p in entries if p.is_dir() for m in sorted(p.iterdir())]
        for e in entries:
            size, newest = du(e)
            if size >= 10 * 2**20:
                out.append(Item(AI, source, e.name, e, size, newest))
    return out


# ---------------------------------------------------------------------------
# Developer caches
# ---------------------------------------------------------------------------
def dev_caches() -> list[Item]:
    h, c = home(), cache_home()
    candidates = [
        ("pip", c / "pip" if not win() else c / "pip" / "Cache", "pip cache purge"),
        ("uv", env_path("UV_CACHE_DIR", (h / ".cache" / "uv") if not win() else c / "uv" / "cache"), "uv cache clean"),
        ("npm", h / ".npm" / "_cacache" if not win() else Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache",
         "npm cache clean --force"),
        ("Yarn", c / "Yarn" if mac() else h / ".cache" / "yarn", "yarn cache clean"),
        ("pnpm store", h / "Library" / "pnpm" / "store" if mac() else h / ".local" / "share" / "pnpm" / "store",
         "pnpm store prune"),
        ("Bun", h / ".bun" / "install" / "cache", "bun pm cache rm"),
        ("Cargo registry", h / ".cargo" / "registry", ""),
        ("Go modules", h / "go" / "pkg" / "mod", "go clean -modcache"),
        ("Gradle", h / ".gradle" / "caches", ""),
        ("Maven", h / ".m2" / "repository", ""),
        ("Homebrew", c / "Homebrew", "brew cleanup --prune=all"),
        ("Playwright browsers", c / "ms-playwright", ""),
        ("Puppeteer", h / ".cache" / "puppeteer", ""),
        ("Xcode DerivedData", h / "Library" / "Developer" / "Xcode" / "DerivedData", ""),
        ("Xcode archives", h / "Library" / "Developer" / "Xcode" / "Archives", ""),
        ("iOS simulators", h / "Library" / "Developer" / "CoreSimulator" / "Devices",
         "xcrun simctl delete unavailable"),
        ("conda packages", h / "miniconda3" / "pkgs", "conda clean --all"),
        ("conda packages", h / "anaconda3" / "pkgs", "conda clean --all"),
        ("Hugging Face datasets", env_path("HF_DATASETS_CACHE", h / ".cache" / "huggingface" / "datasets"), ""),
        ("pre-commit", h / ".cache" / "pre-commit", "pre-commit gc"),
        ("CocoaPods", c / "CocoaPods", "pod cache clean --all"),
    ]
    out, seen = [], set()
    for source, path, cmd in candidates:
        if not path or not path.is_dir() or path in seen:
            continue
        seen.add(path)
        size, newest = du(path)
        if size >= 50 * 2**20:
            # caches used by long-running tools should be cleared by the tool, not rm -rf
            keep = source in ("iOS simulators", "Go modules")
            out.append(Item(CACHE, source, str(path).replace(str(h), "~"), path, size, newest,
                            how_to_remove=cmd if keep else "", note=cmd))
    return out


def docker() -> list[Item]:
    """Docker's disk image is one big file; report it and let `docker system prune` do the cleaning."""
    paths = [home() / "Library" / "Containers" / "com.docker.docker" / "Data" / "vms" / "0" / "data" / "Docker.raw",
             home() / ".docker" / "desktop" / "vms" / "0" / "data" / "Docker.raw",
             home() / "Library" / "Group Containers" / "group.com.docker" / "Data" / "vms" / "0" / "data" / "Docker.raw"]
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            continue
        used = getattr(st, "st_blocks", 0) * 512 or st.st_size  # the image is sparse: count what's really used
        return [Item(CACHE, "Docker", "Docker Desktop disk image", p, used, st.st_mtime,
                     how_to_remove="docker system prune -a --volumes",
                     note="images, containers and volumes; prune keeps anything in use")]
    return []


# ---------------------------------------------------------------------------
# Project junk
# ---------------------------------------------------------------------------
JUNK = {"node_modules": "node_modules", ".venv": "virtualenv", "venv": "virtualenv", "target": "Rust/Java build",
        ".next": "Next.js build", ".turbo": "Turbo cache", ".gradle": "Gradle", "Pods": "CocoaPods",
        ".tox": "tox", ".nox": "nox", ".mypy_cache": "mypy cache", ".pytest_cache": "pytest cache",
        "__pycache__": "bytecode", ".parcel-cache": "Parcel cache", "dist": "build output", "build": "build output"}
STRONG = {"node_modules", ".venv", "venv", "target", ".next", ".turbo", "Pods", ".tox", ".nox", ".parcel-cache"}


def project_junk(roots: list[Path], max_depth: int = 6) -> list[Item]:
    out: list[Item] = []
    for root in roots:
        root = root.expanduser()

        def walk(d: Path, depth: int):
            if depth > max_depth:
                return
            try:
                entries = list(os.scandir(d))
            except OSError:
                return
            names = {e.name for e in entries}
            is_project = bool(names & {"package.json", "pyproject.toml", "Cargo.toml", "setup.py", "requirements.txt",
                                       "pom.xml", "build.gradle", "Podfile", "go.mod", ".git"})
            for e in entries:
                if not e.is_dir(follow_symlinks=False):
                    continue
                if e.name in STRONG or (is_project and e.name in JUNK and e.name not in ("dist", "build")):
                    p = Path(e.path)
                    size, newest = du(p)
                    if size >= 20 * 2**20:
                        proj_mtime = max((Path(x.path).stat().st_mtime for x in entries
                                          if x.is_file(follow_symlinks=False)), default=newest)
                        out.append(Item(PROJECT, JUNK[e.name], str(p).replace(str(home()), "~"), p, size,
                                        max(proj_mtime, 0) or newest))
                    continue
                if e.name.startswith(".") or e.name in ("Library", "Applications", "Pictures", "Music", "Movies"):
                    continue
                walk(Path(e.path), depth + 1)

        walk(root, 0)
    return out


def scan(projects: list[Path] | None = None) -> list[Item]:
    items = huggingface() + ollama() + gguf_dirs() + dev_caches() + docker()
    if projects:
        items += project_junk(projects)
    return sorted(items, key=lambda i: -i.size)


def total(items: list[Item]) -> int:
    """Bytes the items really take: Ollama models share layers, so each layer counts once."""
    n, layers = 0, {}
    for i in items:
        if i.shared:
            layers.update(i.shared)
        else:
            n += i.size
    return n + sum(layers.values())


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def ago(ts: float | None) -> str:
    if not ts:
        return "?"
    d = (time.time() - ts) / 86400
    if d < 1:
        return "today"
    if d < 45:
        return f"{d:.0f}d ago"
    if d < 365:
        return f"{d / 30.4:.0f}mo ago"
    return f"{d / 365:.1f}y ago"
