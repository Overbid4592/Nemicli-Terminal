"""Ein `import x` mitten in einer Funktion macht `x` in der GANZEN Funktion lokal – wird `x` vorher benutzt,
bricht sie mit UnboundLocalError ab. Prüft alle Teile von main.py und die Programm-Ordner."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATEIEN = sorted(ROOT.glob("main*.py")) + [p for d in ("core", "tools", "engines", "ui")
                                          for p in sorted((ROOT / d).rglob("*.py"))]


def _funde(baum) -> list[str]:
    funde = []
    for f in ast.walk(baum):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        eigene = [k for k in ast.walk(f)
                  if not (isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and k is not f)]
        importiert = {}
        for k in eigene:
            if isinstance(k, (ast.Import, ast.ImportFrom)):
                for a in k.names:
                    name = (a.asname or a.name).split(".")[0]
                    importiert[name] = min(importiert.get(name, k.lineno), k.lineno)
        for k in eigene:
            if (isinstance(k, ast.Name) and isinstance(k.ctx, ast.Load) and k.id in importiert
                    and k.lineno < importiert[k.id]):
                funde.append(f"{f.name}: {k.id} in Zeile {k.lineno}, importiert erst in Zeile {importiert[k.id]}")
    return funde


class LokaleImports(unittest.TestCase):
    def test_kein_name_vor_seinem_lokalen_import(self):
        alle = []
        for p in DATEIEN:
            try:
                baum = ast.parse(p.read_text(encoding="utf-8-sig"))
            except SyntaxError:
                continue
            alle += [f"{p.relative_to(ROOT)} · {f}" for f in _funde(baum)]
        self.assertEqual(alle, [])

    def test_erkennt_den_fehler(self):
        baum = ast.parse("def main():\n    x = chatstore.next_id()\n    import chatstore\n")
        self.assertEqual(len(_funde(baum)), 1)


if __name__ == "__main__":
    unittest.main()
