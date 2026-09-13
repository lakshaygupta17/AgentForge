from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.tools.index import PythonSymbolIndex
from agentforge.tools.repository import RepositoryInspector, RepositoryToolError


class PythonSymbolIndexTests(TestCase):
    def test_finds_classes_functions_methods_and_parameters(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "service.py").write_text(
                "class CheckoutService:\n"
                "    async def checkout(self, cart, *, currency='USD'):\n"
                "        return cart\n\n"
                "def checkout_summary(items, *args, **kwargs):\n"
                "    return len(items)\n",
                encoding="utf-8",
            )
            index = PythonSymbolIndex(RepositoryInspector(root))

            all_matches = index.find("checkout")
            methods = index.find("checkout", kind="method")

        self.assertEqual(
            [match["qualified_name"] for match in all_matches],
            [
                "CheckoutService.checkout",
                "checkout_summary",
                "CheckoutService",
            ],
        )
        self.assertEqual(methods[0]["path"], "service.py")
        self.assertEqual(methods[0]["line"], 2)
        self.assertEqual(methods[0]["parameters"], ("self", "cart", "currency"))

    def test_skips_invalid_ignored_and_non_python_files(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "valid.py").write_text("def visible():\n    pass\n", encoding="utf-8")
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            (root / "notes.txt").write_text("def not_python(): pass", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "hidden.py").write_text(
                "def hidden():\n    pass\n", encoding="utf-8"
            )
            index = PythonSymbolIndex(RepositoryInspector(root))

            self.assertEqual(index.find("visible")[0]["name"], "visible")
            self.assertEqual(index.find("broken"), [])
            self.assertEqual(index.find("not_python"), [])
            self.assertEqual(index.find("hidden"), [])

    def test_enforces_symbol_and_result_limits(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "many.py").write_text(
                "def item_one():\n    pass\n\ndef item_two():\n    pass\n",
                encoding="utf-8",
            )
            inspector = RepositoryInspector(root)

            with self.assertRaisesRegex(RepositoryToolError, "symbol limit"):
                PythonSymbolIndex(inspector, maximum_symbols=1).find("item")
            self.assertEqual(
                len(PythonSymbolIndex(inspector, maximum_results=1).find("item")), 1
            )

    def test_refresh_rebuilds_the_index_after_a_change(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "module.py"
            source.write_text("def before():\n    pass\n", encoding="utf-8")
            index = PythonSymbolIndex(RepositoryInspector(root))
            self.assertEqual(len(index.find("before")), 1)

            source.write_text("def after():\n    pass\n", encoding="utf-8")
            self.assertEqual(index.find("after"), [])
            index.refresh()

            self.assertEqual(len(index.find("after")), 1)

    def test_rejects_invalid_kind_and_path_escape(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            index = PythonSymbolIndex(
                RepositoryInspector(Path(temporary_directory))
            )
            with self.assertRaisesRegex(RepositoryToolError, "kind"):
                index.find("anything", kind="variable")
            with self.assertRaisesRegex(RepositoryToolError, "escapes"):
                index.find("anything", path="../outside")
