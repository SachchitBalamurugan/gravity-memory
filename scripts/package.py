"""Build a Python zipapp with its web interface and a source distribution."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist"


def files_under(directory):
    return sorted(p for p in (ROOT / directory).rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def build():
    OUT.mkdir(exist_ok=True)
    runtime = files_under("gravity_memory") + files_under("recall_ledger")
    with ZipFile(OUT / "gravity-memory.pyz", "w", compression=ZIP_DEFLATED) as app:
        app.writestr("__main__.py", (ROOT / "scripts" / "desktop.py").read_bytes())
        for path in runtime:
            app.write(path, path.relative_to(ROOT))
    files = [ROOT / name for name in (".gitignore", "README.md", "RESEARCH.md", "FIELDNOTES.md", "pyproject.toml",
                                       ".dockerignore", "Dockerfile", "compose.yaml", "Caddyfile")]
    for folder in ("evals", "scripts", "tests", "docs", "sdk", "examples"):
        files.extend(files_under(folder))
    with ZipFile(OUT / "gravity-memory-source.zip", "w", compression=ZIP_DEFLATED) as archive:
        for path in files + runtime:
            archive.write(path, Path("gravity-memory") / path.relative_to(ROOT))
    print(OUT / "gravity-memory.pyz")
    print(OUT / "gravity-memory-source.zip")


if __name__ == "__main__":
    build()
