import json
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "examples" / "backup_workflow.ipynb"
ANDROID_EXAMPLE = ROOT / "examples" / "android_mock_workflow.py"


class ExampleNotebookTests(unittest.TestCase):
    def test_android_example_is_portable_and_synthetic(self) -> None:
        code = ANDROID_EXAMPLE.read_text(encoding="utf-8")
        self.assertNotRegex(code, r"[A-Za-z]:\\|/Users/|/home/|[A-Z0-9]{12,}")
        namespace = {"__name__": "example"}
        exec(compile(code, str(ANDROID_EXAMPLE), "exec"), namespace)
        decisions = namespace["decisions"]()
        self.assertIsNone(decisions[0][2])
        self.assertIn("package", decisions[-1][2])

    def test_notebook_is_portable_and_compiles(self) -> None:
        document = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        self.assertEqual(document["nbformat"], 4)
        code = "\n\n".join(
            "".join(cell["source"])
            for cell in document["cells"]
            if cell["cell_type"] == "code"
        )
        self.assertNotRegex(code, r"[A-Za-z]:\\|/Users/|/home/")
        compile(code, str(NOTEBOOK), "exec")

    @unittest.skipUnless(
        os.environ.get("BACKUP_HARD_EXECUTE_NOTEBOOK") == "1",
        "set BACKUP_HARD_EXECUTE_NOTEBOOK=1 to execute the example",
    )
    def test_notebook_executes(self) -> None:
        document = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        namespace = {"__name__": "__notebook__"}
        previous = Path.cwd()
        try:
            os.chdir(ROOT)
            for cell in document["cells"]:
                if cell["cell_type"] == "code":
                    exec(compile("".join(cell["source"]), str(NOTEBOOK), "exec"), namespace)
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
