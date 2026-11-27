"""Build the Windows CPU executable with pyinstaller, numpy, onnxruntime and tokenizers."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def collect_notices(folder):
    folder.mkdir()
    shutil.copyfile(ROOT / "LICENSE", folder / "Gravity-Memory.txt")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    shutil.copyfile(python_license, folder / "Python.txt")
    packages = []
    for dist in distributions():
        name = dist.metadata["Name"]
        packages.append(f"{name} {dist.version}")
        for file in dist.files or ():
            if not any(word in file.name.lower() for word in ("license", "licence", "notice", "copying")):
                continue
            if file.suffix.lower() not in ("", ".txt", ".rst", ".md", ".html"):
                continue
            original = Path(dist.locate_file(file))
            if original.is_file():
                dest = folder / name / Path(*[p for p in file.parts if p not in (".","..")])
                dest.parent.mkdir(exist_ok=True, parents=True)
                shutil.copyfile(original, dest)
    (folder / "environment.txt").write_text("\n".join(sorted(packages))+"\n", encoding="utf-8")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as stage:
        notices = Path(stage) / "notices"
        collect_notices(notices)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--noconfirm",
            "--distpath", str(ROOT / "dist"), "--workpath", stage, "--specpath", stage,
            "--paths", str(ROOT), "--collect-data", "gravity_memory", "--collect-binaries", "onnxruntime",
            "--copy-metadata", "tokenizers", "--add-data", f"{notices}:notices",
            "--icon", str(ROOT / "gravity_memory" / "web" / "icon.ico"),
            "--name", "GravityMemory", str(ROOT / "scripts" / "desktop.py")
        ], cwd=ROOT, check=True)
