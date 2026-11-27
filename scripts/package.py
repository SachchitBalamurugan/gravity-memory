"""Build a portable Python application and a source archive using the standard library."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist"


def build():
    OUT.mkdir(exist_ok=True)
    runtime = sorted((ROOT / "recall_ledger").glob("*.py"))
    with ZipFile(OUT / "recall-ledger.pyz", "w", compression=ZIP_DEFLATED) as app:
        app.writestr("__main__.py", "from recall_ledger.__main__ import main\nraise SystemExit(main())\n")
        for path in runtime:
            app.write(path, path.relative_to(ROOT))
    files = [ROOT / item for item in (".gitignore", "README.md", "RESEARCH.md", "pyproject.toml")]
    for folder in ("recall_ledger", "evals", "scripts", "tests"):
        files.extend(sorted(p for p in (ROOT / folder).glob("*") if p.suffix in (".py", ".json")))
    with ZipFile(OUT / "recall-ledger-source.zip", "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, Path("recall-ledger") / path.relative_to(ROOT))
    print(OUT / "recall-ledger.pyz")
    print(OUT / "recall-ledger-source.zip")


if __name__ == "__main__":
    build()
