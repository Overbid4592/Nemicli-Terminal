"""main.py: Der Startblock (`if __name__ == "__main__":`) läuft als Modul-Code und darf keine
Namen neu belegen, die das Modul selbst benutzt – sonst überschreibt er sie nur beim echten
Start (in Tests läuft er nicht)."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ziele(knoten) -> set[str]:
    namen = set()
    for n in ast.walk(knoten):
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for z in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                for t in ast.walk(z):
                    if isinstance(t, ast.Name):
                        namen.add(t.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            namen.update((a.asname or a.name).split(".")[0] for a in n.names)
    return namen


class Startblock(unittest.TestCase):
    def test_keine_namensgleichheit_mit_dem_modul(self):
        baum = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
        start = [n for n in baum.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)]
        self.assertEqual(len(start), 1)
        oben = set()
        for n in baum.body:
            if n is start[0]:
                continue
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                oben.add(n.name)
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                oben |= _ziele(n)
        doppelt = sorted((_ziele(start[0]) & oben) - {"sys", "os", "asyncio"})
        self.assertEqual(doppelt, [], f"Startblock überschreibt Modul-Namen: {doppelt}")


if __name__ == "__main__":
    unittest.main()
