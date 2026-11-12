"""Statische Sicherheitsprüfung des Quelltexts: gefährliche Aufrufe, unverschlüsselte Adressen,
Hash-Verfahren, Regex-Bereiche, gespiegelte CORS-Herkunft und eingecheckte Geheimnisse."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_ORDNER = ("core", "engines", "tools", "ui", "ggufengine", "installer", "loramotor", "nemiview", "trtmotor",
               "einrichtungshelfer", "chrome-erweiterung")
AUSGENOMMEN = {"venv", "dist", "build", "__pycache__", "node_modules", "Models", "ModelGGUF"}


def _dateien(*endungen: str) -> list[Path]:
    raus = [p for p in ROOT.glob("*") if p.is_file() and p.suffix in endungen]
    for ordner in CODE_ORDNER:
        for p in (ROOT / ordner).rglob("*"):
            if p.is_file() and p.suffix in endungen and not AUSGENOMMEN & set(p.relative_to(ROOT).parts):
                raus.append(p)
    return sorted(raus)


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


_BAEUME: dict[Path, ast.AST] = {}


def _baum(p: Path) -> ast.AST:
    if p not in _BAEUME:
        _BAEUME[p] = ast.parse(p.read_bytes(), filename=str(p))
    return _BAEUME[p]


def _name(knoten: ast.AST) -> str:
    """Punktname eines Aufrufziels, z. B. 'subprocess.run' oder 'eval'."""
    if isinstance(knoten, ast.Name):
        return knoten.id
    if isinstance(knoten, ast.Attribute):
        return f"{_name(knoten.value) or '?'}.{knoten.attr}"     # dialog.exec() ist nicht exec()
    return ""


def _aufrufe(p: Path):
    for k in ast.walk(_baum(p)):
        if isinstance(k, ast.Call):
            yield k, _name(k.func)


def _wort(k: ast.Call, name: str):
    for w in k.keywords:
        if w.arg == name:
            return w.value
    return None


def _ist(knoten, wert) -> bool:
    return isinstance(knoten, ast.Constant) and knoten.value is wert


class GefaehrlicheAufrufe(unittest.TestCase):
    def test_kein_eval_und_nur_ein_bekanntes_exec(self):
        funde = []
        for p in _dateien(".py"):
            for k, n in _aufrufe(p):
                if n in ("eval", "builtins.eval"):
                    funde.append(f"{_rel(p)}:{k.lineno} eval")
                elif n in ("exec", "builtins.exec") and _rel(p) != "main.py":   # main.py lädt seine eigenen Teile
                    funde.append(f"{_rel(p)}:{k.lineno} exec")
        self.assertEqual([], funde)

    def test_kein_shell_true(self):
        funde = [f"{_rel(p)}:{k.lineno}" for p in _dateien(".py") for k, n in _aufrufe(p)
                 if _ist(_wort(k, "shell"), True)]
        self.assertEqual([], funde)

    def test_kein_pickle_yaml_marshal(self):
        verboten = {"pickle.load", "pickle.loads", "cPickle.load", "cPickle.loads", "marshal.load", "marshal.loads",
                    "yaml.load", "yaml.unsafe_load", "shelve.open", "dill.load", "dill.loads"}
        funde = [f"{_rel(p)}:{k.lineno} {n}" for p in _dateien(".py") for k, n in _aufrufe(p) if n in verboten]
        self.assertEqual([], funde)

    def test_torch_load_nur_mit_weights_only(self):
        funde = [f"{_rel(p)}:{k.lineno}" for p in _dateien(".py") for k, n in _aufrufe(p)
                 if n == "torch.load" and not _ist(_wort(k, "weights_only"), True)]
        self.assertEqual([], funde)

    def test_tls_pruefung_bleibt_an(self):
        funde = [f"{_rel(p)}:{k.lineno} {n}" for p in _dateien(".py") for k, n in _aufrufe(p)
                 if _ist(_wort(k, "verify"), False) or n in ("ssl._create_unverified_context",)]
        for p in _dateien(".py"):
            if "CERT_NONE" in p.read_text(encoding="utf-8", errors="replace"):
                funde.append(f"{_rel(p)} CERT_NONE")
        self.assertEqual([], funde)

    def test_md5_sha1_nur_ohne_sicherheitszweck(self):
        funde = []
        for p in _dateien(".py"):
            for k, n in _aufrufe(p):
                schwach = n in ("hashlib.md5", "hashlib.sha1") or (
                    n == "hashlib.new" and k.args and isinstance(k.args[0], ast.Constant)
                    and str(k.args[0].value).lower() in ("md5", "sha1"))
                if schwach and not _ist(_wort(k, "usedforsecurity"), False):
                    funde.append(f"{_rel(p)}:{k.lineno} {n}")
        self.assertEqual([], funde)

    def test_sql_nur_mit_platzhaltern(self):
        """execute() bekommt keine mit % oder .format() eingesetzten Werte."""
        funde = []
        for p in _dateien(".py"):
            for k, n in _aufrufe(p):
                if not n.endswith((".execute", ".executemany", ".executescript")) or not k.args:
                    continue
                a = k.args[0]
                if (isinstance(a, ast.BinOp) and isinstance(a.op, ast.Mod)) or (
                        isinstance(a, ast.Call) and _name(a.func).endswith(".format")):
                    funde.append(f"{_rel(p)}:{k.lineno}")
        self.assertEqual([], funde)


class Netz(unittest.TestCase):
    # http ist nur zum eigenen Rechner erlaubt. Ausnahmen mit Grund:
    ERLAUBT = {
        # Set-AuthenticodeSignature (Windows PowerShell) nimmt nur http-Zeitstempelserver; die RFC-3161-Antwort
        # ist selbst signiert. Läuft nur beim Bauen der exe.
        "http://timestamp.digicert.com",
    }
    LOKAL = re.compile(r"^http://(127\.\d+\.\d+\.\d+|localhost|\[::1\]|0\.0\.0\.0)([:/]|$)")
    # XML-Namensräume sind Kennungen, keine Abrufe
    NAMENSRAUM = re.compile(r"^http://(schemas\.openxmlformats\.org|schemas\.microsoft\.com|www\.w3\.org|purl\.org"
                            r"|ns\.adobe\.com|schemas\.xmlsoap\.org|www\.inkscape\.org|creativecommons\.org/ns"
                            r"|xmlns\.com)/")

    def test_http_nur_lokal(self):
        funde = []
        for p in _dateien(".py", ".js", ".json", ".html"):
            if p.name == "manifest.json":
                continue
            for i, zeile in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for m in re.finditer(r"http://[^\s\"'`<>)\]]*", zeile):
                    url = m.group(0)
                    if url == "http://" or "{" in url[:8 + 1 + 1] or self.LOKAL.match(url) \
                            or self.NAMENSRAUM.match(url) or url in self.ERLAUBT:
                        continue
                    if url.startswith("http://{") and ("HOST" in url or "host" in url):
                        continue                        # eigene Server, gebunden an 127.0.0.1 (eigener Test)
                    funde.append(f"{_rel(p)}:{i} {url}")
        self.assertEqual([], funde)

    def test_klickbare_links_nur_https(self):
        """Wo Antworttext zu <a href> wird (Chrome-Erweiterung, Web-Oberfläche), passt nur https."""
        funde = []
        for p in sorted((ROOT / "chrome-erweiterung").glob("*.js")):
            for i, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"https\?|http\\?:\\?/\\?/(?!127\.0\.0\.1)", zeile):
                    funde.append(f"{_rel(p)}:{i}")
        for p in _dateien(".py", ".js", ".html"):
            for i, zeile in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if "href" in zeile and re.search(r"https\?|\(http:", zeile):
                    funde.append(f"{_rel(p)}:{i}")
        self.assertEqual([], sorted(set(funde)))

    def test_eigene_server_lauschen_nur_lokal(self):
        """Kein Server bindet an 0.0.0.0 oder ::."""
        funde = []
        for p in _dateien(".py"):
            for k, n in _aufrufe(p):
                if n.split(".")[-1] in ("HTTPServer", "ThreadingHTTPServer", "TCPServer", "bind", "create_server") \
                        and k.args and isinstance(k.args[0], ast.Tuple) and k.args[0].elts \
                        and isinstance(k.args[0].elts[0], ast.Constant) and k.args[0].elts[0].value in ("0.0.0.0",
                                                                                                    "::", ""):
                    funde.append(f"{_rel(p)}:{k.lineno}")
        self.assertEqual([], funde)

    def test_cors_spiegelt_keine_fremde_herkunft(self):
        """Access-Control-Allow-Origin bekommt nie den Origin-Kopf der Anfrage unverändert zurück."""
        funde = []
        for p in _dateien(".py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"Access-Control-Allow-Origin\"\s*,\s*([^)\n]+)", text):
                wert = m.group(1).strip()
                if re.search(r"headers\.get\(|\borigin\b|\bherkunft\b", wert, re.I) and "ERWEITUNG_ORIGIN" not in wert:
                    funde.append(f"{_rel(p)}:{text[:m.start()].count(chr(10)) + 1} {wert[:60]}")
        self.assertEqual([], funde)


class RegexBereiche(unittest.TestCase):
    def test_keine_bereiche_mit_echten_sonderzeichen(self):
        """Zeichenbereiche über Nicht-ASCII stehen als Escape im Muster, nicht als echtes Zeichen."""
        funde = []
        for p in _dateien(".py"):
            for k, n in _aufrufe(p):
                if not n.startswith("re.") or not k.args or not isinstance(k.args[0], ast.Constant) \
                        or not isinstance(k.args[0].value, str):
                    continue
                for klasse in re.findall(r"\[\^?((?:\\.|[^\]\\])*)\]", k.args[0].value):
                    teile = re.findall(r"\\.|.", klasse, re.S)
                    if any(teile[i] == "-" and 0 < i < len(teile) - 1
                           and (ord(teile[i - 1][-1]) > 127 or ord(teile[i + 1][-1]) > 127)
                           for i in range(len(teile))):
                        funde.append(f"{_rel(p)}:{k.lineno}")
                        break
        for rel, name in (("tools/fremddaten.py", "_UNSICHTBAR"), ("einrichtungshelfer/daten_gross.py", "_EMOJI")):
            p = ROOT / rel
            if not p.exists():
                continue
            for k in ast.walk(_baum(p)):
                if isinstance(k, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in k.targets):
                    quelle = ast.get_source_segment(p.read_text(encoding="utf-8"), k) or ""
                    if re.search(r"[^\x00-\x7f]", quelle):
                        funde.append(f"{rel}:{k.lineno} {name}")
        self.assertEqual([], funde)


class Geheimnisse(unittest.TestCase):
    MUSTER = {
        "privater Schlüssel": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
        "OpenAI/Anthropic-Schlüssel": r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}",
        "GitHub-Token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}",
        "Hugging-Face-Token": r"\bhf_[A-Za-z0-9]{30,}",
        "AWS-Schlüssel": r"\bAKIA[0-9A-Z]{16}\b",
        "Google-Schlüssel": r"\bAIza[0-9A-Za-z_\-]{35}\b",
        "Slack-Token": r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
    }

    def test_keine_geheimnisse_im_repository(self):
        funde = []
        dateien = _dateien(".py", ".js", ".json", ".html", ".md", ".txt", ".bat", ".cmd", ".spec", ".toml", ".cfg",
                           ".ini", ".yml", ".yaml", ".css")
        dateien += [p for p in (ROOT / "docs").rglob("*") if p.is_file() and p.suffix in (".md", ".txt")]
        for p in dateien:
            text = p.read_text(encoding="utf-8", errors="replace")
            for art, muster in self.MUSTER.items():
                for m in re.finditer(muster, text):
                    funde.append(f"{_rel(p)}:{text[:m.start()].count(chr(10)) + 1} {art}")
        self.assertEqual([], funde)


if __name__ == "__main__":
    unittest.main()
