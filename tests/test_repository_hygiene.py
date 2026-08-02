import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneTests(unittest.TestCase):
    def test_maintained_text_has_no_trailing_whitespace(self) -> None:
        paths = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "pyproject.toml"]
        paths.extend((ROOT / "src").rglob("*.py"))
        paths.extend((ROOT / "tests").rglob("*.py"))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                lines = path.read_text(encoding="utf-8").splitlines()
                self.assertFalse([number for number, line in enumerate(lines, 1) if line != line.rstrip()])

    def test_readme_local_links_exist(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" not in target:
                with self.subTest(target=target):
                    self.assertTrue((ROOT / target.split("#", 1)[0]).exists())


if __name__ == "__main__":
    unittest.main()
