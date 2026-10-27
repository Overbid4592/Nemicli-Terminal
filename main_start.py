"""main_start.py – Slash-Befehle (zweiter Teil), Wache-Dienst, Start von NemiCLI.

Teil von main.py: läuft im selben Namensraum wie main.py (main._teil_laden) – eine Datei,
nur auf vier verteilt. Namen aus den anderen Teilen sind hier also da, ohne Import."""
# ruff: noqa: F821


_FORTSETZEN_NACH_MODELL = [False]     # beim Start war kein Modell da: mit dem ersten weitermachen


def _gespraech_fortsetzen(ctx: "Ctx") -> dict | None:
    """Wie ein Messenger: Das letzte Gespräch der aktiven Persönlichkeit geht weiter. Geladen wird nur sein
    Ende (pricing.START_TOKENS) – der Rest bleibt in der Datenbank und ist durchsuchbar; beim Speichern
    bleibt er erhalten (chatstore.zusammenfuehren). Gibt den Chat zurück oder None."""
    if ctx.backend is None:
        return None
    zuletzt = chatstore.letzter()
    data = chatstore.load(zuletzt["id"]) if zuletzt else None
    if not data:
        return None
    ctx.backend.messages = pricing.gespraechsende(data.get("messages", []))
    if hasattr(ctx.backend, "stand"):                 # Zusammenfassung des Teils davor
        ctx.backend.stand = data.get("stand") or ""
    ctx.current_prompts = data.get("prompts", [])
    ctx.current_chat = zuletzt["id"]
    chatstore.im_kontext_setzen(ctx.backend.messages)
    plan.verwerfen()
    mitschrift.leeren()
    mitschrift.notiz(f"Gespräch „{zuletzt['title']}“ geht weiter – geladen ist sein Ende, der Anfang ist "
                     "gespeichert und durchsuchbar, sein Denktext nicht.")
    ctx.sync_session()
    return zuletzt


def _start_gruss(ctx: "Ctx") -> None:
    """Die Persönlichkeit schreibt ihren Gruß für die Startseite selbst (eine Einzel-Anfrage, nicht im
    Chatverlauf). Hat der Nutzer schon losgeschrieben, entfällt er; ohne Modell oder bei Fehler ein Satz
    aus ihren eigenen Sprüchen. Config `start_gruss: false` schaltet ihn ab."""
    import startseite
    if not config.load().get("start_gruss", True):
        return

    def ersatz() -> None:
        try:
            if (t := mascot.gruss_text()):
                startseite.setzen(gruss=startseite.gruss_bereinigen(t))
        except Exception:
            pass

    if ctx.backend is None:
        ersatz()
        return

    async def _lauf():
        try:
            async with TURN_LOCK:
                if ui.BANNER_KOMPAKT:
                    return
                system = PS.render_text(PS.active())
                # Funke Groß versteht keine lange Anweisung, deshalb begrüßt er auf ein einfaches Hallo
                frage = "hallo" if getattr(ctx.backend, "provider_id", "") == "funke" else startseite.gruss_prompt()
                text = await asyncio.wait_for(ctx.backend.ask_once(frage, system), 90)
            text = startseite.gruss_bereinigen(text)
            if text:
                startseite.setzen(gruss=text)
            else:
                ersatz()
        except Exception:
            ersatz()
    try:
        asyncio.get_running_loop().create_task(_lauf())
    except RuntimeError:
        pass


async def _befehl_ausfuehren_2(ctx: "Ctx", cmd: str, arg: str, text: str):
    """Die Slash-Befehle, zweiter Teil der Kette (Rückgabe wie _befehl_ausfuehren)."""
    if cmd in ("/undo", "/rueckgaengig", "/papierkorb"):
        eintraege = snapshot.letzte(10)
        if not eintraege:
            ui.info(f"🗑 Papierkorb ist leer. Ordner: {snapshot.ORDNER}")
            return None
        opts = [(str(i), f"{e['zeit']} · {e['werkzeug']} · {e['original']}")
                for i, e in enumerate(eintraege)]
        opts.append(("x", "Nichts wiederherstellen"))
        mb = snapshot.groesse() / 1_048_576
        wahl = await ask_confirm(
            f"🗑 Papierkorb ({mb:.1f} MB) – was soll zurück an seinen Platz?", opts)
        if wahl == "x" or not wahl.isdigit():
            return None
        e = eintraege[int(wahl)]
        if Path(e["original"]).exists():
            sicher = await ask_confirm(
                f"Am Ziel liegt schon etwas: {e['original']}\n  Das wird vorher selbst "
                "in den Papierkorb gesichert. Trotzdem zurückholen?",
                [("ja", "Ja, zurückholen"), ("nein", "Nein")])
            if sicher != "ja":
                return None
        try:
            ziel = snapshot.wiederherstellen(e)
            ui.success(f"↩ Wiederhergestellt: {ziel}")
            protokoll.schreibe("undo", f"Wiederhergestellt: {ziel}", "success",
                               veraendernd=True, wer="Nutzer")
        except Exception as exc:
            ui.error(f"Wiederherstellen fehlgeschlagen: {exc}")
    elif cmd == "/limit":
        if arg.strip().isdigit():
            neu = sicherheit.setze_loesch_limit(int(arg))
            ui.success(f"🛑 Lösch-Limit: {neu} Dateien pro Sitzung (gespeichert).")
            protokoll.schreibe("limit", f"Lösch-Limit auf {neu} gesetzt", "success",
                               veraendernd=True, wer="Nutzer")
        else:
            ui.kv_panel("🛑 Obergrenze fürs Löschen", [
                ("Limit pro Sitzung", f"{sicherheit.loesch_limit()} Dateien"),
                ("Diese Sitzung gelöscht", str(sicherheit.geloescht())),
                ("Ändern", "/limit <zahl>  – nur du, die KI hat kein Werkzeug dafür"),
                ("Wirkung", "mehr als das Limit auf einmal oder in Summe → STOPP, "
                            "auch ohne Rückfrage"),
            ])
    elif cmd == "/whitelist":
        sicherheit.whitelist_neu_laden()
        liste = sicherheit.whitelist()
        if arg.strip():
            w = arg.strip()
            ui.info(f"'{w}' → " + ("harmlos (eine Frage)" if sicherheit.befehl_harmlos(w)
                                   else "NICHT auf der Whitelist (fragt zweimal)"))
        else:
            ui.text_panel(
                f"✅ Befehls-Whitelist ({len(liste)} Einträge) – {sicherheit.WHITELIST_DATEI}",
                "Diese Befehlsanfänge gelten bei `befehl` als harmlos: eine Frage statt zwei.\n"
                "Alles, was nur liest (wie bei `abfragen`), zählt automatisch dazu.\n"
                "Bearbeiten: die JSON-Datei im Programm-Ordner – die KI kommt da nicht ran.\n\n"
                + "\n".join(f"  • {e}" for e in liste))
    elif cmd == "/version":
        ui.kv_panel("NemiCLI", VER.report(model=ctx.model, persona=PS.active().name))
    elif cmd == "/selbsttest":
        zeilen: list[str] = []
        try:
            await _run_with_status("🔧 Selbsttest läuft …",
                                   lambda st: selftest(out=zeilen.append))
        except Exception as e:
            ui.error(f"Selbsttest abgebrochen: {e}")
        ui.text_panel("🔧 Selbsttest", "\n".join(zeilen))
    elif cmd == "/update":
        frage = await update_befehl(ctx)
        if frage:                          # KI soll die gefundenen Lücken einordnen
            text, cmd = frage, None
    elif cmd in ("/code", "/codeend"):
        await code_befehl(ctx, "end" if cmd == "/codeend" else arg)
    elif cmd == "/nemi":
        mascot.bubble()
    elif cmd == "/chrome":
        import chromebridge
        sub = arg.strip().lower()
        if sub == "neu":
            k = chromebridge.key(neu=True)
            if CHROME_BRIDGE is not None:
                CHROME_BRIDGE.key = k
            ui.success("🐈 Neuer Schlüssel erzeugt – in der Erweiterung neu eintragen.")
        k = chromebridge.key()
        laeuft = CHROME_BRIDGE is not None and CHROME_BRIDGE.laeuft
        ordner = paths.INSTALL / "chrome-erweiterung"
        ui.kv_panel("🐈 Chrome-Erweiterung", [
            ("Empfang", ("läuft auf " + CHROME_BRIDGE.adresse) if laeuft else
             ("AUS – " + (CHROME_BRIDGE.letzter_fehler if CHROME_BRIDGE else "nicht gestartet"))),
            ("Schlüssel", k),
            ("Ordner", str(ordner)),
            ("Einrichten", "Chrome → chrome://extensions → Entwicklermodus an → "
                           "„Entpackte Erweiterung laden“ → diesen Ordner wählen"),
            ("Dann", "Erweiterung → Details → Erweiterungsoptionen → Port + Schlüssel eintragen"),
            ("Nutzen", "Rechtsklick auf einer Seite → „Nemi antwortet“ (Cursor im Textfeld) "
                       "oder Text markieren → „Nemi, erklär mir das“"),
            ("Seitenleiste", "🐈-Symbol in Chrome → Chat; 📄 schickt die Seite mit, 📸 ein Bildschirmfoto"),
        ])
    elif cmd in ("/schluessel", "/schlüssel"):
        teile = arg.split(None, 1)
        sub = teile[0].lower() if teile else ""
        if sub in ("aus", "zu", "weg", "ende"):
            sicherheit.schluessel_zurueck()
            ui.info("🔒 Schlüssel abgezogen – der Programm-Ordner ist wieder zu.")
        elif sub.replace(",", ".").replace(".", "", 1).isdigit():
            s = sicherheit.schluessel_setzen(float(sub.replace(",", ".")),
                                             teile[1] if len(teile) > 1 else "")
            bis = time.strftime("%H:%M", time.localtime(s["bis"]))
            ui.success(f"🔑 Schlüssel steckt bis {bis}: {PS.active().name} darf im Programm-Ordner "
                       "lesen und Änderungen vorschlagen – jede Änderung fragt dich einzeln."
                       + (f"  Aufgabe: {s['aufgabe']}" if s["aufgabe"] else ""))
            protokoll.schreibe("schluessel", f"Programm-Ordner bis {bis} freigegeben"
                               + (f" – {s['aufgabe']}" if s["aufgabe"] else ""),
                               "success", veraendernd=True, wer="Nutzer", stufe="riskant")
        else:
            s = sicherheit.schluessel_aktiv()
            if s:
                ui.info(f"🔑 Schlüssel steckt noch {s['rest_min']} min"
                        + (f" – Aufgabe: {s['aufgabe']}" if s["aufgabe"] else "") + ".  /schluessel aus")
            else:
                ui.info("🔒 Programm-Ordner ist zu. /schluessel <minuten> [aufgabe] gibt ihn auf Zeit frei "
                        "(jede Änderung fragt trotzdem), /schluessel aus schließt wieder.")
    elif cmd == "/appstore":
        global APPSTORE_SERVER
        import appstore
        teile = arg.strip().split(None, 1)
        was = teile[0].lower() if teile else ""
        if was == "liste":
            ui.info(appstore.liste_text())
            return None
        if was == "ordner":
            appstore.ORDNER.mkdir(parents=True, exist_ok=True)
            try:
                os.startfile(str(appstore.ORDNER))
            except Exception:
                ui.info(str(appstore.ORDNER))
            return None
        if APPSTORE_SERVER is None:
            try:
                APPSTORE_SERVER, neu = await start_appstore(ctx)
            except Exception as e:
                ui.error(f"Appstore-Start fehlgeschlagen: {e}")
                return None
            if neu:
                ui.info(f"🛍 {neu} Beispiel-App(s) eingerichtet in {appstore.ORDNER}")
        zusatz = ["--speicher", str(appstore.SPEICHER)]
        debug = was in ("debug", "debugai", "bearbeiten", "edit")
        if debug or (was and was not in ("oeffnen", "öffnen")):
            name = teile[1].strip().lower() if debug and len(teile) > 1 else was
            a = appstore.app(name)
            if a is None:
                ui.warn(f"Keine App „{name}“. Vorhanden: {', '.join(appstore.apps()) or 'keine'} · /appstore liste")
                return None
            zusatz += ["--app", a.name] + (["--debug"] if debug else [])
        nemiview_oeffnen(APPSTORE_SERVER, str(appstore.ORDNER), *zusatz)
        ui.success(f"🛍 Appstore in NemiView (eigene Engine) · {len(appstore.apps())} App(s) · Apps-Ordner: "
                   f"{appstore.ORDNER}")
        ui.info("🐞 debugAI in jeder App: sagen, was stört, auf die Stelle tippen – die KI ändert es; "
                "danach vorher → nachher mit Behalten oder Zurück.")
    elif cmd in ("/spiel", "/spiele"):
        global SPIELE_SERVER
        if SPIELE_SERVER is None:
            try:
                SPIELE_SERVER = await start_spiele(ctx)
            except Exception as e:
                ui.error(f"Spiele-Start fehlgeschlagen: {e}")
                return None
            ui.success(f"🎲 Spiele offen: {SPIELE_SERVER.url}")
            ui.info("Schach · Mühle · Dame · TicTacToe – im Browser, gegen "
                    f"{PS.active().name}. Das Terminal bleibt der Motor; /exit beendet beides.")
        else:
            ui.info(f"🎲 Spiele laufen schon: {SPIELE_SERVER.url}")
        try:
            webbrowser.open(SPIELE_SERVER.url)
        except Exception:
            pass
    elif cmd == "/webui":
        global WEB_BRIDGE
        if WEB_BRIDGE is not None:
            ui.info(f"🌐 WebUI läuft schon: {WEB_BRIDGE.url}")
            try:
                webbrowser.open(WEB_BRIDGE.url)
            except Exception:
                pass
        else:
            try:
                WEB_BRIDGE = await start_webui(ctx)
            except Exception as e:
                ui.error(f"WebUI-Start fehlgeschlagen: {e}")
            else:
                ui.success(f"🌐 WebUI offen: {WEB_BRIDGE.url}")
                ui.info("Im Browser bedienbar – das Terminal bleibt verbunden und macht "
                        "die Arbeit. Lesehilfen (Schrift/Größe/Kontrast/Vorlesen) oben rechts. "
                        "/exit beendet beides.")
                try:
                    webbrowser.open(WEB_BRIDGE.url)
                except Exception:
                    pass
    elif cmd == "/reset":
        if ctx.backend:
            ctx.backend.reset()
        ctx.current_prompts = []
        ctx.current_chat = chatstore.next_id()
        plan.verwerfen()
        mitschrift.leeren()
        ui.success("Neuer Chat gestartet.")
    elif cmd == "/venv":
        import wizard
        try:
            ui.info(f"🪟 venv-Fenster offen – torch für diese Grafikkarte:  {wizard.venv_fenster()}")
            ui.info("Nach der Installation /restart tippen – NemiCLI und Wache starten neu.")
        except Exception as e:
            ui.warn(str(e))
    elif cmd == "/theme":
        if not arg:
            ui.theme_menu()
        elif ui.set_theme(arg):
            config.update(theme=arg)
            if ui.TUI is not None:
                ui.TUI.clear()
            else:
                ui.console.clear()
            ui.banner(animate=False)
            ui.welcome(model=ctx.model or "—", mode="Chat",
                       strength=ctx.strength if ctx.strength_active() else None)
        else:
            ui.warn(f"Theme '{arg}' gibt es nicht. Verfügbar: {', '.join(ui.THEMES)}")
    elif cmd == "/model":
        ref = None
        if not arg:
            ref = await pick_model(ctx.model)
        else:
            tokens = arg.split()
            head = tokens[0].lower()
            if head in ("huggingface", "hf"):
                ref = await hf_downloader()
            elif head == "ersatz":
                await ersatzbank_waehlen(ctx)
            elif ":" in arg:                     # direkte Referenz: anbieter:modell
                ref = arg.strip()
            elif len(tokens) > 1 and P.get(head):   # "openai gpt-4o"
                ref = M.make_ref(head, tokens[1])
            elif P.get(head):
                if not P.get(head).available():
                    prov = P.get(head)
                    ui.warn(f"Für {prov.label} ist noch kein Key gesetzt.")
                    ui.info("Tippe /model (ohne Text) und wähle "
                            "'Cloud-Anbieter hinzufügen', um den Key direkt einzugeben.")
                    picked = "__cancel__"
                else:
                    try:
                        with ui.thinking(f"frage {P.get(head).label}"):
                            ids = await P.list_models(head)
                        opts = [(M.make_ref(head, m), m) for m in ids]
                        opts.append(("__cancel__", "Abbrechen"))
                        picked = await ask_confirm(f"Welches Modell von {P.get(head).label}?", opts)
                    except Exception as e:
                        ui.error(str(e)); picked = "__cancel__"
                ref = None if picked == "__cancel__" else picked
            else:
                ui.warn(f"Unbekannt: '{arg}'. Tippe /model (ohne Text) für die Auswahl.")
        if ref:
            ctx.backend, ctx.model = await switch_model(ref, ctx.backend, ctx.model, ctx.strength)
            if _FORTSETZEN_NACH_MODELL[0] and ctx.backend is not None:
                _FORTSETZEN_NACH_MODELL[0] = False
                if not ctx.current_prompts and (weiter := _gespraech_fortsetzen(ctx)):
                    ui.info(f"📒 Euer Gespräch geht weiter: „{weiter['title']}“")
            _kontext_neu_zaehlen(ctx)
    elif cmd == "/bild" and re.fullmatch(r"(?i)(schritte|steps)(\s+\d+)?", arg.strip()):
        import krea
        zahl = arg.split()[1] if len(arg.split()) > 1 else None
        if zahl is None:
            akt = krea.schritte()
            opts = [(str(n), ("✓  " if n == akt else "   ") + f"{n} Schritte"
                     + {krea.SCHRITTE_MIN: "  · schnell", krea.DEFAULTS["steps"]: "  · Standard",
                        krea.SCHRITTE_MAX: "  · am genauesten"}.get(n, ""))
                    for n in range(krea.SCHRITTE_MIN, krea.SCHRITTE_MAX + 1)]
            opts.append(("__cancel__", "Abbrechen"))
            zahl = await ask_confirm(
                f"Wie viele Schritte soll Krea 2 malen? ({krea.SCHRITTE_MIN} bis max. "
                f"{krea.SCHRITTE_MAX} – mehr Schritte = genauer, aber langsamer)", opts)
            if zahl == "__cancel__":
                return None
        neu = krea.set_schritte(int(zahl))
        ui.success(f"🟣 Krea 2 malt jetzt mit {neu} Schritten – dauerhaft gespeichert.")
        if int(zahl) != neu:
            ui.info(f"Erlaubt sind {krea.SCHRITTE_MIN} bis {krea.SCHRITTE_MAX} Schritte.")
    elif cmd == "/bild":
        import imagegen
        reason = imagegen.missing_reason_aktiv()
        if reason:
            ui.warn("Der Bild-Motor kann gerade nicht malen.")
            ui.info(reason)
            return None
        import sdwebui
        akt_backend = imagegen.backend()
        opts = imagegen.parse_opts(arg)
        if not opts["prompt"]:                   # ohne Prompt: Status + Hilfe
            ui.info(f"🎨 Aktiver Bild-Motor: {imagegen.active_label()}")
            if akt_backend == "webui":
                ui.info(f"🌐 WebUI ({sdwebui.host()}) – Vorgabe: fester Negativ-Prompt, CFG 1, "
                        "832×1216, 14 Schritte. /bildmodel wechselt das Modell.")
            elif akt_backend == "krea":
                import krea
                ui.info(f"🟣 Krea 2 – kein Negativ-Prompt, CFG 1, {krea.schritte()} Schritte "
                        f"(/bild schritte ändert das). Modelle in {krea.krea_dir()}. "
                        "/bildmodel wechselt das Modell.")
            ui.info('Nutzung:  /bild <beschreibung>  [--steps 8] [--size 1024x1024] [--seed 123] '
                    '[--model name] [--ohne-gesicht]  ·  bei WebUI/ComfyUI auch [--neg "..."] [--cfg 7]')
            return None
        if not _grafikkarte_frei():
            return None
        modell, hinweis = imagegen.modell_aufloesen(opts.get("model"))
        if hinweis:
            ui.info(hinweis.strip(" ()") + ".")
        try:
            path = await _run_with_status(
                "🎨 male dein Bild",
                lambda on_status: imagegen.paint(
                    opts["prompt"], model=modell, neg=opts.get("neg"),
                    steps=opts.get("steps"), cfg=opts.get("cfg"),
                    size=opts.get("size"), seed=opts.get("seed"),
                    sampler=opts.get("sampler"), on_status=on_status))
            ui.success(f"🖼 Fertig: {path}")
            if opts.get("gesicht", True):
                try:
                    besser = await _run_with_status(
                        "✨ male das Gesicht nach",
                        lambda on_status: imagegen.gesicht_nachbessern(path, on_status=on_status))
                    if besser:
                        path = besser
                        ui.success(f"✨ Gesicht nachgebessert: {besser}")
                except Exception as e:
                    ui.warn(f"Gesicht nachbessern übersprungen ({e}).")
            _bild_vorschau(str(path))
            try:
                os.startfile(path)            # fertiges Bild anzeigen (Windows)
            except Exception:
                pass
        except Exception as e:
            ui.error(f"Bild fehlgeschlagen: {e}")
    elif cmd == "/video":
        import video
        import videobildcloud as VC
        if re.fullmatch(r"(?i)cloud(\s+\S+)?", arg.strip()):
            wahl = arg.split(None, 1)[1].strip().lower() if len(arg.split(None, 1)) > 1 else ""
            if not wahl:
                ui.info(f"🎬 Videos: {VC.label('video') if VC.video_aktiv() else 'lokal (Wan 2.2)'} · mit Schlüssel: "
                        + (", ".join(VC.VIDEO[a]["titel"] for a in VC.bereit("video")) or "keiner"))
                ui.info(f"Wählen: /video cloud {'|'.join(VC.VIDEO)}|aus · neuer Anbieter: /video cloud neu")
                return None
            if wahl in ("neu", "hinzufuegen", "hinzufügen"):
                await cloud_anbieter_hinzufuegen("video")
                return None
            try:
                ui.success(f"🎬 Videos jetzt: {VC.waehlen('video', None if wahl in ('aus', 'lokal') else wahl)}")
            except ValueError as e:
                ui.warn(str(e))
            return None
        if VC.video_aktiv() and not re.fullmatch(r"(?i)(modell(\s+\S.*)?|ordner|oeffnen|öffnen)", arg.strip()):
            opts = _video_opts(arg)
            if not opts["prompt"]:
                ui.info(f"🎬 Videos aus der Cloud: {VC.label('video')} · zurück auf lokal: /video cloud aus")
                return None
            try:
                path = await _run_with_status("🎬 erzeuge dein Video (Cloud)", lambda on_status: VC.video_generate(
                    opts["prompt"], sekunden=opts.get("sekunden", 5.0), startbild=opts.get("start"), on_status=on_status))
                ui.success(f"🎬 Fertig: {path}")
                try:
                    os.startfile(path)
                except Exception:
                    pass
            except Exception as e:
                ui.error(f"Video fehlgeschlagen: {e}")
            return None
        if re.fullmatch(r"(?i)modell(\s+\S.*)?", arg.strip()):
            alle = video.modelle()
            name = arg.split(None, 1)[1].strip() if len(arg.split(None, 1)) > 1 else ""
            if name:
                try:
                    video.set_modell(name)
                    ui.success(f"🎬 Video-Modell: {name} – dauerhaft gespeichert.")
                except ValueError as e:
                    ui.warn(str(e))
            else:
                akt = video.gewaehlt()
                for n, m in alle.items():
                    try:
                        fam = video.familie(video.dit_config(m))
                        f = video.FAMILIEN[fam]
                        art = f"{fam} · {f['size'][0]}×{f['size'][1]} · {f['fps']} Bilder/s"
                    except Exception as e:
                        art = f"nicht lesbar: {e}"
                    ui.info(f"{'✓ ' if n == akt else '  '}{n}  ({'GGUF' if m['gguf'] else 'safetensors'}, {art})")
                ui.info("Wählen: /video modell <name>")
            return None
        if arg.strip().lower() in ("ordner", "oeffnen", "öffnen"):
            try:
                ziel = video.video_dir()
                ziel.mkdir(parents=True, exist_ok=True)
                os.startfile(str(ziel))
            except Exception as e:
                ui.warn(f"Konnte den Video-Ordner nicht öffnen: {e}")
            return None
        if (grund := video.missing_reason()):
            ui.warn("Der Video-Motor kann nicht starten.")
            ui.info(grund)
            return None
        opts = _video_opts(arg)
        if not opts["prompt"]:
            t = video.teile()
            fam = video.FAMILIEN[video.familie(video.dit_config(t))]
            W, H = fam["size"]
            ram = f" · im RAM: {', '.join(video.im_ram())}" if video.im_ram() else ""
            ui.info(f"🎬 Video-Motor bereit – Modell {t['name']}, {W}×{H}, {fam['fps']} Bilder/s, "
                    f"Clips à 5 s bis {video.MAX_SEKUNDEN} s. Ordner: {video.video_dir()}{ram}")
            s = video.lora_stand()
            ui.info(f"🧬 Charakter-LoRA von {PS.active().name}: "
                    + ("noch keine" if not s["da"] else "fertig, aber ausgeschaltet (video_lora)" if not s["an"]
                       else f"fertig, passt aber nicht zum gewählten Modell ({s['familie']} ≠ {s['modell_familie']})"
                       if not s["passt"] else "fertig – kommt in jedes Video ohne Schlüsselbilder"))
            ui.info('Nutzung:  /video <beschreibung (englisch)>  [--sekunden 10] [--start bild.png] '
                    '[--seed 123] [--steps 30]  ·  /video modell [name]')
            return None
        if not _grafikkarte_frei():
            return None
        try:
            import imagegen
            path = await _run_with_status(
                "🎬 erzeuge dein Video",
                lambda on_status: imagegen._gpu_fuer_bild(video.generate)(
                    opts["prompt"], sekunden=opts.get("sekunden", 5.0), startbild=opts.get("start"),
                    seed=opts.get("seed"), steps=opts.get("steps"), schaerfe=opts.get("schaerfe"),
                    on_status=on_status))
            ui.success(f"🎬 Fertig: {path}")
            if video.GENUTZT:
                ui.info("🧬 Eingemischt: " + " · ".join(video.GENUTZT))
            for h in video.HINWEISE:
                ui.warn(h)
            try:
                os.startfile(path)
            except Exception:
                pass
        except Exception as e:
            ui.error(f"Video fehlgeschlagen: {e}")
    elif cmd == "/bildmodel":
        import imagegen, sdwebui, comfyui
        akt_backend = imagegen.backend()
        webui_da = sdwebui.available()
        webui_models = sdwebui.models() if webui_da else {}
        webui_aktiv = sdwebui.chosen_model() if akt_backend == "webui" else None
        comfy_da = comfyui.available()
        comfy_models = comfyui.models() if comfy_da else {}
        comfy_aktiv = comfyui.chosen_model() if akt_backend == "comfy" else None
        import krea
        krea_models = krea.discover()
        krea_aktiv = krea.chosen_model() if akt_backend == "krea" else None
        if arg:                                  # direkt gesetzt: /bildmodel <name>
            teile = arg.strip().lower().split()
            if teile and teile[0] == "cloud":
                import videobildcloud as VC
                if len(teile) > 1 and teile[1] in ("neu", "hinzufuegen", "hinzufügen"):
                    await cloud_anbieter_hinzufuegen("bild")
                    return None
                try:
                    if len(teile) == 1:
                        ui.info(f"☁ Bilder-Cloud mit Schlüssel: "
                                + (", ".join(VC.BILD[a]["titel"] for a in VC.bereit("bild")) or "keiner")
                                + f"  ·  /bildmodel cloud {'|'.join(VC.BILD)}|aus · /bildmodel cloud neu")
                    else:
                        ui.success(f"🖼 Bilder jetzt: {VC.waehlen('bild', None if teile[1] in ('aus', 'lokal') else teile[1])}")
                except ValueError as e:
                    ui.warn(str(e))
                return None
            if arg.strip().lower() in ("comfy-adresse", "comfyui-adresse", "comfy"):
                neu_adr = await ask_text(f"ComfyUI-Adresse (jetzt: {comfyui.host()}):")
                if neu_adr:
                    comfyui.set_host(neu_adr)
                    ui.success(f"🧩 ComfyUI-Adresse: {comfyui.host()}  "
                               f"({'erreichbar' if comfyui.available() else 'nicht erreichbar'})")
                return None
            if arg.strip().lower() in ("webui-adresse", "host", "url"):
                neu = await ask_text(f"WebUI-Adresse (jetzt: {sdwebui.host()}):")
                if neu:
                    sdwebui.set_host(neu)
                    ui.success(f"🌐 WebUI-Adresse: {sdwebui.host()}  "
                               f"({'erreichbar' if sdwebui.available() else 'nicht erreichbar'})")
                return None
            treffer = [n for n in krea_models if arg.strip().lower() in n.lower()]
            if len(treffer) == 1:
                krea.set_chosen_model(treffer[0])
                ui.success(f"🟣 Bild-Modell (Krea 2): {treffer[0]}")
                return None
            treffer = [n for n in webui_models if arg.strip().lower() in n.lower()]
            if len(treffer) == 1:
                sdwebui.set_chosen_model(treffer[0])
                config.update(bild_backend="webui")
                ui.success(f"🌐 Bild-Modell (WebUI): {treffer[0]}")
                return None
            treffer = [n for n in comfy_models if arg.strip().lower() in n.lower()]
            if len(treffer) == 1:
                comfyui.set_chosen_model(treffer[0])
                config.update(bild_backend="comfy")
                ui.success(f"🧩 Bild-Modell (ComfyUI): {treffer[0]}")
                return None
            ui.warn(f"Kein Modell gefunden, das zu '{arg}' passt (weder Krea 2 noch WebUI noch ComfyUI).")
            return None
        opts = []
        for name in krea_models:
            mark = "✓  " if (akt_backend == "krea" and name == krea_aktiv) else "   "
            opts.append((f"krea::{name}", f"{mark}🟣 {name}  (Krea 2)"))
        for name, title in webui_models.items():
            mark = "✓  " if (akt_backend == "webui" and name == webui_aktiv) else "   "
            opts.append((f"webui::{name}", f"{mark}🌐 {name}  (WebUI)"))
        for name, art in comfy_models.items():
            mark = "✓  " if (akt_backend == "comfy" and name == comfy_aktiv) else "   "
            # Getrennte Modelle (Krea/Qwen/Flux) sind KEINE Checkpoints –
            # dazu gehören CLIP und VAE als eigene Dateien.
            zusatz = "ComfyUI · geteilt" if art == "diffusion" else "ComfyUI"
            opts.append((f"comfy::{name}", f"{mark}🧩 {name}  ({zusatz})"))
        import videobildcloud as VC
        wolke = VC.gewaehlt("bild") if akt_backend == "cloud" else None
        for a in VC.bereit("bild"):
            mark = "✓  " if a == wolke else "   "
            opts.append((f"cloud::{a}", f"{mark}☁ {VC.BILD[a]['titel']} · {VC.modell(a)}  (Cloud)"))
        if VC.ohne_schluessel("bild"):
            opts.append(("__cloudneu__", f"☁  Cloud-Anbieter hinzufügen  ·  Key eintragen "
                                         f"({len(VC.ohne_schluessel('bild'))})"))
        opts.append(("__ordner__", "📂  Krea-2-Ordner öffnen (Modelldateien ablegen)"))
        opts.append(("__host__", f"🌐  WebUI-Adresse ändern  ({sdwebui.host()})"))
        opts.append(("__chost__", f"🧩  ComfyUI-Adresse ändern  ({comfyui.host()})"
                                  + ("" if comfy_da else "  – nicht erreichbar")))
        opts.append(("__cancel__", "Abbrechen"))
        if comfy_da and not comfy_models:
            i = comfyui.info()
            ui.warn(f"🧩 ComfyUI {i['version']} läuft ({i['gpu']}, {i['vram_gb']} GB), "
                    "kennt aber keinen Checkpoint – leg eins in ComfyUIs models/checkpoints.")
        if not krea_models and not webui_models and not comfy_models:
            ui.warn(f"Noch kein Bild-Modell da. Krea-2-Dateien gehören nach:  {krea.krea_dir()}")
            ui.info("… oder eine Forge/A1111-WebUI mit --api starten "
                    f"(erwartet unter {sdwebui.host()}).")
        wahl = await ask_suche("Welches Modell zum Bildermalen?  "
                                 "(🟣 Krea 2 · 🌐 Forge/A1111 · 🧩 ComfyUI)", opts)
        if wahl == "__cancel__":
            return None
        if wahl == "__cloudneu__":
            await cloud_anbieter_hinzufuegen("bild")
            return None
        if wahl == "__ordner__":
            krea.krea_dir().mkdir(parents=True, exist_ok=True)
            try:
                os.startfile(krea.krea_dir())
            except Exception:
                ui.info(str(krea.krea_dir()))
            ui.info("Dateien reinlegen, dann nochmal /bildmodel – sie tauchen automatisch auf.")
            return None
        if wahl == "__host__":
            neu = await ask_text(f"WebUI-Adresse (jetzt: {sdwebui.host()}):")
            if neu:
                sdwebui.set_host(neu)
                ui.success(f"🌐 WebUI-Adresse: {sdwebui.host()}  "
                           f"({'erreichbar' if sdwebui.available() else 'nicht erreichbar'})")
            return None
        if wahl == "__chost__":
            neu = await ask_text(f"ComfyUI-Adresse (jetzt: {comfyui.host()}):")
            if neu:
                comfyui.set_host(neu)
                ui.success(f"🧩 ComfyUI-Adresse: {comfyui.host()}  "
                           f"({'erreichbar' if comfyui.available() else 'nicht erreichbar'})")
            return None
        art, _, name = wahl.partition("::")
        if art == "cloud":
            try:
                ui.success(f"☁ Bilder jetzt aus der Cloud: {VC.waehlen('bild', name)}  ·  zurück: ein Krea-Modell wählen")
            except ValueError as e:
                ui.warn(str(e))
        elif art == "krea":
            krea.set_chosen_model(name)
            ui.success(f"🟣 Bild-Modell (Krea 2): {name}  ·  kein Negativ-Prompt, CFG 1, "
                       f"{krea.schritte()} Schritte, "
                       f"{krea.DEFAULTS['size'][0]}×{krea.DEFAULTS['size'][1]}")
        elif art == "comfy":
            comfyui.set_chosen_model(name)
            config.update(bild_backend="comfy")
            i = comfyui.info()
            ui.success(f"🧩 Bild-Modell (ComfyUI): {name}")
            ui.info(f"ComfyUI {i['version']} · {i['gpu']} · "
                    f"{comfyui.DEFAULTS['steps']} Schritte, CFG "
                    f"{comfyui.DEFAULTS['cfg']}, "
                    f"{comfyui.DEFAULTS['size'][0]}×{comfyui.DEFAULTS['size'][1]}  "
                    "(per bild_comfy_* in der Config änderbar)")
        elif art == "webui":
            sdwebui.set_chosen_model(name)
            config.update(bild_backend="webui")
            ui.success(f"🌐 Bild-Modell (WebUI): {name}  ·  fester Negativ-Prompt, "
                       "CFG 1, 832×1216, 14 Schritte")
    elif cmd == "/staerke":
        options = M.strength_menu(ctx.model)
        if not ctx.strength_active() or not options:
            ui.info("Für das aktive Modell ist noch keine Denksteuerung angebunden. "
                    "Seine vorhandene Denk-Ausgabe wird weiterhin angezeigt.")
            return None
        if not arg:
            ui.strength_menu(options, current=ctx.strength)
            ui.info(M.strength_note(ctx.model, ctx.strength))
        elif arg in options:
            ctx.strength = arg
            config.update(strength=arg)
            ctx.backend.strength = arg
            ctx.sync_session()
            ui.success(f"Denken: {arg}")
            ui.info(M.strength_note(ctx.model, arg))
        else:
            ui.warn(f"Unbekannte Stärke. Optionen: {', '.join(options)}")
    else:
        # Unbekannt: ähnliche Befehle zeigen; ohne Treffer fragt die KI in der Befehlsliste nach.
        import commands as C
        treffer = [n for n, _ in C.find_commands(cmd)[:5]]
        if treffer:
            ui.info(f"{cmd} gibt es nicht. Meintest du: " + " · ".join(treffer))
        elif ctx.backend is None:
            ui.warn(f"Unbekannter Befehl: {cmd}. Tippe /help.")
        else:
            suche = f"{cmd[1:]} {arg}".strip()
            text = (f"Ich suche in NemiCLI: „{suche}“. Welcher Befehl oder welche Taste passt? "
                    "Schlag in anleitung_lesen mit thema \"nemicli\" nach.")
            cmd = None
    return cmd, text


async def _antwort_einfuegen(ctx: "Ctx", hwnd: int, n_vorher: int) -> None:
    """Letzte Assistenten-Antwort dieser Runde säubern und ins Zielfenster tippen
    (hwnd 0: nur Zwischenablage)."""
    import kontextmenue
    if ctx.backend is None:
        return
    neu = ctx.backend.messages[n_vorher:]
    antworten = [m.get("content") for m in neu
                 if m.get("role") == "assistant" and isinstance(m.get("content"), str)]
    if not antworten:
        return
    text = kontextmenue.antwort_text(antworten[-1])
    if not text:
        return
    if hwnd:
        ok = await asyncio.to_thread(kontextmenue.einfuegen, hwnd, text)
        titel = kontextmenue.fenster_titel(hwnd) if ok else ""
        if ok:
            ui.success(f"✍ Antwort eingefügt in: {titel[:60] or 'Zielfenster'}")
            return
    kontextmenue.in_zwischenablage(text)
    ui.info("📋 Antwort in der Zwischenablage (Zielfenster nicht erreichbar) – Strg+V zum Einfügen.")


def _start_nachricht() -> str | None:
    """`nemicli --sag "…"`: Text, der beim Start als erste Nachricht abgeschickt wird."""
    if "--sag" not in sys.argv:
        return None
    i = sys.argv.index("--sag")
    text = " ".join(sys.argv[i + 1:]).strip()
    return text or None


# ---------------------------------------------------------------------------
# Hintergrund-Auftrag (Zeitplan): `main.py --auftrag <name>`
# ---------------------------------------------------------------------------
# Die Windows-Aufgabenplanung startet NemiCLI so – ohne Fenster, ohne Nutzer.
# Die Persönlichkeit bekommt den Auftrag aus Zeitplan/<name>.json als Nachricht,
# arbeitet ihn im Modus „lesen" ab (abfragen, datei_lesen, web_suche … laufen;
# alles Ändernde ist gesperrt, da drückt niemand F8) und ihre letzte Antwort
# wird als Bericht nach Berichte/ geschrieben. Beim nächsten normalen Start
# zeigt main() die erste Zeile jedes neuen Berichts.

async def _nie_freigeben(*_a, **_k):
    """Im Hintergrund gibt es keine Freigabe – jede Rückfrage ist ein Nein."""
    return "no"


async def _nie_pruefen(*_a, **_k):
    return False


async def auftrag_lauf(name: str) -> int:
    global ask_confirm, review_action_confirm
    import zeitplan
    auftrag = zeitplan.lade_auftrag(name)
    if not auftrag:
        print(f"Kein Auftrag namens {name!r} in {zeitplan.ORDNER}.")
        return 1
    name = auftrag["name"]
    ask_confirm = _nie_freigeben
    review_action_confirm = _nie_pruefen
    modes.set_mode("lesen")

    cfg = config.load()
    ref = _migrate_ref(cfg.get("model"))
    backend = None
    fehler = ""
    if ref:
        try:
            backend = await make_backend(ref, None, cfg.get("strength", M.DEFAULT_STRENGTH))
        except Exception as e:
            fehler = f"Modell '{ref}' ließ sich nicht laden: {e}"
    else:
        fehler = "Kein Modell eingestellt (/model)."

    mitschrift.leeren()
    mitschrift.setze_quelle(lambda: {"chat": f"Auftrag {name}", "model": ref or "-",
                                     "persona": PS.active().name, "modus": modes.label(),
                                     "ordner": str(Path.cwd())})
    stats.start_session(ref)
    antwort = ""
    if backend is not None:
        text = (f"[Hintergrund-Auftrag „{name}“ aus deinem Zeitplan, "
                f"{time.strftime('%d.%m.%Y %H:%M')}. Es ist niemand da, der antwortet oder "
                "etwas freigibt: Du kannst nur lesen und abfragen. Deine LETZTE Antwort wird "
                "als Bericht gespeichert – fang sie mit einer klaren Zeile an (✅ Alles OK / "
                "⚠️ n Auffälligkeiten) und schreib dann, was du gesehen hast.]\n\n"
                + auftrag["auftrag"])
        try:
            await asyncio.wait_for(converse(backend, text), timeout=25 * 60)
        except asyncio.TimeoutError:
            fehler = "Abgebrochen: der Auftrag lief länger als 25 Minuten."
        except Exception as e:
            fehler = f"Abgebrochen mit Fehler: {e}"
        antwort = mitschrift.letzte_antwort()
    stats.end_session()

    if not antwort:
        antwort = "❌ Kein Bericht – " + (fehler or "die Persönlichkeit hat nichts geantwortet.")
    elif fehler:
        antwort = f"⚠️ {fehler}\n\n{antwort}"
    pfad = zeitplan.bericht_pfad(name)
    pfad.write_text(
        f"<!-- NemiCLI-Bericht: {name} · {time.strftime('%d.%m.%Y %H:%M')} · {ref or '-'} -->\n\n"
        + antwort.strip() + "\n\n---\n\n## Ablauf\n\n" + mitschrift.als_markdown(),
        encoding="utf-8")
    print(f"Bericht: {pfad}")
    return 0 if backend is not None and not fehler else 1


# ---------------------------------------------------------------------------
# Systemwache im Hintergrund: `main.py --wache`
# ---------------------------------------------------------------------------
# Sensoren, Regeln und Modell laufen in tools/wache; hier steht nur, wie die
# aktive Persönlichkeit geweckt wird: derselbe Weg wie ein Zeitplan-Auftrag
# (Modus lesen, keine Freigaben), plus die Wache-Werkzeuge zum Bewerten und
# Justieren. Die letzte Antwort wird der Bericht in Berichte/Wache_….md.

async def _wache_befehl(ctx: "Ctx", arg: str) -> None:
    from wache import zugang as Z, justierung as J, werkzeuge as W
    teile = arg.split(None, 1)
    sub = (teile[0].lower() if teile else "")
    rest = teile[1].strip() if len(teile) > 1 else ""
    e = J.einstellungen()
    if sub in ("", "status"):
        info = Z.laeuft()
        sp = Z.speicher()
        st = sp.alarm_statistik()
        inv = sp.inventar_zaehlung()
        ui.kv_panel("🛡 Systemwache", [
            ("Hintergrund-Prozess", f"läuft (PID {info['pid']}){Z.still_hinweis(info)}" if info
             else "läuft NICHT – /wache start"),
            ("Autostart mit NemiCLI", "an" if e.get("aktiv", True) else "aus"),
            ("Autostart mit Windows", "an" if Z.autostart_aktiv() else "aus"),
            ("Ereignisse gespeichert", str(sp.anzahl_ereignisse())),
            ("Prozess-Profile", str(sp.anzahl_profile())),
            ("Inventar", ", ".join(f"{n} {k}" for k, n in sorted(inv.items())) or "noch keins"),
            ("Alarme", f"{st.get('offen', 0)} offen · {st.get('gesehen', 0)} gesehen · "
                       f"{st.get('harmlos', 0)} harmlos · {st.get('unpruefbar', 0)} nicht voll prüfbar · "
                       f"{st.get('echt', 0)} ECHT · {st.get('erledigt', 0)} erledigt"),
            ("ML-Schwelle", f"{e['schwelle']}  (Grenzen {e['grenzen']['schwelle_min']}–{e['grenzen']['schwelle_max']})"),
            ("Training", f"erstes ab {e['min_training']} Ereignissen, dann alle {e['training_alle']}; "
                         f"lernt nur aus Ereignissen älter als {e.get('ml_karenz_h', 24)} h"),
            ("Wecken ab", f"{e['trigger_ab']}" + (" + ML-Anomalien" if e.get("trigger_ml") else "")),
            ("Prüfen ab", f"{J.pruefen_ab(e)} ungeprüften Alarmen (jede Stufe; {J.PRUEFEN_MIN}–{J.PRUEFEN_MAX})"),
            ("Stumme Regeln", ", ".join(e.get("stumme_regeln") or []) or "keine"),
            ("Netzwächter", "aus" if not e.get("netz", True) else
             "warnt (ML-002/DL-002)" if e.get("netz_alarm") else "Schattenmodus – nur Protokoll (/wache netz)"),
            ("Folgen-Netz", "aus" if not e.get("dl", True) else
             "warnt (DL-001)" if e.get("dl_alarm") else "Schattenmodus – nur Protokoll (/wache dl)"),
            ("Daten", str(Z.DB_PFAD)),
        ])
        ui.info("/wache alarme · ereignisse · regeln · justierungen · inventar · training · fullscan · "
                "schwelle <x> · trigger <stufe|5–20> · stumm <R00x> · laut <R00x> · start · stop · "
                "an · aus · autostart an|aus · kugel an|aus · dl · netz [adresse] · ml zurueck · erledigt <id> · reset · "
                "rueckgaengig")
    elif sub == "start":
        ui.info(Z.starten(paths.INSTALL))
    elif sub == "stop":
        ui.info(Z.stoppen())
    elif sub == "an":
        if J.speichern({"aktiv": True}):
            ui.success("Wache startet künftig automatisch mit NemiCLI.")
        else:
            ui.warn("Nicht gespeichert – die Wache startet weiter nicht automatisch.")
        if not Z.laeuft():
            ui.info(Z.starten(paths.INSTALL))
    elif sub == "aus":
        gespeichert = J.speichern({"aktiv": False})
        ui.info(Z.stoppen())
        if gespeichert:
            ui.info("Wache startet nicht mehr automatisch.")
        else:
            ui.warn("Nicht gespeichert – beim nächsten NemiCLI-Start läuft die Wache wieder an.")
    elif sub == "autostart":
        an = rest.lower() in ("an", "on", "ein", "ja")
        ui.info(Z.autostart_setzen(an, paths.INSTALL))
    elif sub == "kugel":
        if rest.lower() in ("an", "aus", "on", "off"):
            an = rest.lower() in ("an", "on")
            if J.speichern({"kugel": an}):
                ui.success(f"Schwebekugel {'an' if an else 'aus'} – gilt, sobald kein Terminal offen ist"
                           + ("" if an else "; das Schild neben der Uhr bleibt") + ".")
            else:
                ui.warn("Nicht gespeichert – die Schwebekugel bleibt, wie sie war.")
        else:
            from wache import kugel as K
            ui.kv_panel("🟢 Schwebekugel", [
                ("Zustand", "an" if e.get("kugel", True) else "aus"),
                ("Sichtbar", "nur wenn kein NemiCLI-Terminal offen ist"),
                ("Grüße", f"{K.GRUESSE_DATEI} – die Persönlichkeit darf sie selbst schreiben"),
                ("Klick", "kleines Chatfenster: reden · 📸 Screenshot · Bilder · Alles ok?"),
                ("Ändern", "/wache kugel an|aus"),
            ])
    elif sub == "erledigt":
        if not rest:
            ui.warn("Welcher Alarm? /wache erledigt <id> – die Nummer steht bei /wache alarme.")
        else:
            ui.info(W.bewerten({"id": rest, "urteil": "erledigt",
                                "begruendung": "Vom Nutzer als behoben abgeschlossen."}, wer="Nutzer"))
    elif sub == "alarme":
        ui.text_panel("🛡 Alarme", W.alarme({"anzahl": rest or 30}) if not rest or rest.isdigit()
                      else W.alarme({"id": rest}))
    elif sub == "ereignisse":
        ui.text_panel("🛡 Ereignisse", W.ereignisse({"anzahl": 40, "prozess": rest}))
    elif sub == "inventar":
        ui.text_panel("🛡 Inventar", W.inventar({"art": rest}))
    elif sub == "regeln":
        ui.text_panel("🛡 Regeln", W.regeln_text())
    elif sub in ("bekannt", "bekanntes"):
        if rest.lower().startswith("vergessen "):
            key = rest[10:].strip().lower()
            key = key if ":" in key else "prozess:" + key
            ui.info("Vergessen." if Z.speicher().bekannt_vergessen(key) else f"Nichts unter {key}.")
        else:
            ui.text_panel("🛡 Bekannt (harmlos, mit Bezeichnung)", W.bekannt_text(Z.speicher(), rest.lower()))
    elif sub == "justierungen":
        js = Z.speicher().justierungen(20)
        ui.text_panel("🛡 Justierungen", "\n".join(
            f"{time.strftime('%d.%m. %H:%M', time.localtime(j['zeit']))} · {j['wer']}: {j['was']} "
            f"{j['alt']} → {j['neu']} – {j['begruendung']}" for j in js) or "Noch keine.")
    elif sub == "rueckgaengig":
        ui.info(J.letzte_rueckgaengig(Z.speicher(), Z.motor))
    elif sub == "trigger" and rest.strip().isdigit():
        try:
            ui.success(J.pruefen_ab_setzen(rest, speicher=Z.speicher(), motor=Z.motor))
        except J.Grenzverletzung as exc:
            ui.warn(str(exc))
    elif sub in ("schwelle", "trigger", "cooldown"):
        try:
            ui.success(J.anwenden(sub if sub != "trigger" else "trigger_ab", rest, wer="Nutzer",
                                  begruendung="vom Nutzer über /wache gesetzt", speicher=Z.speicher(),
                                  motor=Z.motor, nutzer=True))
        except J.Grenzverletzung as exc:
            ui.warn(str(exc))
    elif sub in ("stumm", "laut"):
        try:
            ui.success(J.anwenden("regel_stumm", f"{rest}={'aus' if sub == 'stumm' else 'an'}",
                                  wer="Nutzer", begruendung="vom Nutzer über /wache gesetzt",
                                  speicher=Z.speicher(), motor=Z.motor, nutzer=True))
        except J.Grenzverletzung as exc:
            ui.warn(str(exc))
    elif sub == "ml" and rest.lower() in ("zurueck", "zurück"):
        if Z.motor is not None:
            ui.info(Z.motor.ml_zurueckdrehen())
        else:
            from wache import MODELL_ORDNER, detektor as DT
            ui.info("\n".join(DT.zurueckdrehen(MODELL_ORDNER / n) for n in ("prozesse.json", "dateien.json")))
            ui.info("Die laufende Wache lädt das Modell in ein paar Minuten neu.")
            protokoll.schreibe("wache", "ML-Modell zurückgedreht (Nutzer)", "success", veraendernd=True, wer="Nutzer")
    elif sub == "grenzen":
        ui.kv_panel("🛡 Grenzen für die Persönlichkeit", [(k, str(v)) for k, v in e["grenzen"].items()])
        ui.info("Ändern in nemicli.config.json unter wache.grenzen (nur du).")
    elif sub == "training":
        if Z.motor is not None:
            ui.info(str(Z.motor.trainieren("Nutzer")))
        else:
            ui.info("Training läuft im Hintergrund-Prozess: Tray-Menü → „Modell jetzt trainieren“. "
                    "Ohne laufende Wache trainiert der Selbsttest (/wache selbsttest).")
    elif sub == "netz":
        _netz_befehl(rest)
    elif sub in ("programm", "programme", "absolvierung"):
        # /wache programm [liste] · <name> [tage] · stop <name> · erlauben <name> <prozess>
        worte = rest.split()
        was = worte[0].lower() if worte else "liste"
        if was in ("stop", "stoppen", "aus", "status", "erlauben") and len(worte) > 1:
            auftrag = {"aktion": was, "programm": worte[1], "prozess": " ".join(worte[2:])}
        elif was == "liste" or not worte:
            auftrag = {"aktion": "liste"}
        else:
            auftrag = {"aktion": "start", "programm": worte[0], **({"tage": worte[1]} if len(worte) > 1 else {})}
        ui.text_panel("🔍 Programm-Absolvierung", W.programm(auftrag, wer="Nutzer"))
    elif sub in ("dl", "folgen", "folgennetz"):
        _folgen_befehl(rest.lower(), e)
    elif sub in ("fullscan", "vollscan"):
        await _vollscan_befehl(rest.lower())
    elif sub == "selbsttest":
        from wache import selbsttest as ST
        ui.text_panel("🛡 Selbsttest", ST.laufen())
    elif sub == "reset":
        sicher = await ask_confirm("Wirklich Modell, Profile und Inventar der Wache löschen? "
                                   "Ereignisse und Alarme bleiben.", [("ja", "Ja, von null lernen"), ("nein", "Nein")])
        if sicher == "ja":
            if Z.motor is not None:
                Z.motor.lernen_zuruecksetzen()
            else:
                Z.speicher().lernen_zuruecksetzen()
                from wache import MODELL_ORDNER
                for p in [*MODELL_ORDNER.glob("*.json"), *MODELL_ORDNER.glob("*.pause")]:
                    p.unlink(missing_ok=True)
                for unter in ("folgen", "netzfolgen"):
                    for p in [*(MODELL_ORDNER / unter).glob("*.json"), MODELL_ORDNER / unter / "aktuell.txt",
                              MODELL_ORDNER / unter / "auto.pause"]:
                        p.unlink(missing_ok=True)
            ui.success("Wache lernt von null – Inventar beim nächsten Start neu.")
            protokoll.schreibe("wache", "Lernen zurückgesetzt (Nutzer)", "success", veraendernd=True, wer="Nutzer")
    else:
        ui.warn("Unbekannt. /wache zeigt die Möglichkeiten.")


def _netz_befehl(rest: str) -> None:
    """/wache netz [<adresse oder Meldung>|warnen|schatten|aus|an|zurueck|register an|aus]: Netzwächter.
    Scharf schaltet nur der Nutzer – die Persönlichkeit kann das nicht."""
    from wache import MODELL_ORDNER, detektor as DT, folgen as F, justierung as J, werkzeuge as W
    teile = rest.split()
    wahl = teile[0].lower() if teile else ""

    def _setzen(neu: dict, text: str) -> None:
        if not J.speichern(neu):
            ui.warn("Nicht gespeichert – der Netzwächter bleibt, wie er war.")
            return
        protokoll.schreibe("wache_justieren", f"Netzwächter: {neu}", "success", veraendernd=True, wer="Nutzer")
        ui.success(text + " Die laufende Wache übernimmt es in etwa 10 Sekunden.")

    if wahl == "schatten":
        _setzen({"netz": True, "netz_alarm": False}, "Netzwächter im Schattenmodus: Funde nur ins Protokoll.")
    elif wahl in ("warnen", "scharf"):
        _setzen({"netz": True, "netz_alarm": True},
                "Netzwächter warnt: auffällige Verbindungen werden Alarme ML-002/DL-002.")
    elif wahl == "aus":
        _setzen({"netz": False}, "Netzwächter aus: Verbindungen werden nicht mehr bewertet und nicht gelernt.")
    elif wahl == "an":
        _setzen({"netz": True}, "Netzwächter an.")
    elif wahl == "register" and len(teile) > 1:
        an = teile[1].lower() in ("an", "on", "ein", "ja")
        _setzen({"netz_besitzer": an}, "Besitzer-Abfrage beim Adressregister " + ("an." if an else "aus."))
    elif wahl in ("zurueck", "zurück"):
        ui.info(DT.zurueckdrehen(MODELL_ORDNER / "netz.json"))
        ui.info(F.zurueckdrehen(MODELL_ORDNER / "netzfolgen").replace("Folgen-Netz", "Netz-Folgen"))
    else:
        ui.text_panel("🌐 Netzwächter", W.netz({"text": rest} if rest else {}))


def _folgen_befehl(rest: str, e: dict) -> None:
    """/wache dl [schatten|warnen|aus|an|zurueck|schwelle <x>]: Folgen-Netz der Wache.
    Nur der Nutzer schaltet es scharf – die Persönlichkeit kann das nicht."""
    from wache import MODELL_ORDNER, folgen as F, justierung as J
    ordner = MODELL_ORDNER / "folgen"
    teile = rest.split()
    wahl = teile[0] if teile else ""

    def _setzen(neu: dict, text: str) -> None:
        if not J.speichern(neu):
            ui.warn("Nicht gespeichert – das Folgen-Netz bleibt, wie es war.")
            return
        protokoll.schreibe("wache_justieren", f"Folgen-Netz: {neu}", "success", veraendernd=True, wer="Nutzer")
        ui.success(text + " Die laufende Wache übernimmt es in etwa 10 Sekunden.")

    if wahl == "schatten":
        _setzen({"dl": True, "dl_alarm": False}, "Folgen-Netz im Schattenmodus: Funde nur ins Protokoll.")
    elif wahl in ("warnen", "scharf"):
        _setzen({"dl": True, "dl_alarm": True}, "Folgen-Netz warnt: auffällige Schritte werden Alarme DL-001.")
    elif wahl == "aus":
        _setzen({"dl": False}, "Folgen-Netz aus: es bewertet und lernt nicht mehr.")
    elif wahl == "an":
        _setzen({"dl": True}, "Folgen-Netz an" + (" (warnt)." if e.get("dl_alarm") else " (Schattenmodus)."))
    elif wahl in ("zurueck", "zurück"):
        ui.info(F.zurueckdrehen(ordner))
    elif wahl == "schwelle" and len(teile) > 1:
        try:
            wert = round(float(teile[1].replace(",", ".")), 4)
        except ValueError:
            ui.warn("Schwelle als Zahl angeben, z. B. 0.995.")
            return
        if not 0.9 <= wert <= 0.9999:
            ui.warn("Die Schwelle muss zwischen 0.9 und 0.9999 liegen.")
            return
        _setzen({"dl_schwelle": wert}, f"Schwelle des Folgen-Netzes: {wert}.")
    elif wahl in ("", "status"):
        i = F.info(ordner)
        zeilen = [
            ("Modus", "aus" if not e.get("dl", True) else
             "warnt – Alarme DL-001" if e.get("dl_alarm") else "Schattenmodus – nur Protokoll, keine Alarme"),
            ("Modell", f"{i['version']} · trainiert {time.strftime('%d.%m. %H:%M', time.localtime(i['zuletzt']))}"
             if i.get("trainiert") and i.get("zuletzt") else "noch nicht trainiert"),
        ]
        if i.get("trainiert"):
            zeilen += [("Gelernt aus", f"{i.get('folgen', '?')} Prozess-Ketten · {i.get('vokabular', '?')} Zeichen · "
                                       f"Verlust {i.get('verlust', '?')}"),
                       ("Versionen", ", ".join(i.get("versionen", [])) + "  (/wache dl zurueck)")]
        zeilen += [
            ("Schwelle", f"{e.get('dl_schwelle', 0.995)}"),
            ("Lernen", f"nur aus Ereignissen älter als {e.get('dl_karenz_h', 24)} h, ohne Ketten mit "
                       f"offenem, unklarem oder echtem Alarm; höchstens alle {e.get('dl_training_stunden', 6)} h"),
            ("Schatten-Funde", f"{F.schatten_zaehlen(ordner, time.time() - 86400)} in 24 h · "
                               f"Protokoll: {F.schatten_pfad(ordner)} (werden nicht gelernt)"),
        ]
        if F.pausiert(ordner):
            zeilen.append(("Automatik", "zurückgedreht – lernt erst nach deinem nächsten Training (Tray „Modell jetzt trainieren“ oder /wache training) wieder"))
        ui.kv_panel("🛡 Folgen-Netz (Prozess-Ketten)", zeilen)
        letzte = F.schatten_lesen(ordner, 10)
        if letzte:
            ui.text_panel("Letzte Schatten-Funde", "\n".join(letzte))
        ui.info("/wache dl schatten · warnen · aus · an · zurueck · schwelle <0.9–0.9999>")
    else:
        ui.warn("Unbekannt. /wache dl zeigt die Möglichkeiten.")


async def _vollscan_befehl(rest: str) -> None:
    """/wache fullscan [status|stop]: alle Laufwerke als Baseline der Wache."""
    from wache import vollscan as VS
    if rest == "stop":
        ui.info(VS.stoppen())
        return
    if rest != "status" and not VS.laeuft():
        letzter = VS.stand_lesen()
        frage = ("Vollscan starten? Beschreibt jede Festplatte mit Partitionen und liest jede Datei "
                 "aller lokalen Laufwerke (nur lesen, niedrige Priorität, fortsetzbar). Der erste Lauf "
                 "kann eine Stunde oder länger dauern.")
        if letzter.get("phase") == "abgebrochen":
            frage = "Der letzte Vollscan wurde angehalten – dort weitermachen?"
        elif not VS.woche_frei()[0]:
            ui.warn(VS.sperre_text())
            return
        if await ask_confirm(frage, [("ja", "Ja, starten"), ("nein", "Nein")]) != "ja":
            return
        ui.info(await asyncio.to_thread(VS.starten, paths.INSTALL))
        gestartet = True
    else:
        gestartet = False
    z = VS.stand_lesen()
    if not z:
        ui.info("Noch kein Vollscan. /wache fullscan startet einen.")
        return
    if not gestartet:                     # Momentaufnahme; der laufende Stand steht unten in der Statuszeile
        titel = "🛡 Vollscan" + (f" · Stand {time.strftime('%H:%M')}" if VS.laeuft() else "")
        ui.console.print(ui.progress_panel(titel, VS.anzeige(z), live=False))
    if z.get("fehlertext"):
        ui.warn(f"Fehler: {z['fehlertext']}")
    if VS.laeuft():
        ui.info("Fortschritt live unten in der Statuszeile · /wache fullscan status = Stand · "
                "/wache fullscan stop = anhalten (später weiter)")
        if _VOLLSCAN["task"] is None or _VOLLSCAN["task"].done():
            _VOLLSCAN["task"] = asyncio.create_task(_vollscan_melden())


_VOLLSCAN: dict = {"task": None}


def _vollscan_badge(text: str) -> None:
    tui = ui.TUI
    if tui is None:
        return
    try:
        tui.app.call_from_thread(tui.set_vollscan, text)
    except Exception:
        try:
            tui.set_vollscan(text)
        except Exception:
            pass


async def _vollscan_melden() -> None:
    """Fortschritt als Balken in der Statuszeile; Meldung im Chat, wenn eine
    Stufe fertig ist."""
    from wache import vollscan as VS
    phase = VS.stand_lesen().get("phase")
    try:
        while True:
            await asyncio.sleep(2)
            z = VS.stand_lesen()
            _vollscan_badge(VS.kurzanzeige(z))
            if z.get("phase") != phase:
                phase = z.get("phase")
                if phase == "code":
                    ui.info(f"🛡 Vollscan: Stufe 1 fertig ({z.get('dateien', 0):,} Dateien) – "
                            "jetzt Fingerabdrücke, Fortschritt unten in der Statuszeile.".replace(",", "."))
                elif phase == "fertig":
                    ui.success(f"🛡 Vollscan fertig – Bericht: {z.get('bericht', '')}")
                elif phase in ("abgebrochen", "fehler"):
                    ui.info(f"🛡 Vollscan {phase}.")
            if not VS.laeuft():
                return
    finally:
        _vollscan_badge("")


def vollscan_beobachten() -> None:
    """Beim Start von NemiCLI: läuft ein Vollscan, den Balken wieder anzeigen."""
    try:
        from wache import vollscan as VS
        if VS.laeuft() and (_VOLLSCAN["task"] is None or _VOLLSCAN["task"].done()):
            _VOLLSCAN["task"] = asyncio.create_task(_vollscan_melden())
    except Exception:
        pass


KUGEL_MODELL_HALTEN_S = 300      # lokales Modell nach dem letzten Kugel-Gespräch so lange halten


def wache_lauf() -> int:
    global ask_confirm, review_action_confirm
    from wache import dienst as _dienst, wecker as _wecker, zugang as _zugang, ORDNER as _WACHE_ORDNER
    import zeitplan as _ZP
    if _zugang.laeuft():
        print("Die Wache läuft schon.")
        return 0
    ask_confirm = _nie_freigeben
    review_action_confirm = _nie_pruefen
    modes.set_mode("lesen")
    modes.wache_dienst(True)
    stats.start_session(None)

    # Weckruf und Kugel-Gespräch teilen sich converse() und die Mitschrift –
    # deshalb läuft immer nur eines von beiden (Lock auf der Dienst-Schleife).
    gespraech = {"lock": None, "kugel_backend": None, "freigabe": None, "freigabe_nr": 0, "persona": None}

    def _lokales_modell_freigeben() -> None:
        """Ein lokales Sprachmodell bleibt nicht im Wache-Prozess: sein VRAM fehlt
        sonst dem Hauptprogramm. Beim nächsten Weckruf wird es neu geladen."""
        mod = sys.modules.get("gguflokal")
        if mod is not None and mod.geladen():
            mod.entladen()
            print(f"[{time.strftime('%H:%M:%S')}] Lokales Sprachmodell entladen.")

    def _freigabe_absagen() -> None:
        """Neues Gespräch: ein geplantes Entladen gilt nicht mehr."""
        gespraech["freigabe_nr"] += 1
        alt, gespraech["freigabe"] = gespraech["freigabe"], None
        if alt is not None:
            alt.cancel()

    def _freigabe_planen(sekunden: float) -> None:
        """Nach `sekunden` Ruhe entladen – unter der Gesprächssperre, damit nie ein
        laufendes Gespräch das Modell verliert; ein inzwischen neues Gespräch sagt es ab."""
        import threading
        _freigabe_absagen()
        nr, loop = gespraech["freigabe_nr"], asyncio.get_running_loop()

        async def _entladen():
            async with _lock():
                if gespraech["freigabe_nr"] == nr:
                    await asyncio.to_thread(_lokales_modell_freigeben)

        zeit = threading.Timer(sekunden, lambda: asyncio.run_coroutine_threadsafe(_entladen(), loop))
        zeit.daemon = True
        gespraech["freigabe"] = zeit
        zeit.start()

    def _gespraech_datei() -> Path:
        """Kugel-Gespräch je Persönlichkeit – keine liest das einer anderen. Das frühere gemeinsame
        geht an die Persönlichkeit, die es zuerst lädt (die aktive beim ersten Start danach)."""
        import re
        alt = _WACHE_ORDNER / "kugel_gespraech.json"
        neu = _WACHE_ORDNER / f"kugel_gespraech_{re.sub(r'[^A-Za-z0-9_-]', '', PS.active().key)}.json"
        if not neu.exists() and alt.exists():
            try:
                alt.replace(neu)
            except OSError:
                pass
        return neu

    def _gespraech_laden() -> list:
        import json
        try:
            d = json.loads(_gespraech_datei().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [m for m in d if isinstance(m, dict)] if isinstance(d, list) else []

    def _gespraech_speichern(messages: list) -> None:
        """Kugel-Gespräch für den nächsten Start – nur Text, Screenshots bleiben draußen."""
        import json
        raus = []
        for m in messages:
            inhalt = m.get("content")
            if isinstance(inhalt, list):
                inhalt = " ".join(t.get("text", "") for t in inhalt
                                  if isinstance(t, dict) and t.get("type") == "text").strip() or "[Bild]"
            raus.append({"role": m.get("role"), "content": inhalt})
        try:
            datei = _gespraech_datei()
            datei.parent.mkdir(parents=True, exist_ok=True)
            from atomar import text_schreiben
            text_schreiben(datei, json.dumps(raus, ensure_ascii=False))
        except OSError:
            pass

    def _lock() -> asyncio.Lock:
        if gespraech["lock"] is None:
            gespraech["lock"] = asyncio.Lock()
        return gespraech["lock"]

    async def _backend_neu():
        cfg = config.load()
        ref = _migrate_ref(cfg.get("model"))
        if not ref:
            raise RuntimeError("Kein Modell eingestellt (/model).")
        return ref, await make_backend(ref, None, cfg.get("strength", M.DEFAULT_STRENGTH))

    async def wecken(alarme, nachhaken: bool = False) -> str:
        from wache import steuerung as _ST
        import debugbericht as _DBG
        async with _lock():
            _DBG.schleife(asyncio.get_running_loop())
            _DBG.ereignis(f"Wache: Weckruf mit {len(alarme)} Alarm(en)")
            _freigabe_absagen()
            _ST.hintergrund(True)
            try:
                return await _wecken(alarme, nachhaken)
            finally:
                _ST.hintergrund(False)
                await asyncio.to_thread(_lokales_modell_freigeben)

    async def _wecken(alarme, nachhaken: bool = False) -> str:
        name = PS.active().name
        text = _wecker.auftrag_text(alarme, _zugang.speicher(), name, nachhaken)
        raus_hinweis = ""
        for _ in range(1 + ersatzbank.MAX_ERSATZ):      # herausgefiltert: der Ersatz übernimmt den Weckruf
            try:
                ref, backend = await _backend_neu()
            except Exception as e:
                return raus_hinweis + f"❌ Die Wache kann niemanden wecken: {e}"
            mitschrift.leeren()
            mitschrift.setze_quelle(lambda: {"chat": "Wache", "model": ref, "persona": name,
                                             "modus": modes.label(), "ordner": str(Path.cwd())})
            fehler = ""
            try:
                await asyncio.wait_for(converse(backend, text), timeout=15 * 60)
            except asyncio.TimeoutError:
                fehler = "Abgebrochen: der Weckruf lief länger als 15 Minuten."
            except Exception as e:
                fehler = f"Abgebrochen mit Fehler: {e}"
            if (raus := ersatzbank.abholen()) is None:
                break
            raus_hinweis += f"⚠️ {ersatzbank.meldung(raus['neu'])} ({raus['alt']})\n\n"
            if not raus["neu"]:
                return raus_hinweis.strip()
        antwort = raus_hinweis + (mitschrift.letzte_antwort() or "")
        if not antwort:
            antwort = "❌ Kein Bericht – " + (fehler or "die Persönlichkeit hat nichts geantwortet.")
        elif fehler:
            antwort = f"⚠️ {fehler}\n\n{antwort}"
        try:                                     # Stand NACH den Urteilen, nicht der vom Weckruf
            sp = _zugang.speicher()
            alarme = [sp.alarm(a.id) or a for a in alarme]
        except Exception:
            pass
        pfad = _ZP.bericht_pfad("Wache")
        pfad.write_text(
            f"<!-- NemiCLI-Bericht: Wache · {time.strftime('%d.%m.%Y %H:%M')} · {ref} · {name} -->\n\n"
            + antwort.strip() + "\n\n---\n\n## Alarme\n\n"
            + "\n".join(f"- {a.kurz()}" for a in alarme)
            + "\n\n## Ablauf\n\n" + mitschrift.als_markdown(), encoding="utf-8")
        protokoll.schreibe("wache_weckruf", f"{name} geweckt: {len(alarme)} Alarm(e)", "success",
                           veraendernd=False, wer="Wache", ergebnis=antwort)
        print(f"[{time.strftime('%H:%M:%S')}] Weckruf: {len(alarme)} Alarme → {pfad.name}")
        return antwort

    async def chat(text: str, bilder: list) -> tuple[str, list[str], str]:
        """Gespräch über die Schwebekugel: EIN Verlauf, gespeichert bis „+ Neu“.
        Bilder (Screenshots) gehen als Data-URIs mit; neue Bilder aus Bilder/
        kommen als Pfade zurück, damit die Kugel sie zeigt, dazu die Todo-Liste der Runde."""
        async with _lock():
            import debugbericht as _DBG
            _DBG.schleife(asyncio.get_running_loop())
            _DBG.ereignis("Wache: Kugel-Gespräch")
            _freigabe_absagen()
            try:
                return await _kugel_gespraech(text, bilder)
            finally:
                _freigabe_planen(KUGEL_MODELL_HALTEN_S)       # auch nach Fehlern: nicht im VRAM hängen

    async def chat_neu() -> None:
        """„+ Neu“ im Kugel-Fenster: das Gespräch beginnt von vorn."""
        async with _lock():
            if gespraech["kugel_backend"] is not None:
                gespraech["kugel_backend"][1].reset()
            try:
                _gespraech_datei().unlink(missing_ok=True)
            except OSError:
                pass

    async def _kugel_gespraech(text: str, bilder: list) -> tuple[str, list[str], str]:
        if gespraech["persona"] != PS.active().key:      # Persönlichkeit gewechselt: ihr eigenes Gespräch
            gespraech["kugel_backend"], gespraech["persona"] = None, PS.active().key
        if (gespraech["kugel_backend"] is not None
                and gespraech["kugel_backend"][0] != _migrate_ref(config.load().get("model"))):
            gespraech["kugel_backend"] = None            # Modell gewechselt, auch durch die Ersatzbank
        if gespraech["kugel_backend"] is None:
            try:
                ref, backend = await _backend_neu()
            except Exception as e:
                return f"Ich kann gerade nicht antworten: {e}", [], ""
            backend.messages = _gespraech_laden()
            gespraech["kugel_backend"] = (ref, backend)
        ref, backend = gespraech["kugel_backend"]
        name = PS.active().name
        mitschrift.leeren()
        mitschrift.setze_quelle(lambda: {"chat": "Kugel", "model": ref, "persona": name,
                                         "modus": modes.label(), "ordner": str(Path.cwd())})
        uris = []
        for b in bilder or []:
            try:
                uris.append(vision.to_data_uri_small(Path(b)))
            except Exception:
                pass
        start = time.time()
        modes.kugel_runde(True)
        try:
            await asyncio.wait_for(converse(backend, text, images=uris or None), timeout=10 * 60)
        except Exception as e:
            return f"(abgebrochen: {e})", [], plan.text_fuer_modell()
        finally:
            abgelehnt = modes.kugel_abgelehnt()
            modes.kugel_runde(False)
        _gespraech_speichern(backend.messages)
        antwort = modes.KUGEL_TERMINAL_SATZ if abgelehnt else (mitschrift.letzte_antwort() or "")
        if (raus := ersatzbank.abholen()) is not None:
            gespraech["kugel_backend"] = None
            antwort = ersatzbank.meldung(raus["neu"])
        neue = []
        import profilordner
        for ordner in profilordner.bilder_ordner_alle():
            try:
                neue += [str(p) for p in ordner.glob("*.png") if p.stat().st_mtime >= start - 1]
            except OSError:
                pass
        protokoll.schreibe("kugel_chat", f"Kugel: {text[:120]}", "success", veraendernd=False,
                           wer=name, ergebnis=antwort)
        return antwort, sorted(neue), plan.text_fuer_modell()

    d = _dienst.Dienst(wecken, paths.INSTALL, chat=chat, chat_neu=chat_neu, name=PS.active().name,
                       nutzer=PS.nutzername() or "du", mit_tray="--ohne-tray" not in sys.argv,
                       mit_kugel="--ohne-kugel" not in sys.argv, name_von=lambda: PS.active().name)
    print(f"[{time.strftime('%H:%M:%S')}] Wache läuft (PID {os.getpid()})")
    return d.laufen()


async def run_classic(ctx: Ctx) -> None:
    """Klassischer scrollender Loop (Eingabe inline, prompt_toolkit-Session)."""
    session = PromptSession(
        history=FileHistory(str(paths.ROOT / ".cli_history")),
        completer=SlashCompleter(get_themes=lambda: ui.THEMES, get_models=model_completions,
                                 get_strengths=lambda: M.strength_menu(ctx.model),
                                 get_checkpoints=lambda: __import__("imagegen").menu(),
                                 get_personas=PS.menu),
        complete_while_typing=True,
        rprompt=mascot.rprompt,
        bottom_toolbar=lambda: ui.bottom_toolbar(SESSION),
        refresh_interval=0.3,
    )
    while True:
        ctx.sync_session()
        ui.console.print(ui.input_divider())
        try:
            text = await session.prompt_async(mascot.lprompt, style=ui.PROMPT_STYLE)
        except (KeyboardInterrupt, EOFError):
            break
        ui.console.print(ui.input_divider())   # zweite Linie -> Eingabe eingerahmt
        if neustart.ist_befehl(text):
            await neustart.ausfuehren()
            break
        if await handle_line(ctx, text) == "exit" or neustart.ANGEFORDERT:
            break


class _Start:
    """Der Start mit Stoppuhr und Schutz: jeder Schritt wird gemessen; scheitert ein verzichtbarer Schritt, steht
    er im Debugbericht und als kurze Warnung da, und NemiCLI startet weiter. Sicherheitsschritte laufen ungeschützt."""
    LANGSAM_S = 0.3

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.zeiten: list[tuple[str, float]] = []
        self.fehler: list[str] = []

    def schritt(self, name: str):
        import contextlib

        @contextlib.contextmanager
        def uhr():
            t = time.monotonic()
            try:
                yield
            except Exception as exc:
                self.fehler.append(name)
                try:
                    import debugbericht
                    debugbericht.fehler(f"Start: {name}", exc)
                except Exception:
                    pass
                ui.warn(f"⚠ Beim Start ging „{name}“ schief ({exc}) – NemiCLI startet trotzdem.")
            finally:
                self.zeiten.append((name, time.monotonic() - t))
        return uhr()

    def bericht(self) -> str:
        gesamt = time.monotonic() - self.t0
        teile = [f"Start fertig: {gesamt:.1f} s"]
        try:
            import psutil
            teile[0] += f" ({time.time() - psutil.Process().create_time():.1f} s seit dem Programmstart)"
        except Exception:
            pass
        langsam = sorted((z for z in self.zeiten if z[1] >= self.LANGSAM_S), key=lambda z: -z[1])
        if langsam:
            teile.append("langsam: " + ", ".join(f"{n} {s:.1f} s" for n, s in langsam[:6]))
        if self.fehler:
            teile.append("gescheitert: " + ", ".join(self.fehler))
        return " · ".join(teile)


async def main():
    cfg = config.load()
    import debugbericht as _DBG
    _DBG.starten("haupt")
    _DBG.schleife(asyncio.get_running_loop())
    start = _Start()

    # Sicherheitsnetz: Maus-Tracking beim Programm-Ende IMMER zurücksetzen – auch
    # wenn NemiCLI unerwartet endet. Sonst flutet das Terminal die Shell danach mit
    # Maus-Zeichen (`[555;73;46M`). Läuft zusätzlich zum Cleanup in screen.run().
    import atexit
    try:
        import screen as _sc
        atexit.register(_sc.maus_tracking_aus)
    except Exception:
        pass

    # Noch im Klartext liegende API-Keys in den Windows-Umschlag packen (einmalig).
    secured_keys = 0
    with start.schritt("API-Keys schützen"):
        secured_keys = config.secure_existing_keys()

    # Gemerktes Theme wiederherstellen (vor dem Banner, damit es passt)
    if isinstance(cfg.get("theme"), str) and cfg["theme"] in ui.THEMES:     # kaputte Konfig: kein Absturz
        ui.set_theme(cfg["theme"])

    # Vollbild-TUI (Standard) oder klassischer Loop (NEMICLI_CLASSIC=1 als Fallback)
    tui = None
    if os.getenv("NEMICLI_CLASSIC") != "1":
        try:
            # Standard: Textual-Oberfläche (ui/screen_tx.py). NEMICLI_TUI=alt
            # schaltet auf die bisherige prompt_toolkit-Fassung (ui/screen.py) zurück.
            if os.getenv("NEMICLI_TUI", "").lower() in ("alt", "old", "pt"):
                import screen as _screenmod
            else:
                import screen_tx as _screenmod
            tui = _screenmod.Screen()
            tui.install()          # ui.console -> Scroll-Puffer, ui.TUI = tui
        except Exception as e:
            tui = None
            ui.TUI = None
            ui.error(f"Vollbild-TUI konnte nicht starten ({e}) – nutze klassischen Modus.")

    import startseite
    startseite.STEUERN = _fenster_steuern          # eigenes Fenster: echte Bilder statt Zeichen-Blöcke
    with start.schritt("Startseite sammeln"):
        startseite.sammeln(seit=startseite.letzter_start())
    with start.schritt("Banner"):
        ui.banner(animate=(tui is None))   # klassisch animiert, im TUI statisch in den Puffer

    backend = None
    model = None
    strength = cfg.get("strength", M.DEFAULT_STRENGTH)
    if strength not in M.STRENGTHS:
        strength = M.DEFAULT_STRENGTH

    # 1) Gemerktes Modell vom letzten Mal wiederherstellen (ins neue Schema heben)
    gemerkt = None
    with start.schritt("Gemerktes Modell lesen"):
        gemerkt = _migrate_ref(cfg.get("model"))
    if gemerkt:
        with start.schritt("Modell verbinden"):
            try:
                backend = await make_backend(gemerkt, None, strength)
                model = gemerkt
            except Exception as e:
                ui.error(f"Gemerktes Modell '{gemerkt}' ließ sich nicht laden: {e}")

    # 2) Sonst: mit Cloud-Sonnet starten, wenn ein Anthropic-Key da ist
    if backend is None and os.getenv("ANTHROPIC_API_KEY"):
        try:
            from chat import Chat
            model = M.make_ref("anthropic", "claude-sonnet-4-6")
            backend = Chat(model="claude-sonnet-4-6", strength=strength)
        except Exception as e:
            ui.error(f"Cloud-Start fehlgeschlagen: {e}")

    ctx = Ctx(backend=backend, model=model, strength=strength,
              current_chat=chatstore.next_id(), current_prompts=[])
    _weiter = None
    with start.schritt("Gespräch fortsetzen"):
        _weiter = _gespraech_fortsetzen(ctx)
    _FORTSETZEN_NACH_MODELL[0] = ctx.backend is None
    with start.schritt("Kontext zählen"):
        _kontext_neu_zaehlen(ctx)
    import commands as _befehle                  # die KI sieht dieselbe Auswahl wie das Menü
    _befehle.AUSWAHL_JETZT = lambda cmd: _untermenue_eintraege(ctx, cmd)
    with start.schritt("Statistik"):
        stats.start_session(model)
    sicherheit.sitzung_zuruecksetzen()       # bewusst ungeschützt: ohne frische Freigaben kein Start
    with start.schritt("Papierkorb aufräumen"):  # Sicherungen älter als 30 Tage weg
        weg = snapshot.aufraeumen()
        if weg:
            ui.info(f"🗑 Papierkorb: {weg} alte Sicherung(en) entfernt.")

    # F12 sichert das ganze Gespraech - der Mitschnitt holt sich die Kopfdaten
    # hier ab, damit er nichts ueber main wissen muss.
    mitschrift.setze_quelle(lambda: {
        "chat": ctx.current_chat,
        "model": ctx.model or "-",
        "persona": PS.active().name,
        "modus": modes.label(),
        "ordner": str(Path.cwd()),
    })

    with start.schritt("Begrüßung"):
        startseite.setzen(status="bereit" if ctx.backend is not None else "ohne")
        ui.welcome(model=ctx.model or "—", mode="Chat",
                   strength=ctx.strength if ctx.strength_active() else None)
    if tui is None and _weiter:
        ui.info(f"📒 Euer Gespräch geht weiter: „{_weiter['title']}“")
    # Berichte aus dem Zeitplan, die seit dem letzten Start dazukamen: erste Zeile zeigen
    with start.schritt("Zeitplan-Berichte"):
        import zeitplan as _ZP
        for _p in _ZP.neue_berichte()[-3:]:
            if tui is None:
                ui.info(f"📋 Bericht „{_p.stem}“: {_ZP.bericht_kopfzeile(_p) or '(leer)'}  →  {_p}")
            else:
                startseite.offen_dazu(f"📋 Bericht „{_p.stem}“")
    # Systemwache: läuft sie? Wenn gewünscht und nicht da → im Hintergrund starten.
    with start.schritt("Wache"):
        from wache import zugang as _WZ, justierung as _WJ
        _we = _WJ.einstellungen()
        _alt = _WZ.laeuft()
        if _we.get("aktiv", True) and _WZ.veraltet(_alt):
            ui.info("🛡 Wache läuft noch mit altem Programmstand – starte sie neu …")
            _WZ.stoppen()
        if _WZ.autostart_auffrischen(paths.INSTALL):
            ui.info("🛡 Windows-Autostart der Wache zeigt jetzt auf die fensterlose NemiCLI.exe.")
        if not _WZ.laeuft() and _we.get("aktiv", True):
            ui.info(_WZ.starten(paths.INSTALL))
        _wi = _WZ.laeuft()
        _st = _WZ.speicher().alarm_statistik()
        if _wi:
            _lage = f"{_st.get('offen', 0)} offen"
            if _st.get("echt"):
                _lage = f"⚠ {_st['echt']} ECHT · " + _lage
            if (_still := _WZ.still_hinweis(_wi)):
                ui.warn("🛡 Wache:" + _still)
            if tui is None:
                ui.info(f"🛡 Wache läuft (PID {_wi['pid']}) · Alarme: {_lage} · /wache zeigt mehr")
            else:
                startseite.setzen(wache=startseite.wache_text(True, _st))
        elif not _we.get("aktiv", True):
            if tui is None:
                ui.info("🛡 Wache ist aus (/wache start · /wache an schaltet den Autostart wieder ein)")
            else:
                startseite.setzen(wache=startseite.wache_text(False, {}))
    with start.schritt("Vollscan beobachten"):
        vollscan_beobachten()
    with start.schritt("Chats prüfen"):
        if (meldung := chatstore.beim_start()):
            ui.warn("🛟 " + meldung)
    with start.schritt("Auto-Backup"):
        import autobackup
        if (meldung := autobackup.beim_start()):
            ui.warn(meldung)
    if ctx.backend is None:
        ui.warn("Noch kein Modell aktiv – tippe /model zum Wählen.")
        with start.schritt("Anbieter suchen"):
            provs = {pid: p for pid, p in P.detected().items() if not p.keyless}
            if provs:
                ui.info(f"☁ Cloud-Anbieter erkannt: {', '.join(p.label for p in provs.values())}")
            if P.get("ollama").available():
                ui.info(f"🦙 Ollama läuft: {len(P.ollama_models())} Modell(e) – lokal, kein Key")
            if not provs and not P.get("ollama").available():
                ui.info("Cloud: API-Key eintragen · Lokal: Modell von Hugging Face holen – beides unter /model.")
    # Key-Schutz nur als einmaliges Ereignis melden (wenn gerade verschlüsselt wurde).
    # Der dauerhafte „geschützt"-Hinweis steht jetzt unter /help (sauberer Start).
    try:
        import fehlerwaechter
        if hinweis := fehlerwaechter.hinweis_beim_start():
            ui.warn(hinweis)
    except Exception:
        pass
    if secured_keys:
        ui.success(f"🔒 {secured_keys} API-Key(s) verschlüsselt (Windows-Umschlag) – "
                   "in der .env steht jetzt nur noch Buchstabensalat.")

    # Der eingestellte Daten-Ordner ist nicht erreichbar (Laufwerk weg,
    # Buchstabe verrutscht). NICHT stillschweigend mit leerem Gedaechtnis
    # weiterlaufen - das sieht aus wie Datenverlust und ist keiner.
    if paths.PROBLEM:
        ui.error("📁 " + paths.PROBLEM)

    # Ganz erster Start: einmalig fragen, wo der NemiCLI-Ordner liegen soll.
    # Danach steht die Antwort in der Config und es fragt nie wieder; ueber
    # /start kommt man jederzeit zurueck.
    try:
        import reich as _R
        if not _R.schon_gefragt():
            await run_reich(erststart=True)
    except Exception as e:
        ui.warn(f"Konnte das Ordner-Fenster nicht zeigen ({e}). "
                "Mit /start kannst du es jederzeit nachholen.")

    # Erster Start auf diesem PC? Dann auf den Assistenten hinweisen.
    # (Kein Menü hier – die Oberfläche läuft an dieser Stelle noch nicht.)
    try:
        import wizard as _W
        if _W.erster_start():
            ui.info("👋 Zum ersten Mal hier? Tippe  /einrichten  – " +
                    ("Funke ✨, der kleine Einrichtungs-Helfer, führt dich" if funke_da() else "der Assistent führt dich")
                    + " durch alles Nötige.")
    except Exception:
        pass

    # Tipps stehen jetzt unter /help – Start bleibt sauber. Im Vollbild grüßt die Persönlichkeit
    # auf der Startseite selbst.
    with start.schritt("Gruß"):
        if tui is None:
            mascot.greet()
            ui.console.print()
        else:
            _start_gruss(ctx)
    global AKTIVER_CTX
    AKTIVER_CTX = ctx
    asyncio.get_running_loop().create_task(von_sich_aus_abholen())   # Nachrichten aus dem freien Moment
    with start.schritt("Sprüche"):
        _sprueche_im_hintergrund(ctx)
    with start.schritt("Chrome-Brücke"):
        start_chrome_bridge(ctx)     # Chrome-Erweiterung: Empfang auf 127.0.0.1:9000
    with start.schritt("Skill-Wächter"):
        waechter_verbinden(ctx)      # Skill-Wächter: Zweitmeinung über das aktive Modell
    _DBG.ereignis(start.bericht(), speicher=False)

    # --sag "<text>": erste Nachricht gleich abschicken (z.B. aus dem Rechtsklick-Menü)
    start_text = _start_nachricht()
    if not start_text and ctx.backend is None and tui is not None:
        start_text = "/model"                  # ohne Modell: gleich die Begrüßung (Cloud-Key · Hugging Face)
    if start_text and tui is not None:
        tui.start_text = start_text
    elif start_text:
        await handle_line(ctx, start_text)

    try:
        if tui is not None:
            try:
                await tui.run(ctx, handle_line, session_state=SESSION,
                              model_completions=model_completions,
                              practice=practice_round)
            except Exception as e:
                # Vollbild im echten Terminal gescheitert -> sauber zurückfallen
                tui.uninstall()
                tui = None
                ui.error(f"Vollbild-TUI abgestürzt: {e} – wechsle in den klassischen Modus.")
                await run_classic(ctx)
        else:
            await run_classic(ctx)
    finally:
        stats.end_session()       # gesammelte Zahlen sichern
        if CHROME_BRIDGE is not None:
            CHROME_BRIDGE.stop()

    if tui is not None:
        tui.uninstall()
    ui.goodbye()
    if neustart.ANGEFORDERT:
        neustart.beenden()


def selftest(out=print) -> int:
    """`NemiCLI.exe --selftest` – prüft ohne Oberfläche, ob alles an Bord ist.
    Gedacht fürs Testen der gepackten exe (das Vollbild braucht ein echtes
    Terminal und lässt sich so nicht prüfen). `out` nimmt die Zeilen entgegen –
    print für die Konsole, oder eine Liste.append für /selbsttest im Chat."""
    print = out                      # alle print() unten laufen über `out`
    print(f"NemiCLI Selbsttest  ·  {VER.build_label()}  ·  Python {sys.version.split()[0]}  ·  "
          f"{'exe' if getattr(sys, 'frozen', False) else 'Skript'}")
    print(f"Datenordner : {paths.ROOT}")
    fehler = 0
    for mod in ("ui", "screen", "actions", "vision", "config", "chatstore",
                "learn", "emoji", "mascot", "subagents", "models", "providers",
                "pricing", "setup", "syscheck", "extlibs", "practice", "webui",
                "keyvault", "commands", "confirm", "chat", "cloud", "wizard",
                "persona", "memory", "foldersense", "webfetch", "imagegen", "sdwebui", "comfyui",
                "gesichter",
                "stats", "modes", "uvsetup",
                "textproc", "pdfgen", "paths"):
        try:
            __import__(mod)
            print(f"  ok   {mod}")
        except Exception as e:
            fehler += 1
            print(f"  FEHLT {mod}: {e}")
    for rel in ("Models", "Bilder", "chats", "learned"):
        d = paths.ROOT / rel
        print(f"  {'ok  ' if d.exists() else 'FEHLT'} Ordner {rel}")
        fehler += 0 if d.exists() else 1
    import json as _json                              # Modell-Datenbank (ohne torch lesbar)
    for name in ("bauarten", "chatformate", "hardware", "werkzeugformate"):
        try:
            daten = _json.loads((paths.ASSETS / "ggufengine" / "daten" / f"{name}.json").read_text(encoding="utf-8"))
            if not isinstance(daten.get("version"), int):
                raise ValueError("ohne 'version'")
            print(f"  ok   Modell-Datenbank {name}")
        except Exception as e:
            fehler += 1
            print(f"  FEHLT Modell-Datenbank {name}: {e}")
    try:
        import webfetch
        print(f"  ok   Allowlist: {len(webfetch.entries())} Domains")
    except Exception as e:
        fehler += 1
        print(f"  FEHLT Allowlist: {e}")
    if not funke_da():                     # Fassung ohne einrichtungshelfer/: kein Fehler
        print(f"  info {FUNKE_FEHLT} /einrichten läuft klassisch")
    else:
        try:                               # Einrichtungs-Helfer: lädt er und antwortet er (reines Python)?
            from einrichtungshelfer import gespraech as G
            if G.verfuegbar():
                t0 = time.perf_counter()
                probe = G.Funke().sagen("modell", "lokal bitte")
                ok = probe.art == "modell" and probe.wert == "lokal"
                fehler += 0 if ok else 1
                print(f"  {'ok  ' if ok else 'FEHLT'} {G.NAME} antwortet ({probe.art}={probe.wert}, "
                      f"{time.perf_counter() - t0:.1f} s)")
            else:
                print(f"  info {G.NAME} ist nicht mitgeliefert – /einrichten läuft klassisch")
        except Exception as e:
            fehler += 1
            print(f"  FEHLT Einrichtungs-Helfer: {e}")
        try:                               # Funke Groß (/model): Datei vollständig? Antworten wäre ohne torch zu langsam
            from einrichtungshelfer import funkechat as FC
            if FC.verfuegbar():
                k = FC.kopf()
                print(f"  ok   Funke Groß in /model ({k['parameter'] / 1e6:.1f} Mio. Parameter, {k['typ']})")
            else:
                print("  info Funke Groß ist nicht mitgeliefert")
        except Exception as e:
            fehler += 1
            print(f"  FEHLT Funke Groß: {e}")
    import imagegen
    grund = imagegen.missing_reason_aktiv()
    motor = imagegen.active_label()
    print(f"  {'ok   Bilder-Malen bereit  ' + motor if not grund else 'info Bilder-Malen: ' + grund.splitlines()[0]}")
    # Werkzeug-Rundlauf in NemiSandbox/: Ordner anlegen → Datei schreiben → lesen →
    # löschen. Direkt über die Werkzeug-Funktionen (ohne Rückfrage – es ist die
    # Sandbox), damit auffällt, wenn eines davon kaputt ist oder der Schutz greift.
    print("\nWerkzeuge:")
    try:
        import actions as A
        box = paths.ROOT / "NemiSandbox" / "_selbsttest"
        datei = box / "probe.txt"
        schritte = [
            ("ordner_erstellen", lambda: A._ordner_erstellen({"pfad": str(box)})),
            ("datei_schreiben", lambda: A._datei_schreiben({"pfad": str(datei),
                                                            "inhalt": "NemiCLI Selbsttest äöü"})),
            ("datei_lesen", lambda: A._datei_lesen({"pfad": str(datei)})),
            ("loeschen", lambda: A._loeschen({"pfad": str(box)})),
        ]
        for name, fn in schritte:
            r = fn()
            text = r.text if isinstance(r, A.ActionResult) else str(r)
            ok = (r.ok is not False) if isinstance(r, A.ActionResult) else True
            if name == "datei_lesen" and "äöü" not in text:
                ok = False
            fehler += 0 if ok else 1
            print(f"  {'ok  ' if ok else 'FEHLT'} {name:<18} {text.splitlines()[0][:60]}")
        if box.exists():
            fehler += 1
            print("  FEHLT Aufräumen: _selbsttest-Ordner ist noch da")
        # loeschen verschiebt in den Papierkorb – die Probe muss dort nicht liegen bleiben.
        import shutil as _sh
        for e in snapshot.letzte(3):
            if e.get("werkzeug") == "loeschen" and Path(e["original"]) == box:
                _sh.rmtree(e["kopie"], ignore_errors=True)
                print(f"  ok   Papierkorb         Probe gesichert und wieder entfernt ({snapshot.ORDNER.name}/)")
                break
        else:
            fehler += 1
            print("  FEHLT Papierkorb: loeschen hat keine Sicherung angelegt")
    except Exception as e:
        fehler += 1
        print(f"  FEHLER Werkzeuge: {e}")
    try:
        import wizard as W
        print("\nEinrichtung:")
        for s in W.check_all():
            print(f"  [{'x' if s['ok'] else ' '}] {s['titel']:<45} {s['info']}")
    except Exception as e:
        fehler += 1
        print(f"  FEHLER Assistent: {e}")
    print("\nErgebnis:", "alles gut ✅" if not fehler else f"{fehler} Problem(e) ❌")
    return 0 if not fehler else 1


if __name__ == "__main__":
    if "--version" in sys.argv:
        for k, v in VER.report():
            print(f"{k:<14} {v}")
        raise SystemExit(0)
    try:                                 # eigene Namensauflösungen mitschreiben (Netz-Auskunft der Wache)
        from wache import netzspur as _NS
        _NS.installieren()
    except Exception:
        pass
    # Eigenes Terminal-Fenster: mit --fenster, oder NemiCLI.exe (Fenster-App) ohne Hintergrund-Auftrag.
    # Das Fenster startet NemiCLI selbst in einer Pseudokonsole – ohne Windows Terminal.
    _HINTERGRUND = ("--wache", "--vollscan", "--auftrag", "--sag", "--selftest", "--bild",
                    "--systemcheck", "--kontext")
    if "--kontext" in sys.argv:          # Rechtsklick-Menü: Screenshot / Bild / Antwort an NemiCLI
        import kontextmenue
        raise SystemExit(kontextmenue.main(sys.argv[sys.argv.index("--kontext") + 1:]))
    if "--gui" in sys.argv:              # GUI-Fenster (verbindet sich mit einer laufenden Sitzung)
        import gui_fenster
        raise SystemExit(gui_fenster.starten())
    if "--nemiview" in sys.argv:         # NemiView: Appstore und Apps mit eigener Engine (/appstore)
        from nemiview.fenster import starten as nemiview_starten
        raise SystemExit(nemiview_starten(sys.argv[sys.argv.index("--nemiview") + 1:]))
    if "--seitenbild" in sys.argv:       # seite_ansehen: lokale Seite unsichtbar mit NemiView laden
        from nemiview.bild import main as seitenbild
        raise SystemExit(seitenbild(sys.argv[sys.argv.index("--seitenbild") + 1:]))
    if "--musik" in sys.argv:            # Musik-Fenster (/musik, verbindet sich mit einer laufenden Sitzung)
        import musik_fenster
        raise SystemExit(musik_fenster.starten())
    if "--charaktere" in sys.argv:       # Charakter-Galerie (/charaktere, verbindet sich mit einer laufenden Sitzung)
        import charakter_fenster
        raise SystemExit(charakter_fenster.starten())
    if "--vorschau" in sys.argv:         # kleines Fenster mit bewegtem Porträt (portraet_bewegen)
        import vorschau_fenster
        _i = sys.argv.index("--vorschau")
        raise SystemExit(vorschau_fenster.zeigen(sys.argv[_i + 1] if len(sys.argv) > _i + 1 else ""))
    if "--fenster" in sys.argv or (getattr(sys, "frozen", False) and sys.stdout is None
                                   and not any(a in sys.argv for a in _HINTERGRUND)):
        import terminal_fenster as _TF
        _rest = [a for a in sys.argv[1:] if a != "--fenster"]
        if getattr(sys, "frozen", False):
            _kind = [str(paths.exe_mit_fenster()), *_rest]
        else:
            _kind = [sys.executable, str(Path(__file__).resolve()), *_rest]
        raise SystemExit(_TF.starten(_kind, ordner=os.getcwd()))
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--bild" in sys.argv:             # Bild malen ohne Oberfläche (zum Testen)
        import imagegen
        _i = sys.argv.index("--bild")
        _prompt = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else "a red fox in a forest"
        _g = imagegen.missing_reason_aktiv()
        if _g:
            print(_g)
            raise SystemExit(1)
        _opts = imagegen.parse_opts(_prompt)      # --steps/--size/--seed ... erlaubt
        _text = _opts.pop("prompt", _prompt)
        print(f"male: {_text}  ({imagegen.device_info()})")
        _erlaubt = ("model", "neg", "steps", "cfg", "size", "seed", "sampler")
        _res = imagegen.paint(_text, on_status=lambda m: print("  ", m),
                              **{k: v for k, v in _opts.items() if k in _erlaubt})
        print("fertig:", _res)
        raise SystemExit(0)
    if "--vollscan" in sys.argv:         # Vollscan aller Laufwerke (eigener Prozess, ohne Fenster)
        from wache import vollscan as _VS
        raise SystemExit(_VS.hauptprogramm())
    if "--wache" in sys.argv:            # Systemwache als Hintergrund-Prozess (Tray)
        try:
            from wache import ORDNER as _WO
            _WO.mkdir(parents=True, exist_ok=True)
            if sys.stdout is None or "--ohne-tray" not in sys.argv:
                _lp = _WO / "dienst.log"
                if _lp.exists() and _lp.stat().st_size > 5_000_000:
                    _lp.replace(_lp.with_suffix(".log.1"))
                _log = open(_lp, "a", encoding="utf-8", errors="replace", buffering=1)

                class _OhneFarben:
                    """Farbcodes raus, bevor es in die Datei geht – sonst ist das
                    Log voller Steuerzeichen, die kein Editor lesbar zeigt."""
                    _re = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

                    def write(self, s):
                        return _log.write(self._re.sub("", s))

                    def flush(self):
                        _log.flush()

                    def __getattr__(self, name):
                        return getattr(_log, name)

                sys.stdout = sys.stderr = _OhneFarben()
                # Die Antworten der Persönlichkeit (Kästen, Nachgedacht-Zeilen …)
                # stehen komplett in Berichte/Wache_*.md – im Dienst-Log wären sie
                # nur Ballast. Hier bleiben nur die Zeilen des Dienstes selbst.
                ui.console.quiet = True
            print(f"\n===== {time.strftime('%d.%m.%Y %H:%M:%S')} Wache startet =====")
        except Exception:
            pass
        import debugbericht as _DBG
        _DBG.starten("wache")
        raise SystemExit(wache_lauf())
    if "--auftrag" in sys.argv:          # Zeitplan: ohne Fenster, Antwort wird Bericht
        _i = sys.argv.index("--auftrag")
        _name = " ".join(sys.argv[_i + 1:]).strip()
        if not _name:
            print("--auftrag braucht den Namen des Auftrags (Zeitplan/<name>.json).")
            raise SystemExit(2)
        # Unter pythonw.exe gibt es keine Konsole – alles, was NemiCLI sonst ins
        # Terminal malt, landet in einer Log-Datei neben den Berichten.
        try:
            import zeitplan as _ZP
            _ZP.BERICHTE.mkdir(parents=True, exist_ok=True)
            _log = open(_ZP.BERICHTE / f"{_ZP.sauberer_name(_name) or 'auftrag'}.log",
                        "a", encoding="utf-8", errors="replace")
            sys.stdout = sys.stderr = _log
            print(f"\n===== {time.strftime('%d.%m.%Y %H:%M:%S')} Auftrag {_name!r} =====")
        except Exception:
            pass
        raise SystemExit(asyncio.run(auftrag_lauf(_name)))
    if "--systemcheck" in sys.argv:      # Bericht ohne Oberfläche (auch zum Testen)
        import syscheck
        _rep = syscheck.report(True)
        ui.system_panel(_rep, syscheck.todo(_rep))
        if "--debug" in sys.argv:        # Rohdaten zum Fehlersuchen
            import json
            print(json.dumps(_rep["torch"], indent=2, ensure_ascii=False))
        raise SystemExit(0)
    try:
        import stimme as _ST
        _ST.AKTIV = True                 # nur das große NemiCLI spricht, nicht Wache oder Aufträge
    except Exception:
        pass
    neustart.warten(sys.argv)            # /restart außerhalb des eigenen Fensters: erst nach dem alten Prozess
    asyncio.run(main())
