"""main_befehle.py – Downloads, Update, Eingabezeile und Slash-Befehle (erster Teil).

Teil von main.py: läuft im selben Namensraum wie main.py (main._teil_laden) – eine Datei,
nur auf vier verteilt. Namen aus den anderen Teilen sind hier also da, ohne Import."""
# ruff: noqa: F821


async def hf_downloader() -> str | None:
    """Modell von Hugging Face holen: Größe → MoE → Modell → Quantisierung → Sehen →
    Steckbrief → Download mit SHA-256-Prüfung. Gibt die gguf-Referenz zurück, wenn das
    Modell gleich verwendet werden soll."""
    import extlibs
    import gguflokal
    import hfladen as H
    abbruch = ("__cancel__", "↩  Abbrechen")
    import systemprofil as SP
    with ui.thinking("schaue mir deinen PC an"):
        profil = await asyncio.to_thread(SP.erkennen)
        motor = await asyncio.to_thread(lambda: bool(extlibs.enable().get("aktiv")))
    vram = profil.nvidia.vram_mb if profil.nvidia else 0      # AMD/Intel: der Motor rechnet auf der CPU
    ram = profil.ram_mb
    ui.hf_willkommen(profil, H.gb(H.frei_auf_platte(gguflokal.ORDNER)), motor)

    hinweise = {"4-8": "klein & flott", "8-15": "ausgewogen", "15-35": "stark", "35+": "sehr groß"}
    opts = []
    for key, (von, _, text) in H.GROESSEN.items():
        ab = int(von * 1e9 * 4.9 / 8)
        opts.append((key, f"{H.passt(ab, vram, ram)[0]}  {text}  ·  {hinweise[key]}  ·  ab ≈ {H.gb(ab)}"))
    legende = ("✅ passt in die Grafikkarte · ⚠ knapp · 🐢 teils im RAM · ❌ zu groß" if vram
               else "🐢 läuft auf der CPU · ❌ zu groß für den Arbeitsspeicher")
    groesse = await ask_confirm(f"Wie groß soll das Modell sein?  ({legende})", opts + [abbruch])
    if groesse == "__cancel__":
        return None
    moe = await ask_confirm("Mixture of Experts (MoE)?", [
        ("egal", "🔀  Egal – alle zeigen"),
        ("ja", "🧩  Nur MoE – viele Experten, je Wort rechnet nur ein Teil: schnell trotz Größe"),
        ("nein", "🧱  Nur klassisch – alles rechnet immer mit"),
        abbruch])
    if moe == "__cancel__":
        return None
    with ui.thinking("frage Hugging Face nach passenden Modellen"):
        try:
            liste = await asyncio.to_thread(H.suchen, groesse, {"ja": True, "nein": False}.get(moe))
        except Exception as e:
            ui.error(f"{e} – ist das Internet da?")
            return None
    if not liste:
        ui.warn("Dazu gibt es gerade nichts Passendes. Probier eine andere Größe oder „Egal“ bei MoE.")
        return None
    vorhanden = set(gguflokal.modelle())
    opts = []
    for m in liste:
        da = "  ·  ✔ schon da" if m.name in vorhanden else ""
        sieht = "  ·  👁" if m.sieht else ""
        opts.append((m.repo, f"{H.passt(m.q4_schaetzung, vram, ram)[0]}  {m.name}  ·  {m.groesse_text}  ·  "
                             f"≈ {H.gb(m.q4_schaetzung)}{sieht}{da}"))
    repo = await ask_confirm(f"{len(liste)} Modelle, meistgeladene zuerst – welches?", opts + [abbruch])
    if repo == "__cancel__":
        return None
    modell = next(m for m in liste if m.repo == repo)

    with ui.thinking(f"schaue in {modell.name}"):
        try:
            sprach, bild = await asyncio.to_thread(H.dateien, repo)
        except Exception as e:
            ui.error(f"Dateiliste nicht abrufbar: {e}")
            return None
    tipp = H.empfohlen(sprach)
    if tipp is None:
        ui.warn(f"{modell.name} hat keine einzelne GGUF-Datei mit Prüfsumme – das lässt sich nicht sicher laden.")
        return None
    opts = [(tipp.pfad, f"⭐ {tipp.quant}  ·  {H.gb(tipp.groesse)}  ·  {H.passt(tipp.groesse, vram, ram)[0]}  ·  empfohlen")]
    opts += [(d.pfad, f"   {d.quant}  ·  {H.gb(d.groesse)}  ·  {H.passt(d.groesse, vram, ram)[0]}")
             for d in sprach if d is not tipp]
    wahl = await ask_confirm("Welche Quantisierung?  (weniger Bit: kleiner und schneller · mehr Bit: genauer)",
                             opts + [abbruch])
    if wahl == "__cancel__":
        return None
    datei = next(d for d in sprach if d.pfad == wahl)
    mm = H.mmproj_wahl(bild)
    if mm is not None:
        sehen = await ask_confirm("👁 Dieses Modell kann Bilder ansehen. Die Sehen-Datei dazu laden?", [
            ("ja", f"👁  Ja, mit Sehen  ·  +{H.gb(mm.groesse)}"), ("nein", "Nein, nur Text")])
        if sehen != "ja":
            mm = None

    ziel = gguflokal.ORDNER / modell.name
    andere = [p for p in ziel.glob("*.gguf") if not gguflokal._ist_beiwerk(p) and p.name != datei.name]
    if andere:                                 # andere Quantisierung schon da: eigener Ordner
        ziel = gguflokal.ORDNER / f"{modell.name}-{datei.quant}"
    noetig = datei.groesse + (mm.groesse if mm else 0)
    frei = H.frei_auf_platte(ziel)
    ui.hf_modell(modell, datei, mm, H.passt(datei.groesse, vram, ram), H.gb(frei), str(ziel))
    if frei < noetig + 2**30:
        ui.error(f"Nicht genug Platz: gebraucht {H.gb(noetig)}, frei {H.gb(frei)}.")
        return None
    los = await ask_confirm(f"{H.gb(noetig)} herunterladen?", [("ja", "⬇  Ja, herunterladen"), abbruch])
    if los != "ja":
        return None

    teile = [datei] + ([mm] if mm else [])
    for nr, d in enumerate(teile, 1):
        try:
            await _hf_datei_laden(H, repo, d, ziel, nr, len(teile))
        except H.Abbruch:
            ui.info("⏸ Angehalten. Starte den Download einfach nochmal – er macht an derselben Stelle weiter.")
            return None
        except H.PruefFehler as e:
            ui.error(f"🔐 {e}")
            return None
        except Exception as e:
            ui.error(f"Download fehlgeschlagen: {e} – nochmal starten setzt fort.")
            return None
    ui.success(f"✅ {ziel.name} ist da. SHA-256 geprüft: stimmt mit Hugging Face überein.")
    if not motor:
        ui.info("Zum Verwenden fehlt noch torch – /einrichten installiert es, danach /model → Lokal.")
        return None
    jetzt = await ask_confirm("Gleich verwenden?", [("ja", f"🧠  Ja, {ziel.name} laden"),
                                                    ("nein", "Später  ·  /model → Lokal")])
    return M.make_ref("gguf", ziel.name) if jetzt == "ja" else None


def _cloud_modell(ctx) -> bool:
    """Läuft gerade ein Cloud-Modell (mit Key) – kein lokales?"""
    anbieter = M.split_ref(ctx.model or "")[0]
    prov = P.get(anbieter) if anbieter else None
    return ctx.backend is not None and prov is not None and not prov.keyless and anbieter not in ("gguf", "ollama")


async def update_befehl(ctx) -> str | None:
    """/update: Programm per Git (nur Quelltext mit Remote), dann das venv prüfen und auf Stand
    bringen – Nötiges (Lücken, Schadpakete, fehlt, Version passt nicht, torch-Bau) und erlaubte
    Updates. Gibt eine Nachricht an die KI zurück, wenn sie die Lücken erklären soll."""
    import venvpflege as V
    grund = updater.why_not()
    if grund is None:
        try:
            res = await _run_with_status("⬇ Programm", updater.run)
        except Exception as e:
            res = {"ok": False, "output": str(e)}
        if not res["ok"]:
            ui.warn("⬇ Programm-Update nicht durchgeführt.")
            ui.text_panel("Git sagt", res["output"] or "(keine Ausgabe)")
        elif res["changed"]:
            ui.success(f"⬇ Programm: {res['before']} → {res['after']}  ({len(res['files'])} Datei(en)) – "
                       "Neustart nötig, damit der neue Code läuft.")
        else:
            ui.info(f"⬇ Programm ist aktuell ({res['before']}).")
    else:
        ui.info("⬇ Programm: " + ("exe – neue Versionen kommen als neuer Ordner." if getattr(sys, "frozen", False)
                                 else "kein Git-Remote, übersprungen."))

    if V.site_packages() is None:
        import uvsetup
        if uvsetup.VENV_DIR.exists() and not uvsetup.venv_passt():
            ui.warn(f"📦 Das venv ist für Python {uvsetup.venv_version()}, NemiCLI läuft mit {uvsetup.PYVER} – "
                    "/update kann es nicht umbauen. /einrichten legt es neu an (das alte bleibt umbenannt liegen).")
        else:
            ui.warn("📦 Noch kein venv neben NemiCLI – /einrichten legt es an.")
        return None
    with ui.thinking("prüfe das venv: Pakete, Versionen, torch, bekannte Sicherheitslücken"):
        liste = await asyncio.to_thread(V.pruefen)
    if V.STAND["luecken"]:
        ui.warn(f"🛡 {V.STAND['luecken']} – Lücken diesmal nicht geprüft.")
    if not liste:
        ui.success("📦 venv ist aktuell, keine bekannten Lücken – nichts zu tun.")
        return None
    ui.update_tabelle(liste)
    machbar = [e for e in liste if e.machbar]
    noetig = [e for e in machbar if e.noetig]
    updates = [e for e in machbar if not e.noetig]
    mit_luecken = [e for e in liste if e.luecken]
    opts = []
    if noetig and updates:
        opts += [("alles", f"🔧  Nötiges reparieren + {len(updates)} Update(s)"),
                 ("noetig", f"🔧  Nur Nötiges ({len(noetig)})")]
    elif noetig:
        opts.append(("noetig", f"🔧  Reparieren ({len(noetig)})"))
    elif updates:
        opts.append(("alles", f"⬆  {len(updates)} Update(s) installieren"))
    erklaeren = bool(mit_luecken) and _cloud_modell(ctx)
    offen = [e for e in liste if not e.machbar]
    if not machbar and not erklaeren:
        ui.info("🛡 Offen (nicht automatisch behebbar): "
                + ", ".join(f"{e.name} – {e.hinweis or 'selbst im venv-Fenster (/venv)'}" for e in offen))
        return None
    if erklaeren:
        opts.append(("ki", "🤖  Erst erklären lassen: wie gefährlich sind die Lücken?"))
    opts.append(("__cancel__", "↩  Nicht jetzt"))
    wahl = await ask_confirm("Was soll ich tun?", opts)
    if wahl == "ki":
        return _luecken_frage(mit_luecken)
    if wahl == "__cancel__":
        return None
    auswahl = noetig if wahl == "noetig" else machbar
    try:
        res = await _run_with_status("📦 venv aktualisieren", lambda on_status: V.einspielen(auswahl, on_status))
    except Exception as e:
        ui.error(f"📦 Update fehlgeschlagen: {e}")
        return None
    if res["jetzt"]:
        ui.success(f"📦 Aktualisiert und geprüft (lädt): {', '.join(res['jetzt'])}. Ein Neustart nimmt die neuen Fassungen.")
    if res["entfernt"]:
        ui.success(f"🚫 Schadpaket entfernt: {', '.join(res['entfernt'])}.")
    if res["aufgeraeumt"]:
        ui.success(f"🧹 Nicht mehr gebraucht, entfernt: {', '.join(res['aufgeraeumt'])}.")
    for name, fehler in res["zurueck"]:
        ui.warn(f"↩ {name} lud nach dem Update nicht mehr – alte Version zurückgeholt: {fehler}")
    if res["spaeter"]:
        ui.info(f"⏳ Gerade in Benutzung, kommt beim nächsten Start: {', '.join(res['spaeter'])}.")
    if res["neustart"]:
        ui.warn(f"⚙ {', '.join(res['neustart'])} ist geladen – NemiCLI neu starten und gleich /update tippen.")
    for name, grund in res["abgelehnt"]:
        ui.warn(f"⏭ {name} übersprungen, nichts verändert: {grund}")
    if offen:
        ui.info("🛡 Offen (nicht automatisch behebbar): "
                + ", ".join(f"{e.name} – {e.hinweis or 'selbst im venv-Fenster (/venv)'}" for e in offen))
    return None


def _luecken_frage(eintraege) -> str:
    """Nachricht an die KI: Funde als Daten, Bitte um eine verständliche Einordnung."""
    import fremddaten
    zeilen = []
    for e in eintraege:
        for l in e.luecken:
            zeilen.append(f"{e.name} {e.installiert}: {l.kennung} · Schwere {l.schwere} · "
                          f"behoben in {l.behoben or '–'} · {l.text}")
        if e.hinweis:
            zeilen.append(f"{e.name}: {e.hinweis}")
    return ("/update hat in meinem NemiCLI-venv diese bekannten Sicherheitslücken gefunden (Daten aus OSV.dev). "
            "Erklär mir kurz und verständlich, wie gefährlich sie für meine Nutzung sind und ob ich sofort "
            "handeln sollte. Nichts installieren oder ändern – nur einordnen.\n\n"
            + fremddaten.rahmen("\n".join(zeilen), "ausgabe", "OSV.dev"))


async def setup_ollama() -> None:
    """Ollama-Modell laden: erst GRÖSSE wählen, dann ein Modell dieser Größe aus
    der LIVE-Liste von ollama.com (immer aktuell, nichts vorgegeben). Bei nicht
    installiertem/laufendem Ollama: Hinweis."""
    if not S.ollama_installed():
        ui.warn("🦙 " + S.ollama_install_hint())
        return
    if not S.ollama_running():
        ui.warn("🦙 Ollama ist installiert, läuft aber nicht. Starte die Ollama-App "
                "und versuch es nochmal.")
        return

    with ui.thinking("hole die aktuelle Ollama-Modell-Liste"):
        catalog = await asyncio.to_thread(S.ollama_catalog)
    with ui.thinking("erkenne deine Grafikkarte"):
        gpu = await asyncio.to_thread(S.gpu_info)
    ui.setup_panel(gpu, S.hardware_hint(gpu),
                   links=[("🦙 Ollama-Bibliothek:", S.OLLAMA_LIBRARY_URL)])

    # Netz weg / Liste leer -> wenigstens manuell eingeben lassen.
    if not catalog:
        ui.warn(f"Konnte die Live-Liste nicht laden. Stöbere selbst: {S.OLLAMA_LIBRARY_URL}")
        name = (await ask_text("Modellname zum Laden (Format name:größe, leer = abbrechen):")).strip()
        if name:
            await _ollama_pull_and_done(name)
        return

    # --- Stufe A: Größe wählen (ab 4B; alles darunter ist zu schwach für Logik) ---
    sizes = S.ollama_sizes(catalog, lo=4.0, hi=14.0)        # 4B … 14B
    big = S.ollama_models_for(catalog, min_b=14.0, limit=99)    # > 14B
    opts: list[tuple[str, str]] = []
    opts += [(s, f"{s.upper()}  ·  {n} Modell(e)") for s, n in sizes]
    if big:
        opts.append(("__big__", f"groß  > 14B  ·  {len(big)} Modell(e)"))
    opts.append(("__manual__", "Name selbst eingeben"))
    opts.append(("__cancel__", "Abbrechen"))
    pick = await ask_confirm("Welche Größe?  (Ollama lädt standardmäßig Q4_K_M)", opts)
    if pick == "__cancel__":
        return
    if pick == "__manual__":
        name = (await ask_text("Modellname (Format name:größe, leer = abbrechen):")).strip()
        if name:
            await _ollama_pull_and_done(name)
        return

    # --- Stufe B: konkretes Modell dieser Größe wählen ---
    if pick == "__big__":
        models = S.ollama_models_for(catalog, min_b=14.0, limit=18)
        titel = "Modelle größer als 14B  ·  Q4_K_M"
    else:
        models = S.ollama_models_for(catalog, size=pick, limit=18)
        titel = f"{pick.upper()}-Modelle  ·  Q4_K_M"
    mopts = [(m, m) for m in models]
    mopts.append(("__cancel__", "Abbrechen / zurück"))
    chosen = await ask_confirm(titel, mopts)
    if chosen == "__cancel__":
        return
    await _ollama_pull_and_done(chosen)


# Sitzungs-Zähler für den Übungsmodus (wie viele Runden bisher gelaufen sind)
PRACTICE_STATE = {"count": 0}


def _practice_emit(kind: str, payload) -> None:
    """Übersetzt die Schritt-Ereignisse einer Übungsrunde in hübsche Ausgaben."""
    if kind == "start":
        ui.console.print(ui.practice_header(payload))
    elif kind == "code":
        path, code = payload
        ui.console.print(ui.practice_code(path.name, code))
    elif kind == "run":
        ui.info(f"▶ {payload}")
    elif kind == "ok":
        ui.console.print(ui.action_result(payload, ok=True))
    elif kind == "fail":
        ui.console.print(ui.action_result(payload, ok=False))
    elif kind == "learned":
        ui.success(payload)
    elif kind == "error":
        ui.error(payload)
    elif kind in ("giveup", "cancelled"):
        ui.warn(payload)


async def practice_round(ctx: "Ctx", should_stop) -> str:
    """Eine freigegebene Übungsrunde im Arbeitsordner.
    Teilt sich den TURN_LOCK mit echten Chats – nie beides gleichzeitig."""
    if ctx.backend is None:
        return "abgebrochen"

    async def approve(path, code):
        if should_stop():
            return False
        preview = (f"Ziel: {path}\n\n"
                   "Diesen Code als Datei speichern und danach mit Python ausführen.\n"
                   "Ausgeführt wird in der Sandbox (AppContainer): nur NemiSandbox "
                   "und Internet, kein Zugriff auf deine Dateien.\n\n" + code)
        return await review_action_confirm("Übung: Code prüfen und freigeben", preview)

    async with TURN_LOCK:
        if should_stop():
            return "abgebrochen"
        PRACTICE_STATE["count"] += 1
        return await PRAC.one_round(ctx.backend, _practice_emit, should_stop,
                                    n=PRACTICE_STATE["count"], approve=approve)


async def pick_model(current: str | None) -> str | None:
    """Zweistufige Auswahl: erst Cloud-Anbieter ODER Lokal, dann das Modell.
    Gibt eine Modell-Referenz zurück oder None (abgebrochen)."""
    provs = {pid: p for pid, p in P.detected().items() if not p.keyless}
    ollama_on = P.get("ollama").available()
    # Anbieter ohne Key, die man einrichten kann (keyless wie Ollama ausgenommen).
    keyless = [p for pid, p in P.PROVIDERS.items() if not p.available() and not p.keyless]

    # --- Stufe 1: Wohin? ---
    stufe1: list[tuple[str, str]] = []
    for pid, p in provs.items():            # Anbieter MIT Key: einzeln wählbar
        extra = f"  ·  {p.note}" if p.note else ""
        stufe1.append((f"cloud:{pid}", f"☁  {p.label}{extra}"))
    if keyless:                             # Anbieter OHNE Key: hinzufügen (Key eintippen)
        stufe1.append(("cloudadd",
                       f"☁  Cloud-Anbieter hinzufügen  ·  Key eintragen ({len(keyless)})"))
    import gguflokal
    lokale = gguflokal.modelle()
    if lokale:                              # eigener GGUF-Motor: Modelle aus ModelGGUF/
        stufe1.insert(0, ("gguf", f"🧠  Lokal (eigener Motor)  ·  {len(lokale)} Modell(e)  ·  "
                                  "ohne Server, kein Key"))
    from einrichtungshelfer import funkechat
    if funkechat.verfuegbar():              # Funke Groß: NemiCLIs eigenes kleines Modell, immer dabei
        stufe1.append((funkechat.REF, f"{funkechat.beschreibung()}  ·  kein Key"))
    if ollama_on:                           # lokales Ollama läuft -> eigener Zweig
        n = len(P.ollama_models())
        stufe1.append(("ollama", f"🦙  Ollama  ·  {n} Modell(e)  ·  lokal, kein Key"))
    # Ollama-Modell laden – bzw. einrichten, wenn Ollama noch nicht da/aus ist.
    stufe1.append(("ollama_get",
                   "🦙  Ollama-Modell herunterladen" if ollama_on
                   else "🦙  Ollama einrichten  ·  installieren & Modell laden"))
    hf = ("hf", "🤗  Modell von Hugging Face holen  ·  lokal, Größe und MoE wählen")
    if current:
        stufe1.append(hf)
        stufe1.append(("__cancel__", "Abbrechen"))
        frage = "Cloud-Anbieter oder Lokal?"
    else:                                   # noch kein Modell: die zwei Einstiege zuerst
        oben = [o for o in stufe1 if o[0] == "cloudadd"] + [hf]
        stufe1 = oben + [o for o in stufe1 if o[0] != "cloudadd"] + [("__cancel__", "Später")]
        frage = ("👋 Willkommen! Womit soll NemiCLI denken?  Cloud: API-Key eintragen · "
                 "Lokal: ein Modell auf diesen PC holen")

    wahl = await ask_confirm(frage, stufe1)
    if wahl == "__cancel__":
        return None
    if wahl == funkechat.REF:
        await _funke_vorgaben_waehlen()
        return wahl

    # --- Einrichtungs-Zweige (laden/installieren, dann zurück ins /model) ---
    if wahl == "hf":
        return await hf_downloader()
    if wahl == "ollama_get":
        await setup_ollama()
        return None
    # Neuen Anbieter einrichten: auswählen -> Key eintippen -> speichern -> weiter
    if wahl == "cloudadd":
        opts = [(pid, f"☁  {p.label}  ·  {p.env}")
                for pid, p in P.PROVIDERS.items()
                if not p.available() and not p.keyless]
        opts.append(("__cancel__", "Abbrechen"))
        pid = await ask_confirm("Welchen Anbieter möchtest du einrichten?", opts)
        if pid == "__cancel__":
            return None
        prov = P.get(pid)
        ui.info(f"{prov.label}: Key holen unter  {P.KEY_URLS.get(pid, '(siehe Anbieter-Website)')}")
        key = await ask_text(f"{prov.label} API-Key einfügen (leer = abbrechen):", password=True)
        if not key:
            ui.warn("Kein Key eingegeben – abgebrochen.")
            return None
        if not config.set_env(prov.env, key):
            ui.error("Konnte den Key nicht in die .env schreiben.")
            return None
        wie = "verschlüsselt & sofort aktiv" if keyvault.available() else "gespeichert & sofort aktiv"
        ui.success(f"{prov.label}-Key {wie}. 🔑  {keyvault.mask(key)}")
        wahl = f"cloud:{pid}"            # direkt weiter zur Modellauswahl

    # --- Stufe 2: konkretes Modell ---
    if wahl == "gguf":
        opts = [(M.make_ref("gguf", name), gguflokal.beschreibung(name)) for name in lokale]
        opts.append(("__cancel__", "Zurück / Abbrechen"))
        ref = await ask_confirm(f"Welches lokale Modell?  (Kontext {gguflokal.kontext():,} Token, "
                                f"höchstens {gguflokal.tok_s() or '∞'} Token/s)".replace(",", "."), opts)
        return None if ref == "__cancel__" else ref
    if wahl == "ollama":
        ids = P.ollama_models()
        if not ids:
            ui.warn("Ollama läuft, hat aber keine Chat-Modelle (nur Embeddings?). "
                    "Mit  ollama pull <modell>  eins holen.")
            return None
        opts = [(M.make_ref("ollama", mid),
                 mid + ("  👁 Vision" if P.ollama_is_vision(mid) else ""))
                for mid in ids]
        opts.append(("__cancel__", "Zurück / Abbrechen"))
        ref = await ask_confirm("Welches Ollama-Modell?", opts)
        return None if ref == "__cancel__" else ref

    pid = wahl.split(":", 1)[1]
    prov = P.get(pid)
    try:
        with ui.thinking(f"frage {prov.label} nach verfügbaren Modellen"):
            ids = await P.list_models(pid)
    except Exception as e:
        ui.error(f"Modell-Liste von {prov.label} fehlgeschlagen: {e}")
        return None
    if not ids:
        ui.warn(f"{prov.label} hat keine passenden Chat-Modelle geliefert.")
        return None

    opts = [(M.make_ref(pid, mid), mid) for mid in ids]
    opts.append(("__cancel__", "Zurück / Abbrechen"))
    ref = await ask_confirm(f"Welches Modell von {prov.label}?", opts)
    return None if ref == "__cancel__" else ref


def _video_opts(text: str) -> dict:
    """`/video <prompt> [--sekunden 10] [--start pfad] [--seed 1] [--steps 30] [--schaerfe mittel]`."""
    opts: dict = {}
    muster = re.compile(r'--(sekunden|start|seed|steps|schaerfe)\s+("[^"]+"|\S+)')
    for m in muster.finditer(text):
        wert = m.group(2).strip('"')
        opts[m.group(1)] = wert
    opts["prompt"] = muster.sub("", text).strip()
    for k, art in (("sekunden", float), ("seed", int), ("steps", int)):
        if k in opts:
            try:
                opts[k] = art(opts[k])
            except ValueError:
                del opts[k]
    return opts


async def _funke_vorgaben_waehlen() -> None:
    """Vorgaben für Funke Groß (du/Sie, Länge, Emojis) – per Auswahl, gespeichert als funke_vorgaben."""
    jetzt = {"anrede": "du", "laenge": "normal", "emoji": "ja"} | (config.load().get("funke_vorgaben") or {})
    kurz = f"{'du' if jetzt['anrede'] == 'du' else 'Sie'} · {jetzt['laenge']} · Emojis {jetzt['emoji']}"
    if await ask_confirm("Wie soll Funke sprechen?", [("so", f"So lassen  ({kurz})"),
                                                      ("anpassen", "Anpassen: du/Sie · Länge · Emojis")]) != "anpassen":
        return
    neu = dict(jetzt)
    neu["anrede"] = await ask_confirm("Anrede?", [("du", "du"), ("sie", "Sie")])
    neu["laenge"] = await ask_confirm("Wie ausführlich?", [("kurz", "kurz (ein Satz)"), ("normal", "normal"),
                                                           ("lang", "ausführlich")])
    neu["emoji"] = await ask_confirm("Emojis?", [("ja", "ja 😊"), ("nein", "nein")])
    config.update(funke_vorgaben=neu)
    ui.success("✨ Gemerkt – gilt ab der nächsten Antwort.")


async def switch_model(ref: str, backend, model, strength):
    """Wechselt das aktive Modell. Gibt (backend, model) zurück (alt bei Fehler)."""
    carry = backend.messages if backend else None
    if (schliessen := getattr(backend, "schliessen", None)) is not None:
        await schliessen()                       # Hintergrund-Arbeit hält sonst das alte Modell im Grafikspeicher
    try:
        new_backend = await make_backend(ref, carry, strength)
    except Exception as e:
        ui.error(str(e))
        return backend, model
    config.update(model=ref)
    if M.split_ref(ref)[0] == "ollama":
        where = "lokal über Ollama"
    elif M.split_ref(ref)[0] == "gguf":
        where = "lokal im eigenen Motor – das erste Laden dauert ein paar Sekunden"
    elif M.split_ref(ref)[0] == "funke":
        where = "NemiCLIs eigenes kleines Modell auf diesem PC – ohne Werkzeuge und Bilder"
    else:
        where = "in der Cloud"
    ui.success(f"Modell aktiv: {ref}  ({where})")
    if carry:
        ui.info("Das Gespräch geht mit dem neuen Modell weiter (es liest den Verlauf neu). "
                "Frisch anfangen: /reset")
    return new_backend, ref


async def neue_persoenlichkeit() -> "PS.Persoenlichkeit | None":
    """Fragt Name, Kurzbeschreibung, Geschlecht und Tonfall ab und legt die
    Datei an. Gibt die neue Persönlichkeit zurück (oder None bei Abbruch)."""
    name = await ask_text("Wie soll die Persönlichkeit heißen? (leer = abbrechen)")
    if not name:
        ui.info("Abgebrochen.")
        return None
    kurz = await ask_text("In ein paar Worten: wie ist sie? (z.B. „ruhig, nüchtern, präzise“)")
    geschlecht = await ask_confirm("Wie spricht sie von sich?",
                                   PS.GESCHLECHT_OPTIONEN + [("__cancel__", "Abbrechen")])
    if geschlecht == "__cancel__":
        ui.info("Abgebrochen.")
        return None
    ton = await ask_confirm("Welcher Tonfall?",
                            PS.TON_OPTIONEN + [("__cancel__", "Abbrechen")])
    if ton == "__cancel__":
        ui.info("Abgebrochen.")
        return None
    pfad = PS.create(name, kurz, geschlecht, ton)
    neu = PS.list_all().get(pfad.stem)
    if neu is None:
        ui.error("Die Datei konnte nicht gelesen werden – seltsam. Schau in Persoenlichkeiten/.")
        return None
    PS.set_active(neu.key)
    ui.success(f"🎭 {neu.name} angelegt und aktiv – ab der nächsten Nachricht.")
    _neuer_chat_nach_wechsel()
    ui.info(f"Datei: {pfad}")
    oeffnen = await ask_confirm("Die Datei jetzt im Editor öffnen und weiter ausfeilen?",
                                [("ja", "Ja – öffnen"),
                                 ("nein", "Nein – erstmal so lassen")])
    if oeffnen == "ja":
        try:
            os.startfile(pfad)
        except Exception:
            ui.info(str(pfad))
        ui.info("Änderungen in der Datei gelten sofort – kein Neustart, kein /reset nötig.")
    return neu


def _ps_kurz(s: str, n: int = 70) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def _ps_bearbeiten(p: "PS.Persoenlichkeit") -> None:
    """Untermenü „Bearbeiten": Kopfzeilen, Geschlecht, Tonfall, Zeilen, Editor."""
    while True:
        p = PS.list_all().get(p.key)              # nach jeder Änderung frisch lesen
        if p is None:
            return
        wahl = await ask_confirm(f"✏️ {p.name} bearbeiten – was?", [
            ("name", f"Name ändern           ({p.name})"),
            ("kurz", f"Kurzbeschreibung      ({_ps_kurz(p.kurz or '–', 40)})"),
            ("geschlecht", "Geschlecht            (weiblich · männlich · neutral)"),
            ("ton", "Tonfall               (locker · sachlich · förmlich · verspielt)"),
            ("add", "Zeile hinzufügen      (z.B. „Du nennst mich Boss“)"),
            ("del", "Zeile entfernen"),
            ("editor", "Im Editor öffnen      (für größere Umbauten)"),
            ("__back__", "Zurück"),
        ])
        if wahl == "__back__":
            return
        try:
            if wahl == "name":
                neu = await ask_text(f"Neuer Name (leer = bleibt „{p.name}“):")
                if neu:
                    import profilordner
                    alt_ordner = profilordner.wurzel() / profilordner.ordnername(p.name)
                    neu_ordner = profilordner.wurzel() / profilordner.ordnername(neu)
                    if alt_ordner.is_dir() and not neu_ordner.exists():
                        alt_ordner.rename(neu_ordner)               # Chats, Bilder … ziehen mit
                    PS.set_name(p, neu)
                    if PS.active().key == p.key:
                        PS.set_active(p.key)
                    ui.success(f"🎭 Heißt jetzt {neu.strip()}.")
            elif wahl == "kurz":
                neu = await ask_text("Kurzbeschreibung fürs Menü (leer = entfernen):")
                PS.set_kurz(p, neu)
                ui.success("Kurzbeschreibung gespeichert.")
            elif wahl == "geschlecht":
                g = await ask_confirm("Wie spricht sie/er von sich?",
                                      PS.GESCHLECHT_OPTIONEN + [("__cancel__", "Abbrechen")])
                if g != "__cancel__":
                    PS.set_geschlecht(p, g)
                    ui.success("Geschlecht gesetzt.")
            elif wahl == "ton":
                tn = await ask_confirm("Welcher Tonfall?",
                                       PS.TON_OPTIONEN + [("__cancel__", "Abbrechen")])
                if tn != "__cancel__":
                    PS.set_ton(p, tn)
                    ui.success("Tonfall gesetzt.")
            elif wahl == "add":
                zeile = await ask_text("Neue Zeile (leer = abbrechen):")
                if zeile:
                    PS.add_line(p, zeile)
                    ui.success("Zeile angehängt.")
            elif wahl == "del":
                zeilen = PS.body_lines(p)
                if not zeilen:
                    ui.info("Da ist noch keine Zeile zum Entfernen.")
                    continue
                opts = [(str(i), _ps_kurz(text)) for i, text in zeilen[:40]]
                opts.append(("__cancel__", "Abbrechen"))
                z = await ask_confirm("Welche Zeile soll weg?", opts)
                if z != "__cancel__":
                    PS.remove_line(p, int(z))
                    ui.success("Zeile entfernt.")
            elif wahl == "editor":
                try:
                    os.startfile(p.path)
                except Exception:
                    ui.info(str(p.path))
                ui.info("Speichern genügt – gilt ab der nächsten Nachricht.")
                return
        except Exception as e:
            ui.error(f"Bearbeiten fehlgeschlagen: {e}")


async def _ps_profil(p: "PS.Persoenlichkeit") -> None:
    """Untermenü für ein einzelnes Profil: aktivieren, bearbeiten, kopieren, löschen."""
    aktiv = PS.active().key == p.key
    opts = [("on", "Aktivieren" + ("   (ist schon aktiv)" if aktiv else ""))]
    if p.eingebaut:
        opts.append(("copy", "Als Kopie anlegen     (die Eingebaute ist schreibgeschützt)"))
    else:
        opts += [("edit", "Bearbeiten"),
                 ("copy", "Als Kopie anlegen"),
                 ("del", "Löschen")]
    opts.append(("__back__", "Zurück"))
    wahl = await ask_confirm(f"🎭 {p.label}", opts)
    if wahl == "on":
        wechsel = p.key != PS.active().key
        PS.set_active(p.key)
        ui.success(f"🎭 Persönlichkeit: {p.name} – gilt ab der nächsten Nachricht.")
        _sprueche_im_hintergrund()
        if wechsel:
            _neuer_chat_nach_wechsel()
    elif wahl == "edit":
        await _ps_bearbeiten(p)
    elif wahl == "copy":
        name = await ask_text(f"Name der Kopie (leer = „{p.name} Kopie“):")
        pfad = PS.duplicate(p, name)
        neu = PS.list_all().get(pfad.stem)
        ui.success(f"🎭 Kopie angelegt: {neu.name if neu else pfad.name}")
        if neu is not None:
            await _ps_bearbeiten(neu)
    elif wahl == "del":
        sicher = await ask_confirm(f"{p.name} wirklich löschen? (Datei {p.path.name})",
                                   [("ja", "Ja, löschen"), ("nein", "Nein")])
        if sicher == "ja":
            PS.delete(p)
            ui.success(f"🎭 {p.name} gelöscht." + ("  Nemi übernimmt." if aktiv else ""))


async def persoenlichkeiten_menu(arg: str) -> None:
    """/persönlichkeiten [name | neu] – Menü, direkt setzen oder neue anlegen."""
    arg = arg.strip()
    if arg.lower() in ("neu", "new", "anlegen", "+"):
        await neue_persoenlichkeit()
        return
    if arg:                                      # direkt gesetzt: /persönlichkeiten <name>
        p = PS.resolve(arg)
        if p is None:
            ui.warn(f"Keine Persönlichkeit gefunden, die zu '{arg}' passt. "
                    "/persönlichkeiten zeigt die Liste.")
            return
        wechsel = p.key != PS.active().key
        PS.set_active(p.key)
        ui.success(f"🎭 Persönlichkeit: {p.name} – gilt ab der nächsten Nachricht.")
        _sprueche_im_hintergrund()
        if wechsel:
            _neuer_chat_nach_wechsel()
        return

    while True:
        alle = PS.list_all()
        aktiv = PS.active().key
        opts = [(k, ("✓  " if k == aktiv else "   ") + p.label) for k, p in alle.items()]
        opts.append(("__neu__", "➕  Neue Persönlichkeit anlegen (ein paar Fragen)"))
        opts.append(("__ordner__", "📂  Ordner öffnen (Dateien von Hand bearbeiten)"))
        opts.append(("__cancel__", "Fertig"))
        wahl = await ask_confirm("Wer soll sprechen?  (Profil wählen → aktivieren · bearbeiten · löschen)", opts)
        if wahl == "__cancel__":
            return
        if wahl == "__neu__":
            await neue_persoenlichkeit()
            continue
        if wahl == "__ordner__":
            PS.ensure_dir()
            try:
                os.startfile(PS.DIR)
            except Exception:
                ui.info(str(PS.DIR))
            ui.info("Eine .md-Datei pro Persönlichkeit – erste Überschrift = Name, "
                    "erste „> Zeile“ = Kurzbeschreibung. Platzhalter wie {{user}} und "
                    "{{current_time}} werden eingesetzt.")
            return
        await _ps_profil(alle[wahl])


def model_completions() -> dict[str, str]:
    """Vorschläge für /model: die Anbieter, für die ein Key gesetzt ist."""
    out: dict[str, str] = {}
    for pid, p in P.detected().items():
        out[pid] = p.label
    return out


class Ctx:
    """Veränderlicher Sitzungs-Status, den sich Klassik-Loop und TUI teilen."""

    def __init__(self, backend, model, strength, current_chat, current_prompts):
        self.backend = backend
        self.model = model
        self.strength = strength
        self.current_chat = current_chat
        self.current_prompts = current_prompts

    def strength_active(self) -> bool:
        """Nur tatsächlich angebundene Denk- oder Budgetsteuerung anzeigen."""
        return self.backend is not None and M.strength_supported(self.model)

    def sync_session(self) -> None:
        """Statusleisten-Daten (SESSION) an den aktuellen Stand angleichen."""
        import persona
        persona.chat_gewechselt(self.current_chat)          # Kern-Gedächtnis: neue Momentaufnahme je Chat
        SESSION.update(model=self.model or "—",
                       strength=self.strength if self.strength_active() else None,
                       chat=self.current_chat,
                       ctx_max=pricing.context_window(self.model))


def _untermenue_eintraege(ctx: Ctx, cmd: str) -> list[tuple[str, str, str | None]]:
    """Auswahl für einen Befehl ohne Zusatz – fest aus commands.UNTERMENUES oder
    nach aktuellem Stand (Modus, Theme, Denkstufe)."""
    import commands as C
    if cmd in C.UNTERMENUES:
        return C.UNTERMENUES[cmd]
    haken = lambda an: "✓ " if an else "   "
    if cmd == "/modus":
        return [(k, f"{haken(k == modes.current())}{sym} {name}", None) for k, sym, name in modes.MODES]
    if cmd == "/theme":
        return [(k, f"{haken(k == ui.current_theme())}{k}  · {p.get('label', k)}"
                 + ("  ✨ eigenes" if k not in ui.EINGEBAUT else ""), None) for k, p in ui.THEMES.items()]
    if cmd == "/kontext":
        prov, name = M.split_ref(ctx.model or "")
        if prov != "gguf":
            return []
        import gguflokal
        pro_token, grenze = gguflokal.kv_bytes_pro_token(name), gguflokal.kontext_max(name)
        aktuell = gguflokal.kontext(name)
        kosten = lambda n: f"  ·  Gesprächsspeicher ~{n * pro_token / 2**30:.1f} GB" if pro_token else ""
        stufen = [n for n in gguflokal.KONTEXT_STUFEN if not grenze or n < grenze]
        eintraege = [("anzeigen", "   📊 Anzeigen – womit ist der Kontext gefüllt?", None)]
        eintraege += [(str(n), f"{haken(n == aktuell)}{n // 1024}k{kosten(n)}"
                       + ("  (Vorgabe)" if n == gguflokal.VORGABE_KONTEXT else ""), None) for n in stufen]
        if grenze:
            eintraege.append(("max", f"{haken(grenze == aktuell)}max ({grenze // 1024}k){kosten(grenze)}", None))
        return eintraege
    if cmd == "/embeddings":
        aktiv = memory.modell_name()
        eintraege = [(d.name, f"{haken(d.name == aktiv)}{d.name}  ·  {_embedding_art(d)}", None)
                     for d in memory.modelle()]
        return eintraege + [("huggingface", "   🤗 Weitere Modelle auf Hugging Face", None)]
    if cmd == "/staerke" and ctx.strength_active():
        return [(k, f"{haken(k == ctx.strength)}{k} – {text}", None)
                for k, text in M.strength_menu(ctx.model).items()]
    if cmd == "/video":
        frage = "Was soll im Video passieren? (am besten englisch)"
        eintraege = [("", "   🎬 Stand: Modell, Größe, Ordner", None),
                     ("{}", "   🎬 Video erzeugen – 5 s", frage),
                     ("{} --sekunden 10", "   🎬 Video erzeugen – 10 s", frage),
                     ("{} --sekunden 20", "   🎬 Video erzeugen – 20 s", frage)]
        try:
            import video
            akt = video.gewaehlt()
            eintraege += [(f"modell {n}", f"{haken(n == akt)}🎞 Modell: {n}", None) for n in video.modelle()]
        except Exception:
            pass
        return eintraege + [("ordner", "   📂 Video-Ordner öffnen", None)]
    if cmd == "/debug":
        import debugbericht as _DBG
        an, motor = _DBG.an(), _DBG.motor_log_an()
        return [("", "   🐞 Stand anzeigen", None),
                ("an", f"{haken(an)}Debugbericht an – die KI kann hineinsehen (Cloud)", None),
                ("aus", f"{haken(not an)}Debugbericht aus", None),
                ("motor an", f"{haken(motor)}Motor-Log an – jeder Prompt des lokalen Motors (groß, privat)", None),
                ("motor aus", f"{haken(not motor)}Motor-Log aus", None)]
    return []


_KONTEXT_FARBEN = {"Persönlichkeit": "magenta", "Regeln & Werkzeuge": "bright_black", "Hinweise": "cyan",
                   "Gedächtnis": "bright_red", "Skills": "yellow", "Stand des Gesprächs": "green",
                   "Nachrichten": "blue"}


def _kontext_teile(ctx: "Ctx") -> tuple[list, int, int, bool]:
    """Womit der Kontext des aktiven Modells gefüllt ist: (Bereiche mit Token, Fenster,
    Platz für die Antwort, genau gezählt). Genau mit dem Tokenizer eines geladenen lokalen
    Modells, sonst geschätzt."""
    import persona
    prov, name = M.split_ref(ctx.model or "")
    lokal = prov == "gguf"
    fenster = max(1, pricing.context_window(ctx.model))
    zaehlen, genau = (lambda t: pricing.est_tokens(t) if t else 0), False
    reserve = 0
    if lokal:
        import gguflokal
        reserve = gguflokal.antwort_reserve(fenster)
        motor = gguflokal._motor
        if motor is not None and motor[0] == gguflokal.modelle().get(name):    # nur der Motor DIESES Modells
            tok = motor[2].tokenizer
            zaehlen, genau = (lambda t: len(tok.encode(t)) if t else 0), True
    teile = [(n, zaehlen(t)) for n, t in persona.prompt_bilanz(lokal)]
    sichtbar = getattr(ctx.backend, "sichtbar", None)
    verlauf = list(sichtbar() if callable(sichtbar) else (getattr(ctx.backend, "messages", None) or []))
    if (stand := getattr(ctx.backend, "stand", "")):
        teile.append(("Stand des Gesprächs", zaehlen(stand)))
    teile.append(("Nachrichten", sum(pricing.est_tokens(m.get("content")) for m in verlauf)))
    return teile, fenster, reserve, genau


def _kontext_neu_zaehlen(ctx: "Ctx", vorbereiten: bool = True) -> None:
    """Nach einem Modellwechsel die Füllung für das neue Modell zählen – sonst stünde bis zur
    nächsten Antwort der letzte Wert des alten Modells (mit dessen Tokenizer) in der Leiste.
    Lokal und zu voll: gleich im Hintergrund aufräumen (Cloud-Modelle bleiben unberührt)."""
    try:
        teile, fenster, reserve, _ = _kontext_teile(ctx)
    except Exception:
        SESSION["ctx"] = 0
        return
    SESSION["ctx"] = sum(n for _, n in teile)
    SESSION["ctx_max"] = fenster
    starten = getattr(ctx.backend, "vorbereiten", None)
    if (vorbereiten and callable(starten) and SESSION["ctx"] + reserve > fenster
            and starten(fertig=lambda: _kontext_neu_zaehlen(ctx, vorbereiten=False))):
        ui.info(f"📌 Der Verlauf passt nicht in {fenster // 1024}k – ich lade das Modell und räume im "
                "Hintergrund auf (Älteres kommt in den Stand des Gesprächs und ins Gedächtnis).")


def kontext_zahl(wahl: str) -> int:
    """Kontext-Eingabe → Token. „32k“ = 32768 (ganze Zahl mit k, wie bei Modell-Kontexten üblich);
    „25.6k“ = 25600 (mit Nachkomma, so wie die Anzeige rechnet); „32768“ und „32.768“ = 32768."""
    wahl = wahl.strip().lower().replace(",", ".").replace(" ", "")
    if wahl.endswith("k"):
        zahl = wahl[:-1]
        return int(round(float(zahl) * (1000 if "." in zahl else 1024)))
    return int(wahl.replace(".", ""))


def _kontext_anzeige(ctx: "Ctx") -> None:
    """/kontext: womit der Kontext gefüllt ist – Raster und Liste wie /context bei Claude Code."""
    from rich.text import Text
    teile, fenster, reserve, genau = _kontext_teile(ctx)
    lokal = M.split_ref(ctx.model or "")[0] == "gguf"
    belegt = sum(n for _, n in teile)
    frei = max(0, fenster - belegt - reserve)
    zellen, raster = 200, Text()
    for bereich, n in teile:
        k = max(1, round(n / fenster * zellen)) if n else 0
        raster.append("■ " * k, style=_KONTEXT_FARBEN.get(bereich, "white"))
    rest = zellen - len(raster.plain) // 2
    k_res = min(rest, round(reserve / fenster * zellen))
    raster.append("· " * max(0, rest - k_res), style="dim")
    raster.append("× " * k_res, style="dim")
    zeilen = [Text(raster.plain[i:i + 40]) for i in range(0, len(raster.plain), 40)]
    pos = 0
    for z in zeilen:                                  # Farben je Zeile übernehmen
        for span in raster.spans:
            a, b = max(span.start, pos), min(span.end, pos + 40)
            if a < b:
                z.stylize(span.style, a - pos, b - pos)
        pos += 40
    fmt = lambda n: f"{n / 1000:.1f}k" if n >= 1000 else str(n)
    pct = lambda n: f"{n / fenster * 100:.1f} %"
    ui.info(f"📊 Kontext · {ctx.model or 'kein Modell'} · {fmt(belegt)} von {fmt(fenster)} Token ({pct(belegt)})"
            + ("" if genau else "  ·  geschätzt"))
    liste = []
    for bereich, n in teile:
        liste.append(Text.assemble(("■ ", _KONTEXT_FARBEN.get(bereich, "white")), f"{bereich}: {fmt(n)} ({pct(n)})"))
    liste.append(Text.assemble(("· ", "dim"), f"Frei: {fmt(frei)} ({pct(frei)})"))
    if reserve:
        liste.append(Text.assemble(("× ", "dim"), f"Platz für die Antwort: {fmt(reserve)} ({pct(reserve)})"))
    for i in range(max(len(zeilen), len(liste))):
        links = zeilen[i] if i < len(zeilen) else Text(" " * 40)
        ui.console.print(Text("  ") + links + Text("   ") + (liste[i] if i < len(liste) else Text("")))
    if lokal:
        import gguflokal
        ui.info(f"🧱 Modell: {gguflokal.modell_ort(M.split_ref(ctx.model)[1])}")
        ui.info("Wird es voll, wandern die ältesten Nachrichten ins Gedächtnis (vektorisiert, auffindbar); "
                f"die KI holt sich zurück, was zur Frage passt. Größe einstellen: /kontext (jetzt {fmt(fenster)}).")
    else:
        ui.info(f"Der Verlauf wird aufgeräumt ab etwa {fmt(int(fenster * pricing.HISTORY_SHARE))} Token – "
                "das Wichtigste geht ins Gedächtnis.")


HF_EMBEDDINGS = "https://huggingface.co/models?library=gguf&pipeline_tag=feature-extraction&sort=trending"


def _embedding_art(ordner) -> str:
    import embedder
    return "GGUF, gepackt auf der GPU" if embedder.gguf_datei(ordner) is not None else "safetensors (transformers)"


async def _untermenue(ctx: Ctx, cmd: str) -> str | None:
    """Zeigt die Auswahl und gibt den Zusatz zurück – None, wenn abgebrochen
    oder der Befehl keine Auswahl hat ("" = Befehl ohne Zusatz ausführen)."""
    import commands as C
    eintraege = _untermenue_eintraege(ctx, cmd)
    if not eintraege:
        return ""
    optionen = [(str(i), anzeige) for i, (_, anzeige, _) in enumerate(eintraege)]
    optionen.append((C.ZURUECK, "↩ Zurück"))
    wahl = await ask_confirm(f"{cmd} – was möchtest du?  (↑/↓ oder 1–9, Enter)", optionen)
    if wahl == C.ZURUECK or not str(wahl).isdigit():
        return None
    zusatz, _, frage = eintraege[int(wahl)]
    if "{}" in zusatz:
        antwort = (await ask_text(frage or "Angabe?")).strip()
        if not antwort and not zusatz.startswith("malen"):
            return None
        zusatz = zusatz.replace("{}", antwort)
    return " ".join(zusatz.split())


async def handle_line(ctx: Ctx, text: str) -> str | None:
    """Verarbeitet EINE Eingabezeile (Befehl oder Chat). Gibt 'exit' zurück,
    wenn NemiCLI beendet werden soll – sonst None. Von Klassik-Loop UND TUI genutzt."""
    # „Nemi antwortet" (Rechtsklick): Auftrag trägt das Zielfenster im Präfix –
    # nach der Antwort wird der Text dort eingefügt.
    import kontextmenue
    einfuegen_hwnd, text = kontextmenue.antwort_tag(text)
    if einfuegen_hwnd is not None:
        n_vorher = len(ctx.backend.messages) if ctx.backend is not None else 0
        ergebnis = await handle_line(ctx, text)
        await _antwort_einfuegen(ctx, einfuegen_hwnd, n_vorher)
        return ergebnis
    cmd, arg = parse(text)

    # --- Slash-Befehle ----------------------------------------------------
    if cmd is not None:
        try:
            stats.record_command(cmd)
        except Exception:
            pass
        if not arg.strip():
            zusatz = await _untermenue(ctx, cmd)
            if zusatz is None:
                return None
            if zusatz:                                       # gewählter Punkt bleibt im Verlauf sichtbar
                from rich.markup import escape
                ui.console.print(f"[muted]  ↳ {cmd} {escape(zusatz)}[/muted]")
            arg = zusatz
        weiter = await _befehl_ausfuehren(ctx, cmd, arg, text)
        if not isinstance(weiter, tuple):
            return weiter
        cmd, text = weiter
        if cmd is not None:
            if GUI_BRUECKE is not None:
                _gui_spiegeln(ctx)
            return None

    # --- Normaler Chat ----------------------------------------------------
    if not text.strip():
        return None
    if ctx.backend is None:
        if not await funke_plaudern(ctx, text):
            ui.warn("Kein Modell aktiv. Wähle eins mit /model (z.B. /model gemma-12b).")
        return None

    text = emoji.expand(text)   # =)  <3  :feuer:  ->  echte Emojis
    getippt = text              # fürs GUI-Fenster: wie eingegeben, ohne angehängte Datenblöcke

    # Drag & Drop: Windows Terminal setzt nur den PFAD in die Eingabe. Text,
    # Markdown, Quelltext, PDF haengen wir als Daten an; Bilder holt sich
    # vision gleich danach; Binaeres (exe, zip, safetensors) bleibt liegen.
    dateien = anhang.anhaengen(text)
    text = dateien["text"]
    if (h := anhang.hinweis(dateien)):
        for zeile in h.splitlines():
            (ui.warn if "übersprungen" in zeile else ui.info)(zeile)

    angehaengt = vision.find_images(text)
    text, imgs, hinweis = await _bilder_fuer_chat(ctx.model, text)
    if hinweis:
        (ui.warn if "fehlt" in hinweis or "Helfer:" in hinweis else ui.info)(hinweis)
    for _p in angehaengt[:3]:
        _bild_vorschau(str(_p), title=f"angehängt: {_p.name}")

    try:
        async with TURN_LOCK:        # nur ein Zug gleichzeitig (Terminal oder Browser)
            if GUI_BRUECKE is not None:
                GUI_BRUECKE.senden({"t": "nutzer", "text": getippt, "quelle": "terminal"})
                GUI_BRUECKE.senden({"t": "busy", "on": True})
            records = await converse(ctx.backend, text, images=imgs)
            aus_dem_netz = actions.web_tainted()
            ctx.current_prompts.append(text)
            chatstore.save(ctx.current_chat, ctx.backend.messages, ctx.current_prompts, ctx.model or "—")
            n_code = learn.capture_from_answer(text, ctx.backend.messages, ctx.current_chat)
            await bibliothekar(ctx.current_chat, erzwingen=_chat_jetzt(ctx))
            memory.release_encoder()
        lernen_planen(ctx, getippt, records, aus_dem_netz, pricing.weggefallen_holen(ctx.backend))
        if n_code:
            ui.info(f"🐍 {n_code} Code-Snippet(s) in den Wissensspeicher gelernt.")
        # Terminal-Antwort auch im offenen Browser spiegeln (Status & Hinweis)
        if WEB_BRIDGE is not None:
            WEB_BRIDGE.broadcast({"t": "info", "text": "(im Terminal beantwortet)"})
            WEB_BRIDGE.broadcast({"t": "state", "model": ctx.model or "—",
                                  "chat": ctx.current_chat, "tokens": SESSION["tokens"],
                                  "strength": ctx.strength if ctx.strength_active() else None})
        mascot.maybe()
    except Exception as e:
        ui.error(f"Fehler bei der Antwort: {e}")
        await funke_notfall(ctx, e)
    finally:                         # auch nach Esc (CancelledError) oder Fehler
        if GUI_BRUECKE is not None:
            GUI_BRUECKE.senden({"t": "busy", "on": False})
            _gui_spiegeln(ctx)
    return None


async def _befehl_ausfuehren(ctx: "Ctx", cmd: str, arg: str, text: str):
    """Die Slash-Befehle aus handle_line, erster Teil der Kette (der Rest: _befehl_ausfuehren_2).
    Rückgabe wie handle_line – oder (cmd, text), wenn es danach normal weitergeht."""
    if cmd in ("/exit", "/quit"):
        return "exit"
    elif cmd == "/help":
        ui.help_panel()
    elif cmd == "/clear":
        # Wie beim Start: Banner + Status-Zeile + Chat-Hinweis + Maskottchen
        if ui.TUI is not None:
            ui.TUI.clear()
        else:
            ui.console.clear()
        ui.banner(animate=False)
        ui.welcome(model=ctx.model or "—", mode="Chat",
                   strength=ctx.strength if ctx.strength_active() else None)
        mascot.greet()
        ui.console.print()
    elif cmd == "/wissen":
        ui.knowledge_menu(learn.list_skills(), learn.list_snippets())
    elif cmd == "/gedaechtnis":
        sub = arg.strip().lower()
        if sub in ("indexieren", "index", "vektorisieren"):
            o = indexdb.offen()
            ui.info(f"📚 Gedächtnis-DB: {o['brocken']} Brocken · {o['ohne_vektor']} ohne Vektor · "
                    f"{o['berichte_offen']} Berichte noch nicht drin – hole alles nach "
                    + ("(Balken in der Statuszeile, du kannst weiterchatten)." if ui.TUI is not None else "…"))
            await bibliothekar(ctx.current_chat, alles=True)
        elif sub in ("leeren", "loeschen", "clear"):
            if not memory.count():
                ui.info("Das Langzeitgedächtnis ist schon leer.")
            else:
                ja = await ask_confirm(
                    f"Wirklich ALLE {memory.count()} Erinnerungen löschen?",
                    [("nein", "Nein, behalten"), ("ja", "Ja, alles löschen")])
                if ja == "ja":
                    ui.success(f"{memory.clear()} Erinnerung(en) gelöscht.")
                else:
                    ui.info("Abgebrochen – nichts gelöscht.")
        elif sub.startswith("vergiss") or sub.startswith("vergessen"):
            rest = sub.split(maxsplit=1)
            if len(rest) > 1 and rest[1].isdigit() and memory.forget(int(rest[1])):
                ui.success(f"Erinnerung #{rest[1]} vergessen.")
            else:
                ui.warn("Konnte das nicht. Nutzung: /gedaechtnis vergiss <nummer>")
        else:
            entries = memory.all_entries()
            smart = (f"🧠 Stichworte + Bedeutung ({memory.encoder_status()})"
                     if memory.embedding_ready()
                     else f"🔤 nur Stichworte – {memory.encoder_status()}")
            ui.info(f"🧠 Langzeitgedächtnis: {len(entries)} Erinnerung(en)  ·  Suche: {smart}")
            if not entries:
                ui.info("Noch nichts gemerkt. Sag z.B. 'merk dir, ich mag knappe Antworten' "
                        "oder NemiCLI merkt sich Wichtiges von selbst.")
            else:
                for e in entries:
                    ui.console.print(f"  [dim]#{e['id']} · {e['ts']} · {e['kind']}[/dim]  {e['text']}")
                ui.info("Löschen: /gedaechtnis vergiss <nummer>  ·  alles: /gedaechtnis leeren")
            try:
                o = indexdb.offen()
                ui.info(f"📚 Vektor-DB: {o['brocken']} Brocken · {o['ohne_vektor']} ohne Vektor · "
                        f"{o['berichte_offen']} Berichte offen  →  /gedaechtnis indexieren holt alles nach")
            except Exception:
                pass
    elif cmd == "/aufraeumen":
        n = memory.count()
        if n < 2:
            ui.info("Zu wenig im Gedächtnis zum Aufräumen (mindestens 2 Notizen).")
        else:
            with ui.thinking("räume das Gedächtnis auf"):
                r = await asyncio.to_thread(memory.consolidate)
            await bibliothekar()
            memory.release_encoder()
            if r["merged"]:
                ui.success(f"🗜️ Aufgeräumt: {r['merged']} ähnliche Notiz(en) verschmolzen "
                           f"({r['before']} → {r['after']}).")
            else:
                ui.info(f"Alles schon ordentlich – nichts zu verschmelzen ({n} Notizen).")
    elif cmd == "/reflektieren" and arg.split()[:1] == ["auto"]:
        import lernschleife as LS
        wahl = (arg.split() + [""])[1].lower()
        if wahl in ("an", "aus"):
            LS.setzen(wahl == "an")
        ui.info("🪞 Automatisch reflektieren: " + (
            "AN – nach Korrekturen, Fehlern und größeren Aufgaben (nie nach Web-Inhalt)"
            if LS.an() else "AUS – nur noch mit /reflektieren"))
    elif cmd == "/reflektieren":
        if ctx.backend is None:
            ui.warn("Kein Modell aktiv – mit /model eins wählen.")
        else:
            saved = await do_reflect(ctx)
            if saved:
                ui.success(f"🪞 {len(saved)} Lehre(n) gemerkt:")
                for art, t in saved:
                    ui.console.print(f"  [dim]{art}[/dim]  {t}")
            else:
                ui.info("🪞 Nichts Bleibendes zum Lernen gefunden – alles gut.")
    elif cmd == "/start":
        await run_reich()
    elif cmd == "/einrichten":
        for befehl in await run_wizard():                 # z. B. /model nach der Wahl Cloud/lokal
            await handle_line(ctx, befehl)
    elif cmd == "/systemcheck":
        import syscheck
        import systemprofil as SP
        ui.info("🩺 Schaue mir deinen PC an … (ein paar Sekunden)")
        ui.steckbrief_panel(await asyncio.to_thread(SP.erkennen, True))
        rep = await asyncio.to_thread(syscheck.report, True)
        ui.system_panel(rep, syscheck.todo(rep))
    elif cmd == "/ml":
        import foldersense
        ui.ml_panel(foldersense.stats(arg or os.getcwd()))
    elif cmd == "/ordner":
        import foldersense
        ziel = arg or os.getcwd()
        r = foldersense.classify(ziel)
        ui.info(f"📂 {ziel}")
        if r["empty"]:
            ui.warn("Leerer oder nicht lesbarer Ordner – keine Merkmale.")
        else:
            ui.success(f"Ordner-Typ (ML): {r['name']}  "
                       f"· {round(r['confidence']*100)} % sicher  "
                       f"· {r['tokens']} Merkmale")
        ui.info("Bekannte Typen: " + ", ".join(foldersense.labels().values()))
    elif cmd == "/emoji":
        ui.emoji_menu(emoji.EMOTICONS, emoji.SHORTCODES)
    elif cmd == "/name":
        await name_befehl(arg)
    elif cmd == "/skills":
        await skills_befehl(arg)
    elif cmd == "/gui":
        global GUI_BRUECKE  # noqa: PLW0603
        if GUI_BRUECKE is None:
            GUI_BRUECKE = await start_gui(ctx)
        if GUI_BRUECKE.verbunden():
            GUI_BRUECKE.senden({"t": "vorne"})
            ui.info("🖥 Das GUI-Fenster ist schon offen.")
        else:
            gui_oeffnen(GUI_BRUECKE)
            ui.success("🖥 Das GUI-Fenster öffnet sich – es ist mit dieser Sitzung verbunden.")
    elif cmd == "/musik":
        global MUSIK_BRUECKE  # noqa: PLW0603
        from musik import gewichte as _musik_gewichte
        if (grund := _musik_gewichte.fehlt()):
            ui.warn("🎵 " + grund)
            return
        if MUSIK_BRUECKE is None:
            MUSIK_BRUECKE = await start_musik(ctx)
        if MUSIK_BRUECKE.verbunden():
            MUSIK_BRUECKE.senden({"t": "vorne"})
            ui.info("🎵 Das Musik-Fenster ist schon offen.")
        else:
            gui_oeffnen(MUSIK_BRUECKE, "--musik")
            ui.success("🎵 Das Musik-Fenster öffnet sich: Wunsch eintippen, ausarbeiten lassen, komponieren.")
    elif cmd == "/web":
        import webfetch
        if arg.strip().lower() == "key":      # /web key -> Ollama-API-Key (Cloud + Suche)
            ui.info("Ollama-API-Key holen unter  https://ollama.com/settings/keys")
            key = await ask_text("Ollama-API-Key einfügen (leer = abbrechen):", password=True)
            if not key:
                ui.warn("Kein Key eingegeben – abgebrochen.")
            elif config.set_env(webfetch.SEARCH_ENV, key):
                wie = "verschlüsselt & " if keyvault.available() else ""
                ui.success(f"Ollama-Suche aktiv 🔎  ({wie}sofort nutzbar)  {keyvault.mask(key)}")
            else:
                ui.error("Konnte den Key nicht speichern.")
        elif arg.strip().lower().split()[:1] == ["fremdseiten"]:
            wert = {"an": "frei"}.get((arg.strip().lower().split() + [""])[1],
                                      (arg.strip().lower().split() + [""])[1])
            if wert in webfetch.FREMD_MODI:
                config.update(**{webfetch.FREMD_SCHALTER: wert})
            modus = webfetch.fremdseiten_modus()
            ui.info("◐ Seiten außerhalb der Allowlist: " + {
                "frei": "über Ollama lesbar, aus Suchtreffern ohne Rückfrage (niedrigstes Vertrauen)",
                "fragen": "über Ollama lesbar, jede fragt vorher",
                "aus": "gesperrt"}[modus]
                + ("" if webfetch.search_available() else "  ·  ⚠ ohne Ollama-Schlüssel (/web key) aus")
                + "  ·  /web fremdseiten frei|fragen|aus")
        else:
            status = ("🔎 Web-Suche (Ollama): aktiv" if webfetch.search_available()
                      else "🔎 Web-Suche (Ollama): aus – mit  /web key  einrichten")
            status += (f"  ·  ◐ Fremdseiten über Ollama: {webfetch.fremdseiten_modus()}"
                       if webfetch.fremdseiten_an() else "  ·  ◐ Fremdseiten: aus")
            ui.info(status)
            eintraege = webfetch.entries()
            filt = arg.strip().lower()
            # deutsche Suchwörter auf die Kategorie-Namen der Liste abbilden
            filt = {"presse": "press", "behörde": "gov", "behoerde": "gov", "recht": "law",
                    "doku": "docs", "wissenschaft": "science", "sicherheit": "sec"}.get(filt, filt)
            if filt:                       # /web python  → nur passende Domains/Notizen
                eintraege = [e for e in eintraege
                             if filt in e.get("domain", "").lower()
                             or filt in e.get("note", "").lower()
                             or filt in e.get("category", "").lower()]
                if not eintraege:
                    ui.warn(f"Keine Domain passt zu '{arg.strip()}'. /web zeigt alle "
                            f"{len(webfetch.entries())}.")
                    return None
                ui.info(f"{len(eintraege)} von {len(webfetch.entries())} Domains passen zu "
                        f"'{arg.strip()}'.")
            else:
                ui.info(f"{len(eintraege)} Domains · Filter: /web <wort>  (z.B. /web python, /web presse)")
            ui.web_menu(eintraege)
    elif cmd == "/voice":
        import stimme
        sub = arg.strip().lower()
        if sub in ("an", "on", "ein", "knopf", "button"):
            if not stimme.verfuegbar():
                ui.warn("Kein Stimm-Modell gefunden – Qwen3-TTS-Talker und 12-Hz-Tokenizer (GGUF) "
                        "in einen Ordner unter Vision/ legen.")
            elif sub in ("knopf", "button"):
                stimme.modus_setzen("knopf")
                ui.success("🔊 Stimme auf Knopf – spricht nur auf Anfrage (/voice jetzt, Vorlese-Werkzeug, "
                           "Kugel) und gibt den Grafikspeicher danach sofort wieder frei.")
            else:
                stimme.modus_setzen("an")
                ui.success("🔊 Sprachmodus an – du hörst jede Antwort, auf dem Bildschirm steht nur der "
                           "Anfang. Esc stoppt, /voice text zeigt alles, /voice knopf oder aus beendet.")
        elif sub in ("aus", "off"):
            stimme.modus_setzen("aus")
            ui.info("🔇 Stimme aus – bleibt aus, bis du /voice an oder /voice knopf sagst. "
                    "Nichts lädt mehr in den Grafikspeicher, auch nicht auf Wunsch der KI.")
        elif sub in ("jetzt", "now", "vorlesen"):
            if stimme.modus() == "aus":
                ui.info("🔇 Die Stimme ist aus – /voice knopf schaltet sie für solche Anfragen ein.")
            elif not _LETZTE_ANTWORT[0]:
                ui.info("Noch keine Antwort in dieser Sitzung.")
            else:
                stimme.stopp()
                stimme.sagen(_LETZTE_ANTWORT[0])
                ui.info("🔊 Ich lese die letzte Antwort vor – Esc stoppt. Danach ist der Grafikspeicher "
                        "wieder frei.")
        elif sub in ("text", "lesen", "zeigen"):
            if _LETZTE_ANTWORT[0]:
                ui.console.print(_render("", _LETZTE_ANTWORT[0], done=True))
            else:
                ui.info("Noch keine Antwort in dieser Sitzung.")
        else:
            m = stimme.modus()
            erklaert = {"aus": "aus – nichts spricht, nichts lädt",
                        "knopf": "Knopf – nur auf Anfrage, danach sofort entladen",
                        "an": "an – jede Antwort wird vorgelesen"}[m]
            ui.info(f"🔊 Stimme: {erklaert} · Modell: {stimme.ordner_info()} · "
                    "/voice an · knopf · aus · jetzt · text")
    elif cmd == "/debug":
        import debugbericht as _DBG
        sub = arg.strip().lower()
        if sub in ("an", "on"):
            _DBG.setzen(True)
            pfad = _DBG.starten("haupt")
            _DBG.schleife(asyncio.get_running_loop())
            ui.info(f"🐞 Debugbericht an – schreibt wieder mit (die Wache ab ihrem nächsten Start).\n   {pfad}")
        elif sub in ("aus", "off"):
            _DBG.setzen(False)
            _DBG.beenden()
            ui.info("🐞 Debugbericht aus – bleibt aus, bis du /debug an sagst. Die KI kann dann nicht hineinsehen.")
        elif sub.startswith("motor"):
            an = sub.split()[-1] in ("an", "on") if " " in sub else not _DBG.motor_log_an()
            config.update(debug_motor=an)
            if an:
                _DBG.motor_log()
            ui.info("🐞 Motor-Log " + ("an – jeder Prompt und jede Antwort des lokalen Motors landen neben dem "
                                       "Debugbericht (groß, privat)." if an else "aus."))
        else:
            stand = f"läuft – {_DBG.datei()}" if _DBG.laeuft() else ("aus" if not _DBG.an() else "noch nicht gestartet")
            ki = ("die KI kann ihn lesen (Cloud-Modell)" if _DBG.cloud_modell()
                  else "die KI liest ihn nur mit einem Cloud-Modell")
            ui.info(f"🐞 Debugbericht: {stand} · {ki} · Motor-Log {'an' if _DBG.motor_log_an() else 'aus'}"
                    f"\n   Ordner: {_DBG.ordner()}\n   /debug aus · an · motor an|aus")
    elif cmd == "/uebung":
        sub = arg.strip().lower()
        if sub in ("aus", "off", "stop"):
            if ui.TUI is not None:
                ui.TUI.practice_on = False
                ui.TUI._stop_practice()
            config.update(uebung=False)                  # bleibt aus, auch nach Neustart
            ui.info("🎓 Übungsmodus aus – dauerhaft, bis du /uebung an sagst.")
        elif sub in ("an", "on"):
            if ui.TUI is not None:
                ui.TUI.practice_on = True
                ui.TUI._practice_consent = None      # Cloud-Frage neu stellen
            config.update(uebung=True)
            ui.info("🎓 Übungsmodus an – nach ~10 min Ruhe wird eine Übung vorbereitet. "
                    "Schreiben und Ausführen erst nach deiner Codefreigabe. Bleibt gespeichert.")
        elif sub in ("jetzt", "now"):
            if ctx.backend is None:
                ui.warn("Kein Modell aktiv – erst mit /model eins wählen.")
            else:
                if ui.TUI is not None:
                    await ui.TUI.run_practice_once(ctx, practice_round)
                else:
                    await practice_round(ctx, _esc_pressed)
        else:
            an = (ui.TUI.practice_on if ui.TUI is not None
                  else bool(config.load().get("uebung", True)))
            ui.info(f"🎓 Übungsmodus ist {'an' if an else 'aus'} (gespeichert in der Config). "
                    "Nach ~10 min Ruhe bereitet NemiCLI Programme vor; vor jedem "
                    "Schreiben und Ausführen prüfst du den vollständigen Code. "
                    "/uebung an · aus · jetzt")
    elif cmd == "/sandbox":
        import sandbox
        teile = arg.strip().split(maxsplit=1)
        sub = teile[0].lower() if teile else ""
        ziel = teile[1].strip().strip('"') if len(teile) > 1 else ""
        try:
            if sub in ("freigeben", "frei", "add"):
                if not ziel:
                    ui.warn("Welcher Ordner? /sandbox freigeben <Ordner>")
                else:
                    schreiben = False
                    m = re.match(r'^(.*?)\s+(schreiben|rw)$', ziel, re.IGNORECASE)
                    if m:
                        ziel, schreiben = m.group(1).strip().strip('"'), True
                    if (grund := actions._read_guard(ziel) or
                            (actions._guard(ziel) if schreiben else None)):
                        ui.warn(grund)
                    else:
                        p = sandbox.projekt_freigeben(Path(ziel), schreiben=schreiben)
                        if schreiben:
                            ui.success(f"🧪 Freigegeben (lesen + schreiben): {p} – Code aus diesem "
                                       "Projekt läuft dort in der Sandbox und darf darin ändern.")
                        else:
                            ui.success(f"🧪 Freigegeben (nur lesen): {p} – Code aus diesem Projekt "
                                       "läuft dort in der Sandbox; schreiben kann er nur in seinen "
                                       "Lauf-Ordner. Mit '… schreiben' darf er auch dort ändern.")
            elif sub in ("entziehen", "weg", "remove"):
                if not ziel:
                    ui.warn("Welcher Ordner? /sandbox entziehen <Ordner>")
                else:
                    ui.success(f"🧪 Freigabe entzogen: {sandbox.projekt_entziehen(Path(ziel))}")
            else:
                try:
                    py = str(sandbox.interpreter()[0])
                except sandbox.SandboxFehler as exc:
                    py = f"– ({exc})"
                projekte = [f"{p} ({'lesen + schreiben' if sandbox.schreibbar(p) else 'nur lesen'})"
                            for p in sandbox.projekte()]
                pakete_dir = sandbox.ARBEIT / sandbox.PAKETE_ORDNER
                pakete = sorted(d.name.split("-")[0] for d in pakete_dir.glob("*.dist-info")) \
                    if pakete_dir.is_dir() else []
                ui.info("🧪 Sandbox (Windows-AppContainer „NemiCLI.Sandbox“): Code der KI läuft "
                        "isoliert – Internet ja, deine Dateien, Registry und andere Programme nein.\n"
                        f"  Arbeitsordner: {sandbox.ARBEIT}\n"
                        f"  Python:        {py}\n"
                        "  Projekte:     " + (", ".join(map(str, projekte)) or "keine") + "\n"
                        "  Pakete:        " + (", ".join(pakete) or "keine") + "\n"
                        "  /sandbox freigeben <Ordner> [schreiben] · /sandbox entziehen <Ordner>")
        except sandbox.SandboxFehler as exc:
            ui.warn(f"🧪 {exc}")
    elif cmd == "/embeddings":
        wahl = arg.strip()
        if wahl.lower() in ("huggingface", "hf"):
            webbrowser.open(HF_EMBEDDINGS)
            ui.info("🤗 Hugging Face ist offen. Ein Modell kommt als eigener Ordner nach "
                    f"{memory.EMBED_ORDNER} – und vor dem ersten Laden die SHA-256 der Datei "
                    "mit der Angabe auf der Seite vergleichen.")
        elif not wahl:
            ui.info(f"🧬 Embedding-Modell: {memory.encoder_status()}")
        else:
            ordner = {d.name.lower(): d for d in memory.modelle()}.get(wahl.lower())
            if ordner is None:
                ui.warn(f"🧬 Kein vollständiges Modell „{wahl}“ in {memory.EMBED_ORDNER}.")
                return None
            alt = memory.modell_kennung()
            config.update(embedding_modell=ordner.name)
            memory.unload_encoder()
            neu = memory.modell_kennung()
            ui.success(f"🧬 Embedding-Modell: {ordner.name} ({_embedding_art(ordner)}) – lädt bei Bedarf.")
            if alt and neu != alt:
                ui.info("Andere Vektoren als bisher: der Bibliothekar rechnet das Gedächtnis nach und "
                        "nach neu um (/gedaechtnis indexieren = sofort). Bis dahin hilft die Stichwortsuche.")
    elif cmd == "/kontext":
        prov, name = M.split_ref(ctx.model or "")
        wahl = arg.strip().lower().replace(",", ".").replace(" ", "")
        if prov != "gguf" or wahl in ("", "anzeigen"):
            _kontext_anzeige(ctx)
            return None
        import gguflokal
        if wahl == "max":
            token = 0
        else:
            try:
                token = kontext_zahl(wahl)
            except ValueError:
                ui.warn("Kontext als Zahl (32768), mit k (32k) oder „max“ angeben.")
                return None
        gguflokal.setze_kontext(token)
        neu = gguflokal.kontext(name)
        ui.success(f"📏 Kontext: {neu:,} Token".replace(",", ".") +
                   ("  ·  das Modell wird beim nächsten Senden neu geladen." if gguflokal.geladen() else ""))
    elif cmd == "/kontextmenue":
        sub = arg.strip().lower()
        import kontextmenue
        try:
            if sub in ("an", "on", "ein"):
                labels = kontextmenue.installieren()
                ui.success("🖱 Eingetragen: " + "  ·  ".join(labels))
                ui.info("Windows 11 zeigt sie unter „Weitere Optionen anzeigen“ (Shift+F10). "
                        "Ein Klick öffnet NemiCLI und schickt „Schau dir dieses Bild an“ gleich ab.")
            elif sub in ("aus", "off"):
                n = kontextmenue.entfernen()
                ui.info(f"🖱 Rechtsklick-Einträge entfernt ({n} Schlüssel)." if n
                        else "🖱 Es war nichts eingetragen.")
            else:
                st = kontextmenue.status()
                an = st["bildschirm"] and st["bild"]
                ui.info(f"🖱 Rechtsklick-Menü ist {'an' if an else 'aus'}.  "
                        "/kontextmenue an · aus")
        except Exception as e:
            ui.warn(f"Rechtsklick-Menü: {e}")
    elif cmd == "/subagenten":
        sub = arg.strip().lower()
        texte = {"an": "🤝 Helfer an – vor jedem Losschicken wirst du gefragt.",
                 "auto": "🤝 Helfer auto – Helfer nach Bedarf, ohne Frage.",
                 "off": "🤝 Helfer aus – das Werkzeug ist gesperrt."}
        if sub:
            neu = subagents.set_stufe(sub)
            if neu is None:
                ui.warn("Unbekannte Stufe. /subagenten an · auto · off")
            else:
                ui.info(texte[neu] + " (gespeichert)")
        else:
            st = subagents.stufe()
            n = subagents.aktiv()
            laufen = f"  ·  gerade aktiv: {n}" if n else ""
            ui.info(f"{texte[st]}{laufen}  /subagenten an · auto · off")
    elif cmd == "/auto":
        sub = arg.strip().lower()
        if sub in ("an", "on", "aus", "off"):
            an = sub in ("an", "on")
            config.update(auto_stark=an)
            # am laufenden Backend sofort wirksam machen (kein Neustart nötig)
            if ctx.backend is not None and getattr(ctx.backend, "provider_id", "") == "ollama":
                ziel = P.gemma_upgrade_ziel(ctx.backend.model_id) if an else None
                ctx.backend.auto_stark = bool(an and ziel)
                ctx.backend.auto_ziel = ziel
            if not an:
                ui.info("🧠 Auto-Stark aus – es antwortet immer das gewählte Modell.")
            else:
                ziel = (P.gemma_upgrade_ziel(ctx.backend.model_id)
                        if ctx.backend is not None
                        and getattr(ctx.backend, "provider_id", "") == "ollama" else None)
                if ziel:
                    ui.info(f"🧠 Auto-Stark an – bei schweren Fragen übernimmt {ziel}.")
                else:
                    ui.info("🧠 Auto-Stark an (gespeichert). Greift, sobald ein leichtes "
                            "Gemma aktiv ist, zu dem ein größeres installiert ist.")
        else:
            an = bool(config.load().get("auto_stark"))
            aktiv = (ctx.backend is not None
                     and getattr(ctx.backend, "auto_stark", False))
            zusatz = f" · aktiv → {ctx.backend.auto_ziel}" if aktiv else ""
            ui.info(f"🧠 Auto-Stark ist {'an' if an else 'aus'}{zusatz}.  "
                    "Schwere Frage → automatisch großes Gemma.  /auto an · aus")
    elif cmd in PERSONA_CMDS:
        await persoenlichkeiten_menu(arg)
    elif cmd == "/modus":
        sub = arg.strip().lower()
        if not sub:
            zeilen = [(("✓ " if k == modes.current() else "  ") + f"{sym} {n}",
                       {"chat": "Gespräch & Planen – jede Aktion fragt, Merken frei",
                        "lesen": "alles lesen, nichts anrühren – Ändern gesperrt",
                        "normal": "Lesen sofort, Ändern mit Rückfrage (F8)",
                        "auto": f"ohne Rückfragen im aktuellen Ordner · Zünder {modes.AUTO_FUSE_S // 60} min"}[k])
                      for k, sym, n in modes.MODES]
            ui.kv_panel(f"Arbeitsmodus: {modes.label()}   ·   Shift+Tab schaltet weiter", zeilen)
            return None
        key = modes.resolve(sub)
        if key is None:
            ui.warn("Unbekannter Modus. Zur Wahl: chat · lesen · normal · auto · autoan")
            return None
        modes.set_mode(key)
        ui.success(f"Modus: {modes.label()}" + (
            f"  ·  Zünder läuft: nach {modes.AUTO_FUSE_S // 60} min zurück in Chatten"
            if key == "auto" else ""))
    elif cmd == "/statistik":
        if arg.strip().lower() in ("reset", "leeren", "loeschen", "löschen"):
            sicher = await ask_confirm("Wirklich alle gesammelten Zahlen löschen? "
                                       "(die Chats selbst bleiben)",
                                       [("ja", "Ja, zurücksetzen"), ("nein", "Nein")])
            if sicher == "ja":
                stats.reset()
                ui.success("📊 Statistik zurückgesetzt – wir fangen frisch an.")
            return None
        ui.stats_panel(stats.report())
    elif cmd == "/protokoll":
        n = int(arg) if arg.strip().isdigit() else 30
        zeilen = protokoll.letzte(n)
        if not zeilen:
            ui.info(f"📜 Noch keine Einträge. Datei: {protokoll.PFAD}")
        else:
            ui.text_panel(f"📜 Aktions-Protokoll – letzte {len(zeilen)}",
                          "\n".join(zeilen))
            ui.info(f"Datei: {protokoll.PFAD}")
    elif cmd == "/wache":
        await _wache_befehl(ctx, arg.strip())
    elif cmd == "/backup":
        await _backup_befehl(arg.strip())
    elif cmd == "/doku":
        import doku
        teile = arg.lower().split()
        if teile[:1] == ["laden"] and len(teile) > 1 and teile[1] in doku.QUELLEN:
            laden = doku.laden_python if teile[1] == "python" else doku.laden_mdn
            try:
                ergebnis = await _run_with_status(
                    f"📚 lade Doku: {teile[1]}", lambda on_status: laden(melde=on_status))
                ui.success(ergebnis)
            except Exception as e:
                ui.error(f"Doku laden fehlgeschlagen: {e}")
        elif teile[:1] == ["loeschen"] and len(teile) > 1:
            ui.info(doku.loeschen(teile[1]))
        elif teile:
            ui.warn("Nutzung: /doku · /doku laden python|mdn · /doku loeschen python|mdn")
        else:
            ui.info(doku.status_text())
    elif cmd == "/charakter":
        import charakter
        pfad = charakter.datei()
        if arg.strip().lower() in ("oeffnen", "öffnen", "edit"):
            if not pfad.is_file():
                ui.warn(f"Noch keine Charakter-Datei: {pfad}")
                ui.info("Bitte die Persönlichkeit, sie anzulegen – oder lege die JSON selbst an "
                        "(kern, seed_referenz, stil, outfits, posen, regeln).")
            else:
                try:
                    os.startfile(str(pfad))
                except Exception as e:
                    ui.warn(f"Öffnen fehlgeschlagen: {e}")
        else:
            ui.info(charakter.zeigen(charakter.laden()))
            ui.info(f"📄 {pfad}")
    elif cmd == "/kugel":
        await _kugel_befehl(ctx, arg.strip())
    else:
        return await _befehl_ausfuehren_2(ctx, cmd, arg, text)
    return cmd, text
