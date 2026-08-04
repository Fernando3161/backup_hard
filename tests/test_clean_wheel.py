import subprocess
import sys
import zipfile
import venv
from pathlib import Path


def test_wheel_contains_package_and_license(tmp_path):
    root = Path(__file__).parents[1]
    wheel_dir = tmp_path / "wheel"
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                    "--wheel-dir", str(wheel_dir), str(root)], check=True, capture_output=True)
    wheel = next(wheel_dir.glob("backup_hard-0.8.0.dev0-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert "backup_hard/cli.py" in names
    assert any(name.endswith("dist-info/LICENSE") or name.endswith("dist-info/licenses/LICENSE") for name in names)
    assert not any(name.startswith("tests/") or "__pycache__" in name for name in names)
    environment = tmp_path / "clean-env"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True,
                   capture_output=True)
    completed = subprocess.run(
        [str(python), "-c", "import importlib.metadata as m; print(m.version('backup-hard'))"],
        check=True, capture_output=True, text=True,
    )
    assert completed.stdout.strip() == "0.8.0.dev0"
