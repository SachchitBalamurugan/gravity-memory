"""Local model catalog, verified downloads and portable weight bundles."""
import hashlib
from importlib import resources, util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

CATALOG = {row["id"]: row for row in json.loads(resources.files(__package__).joinpath("catalog.json").read_text("utf-8"))}
RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b12807/llama-b12807-bin-win-cpu-x64.zip"
RUNTIME_SHA = "12454972ffe890c9adc77ad2d0971c8f8d8802c73cae32e0c83b41c040805f61"
RUNTIME_BYTES = 25324623
RUNTIME_FILE = "llama-b12807-win-x64.zip"
RUNTIME_MEMBER = "runtime/" + RUNTIME_FILE


def cache():
    return Path(os.environ.get("GRAVITY_MODEL_DIR", Path.home() / ".cache" / "gravity-memory" / "models"))


def entry(model):
    if not isinstance(model, str) or model not in CATALOG:
        raise ValueError("unknown catalog model")
    return CATALOG[model]


def asset(model, filename):
    return cache() / model / entry(model)["revision"] / filename


def installed(model):
    for filename, size, _ in entry(model)["files"]:
        path = asset(model, filename)
        if not path.is_file() or path.stat().st_size != size:
            return False
    return True


def native_server():
    override = os.environ.get("GRAVITY_LLAMA_SERVER") or shutil.which("llama-server")
    if override and Path(override).is_file():
        return Path(override)
    return next((cache() / "runtime" / RUNTIME_SHA).glob("**/llama-server.exe"), None)


def preferences():
    path = cache() / "routing.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {"preferred": "qwen-small", "fallback": True}


def configure(preferred, fallback=True):
    if entry(preferred)["task"] != "review" or type(fallback) is not bool:
        raise ValueError("choose a review model and a boolean fallback policy")
    values = {"preferred": preferred, "fallback": fallback}
    cache().mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache(), delete=False) as stream:
        json.dump(values, stream)
    os.replace(stream.name, cache() / "routing.json")
    return values


def inventory():
    onnx = all(util.find_spec(m) is not None for m in ("numpy", "onnxruntime", "tokenizers"))
    return {"routing": preferences(), "windows_runtime_cached": (cache() / RUNTIME_MEMBER).is_file(), "models": [
        {k: v for k, v in m.items() if k != "files"} | {"bytes": sum(f[1] for f in m["files"]),
         "installed": installed(m["id"]), "runtime_ready": bool(native_server()) if m["task"] == "review" else onnx}
        for m in CATALOG.values()]}


def verify(path, size, sha):
    if path.stat().st_size != size:
        raise ValueError(f"incomplete model asset {path.name}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != sha:
        raise ValueError(f"model asset checksum mismatch for {path.name}")


def fetch(urls, target, size, sha):
    if target.exists():
        try:
            verify(target, size, sha)
            return
        except ValueError:
            pass  # Replace the damaged file only after a verified download succeeds.
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".part")
    os.close(fd)
    temp = Path(temporary)
    try:
        for url in urls:
            try:
                with urlopen(Request(url, headers={"User-Agent": "GravityMemory/0.2"}), timeout=45) as src, temp.open("wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                verify(temp, size, sha)
                os.replace(temp, target)
                return
            except (OSError, ValueError) as error:
                last = error
        # Windows system TLS/proxies can differ from the bundled Python CA stack.
        curl = shutil.which("curl.exe" if sys.platform == "win32" else "curl")
        if curl:
            for url in urls:
                proc = subprocess.run([curl, "--fail", "--silent", "--show-error", "--location", "--proto", "=https",
                                       "--proto-redir", "=https", "--connect-timeout", "12", "--max-time", "900",
                                       "--output", str(temp), url], capture_output=True)
                if proc.returncode == 0:
                    verify(temp, size, sha)
                    os.replace(temp, target)
                    return
        raise ValueError(f"download of {target.name} failed: {last}")
    finally:
        temp.unlink(missing_ok=True)


def ensure_runtime():
    if native_server():
        return
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("install llama-server on PATH or set GRAVITY_LLAMA_SERVER")
    root = cache() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    archive = root / RUNTIME_FILE
    fetch([RUNTIME_URL], archive, RUNTIME_BYTES, RUNTIME_SHA)
    with tempfile.TemporaryDirectory(dir=root) as tmp, ZipFile(archive) as z:
        stage = Path(tmp) / "bin"
        for member in z.infolist():
            name = Path(member.filename).name
            if not member.is_dir() and (name.endswith(".dll") or name == "llama-server.exe"):
                stage.mkdir(exist_ok=True)
                with z.open(member) as src, (stage / name).open("wb") as out:
                    shutil.copyfileobj(src, out)
        if not (stage / "llama-server.exe").exists():
            raise ValueError("runtime archive missing llama-server")
        try:
            stage.rename(root / RUNTIME_SHA)
        except FileExistsError:
            pass


def install(model, *, runtime=True):
    m = entry(model)
    for filename, size, sha in m["files"]:
        base = "https://huggingface.co/" + m["repo"] + "/resolve/"
        urls = [base + rev + "/" + filename + suffix for rev in (m["revision"], "main")
                for suffix in ("?download=true", "")]
        fetch(urls, asset(model, filename), size, sha)
    if runtime and m["task"] == "review":
        ensure_runtime()
    return {"installed": model, "revision": m["revision"]}


def pack(model, destination, *, with_runtime=False):
    m = entry(model)
    for filename, size, sha in m["files"]:
        verify(asset(model, filename), size, sha)
    manifest = {"format": 2, "model": model, "revision": m["revision"]}
    if with_runtime:
        if m["task"] != "review":
            raise ValueError("the Windows review runtime applies to GGUF models")
        verify(cache() / RUNTIME_MEMBER, RUNTIME_BYTES, RUNTIME_SHA)
        manifest["runtime_sha256"] = RUNTIME_SHA
    with open(destination, "xb") as stream:
        try:
            with ZipFile(stream, "w", allowZip64=True) as bundle:
                bundle.writestr("manifest.json", json.dumps(manifest))
                bundle.writestr("LICENSE.txt", resources.files(__package__).joinpath("licenses", "Apache-2.0.txt").read_bytes())
                bundle.writestr("MODEL.txt", f"{m['repo']}\nhttps://huggingface.co/{m['repo']}/tree/{m['revision']}\nLicense: {m['license']}\n")
                if with_runtime:
                    bundle.write(cache() / RUNTIME_MEMBER, RUNTIME_MEMBER)
                for filename, _, _ in m["files"]:
                    bundle.write(asset(model, filename), filename)
        except BaseException:
            stream.close()
            Path(destination).unlink(missing_ok=True)
            raise
    return {"model": model, "bundle": str(destination)}


def unpack(bundle_path):
    try:
        return _unpack(bundle_path)
    except (BadZipFile, KeyError, TypeError, UnicodeError, NotImplementedError) as exc:
        raise ValueError("invalid model pack") from exc


def _unpack(bundle_path):
    with ZipFile(bundle_path) as z:
        if z.getinfo("manifest.json").file_size > 2048:
            raise ValueError("invalid model pack manifest")
        manifest = json.loads(z.read("manifest.json"))
        model = manifest["model"]
        m = entry(model)
        runtime = manifest.get("runtime_sha256")
        version = manifest.get("format")
        if type(version) is not int or version not in (1, 2):
            raise ValueError("unsupported model pack version")
        canonical = {"format": version, "model": model, "revision": m["revision"]}
        files = list(m["files"])
        if runtime is not None:
            if runtime != RUNTIME_SHA or m["task"] != "review":
                raise ValueError("unsupported runtime in model pack")
            canonical["runtime_sha256"] = runtime
            files.append((RUNTIME_MEMBER, RUNTIME_BYTES, RUNTIME_SHA))
        if manifest != canonical:
            raise ValueError("unsupported model revision")
        expected = ["manifest.json"] + [f[0] for f in files]
        if version == 2:
            expected += ["LICENSE.txt", "MODEL.txt"]
            if z.getinfo("LICENSE.txt").file_size > 15000 or z.getinfo("MODEL.txt").file_size > 2048:
                raise ValueError("invalid model attribution")
        if sorted(z.namelist()) != sorted(expected):
            raise ValueError("unexpected contents of model pack")
        cache().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache()) as tmp:
            stage = Path(tmp)
            for filename, size, sha in files:
                if z.getinfo(filename).file_size != size:
                    raise ValueError("incorrect size in model pack")
                path = stage / filename
                path.parent.mkdir(exist_ok=True, parents=True)
                with z.open(filename) as data, path.open("wb") as out:
                    shutil.copyfileobj(data, out)
                verify(path, size, sha)
            for filename, _, _ in files:
                path = cache() / RUNTIME_MEMBER if filename == RUNTIME_MEMBER else asset(model, filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / filename, path)
    if runtime and sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"):
        ensure_runtime()
    return {"installed": model, "revision": m["revision"], "runtime_included": bool(runtime)}
