"""main_dienste.py – Lernen, Brücken (Chrome, Fenster), Bibliothekar, Einrichtung.

Teil von main.py: läuft im selben Namensraum wie main.py (main._teil_laden) – eine Datei,
nur auf vier verteilt. Namen aus den anderen Teilen sind hier also da, ohne Import."""
# ruff: noqa: F821


async def skill_anlegen(backend, records: list[dict]) -> tuple[str, str | None, str] | None:
    """Nach einer größeren gelungenen Aufgabe: lohnt sich ein neuer Skill? Gespeichert wird nur,
    was der Skill-Wächter durchlässt; ein vorhandener Skill wird hier nie überschrieben.
    (Name, None, Beschreibung) = angelegt · (Name, Grund, "") = aufgehalten · None = keiner."""
    import lernschleife as LS
    import skills
    import skillwaechter
    verlauf = _rueckblick(backend)
    if not verlauf:
        return None
    vorhanden = "".join(f"\n- {s.name} — {s.beschreibung}" for s in skills.alle())
    f = LS.skill_lesen(await backend.ask_once(LS.neu_prompt(vorhanden, verlauf, records), LS.NEU_SYSTEM))
    if f is None or skills.finden(f["name"]) is not None:
        return None
    if (grund := await asyncio.to_thread(skillwaechter.pruefen, **f, neu_angelegt=True, fremd=False,
                                         zaehlen=False, quelle="neu")):
        return f["name"], grund, ""
    skills.schreiben(**f)
    skillwaechter.gespeichert(f["name"], "neu")
    return f["name"], None, f"{f['beschreibung']} (wann: {f['wann']})"


async def skill_zweitmeinung(backend, felder: dict, alt: str | None) -> str | None:
    """Zweite Prüfung einer Skill-Änderung durch das Modell (mit den Vorlieben des Nutzers)."""
    import skillwaechter as W
    vorlieben = [memory.entschaerfen(e["text"]) for e in memory.all_entries() if e.get("kind") == "vorliebe"]
    return W.zweit_lesen(await backend.ask_once(W.zweit_prompt(felder, alt, vorlieben[-20:]), W.ZWEIT_SYSTEM))


def waechter_verbinden(ctx: "Ctx") -> None:
    """Skill-Wächter mit der Sitzung verbinden: seine Zweitmeinung fragt das aktive Modell.
    Der Wächter ruft sie aus dem Werkzeug-Thread; die Schleife ist dann frei."""
    import skillwaechter
    loop = asyncio.get_running_loop()

    def zweitmeinung(felder: dict, alt: str | None) -> str | None:
        backend = ctx.backend
        if backend is None:
            return None
        try:
            if asyncio.get_running_loop() is loop:        # auf der Schleife selbst nie blockieren
                return None
        except RuntimeError:
            pass
        return asyncio.run_coroutine_threadsafe(skill_zweitmeinung(backend, felder, alt), loop).result(timeout=180)

    skillwaechter.ZWEITMEINUNG = zweitmeinung


def _skill_strichliste(diese: list[str], betroffen: list[str], wer: str) -> None:
    """Benutzt/Probleme zählen; einmaliger Hinweis (Nutzer und Persönlichkeit `wer`), wenn ein
    Skill wackelig wird."""
    import lernschleife as LS
    import skills
    for k in diese:
        skills.zaehlen(k, benutzt=1)
    for k in betroffen:
        st = skills.zaehlen(k, probleme=1)
        if st and skills.wackelig(st) and st["probleme"] == 3:
            s = next((x for x in skills.alle() if x.kennung == k), None)
            ui.info(f"🧩 Skill „{s.name if s else k}“ macht oft Probleme ({skills.bilanz(st)}) – "
                    "er wird bei jedem Problem nachgebessert; /skills zeigt alle Bilanzen.")
            LS.melden(f"Skill „{s.name if s else k}“ ist als wackelig markiert ({skills.bilanz(st)}).", wer)


_LERN_AUFGABEN: set = set()          # laufende Hintergrund-Aufgaben (Referenz halten)


def lernen_planen(ctx: "Ctx", nutzertext: str, records: list[dict], web: bool,
                  weg: list[dict] | None = None) -> None:
    """Automatisch reflektieren, wenn die Runde einen Anlass hat (lernschleife.anlass), und
    sichern, was gerade aus dem Kontext gekürzt wurde (`weg`). Skills: Strichliste führen,
    nach Problemen nachbessern, nach einer größeren Aufgabe ggf. einen neuen anlegen.
    Läuft im Hintergrund unter TURN_LOCK – die nächste Runde wartet kurz, statt mit der
    Reflexion um das Modell zu konkurrieren. Nach Web-Inhalt in der Runde kein Rückblick auf sie."""
    import lernschleife as LS
    import skills
    wer = PS.active().key                         # alles hier gehört dieser Persönlichkeit
    diese, vorige = LS.runde_abschliessen(wer)
    anlass = LS.anlass(nutzertext, records)
    verdacht = LS.betroffen(anlass, diese, vorige)
    try:
        _skill_strichliste(diese, verdacht, wer)
    except Exception:
        pass
    backend = ctx.backend
    if backend is None or not LS.an():
        return
    grund = None if web else anlass
    pflegen = [] if web or not skills.selbst() else verdacht[:2]
    anlegen = not web and skills.selbst() and LS.neuer_skill_anlass(anlass, records, diese)
    if grund is None and not weg and not pflegen and not anlegen:
        return

    async def lauf() -> None:
        saved, skill_meldungen = [], []
        async with TURN_LOCK:
            if ctx.backend is not backend or PS.active().key != wer:   # Modell/Persönlichkeit gewechselt
                return
            try:
                if weg:
                    saved += await reflexion(backend, LS.SICHERN, nachrichten=weg, max_zeilen=5)
                if grund:
                    saved += await reflexion(backend, LS.zusatz(grund, records))
            except Exception:
                pass
            for k in pflegen:
                try:
                    if (erg := await skill_pflegen(backend, k, anlass)):
                        skill_meldungen.append(("verbessert", *erg))
                except Exception:
                    pass
            if anlegen:
                try:
                    if (erg := await skill_anlegen(backend, records)):
                        skill_meldungen.append(("neu", *erg))
                except Exception:
                    pass
        for art, name, grund_w, was in skill_meldungen:
            if grund_w:
                ui.info(f"🛡 Skill-Wächter hielt „{name}“ auf: {grund_w}.")
                LS.melden(f"Der Skill-Wächter hielt deine Änderung an Skill „{name}“ auf: {grund_w}.", wer)
            elif art == "verbessert":
                ui.info(f"🧩 Skill „{name}“ verbessert.")
                LS.melden(f"Skill „{name}“ nach einem Problem verbessert – {was or 'kleine Änderung'}.", wer)
            else:
                ui.info(f"🧩 Neuer Skill: „{name}“.")
                LS.melden(f"Neuen Skill „{name}“ angelegt: {was}.", wer)
        if saved:
            ui.info("🪞 Gelernt: " + "  ·  ".join(f"{art}: {t}" if t else art for art, t in saved))
            LS.melden("Ins Gedächtnis übernommen: "
                      + "; ".join(f"{art} „{t}“" if t else art for art, t in saved) + ".", wer)
            await bibliothekar(ctx.current_chat)
            memory.release_encoder()

    aufgabe = asyncio.get_running_loop().create_task(lauf())
    _LERN_AUFGABEN.add(aufgabe)
    aufgabe.add_done_callback(_LERN_AUFGABEN.discard)


def _kugel_stand() -> dict | None:
    """Kugel-Bild wie die Kugel es wählt (Stimmung, sonst neutral; „kugel“ = ohne Bild),
    dazu das Denk-Bild. stand ändert sich, wenn ein Bild neu gemalt wurde."""
    from wache import steuerung
    try:
        da = steuerung.bilder(PS.active().name)
        stimmung = str(steuerung.lesen().get("stimmung") or "")
        aktuell = stimmung if stimmung in da else ""
        if stimmung == "kugel" or aktuell not in da:
            return None
        denkt = "denkt" if "denkt" in da else None
        stand = f"{PS.active().key}:{max(int(da[k].stat().st_mtime) for k in (aktuell, denkt) if k is not None)}"
    except OSError:
        return None
    return {"aktuell": aktuell, "denkt": denkt, "stand": stand}


def start_chrome_bridge(ctx: "Ctx") -> None:
    """Empfangsstelle für die Chrome-Erweiterung starten – nur 127.0.0.1, mit Schlüssel.
    Läuft still mit; schlägt der Port fehl, sagt /chrome warum."""
    global CHROME_BRIDGE
    import chromebridge

    async def beantworten(nachricht: str) -> str:
        """Ein Chat-Zug im NemiCLI-Fenster; zurück kommt nur der gesäuberte Antworttext."""
        if ctx.backend is None:
            raise RuntimeError("Kein Modell aktiv – in NemiCLI /model wählen.")
        import kontextmenue
        n_vorher = len(ctx.backend.messages)
        if ui.TUI is not None and hasattr(ui.TUI, "_echo_user"):
            ui.TUI._echo_user(nachricht.split("\n", 1)[0][:160] + " …")
        await handle_line(ctx, nachricht)
        neu = ctx.backend.messages[n_vorher:]
        antworten = [m.get("content") for m in neu
                     if m.get("role") == "assistant" and isinstance(m.get("content"), str)]
        return kontextmenue.antwort_text(antworten[-1]) if antworten else ""

    async def chatten(auftrag: dict, senden, fragen) -> None:
        """Seitenleiste: Chat-Zug wie im GUI-Modus „Chat“; danach zieht das GUI-Fenster mit."""
        await _fenster_zug(ctx, auftrag["text"], senden, fragen, erlaubt=CHAT_WERKZEUGE,
                           quelle="chrome", kopf="🐈 Chrome", zusatz=auftrag["zusatz"],
                           bilder=auftrag["bilder"], fremd=auftrag["fremd"], anzeige=auftrag["anzeige"])
        _gui_spiegeln(ctx)

    async def zustand() -> dict:
        p = PS.active()
        return {"persona": p.name, "model": ctx.model or "—", "chat": ctx.current_chat,
                "beschaeftigt": TURN_LOCK.locked(), "verlauf": _gui_verlauf(ctx), "kugel": _kugel_stand()}

    async def neu() -> dict:
        await _chat_neu(ctx, "🐈 Chrome")
        return await zustand()

    def bild_erlaubt(pfad: Path) -> bool:
        """Nur Bilder aus den Bilder-Ordnern der Persönlichkeiten."""
        import profilordner
        try:
            echt = pfad.resolve(strict=True)
            return any(echt.is_relative_to(o.resolve()) for o in profilordner.bilder_ordner_alle())
        except (OSError, ValueError):
            return False

    def kugelbild(stimmung: str) -> Path | None:
        from wache import steuerung
        return steuerung.bilder(PS.active().name).get(stimmung)

    try:
        loop = asyncio.get_running_loop()
        CHROME_BRIDGE = chromebridge.Bridge(loop, beantworten, chromebridge.key(), chatten=chatten,
                                            zustand=zustand, neu=neu, bild_erlaubt=bild_erlaubt,
                                            kugelbild=kugelbild)
        if not CHROME_BRIDGE.start():
            ui.warn(f"🐈 Chrome-Empfang nicht gestartet: {CHROME_BRIDGE.letzter_fehler}")
    except Exception as e:
        CHROME_BRIDGE = None
        ui.warn(f"🐈 Chrome-Empfang nicht gestartet: {e}")


async def start_webui(ctx: "Ctx") -> "webui.WebBridge":
    """Startet den WebUI-Server und verbindet ihn mit der aktuellen Sitzung (ctx).
    Gibt die Brücke zurück (oder wirft bei Fehler)."""
    loop = asyncio.get_running_loop()
    bridge = webui.WebBridge(loop)

    subagents.on_aktiv(lambda n: bridge.broadcast({"t": "helfer", "n": n}))

    def web_state() -> dict:
        return {
            "t": "state",
            "helfer": subagents.aktiv(),
            "model": ctx.model or "—",
            "chat": ctx.current_chat,
            "tokens": SESSION["tokens"],
            "strength": ctx.strength if ctx.strength_active() else None,
            "ctx": SESSION.get("ctx") or 0,
            "ctx_max": SESSION.get("ctx_max") or 0,
            "cost": SESSION.get("cost") or 0.0,
        }

    async def do_turn(text: str, image_uris: list) -> None:
        async with TURN_LOCK:
            if ctx.backend is None:
                bridge.broadcast({"t": "warn", "text": "Kein Modell aktiv – oben eins wählen."})
                bridge.broadcast({"t": "turn_end"})
                return
            bridge.broadcast({"t": "busy", "on": True})
            try:
                txt = emoji.expand(text or "")
                dateien = anhang.anhaengen(txt)
                txt = dateien["text"]
                if (h := anhang.hinweis(dateien)):
                    bridge.broadcast({"t": "info", "text": h})
                txt, imgs, hinweis = await _bilder_fuer_chat(
                    ctx.model, txt, extra_uris=list(image_uris or []))
                if hinweis:
                    bridge.broadcast({"t": "info", "text": hinweis})
                if not imgs and not txt.strip():
                    return
                await web_converse(ctx.backend, txt, imgs,
                                   bridge.broadcast, bridge.ask)
                ctx.current_prompts.append(txt)
                chatstore.save(ctx.current_chat, ctx.backend.messages,
                               ctx.current_prompts, ctx.model or "—")
                learn.capture_from_answer(txt, ctx.backend.messages, ctx.current_chat)
                await bibliothekar(ctx.current_chat, erzwingen=_chat_jetzt(ctx))
                memory.release_encoder()
            except Exception as e:
                bridge.broadcast({"t": "error", "text": f"Fehler: {e}"})
            finally:
                bridge.broadcast({"t": "busy", "on": False})
                bridge.broadcast({"t": "turn_end"})
                bridge.broadcast(web_state())
                ctx.sync_session()

    async def do_switch(ref: str) -> None:
        if not ref:
            return
        ctx.backend, ctx.model = await switch_model(ref, ctx.backend, ctx.model, ctx.strength)
        if _FORTSETZEN_NACH_MODELL[0] and ctx.backend is not None:
            _FORTSETZEN_NACH_MODELL[0] = False
            if not ctx.current_prompts:
                _gespraech_fortsetzen(ctx)
        _kontext_neu_zaehlen(ctx)
        bridge.broadcast({"t": "info", "text": f"Modell aktiv: {ctx.model}"})
        bridge.broadcast(web_state())
        ctx.sync_session()

    async def do_newchat() -> None:
        async with TURN_LOCK:
            if ctx.backend:
                ctx.backend.reset()
            ctx.current_prompts = []
            ctx.current_chat = chatstore.next_id()
            ctx.sync_session()
            bridge.broadcast({"t": "chat_switch", "chat": ctx.current_chat, "title": ""})
            bridge.broadcast({"t": "info", "text": "Neuer Chat gestartet."})
            bridge.broadcast(web_state())

    async def list_all_models() -> list:
        def _ollama() -> list:
            # Synchrone HTTP-Abfragen an localhost: im Faden, sonst steht die Oberfläche,
            # solange ein nicht laufendes Ollama die Verbindung ablehnt.
            if not P.get("ollama").available():
                return []
            return [{"ref": M.make_ref("ollama", mid), "label": mid + (" 👁" if P.ollama_is_vision(mid) else ""),
                     "group": "Ollama"} for mid in P.ollama_models()]
        out = await asyncio.to_thread(_ollama)
        for pid, p in (await asyncio.to_thread(P.detected)).items():
            if p.keyless:
                continue
            try:
                ids = await P.list_models(pid)
            except Exception:
                ids = []
            for mid in ids:
                out.append({"ref": M.make_ref(pid, mid), "label": mid, "group": "☁ " + p.label})
        return out

    def schedule(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def models_blocking() -> list:
        try:
            return schedule(list_all_models()).result(timeout=25)
        except Exception:
            return []

    handlers = {
        "send":    lambda text, images: schedule(do_turn(text, images)),
        "switch":  lambda ref: schedule(do_switch(ref)),
        "models":  models_blocking,
        "state":   web_state,
        "newchat": lambda: schedule(do_newchat()),
    }
    webui.start(bridge, handlers)
    return bridge


def _gui_verlauf(ctx: "Ctx") -> list[dict]:
    """Der aktuelle Chat fürs GUI-Fenster: Nutzer, Antwort (ohne Aktionsblöcke) und
    Werkzeug-Ergebnisse (kurz) – in der Reihenfolge des Verlaufs."""
    import chromebridge
    if ctx.backend is None:
        return []
    eigene = set(ctx.current_prompts or [])
    raus = []

    def als_text(inhalt) -> str:
        if isinstance(inhalt, list):
            return " ".join(t.get("text", "") for t in inhalt if isinstance(t, dict))
        return str(inhalt or "")

    for m in ctx.backend.messages:
        rolle, text = m.get("role"), als_text(m.get("content"))
        if rolle == "user":
            if text in eigene or lernschleife.ohne_hinweise(text) in eigene:
                raus.append({"rolle": "nutzer",
                             "text": chromebridge.anzeige_kuerzen(lernschleife.ohne_hinweise(text))})
            elif text.strip():
                raus.append({"rolle": "werkzeug", "text": text[:600]})
        elif rolle == "assistant":
            acts, sauber = actions.parse_actions(text)
            if sauber.strip():
                raus.append({"rolle": "ki", "text": sauber})
            for a in acts:
                raus.append({"rolle": "aktion", "text": actions.describe(a)})
    return raus


async def skills_befehl(arg: str) -> None:
    """/skills – Liste · selbst an|aus · alte (Notizen aus learned/skills durchsehen)."""
    import skills
    teile = arg.split()
    was = teile[0].lower() if teile else ""
    if was == "selbst":
        if len(teile) > 1 and teile[1].lower() in ("an", "aus"):
            skills.selbst_setzen(teile[1].lower() == "an")
        ui.info("🧩 Skills selbst anlegen/ändern: "
                + ("AN – ohne Prüffenster, der Skill-Wächter passt auf; nach Inhalt aus dem Netz in der Runde "
                   "keine Skill-Änderung. Nach Problemen bessert NemiCLI benutzte Skills selbst nach."
                   if skills.selbst() else "AUS – jeder neue oder geänderte Skill geht durchs Prüffenster; "
                   "die automatische Skill-Pflege ruht."))
        return
    if was == "alte":
        await _alte_skills()
        return
    liste = skills.alle()
    if liste:
        ui.kv_panel(f"🧩 Skills von {PS.active().name} und für alle",
                    [(s.name + (" [für alle]" if s.fuer_alle else ""),
                      f"{s.beschreibung}\nwann: {s.wann}\n{skills.bilanz(skills.statistik(s))}")
                     for s in liste])
    else:
        ui.info("🧩 Noch keine Skills. Im Chat: „Lass uns einen Skill machen“.")
    import skillwaechter
    if (bericht := skillwaechter.bericht_text()):
        ui.text_panel("🛡 Skill-Wächter – zuletzt", bericht)
    if (offen := skills.alte()):
        ui.info(f"📝 {len(offen)} alte Notiz(en) aus learned/skills warten auf Durchsicht: /skills alte")
    ui.info(f"Ordner: {skills.profil_ordner()}  ·  für alle: {skills.ordner()}  ·  "
            f"selbst anlegen: {'an' if skills.selbst() else 'aus'} "
            "(/skills selbst an|aus)")


async def _alte_skills() -> None:
    """Alte Notizen einzeln zeigen: F8 übernimmt als Skill, sonst weglassen oder später."""
    import skills
    offen = skills.alte()
    if not offen:
        ui.info("📝 Keine alten Notizen mehr offen.")
        return
    for nr, pfad in enumerate(offen, 1):
        try:
            felder = skills.alt_zerlegen(pfad)
        except (OSError, UnicodeDecodeError) as e:
            ui.warn(f"{pfad.name}: nicht lesbar ({e})")
            continue
        if skills.finden(felder["name"]) is not None:
            felder["name"] += " (alt)"
        if skills.pruefen(**felder):
            skills.alt_gesehen(pfad)
            ui.info(f"📝 {pfad.name}: leer – übersprungen")
            continue
        vorschau = (f"Alte Notiz {nr}/{len(offen)}: {pfad}\nF8 übernimmt sie als Skill, Esc nicht.\n"
                    f"Beschreibung und „wann“ sind geraten – danach im Chat verbessern lassen.\n\n"
                    + skills.text_bauen(**felder))
        if await review_action_confirm("Alte Notiz als Skill übernehmen?", vorschau):
            ziel = skills.schreiben(**felder, fuer_alle=True)          # alte Notizen galten für alle
            skills.alt_gesehen(pfad)
            ui.success(f"🧩 Übernommen: {felder['name']}  →  {ziel}")
            continue
        wahl = await ask_confirm(f"„{felder['name']}“ nicht übernehmen –", [
            ("weg", "Weglassen (nicht mehr fragen; die Notiz bleibt in learned/skills)"),
            ("spaeter", "Später nochmal zeigen"),
            ("stopp", "Aufhören")])
        if wahl == "weg":
            skills.alt_gesehen(pfad)
        elif wahl == "stopp":
            break


async def name_befehl(arg: str) -> None:
    """/name – Name, Wunsch-Anrede und „über mich“; gilt ab der ersten Nachricht (nutzerprofil.py)."""
    import nutzerprofil as NP
    was = arg.strip().lower()
    alt = NP.laden()
    if was in ("loeschen", "löschen"):
        if await ask_confirm("Name, Anrede und „über mich“ wirklich löschen?",
                             [("ja", "Ja, löschen"), ("nein", "Nein")]) == "ja":
            NP.speichern("", "", "")
            ui.success("👤 Profil gelöscht.")
        return
    if was != "zeigen":
        ui.info("👤 Über dich – Enter behält den bisherigen Wert, ein einzelnes „-“ leert das Feld.")
        felder = {}
        for schluessel, frage in (("name", "Wie heißt du?"),
                                  ("anrede", "Wie möchtest du genannt werden? (mehrere mit Komma)"),
                                  ("ueber", "Erzähl etwas über dich – was du magst, was nicht, was die KI wissen soll:")):
            bisher = alt[schluessel]
            eingabe = (await ask_text(f"{frage}" + (f"  [bisher: {bisher[:60]}{'…' if len(bisher) > 60 else ''}]"
                                                    if bisher else ""))).strip()
            felder[schluessel] = "" if eingabe == "-" else (eingabe or bisher)
        alt = NP.speichern(**felder)
        ui.success("👤 Gespeichert – gilt ab der nächsten Nachricht, für jede Persönlichkeit.")
    if NP.leer(alt):
        ui.info("👤 Noch nichts eingetragen: /name")
        return
    ui.kv_panel("👤 Über dich", [("Name", alt["name"] or "–"), ("Anrede", alt["anrede"] or "–"),
                                  ("Über dich", alt["ueber"] or "–")])


def _gui_zustand(ctx: "Ctx") -> dict:
    import nutzerprofil
    import skills
    p = PS.active()
    return {"t": "state", "model": ctx.model or "—", "chat": ctx.current_chat,
            "modus": modes.label(), "ordner": str(Path.cwd()),
            "persona": {"key": p.key, "name": p.name, "kurz": p.kurz},
            "personas": [{"key": k, "name": v.name, "kurz": v.kurz, "aktiv": k == p.key}
                         for k, v in PS.list_all().items()],
            "skills": [{"kennung": s.kennung, "name": s.name, "beschreibung": s.beschreibung, "wann": s.wann,
                        "fuer_alle": s.fuer_alle} for s in skills.alle()],
            "skills_selbst": skills.selbst(),
            "profil": nutzerprofil.laden(),
            "stimme": _stimme_zustand()}


def _stimme_zustand() -> dict:
    try:
        import stimme
        return {"modus": stimme.modus(), "modell": stimme.verfuegbar()}
    except Exception:
        return {"modus": "aus", "modell": False}


def _gui_bilder(anzahl: int = 300) -> list[str]:
    """Galerie: Bilder der aktiven Persönlichkeit und die gemeinsamen von früher, neueste zuerst."""
    import profilordner
    dateien = []
    for ordner in profilordner.bilder_ordner_alle():
        try:
            dateien += [d for d in ordner.iterdir() if d.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        except OSError:
            continue
    dateien.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return [str(d) for d in dateien[:anzahl]]


def _gui_profil(ctx: "Ctx", bruecke, auftrag: dict) -> None:
    """„Über dich“ aus dem Desktop-Fenster speichern (auf der Sitzungs-Schleife)."""
    import nutzerprofil
    nutzerprofil.speichern(str(auftrag.get("name") or ""), str(auftrag.get("anrede") or ""),
                           str(auftrag.get("ueber") or ""))
    ui.info("🖥 GUI: „Über dich“ gespeichert – gilt ab der nächsten Nachricht.")
    bruecke.senden(_gui_zustand(ctx))


def _gui_skill(bruecke, kennung: str) -> None:
    import skills
    s = next((s for s in skills.alle() if s.kennung == kennung), None)
    if s is not None:
        bruecke.senden({"t": "skill", "name": s.name, "wann": s.wann, "text": s.anleitung})


def _gui_spiegeln(ctx: "Ctx") -> None:
    """Nach einem Zug oder Chatwechsel im Terminal: GUI-Fenster auf denselben Stand."""
    if GUI_BRUECKE is not None:
        GUI_BRUECKE.senden(_gui_zustand(ctx))
        GUI_BRUECKE.senden({"t": "verlauf", "chat": ctx.current_chat, "eintraege": _gui_verlauf(ctx)})


async def _fenster_zug(ctx: "Ctx", text: str, senden, fragen, *, erlaubt, quelle: str, kopf: str,
                       zusatz: str = "", bilder: list[str] | None = None, fremd: bool = False,
                       anzeige: str | None = None) -> None:
    """Ein Chat-Zug aus einem Fenster neben dem Terminal (GUI, Chrome-Seitenleiste).

    senden(ev) bekommt die Ereignisse, fragen(...) beantwortet Nachfragen. zusatz (Fremdinhalt)
    wird erst nach Anhängen und Bildpfaden angefügt – Pfade darin werden nie geöffnet.
    bilder: Data-URIs; anzeige: Kurzform der Nachricht für Fenster und Terminal."""
    async with TURN_LOCK:
        if ctx.backend is None:
            senden({"t": "warn", "text": "Kein Modell aktiv – im Terminal mit /model wählen."})
            senden({"t": "turn_end"})
            return
        senden({"t": "busy", "on": True})
        fertig = {"text": ""}
        anzeige = anzeige or text

        def melden(ev: dict) -> None:
            if ev.get("t") == "answer_end" and ev.get("clean"):
                fertig["text"] = ev["clean"]
            if ev.get("t") == "receipt":
                fertig["records"] = ev.get("records") or []
            senden(ev)
        try:
            txt = emoji.expand(text or "")
            dateien = anhang.anhaengen(txt)
            txt = dateien["text"]
            if (h := anhang.hinweis(dateien)):
                senden({"t": "info", "text": h})
            txt, imgs, hinweis = await _bilder_fuer_chat(ctx.model, txt, bilder)
            if hinweis:
                senden({"t": "info", "text": hinweis})
            eigen, txt = txt, txt + zusatz
            if not imgs and not txt.strip():
                return
            senden({"t": "nutzer", "text": anzeige, "quelle": quelle})   # wie getippt, ohne Datenblöcke
            from rich.text import Text
            ui.console.print(Text(f"{kopf}: {anzeige}", style="bold"))
            await web_converse(ctx.backend, txt, imgs, melden, fragen, erlaubt=erlaubt, fremd=fremd)
            if fertig["text"]:
                ui.console.print(ui.assistant_panel(fertig["text"]))
            ctx.current_prompts.append(txt)
            chatstore.save(ctx.current_chat, ctx.backend.messages, ctx.current_prompts, ctx.model or "—")
            learn.capture_from_answer(eigen, ctx.backend.messages, ctx.current_chat)
            await bibliothekar(ctx.current_chat, erzwingen=_chat_jetzt(ctx))
            memory.release_encoder()
            lernen_planen(ctx, text, fertig.get("records") or [], actions.web_tainted(),
                          pricing.weggefallen_holen(ctx.backend))
        except Exception as e:
            senden({"t": "error", "text": f"Fehler: {e}"})
        finally:
            senden({"t": "busy", "on": False})
            senden({"t": "turn_end"})
            ctx.sync_session()


async def _chat_neu(ctx: "Ctx", kopf: str) -> None:
    """Neuer Chat aus einem Fenster; GUI und Terminal ziehen mit."""
    async with TURN_LOCK:
        if ctx.backend:
            ctx.backend.reset()
        ctx.current_prompts = []
        ctx.current_chat = chatstore.next_id()
        ctx.sync_session()
        ui.info(f"{kopf}: neuer Chat")
    _gui_spiegeln(ctx)


async def start_gui(ctx: "Ctx"):
    """Brücke zum GUI-Fenster aufbauen (einmal je Sitzung)."""
    import gui_bruecke
    loop = asyncio.get_running_loop()
    bruecke = None

    async def zug(text: str, modus: str) -> None:
        await _fenster_zug(ctx, text, bruecke.senden, bruecke.fragen,
                           erlaubt=CHAT_WERKZEUGE if modus == "chat" else None, quelle="gui",
                           kopf=f"🖥 GUI ({'Chat' if modus == 'chat' else 'Work'})")
        bruecke.senden(_gui_zustand(ctx))

    async def neuer_chat() -> None:
        await _chat_neu(ctx, "🖥 GUI")

    async def persona_setzen(key: str) -> None:
        async with TURN_LOCK:                    # nie mitten in einem Zug wechseln
            if key in PS.list_all() and key != PS.active().key:
                PS.set_active(key)
                ui.info(f"🖥 GUI: Persönlichkeit {PS.active().name}")
                _sprueche_im_hintergrund()
                _neuer_chat_nach_wechsel(ctx)
        _gui_spiegeln(ctx)                       # Zustand, Chats und Verlauf der neuen

    def einstellungen(gemeldet: str = "") -> None:
        import einstellseite
        bruecke.senden({"t": "einstellungen", "eintraege": einstellseite.seite(), "gemeldet": gemeldet})

    async def einstellung(befehl: str, wert: str) -> None:
        """Eine Wahl der Einstellungsseite: nur, was die Seite gerade anbietet, als Befehl wie im Terminal."""
        import einstellseite
        zeile = einstellseite.befehl_fuer(befehl, wert)
        if zeile is None:
            einstellungen(f"⚠ {befehl} {wert} gibt es hier nicht (mehr).")
            return
        try:
            async with TURN_LOCK:                # nie mitten in einem Zug umstellen
                ui.info(f"⚙ GUI-Einstellung: {zeile}")
                await handle_line(ctx, zeile)
        except Exception as e:
            einstellungen(f"⚠ {zeile}: {e}")
            return
        einstellungen(f"✓ {zeile}")

    vorlese_nr = [0]

    async def vorlesen(text: str) -> None:
        """Eine Antwort aus dem Fenster vorlesen; am Ende (oder bei Stopp) bekommt das Fenster Bescheid."""
        import stimme
        vorlese_nr[0] += 1
        nr = vorlese_nr[0]
        stimme.stopp()
        if stimme.modus() == "aus":
            bruecke.senden({"t": "vorlesen_ende", "hinweis": "🔇 Die Stimme ist aus – im Terminal schaltet "
                                                            "/voice knopf sie für solche Anfragen ein."})
            return
        if not stimme.sagen(text):
            bruecke.senden({"t": "vorlesen_ende", "hinweis": "🔇 Kein Stimm-Modell gefunden (/voice zeigt mehr)."})
            return
        await asyncio.sleep(0.5)                 # bis der Stimm-Faden den Auftrag hat
        while stimme.spricht() and nr == vorlese_nr[0]:
            await asyncio.sleep(0.3)
        if nr == vorlese_nr[0]:
            bruecke.senden({"t": "vorlesen_ende"})

    def vorlesen_stopp() -> None:
        import stimme
        vorlese_nr[0] += 1
        stimme.stopp()

    plane = lambda coro: asyncio.run_coroutine_threadsafe(coro, loop)
    handlers = {
        "vorlesen": lambda a: plane(vorlesen(str(a.get("text") or ""))),
        "vorlesen_stopp": lambda a: vorlesen_stopp(),
        "einstellungen": lambda a: loop.call_soon_threadsafe(einstellungen),
        "einstellung": lambda a: plane(einstellung(str(a.get("befehl") or ""), str(a.get("wert") or ""))),
        "senden": lambda a: plane(zug(str(a.get("text") or ""),
                                      "work" if a.get("modus") == "work" else "chat")),
        "zustand": lambda a: loop.call_soon_threadsafe(_gui_spiegeln, ctx),
        "neu": lambda a: plane(neuer_chat()),
        "persona": lambda a: plane(persona_setzen(str(a.get("key") or ""))),
        "galerie": lambda a: bruecke.senden({"t": "galerie", "bilder": _gui_bilder()}),
        "skill": lambda a: _gui_skill(bruecke, str(a.get("kennung") or "")),
        "profil": lambda a: loop.call_soon_threadsafe(_gui_profil, ctx, bruecke, a),
    }
    bruecke = gui_bruecke.GuiBruecke(
        loop, handlers,
        bei_wechsel=lambda n: loop.call_soon_threadsafe(_fenster_steuern, "tray" if n else "zeigen"))
    return bruecke


_STEUER_NR = 0


def _fenster_steuern(befehl: str) -> None:
    """Befehl ans eigene Terminal-Fenster (terminal_fenster.py) über den Konsolentitel –
    nur dort; in anderen Terminals bliebe der Titel sonst sichtbar stehen."""
    global _STEUER_NR  # noqa: PLW0603
    if os.name != "nt" or os.environ.get("NEMICLI_FENSTER") != "1":
        return
    _STEUER_NR += 1
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(f"nemicli-steuer:{befehl}:{_STEUER_NR}")
    except Exception:
        pass


def gui_oeffnen(bruecke, schalter: str = "--gui") -> None:
    """Startet ein Fenster (--gui, --musik) als eigenen Prozess; es verbindet sich über die Brücke."""
    import subprocess
    env = dict(os.environ, NEMICLI_GUI_PIPE=bruecke.adresse, NEMICLI_GUI_KEY=bruecke.schluessel.hex())
    if getattr(sys, "frozen", False):
        befehl = [str(paths.exe_ohne_fenster()), schalter]
    else:
        py = Path(sys.executable)
        fensterlos = py.with_name("pythonw.exe")
        befehl = [str(fensterlos if fensterlos.is_file() else py), str(Path(__file__).resolve()), schalter]
    subprocess.Popen(befehl, env=env, cwd=os.getcwd())


async def start_musik(ctx: "Ctx"):
    """Brücke zum Musik-Fenster (/musik): Wunsch ausarbeiten lassen und komponieren."""
    import gui_bruecke
    loop = asyncio.get_running_loop()
    bruecke = None

    def antwort(art: str, **felder) -> None:
        bruecke.senden({"t": art, **felder})

    async def ausarbeiten(a: dict) -> None:
        from musik import wunsch
        if ctx.backend is None:
            antwort("fehler", text="Kein Modell verbunden – im Terminal erst ein Modell wählen.")
            return
        gesang = bool(a.get("gesang", True))
        sekunden = a.get("sekunden")
        antwort("status", text=f"✨ {PS.active().name} arbeitet den Wunsch aus …", laeuft=True)
        try:
            async with TURN_LOCK:
                roh = await ctx.backend.ask_once(
                    wunsch.anfrage(str(a.get("wunsch") or ""), sekunden, gesang, a.get("entwurf") or None),
                    wunsch.SYSTEM)
            antwort("entwurf", **wunsch.lesen(roh, gesang, sekunden))
        except Exception as e:
            antwort("fehler", text=f"Ausarbeiten fehlgeschlagen: {e}")

    async def komponieren(a: dict) -> None:
        import hintergrund
        import imagegen
        from musik import motor
        if (grund := motor.fehlt() or hintergrund.sperre("musik_machen")):
            antwort("fehler", text=grund)
            return

        def melden(text: str) -> None:
            antwort("status", text="🎵 " + text, laeuft=True)
        try:
            async with TURN_LOCK:
                erg = await asyncio.to_thread(imagegen._gpu_fuer_bild(motor.komponieren),
                                              str(a.get("stil") or ""), str(a.get("text") or ""),
                                              float(a.get("sekunden") or 30), bpm=a.get("bpm") or None,
                                              tonart=a.get("tonart") or None, sprache=a.get("sprache") or None,
                                              seed=a.get("seed"), on_status=melden,
                                              titel=str(a.get("titel") or ""), kuenstler=PS.active().name,
                                              planen=bool(a.get("planen", True)))
        except Exception as e:
            antwort("fehler", text=f"Komponieren fehlgeschlagen: {e}")
            return
        ui.info(f"🎵 Musik-Fenster: {erg['pfad']}")
        antwort("fertig", pfad=str(erg["pfad"]), titel=erg.get("titel") or "", seed=erg["seed"],
                sekunden=round(erg["sekunden"]), rechnen=round(erg["sekunden_rechnen"], 1))

    async def covern(a: dict) -> None:
        import hintergrund
        import imagegen
        from musik import motor, tondatei
        vorlage = Path(str(a.get("vorlage") or ""))
        if vorlage.suffix.lower() not in tondatei.ENDUNGEN or not vorlage.is_file():
            antwort("fehler", text=f"Keine lesbare Audiodatei: {vorlage}")
            return
        if (grund := motor.fehlt() or hintergrund.sperre("musik_cover")):
            antwort("fehler", text=grund)
            return

        def melden(text: str) -> None:
            antwort("status", text="🎙 " + text, laeuft=True)
        try:
            async with TURN_LOCK:
                erg = await asyncio.to_thread(imagegen._gpu_fuer_bild(motor.covern), vorlage,
                                              str(a.get("stil") or ""), a.get("text") or None,
                                              titel=str(a.get("titel") or ""), naehe=float(a.get("naehe") or 0),
                                              seed=a.get("seed"), on_status=melden, kuenstler=PS.active().name)
        except Exception as e:
            antwort("fehler", text=f"Cover fehlgeschlagen: {e}")
            return
        ui.info(f"🎙 Musik-Fenster (Cover): {erg['pfad']}")
        antwort("fertig", pfad=str(erg["pfad"]), titel=erg.get("titel") or "", seed=erg["seed"],
                sekunden=round(erg["sekunden"]), rechnen=round(erg["sekunden_rechnen"], 1))

    def songs(_a: dict) -> None:
        import profilordner
        ordner = profilordner.ordner("Musik", anlegen=False)
        liste = sorted(ordner.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)[:30] \
            if ordner.is_dir() else []
        antwort("songs", liste=[str(p) for p in liste], name=PS.active().name)

    plane = lambda coro: asyncio.run_coroutine_threadsafe(coro, loop)     # noqa: E731
    handlers = {
        "ausarbeiten": lambda a: plane(ausarbeiten(a)),
        "komponieren": lambda a: plane(komponieren(a)),
        "covern": lambda a: plane(covern(a)),
        "songs": songs,
    }
    bruecke = gui_bruecke.GuiBruecke(loop, handlers)
    return bruecke


AKTIVER_CTX = None              # die laufende Sitzung – für Stellen ohne ctx-Parameter


def _neuer_chat_nach_wechsel(ctx: "Ctx | None" = None) -> None:
    """Die Persönlichkeit hat gewechselt: Ihr eigenes Gespräch geht weiter (eigener Ordner, eigenes
    Kern-Gedächtnis); das bisherige bleibt bei der vorigen."""
    ctx = ctx or AKTIVER_CTX
    _startseite_wechsel()
    if ctx is None:
        return
    if ctx.backend is not None:
        ctx.backend.reset()
    ctx.current_prompts = []
    ctx.current_chat = chatstore.next_id()
    plan.verwerfen()
    weiter = _gespraech_fortsetzen(ctx)
    _kontext_neu_zaehlen(ctx)
    ctx.sync_session()
    name = PS.active().name
    ui.info(f"📒 Euer Gespräch geht weiter, {name}: „{weiter['title']}“" if weiter
            else f"📒 Neues Gespräch mit {name}.")


def _startseite_wechsel() -> None:
    """Andere Persönlichkeit: Bild, Name und Karten der Startseite neu, ihr Gruß gilt nicht mehr."""
    try:
        import startseite
        startseite.sammeln()
        startseite.STAND["gruss"] = ""
        startseite.auffrischen()
    except Exception:
        pass


def _sprueche_im_hintergrund(ctx: "Ctx | None" = None) -> None:
    """Hat die aktive Persönlichkeit noch keine eigenen Sprüche fürs Maskottchen,
    schreibt sie sie jetzt im Hintergrund (ein kurzer Aufruf). Beim nächsten Gruß
    spricht dann sie – nicht ein fester Spruchzettel."""
    ctx = ctx or AKTIVER_CTX
    if ctx is None or ctx.backend is None or mascot.hat_sprueche():
        return

    async def _lauf():
        try:
            if await mascot.sprueche_nachholen(ctx.backend):
                ui.info(f"💬 {PS.active().name} hat sich eigene Sprüche fürs Terminal geschrieben "
                        f"({mascot.sprueche_datei().name} – darf sie jederzeit ändern).")
        except Exception:
            pass
    try:
        asyncio.get_running_loop().create_task(_lauf())
    except RuntimeError:
        pass


async def start_spiele(ctx: "Ctx"):
    """/spiel – Schach, Mühle, Dame, TicTacToe im Browser gegen die Persönlichkeit.

    Eigener kleiner Server (tools/spiele.py), nichts mit /webui zu tun. Die
    Persönlichkeit wird je Zug über backend.ask_messages() gefragt – ein eigener
    Mini-Verlauf, keine Werkzeuge, der Terminal-Chat bleibt unberührt."""
    import spiele
    loop = asyncio.get_running_loop()

    async def denke(p: dict) -> dict:
        person = PS.active()
        nutzer = PS.nutzername() or "du"
        if p.get("wer"):                       # die Seite fragt beim Öffnen nur die Namen
            return {"name": person.name, "nutzer": nutzer}
        if ctx.backend is None:
            raise RuntimeError("Kein Modell aktiv – im Terminal mit /model eins wählen.")
        system = spiele.system_prompt(PS.render_text(person), person.name, nutzer,
                                      p.get("spiel", ""), p.get("farbe_ki", ""))
        p["nutzer"] = nutzer
        return await spiele.zug_holen(ctx.backend, system, p)

    server = spiele.SpieleServer(loop, denke)
    server.start()
    return server


def _migrate_ref(old: str | None) -> str | None:
    """Alte gemerkte Modellnamen (ohne Anbieter-Präfix) ins neue Ref-Schema heben."""
    if not old:
        return None
    if ":" in old:                       # schon im neuen Format
        return old
    if old.startswith("claude"):         # früher: bare Anthropic-Name
        return M.make_ref("anthropic", old)
    # Frueher stand hier noch die Umschreibung alter GGUF-Kurznamen
    # ("gemma-12b" -> local:...). Mit llama.cpp ist die entfallen.
    return None


async def make_backend(ref: str, carry: list | None, strength: str = M.DEFAULT_STRENGTH):
    """Baut das passende Backend für eine Modell-Referenz. Überträgt den Verlauf."""
    provider, model_id = M.split_ref(ref)
    import debugbericht as _DBG
    _DBG.ereignis(f"Modellwechsel: {ref}")

    if provider != "gguf" and "gguflokal" in sys.modules:
        sys.modules["gguflokal"].entladen()          # anderes Modell: VRAM freigeben
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Für Claude brauchst du einen ANTHROPIC_API_KEY in der .env.")
        from chat import Chat
        backend = Chat(model=model_id, strength=strength)
    elif provider == "gguf":
        import gguflokal
        if model_id not in gguflokal.modelle():
            raise RuntimeError(f"Kein lokales Modell '{model_id}' in {gguflokal.ORDNER}.")
        backend = gguflokal.GgufChat(model_id)
    elif provider == "funke":
        from einrichtungshelfer import funkechat
        if model_id != funkechat.MODELL or not funkechat.verfuegbar():
            raise RuntimeError(f"Kein lokales Modell '{model_id}' – Funke Groß ist nicht mitgeliefert.")
        backend = funkechat.FunkeChat(ref)
    else:
        prov = P.get(provider)
        if prov is None:
            raise RuntimeError(f"Unbekannter Anbieter: '{provider}'.")
        if not prov.available():
            raise RuntimeError(f"Kein Key für {prov.label} – {prov.env} in der .env setzen.")
        from cloud import CloudChat
        backend = CloudChat(provider, model_id, strength=strength)

    if carry:
        backend.messages = carry
    return backend


def _fmt_bytes(n: int) -> str:
    return f"{n / 1_048_576:.0f} MB" if n >= 1_048_576 else f"{n / 1024:.0f} KB"


async def _run_with_status(title: str, work) -> str:
    """Führt blockierende Arbeit (work(on_status) -> str) in einem Thread aus und
    zeigt dabei eine Live-Fortschrittsanzeige. Gibt das Ergebnis zurück."""
    state = {"msg": "starte …", "done": False, "result": "", "err": None}

    def on_status(m: str) -> None:
        state["msg"] = m

    def runner() -> None:
        try:
            state["result"] = work(on_status)
        except Exception as e:                  # an den Aufrufer durchreichen
            state["err"] = e
        finally:
            state["done"] = True

    task = asyncio.create_task(asyncio.to_thread(runner))
    with ui.live_view(ui.progress_panel(title, state["msg"])) as live:
        while not state["done"]:
            live.update(ui.progress_panel(title, state["msg"]))
            await asyncio.sleep(0.2)
        live.update(ui.progress_panel(title, state["msg"]))
    await task
    if state["err"]:
        raise state["err"]
    return state["result"] or ""


# Der Bibliothekar (indexdb.after_turn) läuft nach jeder Runde und zeigt seinen Fortschritt
# als Balken. Er darf den Chat nicht blockieren (ein großer Wache-Bericht = 60 Brocken
# ≈ 90 s auf der CPU). Deshalb zwei Wege:
#   TUI:      Hintergrund-Task, Fortschritt als Badge mit Mini-Balken in der Statuszeile
#             (screen_tx.set_bibliothekar); der Nutzer tippt derweil weiter. Läuft schon
#             einer, wird nur „nochmal“ vorgemerkt – nie zwei gleichzeitig.
#   Klassik:  wie gehabt inline mit Panel (rich.Live und Prompt vertragen sich nicht
#             gleichzeitig), aber portionsweise (indexdb-Grenzen je Runde).
_BIB: dict = {"task": None, "nochmal": None}


def _chat_jetzt(ctx) -> bool:
    """Laufenden Chat sofort einbetten? Ja, wenn das Kontext-Aufräumen (pricing.kuerzen) in
    dieser Runde Nachrichten ausgelagert hat. Bei kleinem Fenster (lokale Modelle) wird schon
    nach der Hälfte des Verlaufs-Budgets eingebettet – dort wird vor CHAT_SCHRITT aufgeräumt."""
    if getattr(ctx.backend, "weggefallen", None):
        return True
    try:
        schritt = int(pricing.context_window(ctx.backend.model) * pricing.HISTORY_SHARE / 2)
        return (schritt < indexdb.CHAT_SCHRITT and bool(ctx.current_chat)
                and indexdb.chat_faellig(ctx.current_chat, schritt))
    except Exception:
        return False


def _bibliothekar_lauf(chat_id, alles: bool, on_status, erzwingen: bool = False) -> dict:
    """Blockierend (im Thread): after_turn + Ergebnis. `n` = neu gespeicherte Brocken,
    `mit_vektor` = wie viele Brocken in diesem Lauf einen Vektor bekommen haben."""
    out = {"n": 0, "err": None, "mit_vektor": 0, "neu": 0}
    try:
        vorher = indexdb.offen()
        out["n"] = indexdb.after_turn(chat_id, status=on_status, alles=alles, chat_erzwingen=erzwingen)
        nachher = indexdb.offen()
        # Direkt gezählt: Brocken insgesamt und Brocken mit Vektor des aktiven Modells.
        out["neu"] = max(0, nachher["brocken"] - vorher["brocken"])
        out["mit_vektor"] = max(0, (nachher["brocken"] - nachher["ohne_vektor"])
                                - (vorher["brocken"] - vorher["ohne_vektor"]))
    except Exception as e:
        out["err"] = e
    return out


def _bibliothekar_text(res: dict) -> str:
    """Ehrliche Zeile: gespeichert ist nicht dasselbe wie mit Vektor versehen."""
    text = f"📚 Gedächtnis: {res.get('neu', 0)} Brocken neu, {res.get('mit_vektor', 0)} mit Vektor"
    if not res.get("mit_vektor"):
        try:
            text += f" – Suche nur nach Stichworten ({memory.encoder_status()})"
        except Exception:
            pass
    return text


BIB_RUHE_S = 20.0          # Vektorisieren erst nach so viel Ruhe (keine Runde, kein Bild/Video, keine Eingabe)


def _beschaeftigt(tui) -> bool:
    """Läuft gerade etwas, dem der Bibliothekar CPU, GPU und Python wegnehmen würde?
    Der Übungsmodus zählt nicht – er setzt `busy` dauerhaft."""
    if tui is not None and getattr(tui, "busy", False) and not getattr(tui, "_practicing", False):
        return True
    try:
        import hintergrund
        if hintergrund.gpu_belegt():
            return True
    except Exception:
        pass
    return memory.gpu_pause()


async def _ruhe_abwarten(tui, erzwingen: bool) -> None:
    """Vektorisiert wird in ruhigen Phasen: BIB_RUHE_S ohne Runde, Bild oder Video und ohne
    Eingabe (ein ausgelagerter Chat soll schneller findbar sein: dann ein Viertel davon)."""
    noetig = BIB_RUHE_S / 4 if erzwingen else BIB_RUHE_S
    ruhig_seit, marke = time.monotonic(), indexdb._unterbrechung
    while True:
        await asyncio.sleep(1.0)
        eingabe = getattr(tui, "_last_input", 0.0) if tui is not None else 0.0
        if _beschaeftigt(tui) or indexdb._unterbrechung != marke:
            ruhig_seit, marke = time.monotonic(), indexdb._unterbrechung
            continue
        if time.monotonic() - max(ruhig_seit, eingabe) >= noetig:
            return


async def _bibliothekar_hintergrund(chat_id, alles: bool, erzwingen: bool = False) -> None:
    tui = ui.TUI
    try:
        while True:
            if not alles:                              # /gedaechtnis indexieren will es jetzt
                await _ruhe_abwarten(tui, erzwingen)
            zuletzt = {"msg": ""}

            def on_status(m: str) -> None:
                zuletzt["msg"] = m
                if tui is not None:
                    try:
                        tui.app.call_from_thread(tui.set_bibliothekar, m)
                    except Exception:
                        pass

            res = await asyncio.to_thread(_bibliothekar_lauf, chat_id, alles, on_status, erzwingen)
            memory.release_encoder()
            if res["err"]:
                ui.info(f"📚 Bibliothekar: {res['err']}")
            elif res["n"]:
                rest = ""
                try:
                    o = indexdb.offen()
                    offen = o["berichte_offen"] + o["ohne_vektor"]
                    if offen and not alles:
                        rest = f" – noch {o['berichte_offen']} Berichte / {o['ohne_vektor']} Brocken offen, geht nächste Runde weiter (/gedaechtnis indexieren = alles jetzt)"
                except Exception:
                    pass
                ui.info(_bibliothekar_text(res) + rest + ".")
            if _BIB["nochmal"] is None:
                break
            chat_id, alles, erzwingen = _BIB["nochmal"]
            _BIB["nochmal"] = None
    finally:
        _BIB["task"] = None
        if tui is not None:
            try:
                tui.set_bibliothekar("")
            except Exception:
                pass


async def bibliothekar(chat_id: int | None = None, alles: bool = False, erzwingen: bool = False) -> int:
    """Nach der Runde: Notizen und Skills einbetten, den Chat je indexdb.CHAT_SCHRITT Token
    (`erzwingen`: sofort, z. B. nach dem Kontext-Aufräumen), dann Rückstände, bis die nächste
    Runde beginnt. Im TUI im Hintergrund (Badge in der Statuszeile, Rückgabe 0), in der
    Klassik-Oberfläche inline mit Panel."""
    if ui.TUI is not None:
        if _BIB["task"] is not None and not _BIB["task"].done():
            alt = _BIB["nochmal"] or (None, False, False)
            _BIB["nochmal"] = (chat_id or alt[0], alles or alt[1], erzwingen or alt[2])
            return 0
        _BIB["task"] = asyncio.create_task(_bibliothekar_hintergrund(chat_id, alles, erzwingen))
        return 0

    state = {"msg": "", "done": False, "res": None}

    def on_status(m: str) -> None:
        state["msg"] = m

    def runner() -> None:
        try:
            state["res"] = _bibliothekar_lauf(chat_id, alles, on_status, erzwingen)
        finally:
            state["done"] = True

    task = asyncio.create_task(asyncio.to_thread(runner))
    titel = "📚 Bibliothekar · vektorisiere fürs Gedächtnis"
    live_cm = live = None
    try:
        while not state["done"]:
            if live is None and state["msg"]:            # Panel erst, wenn wirklich eingebettet wird
                live_cm = ui.live_view(ui.progress_panel(titel, state["msg"]))
                live = live_cm.__enter__()
            if live is not None:
                live.update(ui.progress_panel(titel, state["msg"]))
            await asyncio.sleep(0.15)
    finally:
        if live_cm is not None:
            live_cm.__exit__(None, None, None)
    await task
    res = state["res"] or {"n": 0, "err": None, "mit_vektor": 0}
    if res["err"]:
        ui.info(f"📚 Bibliothekar: {res['err']}")
    elif res["n"]:
        ui.info(_bibliothekar_text(res) + ".")
    return res["n"]


async def _backup_befehl(arg: str) -> None:
    """/backup [teil | voll | voll modelle] [ganz] | pruefen [name] | auspacken <name> | ordner."""
    import backup
    from rich.markup import escape
    teile = arg.lower().split()
    was = teile[0] if teile else ""
    if not was:
        import autobackup
        ui.text_panel("💾 Backups", backup.status_text() + "\n\n" + autobackup.status_text())
        return
    if was == "auto":
        import autobackup
        if teile[1:2] == ["jetzt"]:
            r = await asyncio.to_thread(autobackup.sichern, True)
            (ui.success if r.get("ok") else ui.warn)(escape(r["text"]))
        else:
            ui.text_panel("🔁 Automatische Sicherung", autobackup.status_text())
        return
    if was == "ordner":
        z = backup.ordner()
        z.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(z))
        except Exception as e:
            ui.warn(f"Öffnen fehlgeschlagen: {escape(str(e))}")
        return
    if was in ("pruefen", "prüfen"):
        name = arg.split(None, 1)[1].strip() if len(teile) > 1 else None
        try:
            ergebnis = await _run_with_status("🔍 prüfe Backups",
                                              lambda on_status: backup.pruefen(name, melde=on_status))
        except Exception as e:
            ui.error(f"Prüfen fehlgeschlagen: {escape(str(e))}")
            return
        if not ergebnis:
            ui.text_panel("💾 Backups", backup.status_text())
        for datei, ok, text in ergebnis:
            (ui.success if ok else ui.error)(escape(f"{'✅' if ok else '❌'} {datei}: {text}"))
        return
    halt = {"an": False}

    def _halt() -> bool:
        try:
            if ui.TUI.consume_abort() if ui.TUI is not None else _esc_pressed():
                halt["an"] = True
        except Exception:
            pass
        return halt["an"]

    if was == "auspacken":
        name = arg.split(None, 1)[1].strip() if len(teile) > 1 else ""
        if not name:
            ui.text_panel("💾 Backups", backup.status_text())
            ui.info("Wiederherstellen: /backup auspacken <name aus der Liste>")
            return
        los = await ask_confirm(
            f"📦 {name} in einen neuen Ordner unter {backup.ordner()} auspacken? "
            "Vorhandene Dateien werden nicht angefasst.",
            [("ja", "📦 Ja, auspacken"), ("nein", "↩  Abbrechen")])
        if los != "ja":
            return
        try:
            erg = await _run_with_status(
                "📦 packe aus · Esc bricht ab",
                lambda on_status: backup.auspacken(name, melde=on_status, abbruch=_halt))
        except backup.Abgebrochen:
            ui.warn("📦 Auspacken abgebrochen – der halbe Ordner ist entfernt.")
            return
        except Exception as e:
            ui.error(f"Auspacken fehlgeschlagen: {escape(str(e))}")
            return
        ui.text_panel("📦 Wiederhergestellt", erg.text())
        return
    if was not in backup.ARTEN:
        ui.warn("Nutzung: /backup · teil · voll · voll modelle · … ganz · pruefen · auspacken <name> · ordner")
        return
    modelle = was == "voll" and "modelle" in teile[1:]
    ganz = any(t in ("ganz", "komplett", "neu") for t in teile[1:])
    try:
        plan = await _run_with_status(
            "💾 schaue, was neu ist",
            lambda on_status: backup.planen(was, modelle, ganz, melde=on_status, abbruch=_halt))
    except backup.Abgebrochen:
        ui.info("Kein Backup gemacht.")
        return
    except Exception as e:
        ui.error(f"Backup fehlgeschlagen: {escape(str(e))}")
        return
    if not plan.dateien and not plan.nachtrag:
        ui.warn("Nichts zu sichern – die Ordner sind leer oder fehlen.")
        return
    titel = {"teil": "Teilbackup (Bilder, Persönlichkeiten, Profile)",
             "voll": "Vollständiges Backup" + (" mit Modellen" if modelle else " (ohne Modelle)")}[was]
    if plan.nachtrag:
        vom = str(plan.basis.get("erstellt") or "")[:10]
        if not plan.dateien and not plan.geloescht:
            ui.info(f"💾 Seit dem vollen Backup {plan.basis['name']} ({vom}) hat sich nichts geändert – "
                    "kein Nachtrag nötig.")
            return
        frage = (f"💾 Nachtrag zum {titel} vom {vom}: {len(plan.dateien)} neue/geänderte Dateien "
                 f"({backup.gb(plan.roh)}), {len(plan.geloescht)} gelöscht, {plan.unveraendert} unverändert "
                 f"→ {backup.ordner()}")
        optionen = [("ja", "💾 Ja, Nachtrag sichern"), ("ganz", "📦 Lieber ganz neu sichern"),
                    ("nein", "↩  Abbrechen")]
    else:
        frage = f"💾 {titel}: {len(plan.dateien)} Dateien, {backup.gb(plan.roh)} → {backup.ordner()}"
        optionen = [("ja", "💾 Ja, sichern"), ("nein", "↩  Abbrechen")]
    los = await ask_confirm(frage, optionen)
    if los == "ganz":
        plan = await asyncio.to_thread(backup.planen, was, modelle, True)
        los = "ja"
    if los != "ja":
        ui.info("Kein Backup gemacht.")
        return
    art_text = "Nachtrag" if plan.nachtrag else titel

    try:
        ergebnis = await _run_with_status(
            f"💾 {art_text} · Esc bricht ab",
            lambda on_status: backup.erstellen(was, modelle, melde=on_status, abbruch=_halt, plan=plan))
    except asyncio.CancelledError:
        halt["an"] = True
        raise
    except backup.Abgebrochen:
        ui.warn("💾 Backup abgebrochen – das halbe Zip ist entfernt, nichts weiter verändert.")
        return
    except backup.NichtsNeu as e:
        ui.info(f"💾 {escape(str(e))}")
        return
    except Exception as e:
        ui.error(f"Backup fehlgeschlagen: {escape(str(e))}")
        return
    ui.text_panel("💾 Backup fertig", ergebnis.text())


def _grafikkarte_frei() -> bool:
    """Läuft eine Hintergrund-Aufgabe, startet ein Befehl keine zweite Grafikkarten-Arbeit daneben."""
    import hintergrund as HG
    e = HG.gpu_belegt()
    if e is None:
        return True
    ui.warn(f"⏳ Im Hintergrund läuft gerade Aufgabe #{e['nr']} ({e['titel']}) – die Grafikkarte ist belegt. "
            "Erst danach, sonst kann NemiCLI abstürzen. Stand: Werkzeug aufgaben oder rechts neben der Eingabe.")
    return False


async def _kugel_befehl(ctx: "Ctx", arg: str) -> None:
    """/kugel · malen [beschreibung | <bereich> | alle] · motive · impuls [an|aus] · ankommen [an|aus] ·
    leben [an|aus] · musik [an|aus] · erinnerungen · weg · ordner
    – siehe tools/kugelbilder.py und tools/kugelmotive.py."""
    import kugelbilder as KB
    import kugelmotive as KM
    name = PS.active().name
    teile = arg.split(maxsplit=1)
    sub = teile[0].lower() if teile else ""
    rest = teile[1].strip() if len(teile) > 1 else ""
    if sub == "impuls":
        if rest.lower() in ("an", "aus"):
            _c = config.load()
            _c["kugel_impuls"] = rest.lower() == "an"
            config.save(_c)
        an = bool(config.load().get("kugel_impuls", False))
        ui.info(f"🌈 Freier Moment: {'an' if an else 'aus'} – "
                + (f"alle 1–3 Stunden entscheidet {name} selbst: etwas sagen, malen, Bild wechseln oder nichts. "
                   "Nicht nachts, nicht bei Vollbild. Feste Grüße sind dann aus."
                   if an else "die Kugel grüßt mit den Zeilen aus Wache/gruesse.md. Einschalten: /kugel impuls an"))
        return
    if sub == "ankommen":
        if rest.lower() in ("an", "aus"):
            _c = config.load()
            _c["kugel_ankommen"] = rest.lower() == "an"
            config.save(_c)
        an = bool(config.load().get("kugel_ankommen", True))
        ui.info(f"👋 Ankommen: {'an' if an else 'aus'} – "
                + (f"kommst du nach mindestens 3 Stunden zurück (auch nach Standby oder Ausschalten), "
                   f"entscheidet {name} selbst, ob es an der Kugel eine Begrüßung gibt. Nicht nachts, nicht bei Vollbild."
                   if an else "keine Begrüßung nach längerer Pause. Einschalten: /kugel ankommen an"))
        return
    if sub in ("leben", "musik"):
        schluessel = f"kugel_{sub}"
        if rest.lower() in ("an", "aus"):
            _c = config.load()
            _c[schluessel] = rest.lower() == "an"
            config.save(_c)
        an = bool(config.load().get(schluessel, True))
        if sub == "leben":
            ui.info(f"🐾 Desktop-Leben: {'an' if an else 'aus'} – "
                    + (f"{name} schläft sichtbar, wenn du 10 Minuten weg bist, gähnt nachts, freut sich, wenn du "
                       "mit der Maus um sie kreist, und setzt sich ab und zu auf die Oberkante des aktiven Fensters "
                       "oder geht ein paar Schritte – danach kommt sie zurück. Ziehst du sie woanders hin, ist dort ihr Platz."
                       if an else "die Kugel bleibt still an ihrem Platz. Einschalten: /kugel leben an"))
        else:
            ui.info(f"🎵 Musik spüren: {'an' if an else 'aus'} – "
                    + (f"läuft ein Stück aus Spotify, YouTube, VLC & Co. länger, sagt {name} ab und zu etwas dazu "
                       "(nur die Lautstärke wird gelesen)."
                       if an else "keine Kommentare zur Musik. Einschalten: /kugel musik an"))
        return
    if sub == "erinnerungen":
        from wache import erinnerungen as ER
        offen = ER.offen()
        if not offen:
            ui.info(f"⏰ Keine offenen Erinnerungen. Sag {name} einfach „erinnere mich in 20 Minuten an …“.")
        else:
            ui.info("⏰ Offene Erinnerungen:")
            for d in offen:
                ui.info(f"   #{d['id']} · {ER.zeit_text(float(d['zeit']))} · {d['text']}")
        return
    if sub == "motive" and rest:
        if rest.lower() == "alle" or KM.bereich_finden(rest):
            await _kugel_motive_malen(name, rest)
        else:
            ui.warn(f"Bereich „{rest}“ kenne ich nicht – /kugel motive zeigt die 20 Bereiche.")
        return
    if sub == "motive":
        ordner = KB.motiv_ordner(name)
        ui.info(f"🎨 Motive für {name} (malen: /kugel motive <nummer|name> · alle):")
        for i, (key, (titel, liste)) in enumerate(KM.BEREICHE.items(), 1):
            da = sum(1 for k in liste if (ordner / f"{k}.png").exists())
            ui.info(f"  {i:>2}. {titel:<32} {da:>3}/{len(liste)}   ({key})")
        if not KB.figur(name):
            ui.info("Noch keine Figur: zuerst /kugel malen (Grundbilder) – die Motive zeigen dieselbe Figur.")
        return
    if sub == "malen" and rest and (rest.lower() == "alle" or KM.bereich_finden(rest)):
        await _kugel_motive_malen(name, rest)
        return
    if sub in ("ordner", "oeffnen"):
        try:
            ziel = KB.motiv_ordner(name) if KB.motiv_ordner(name).is_dir() else KB.ORDNER
            ziel.mkdir(parents=True, exist_ok=True)
            os.startfile(str(ziel))
        except Exception as e:
            ui.warn(f"Konnte den Ordner nicht öffnen: {e}")
        return
    if sub in ("weg", "entfernen", "kugel"):
        n = KB.entfernen(name)
        ui.success(f"🔮 {n} Bild(er) von {name} in den Papierkorb – die Kugel ist wieder eine Kugel."
                   if n else f"🔮 {name} hat keine Bilder – die Kugel ist schon eine Kugel.")
        return
    if sub != "malen":
        da = KB.vorhandene(name)
        if KB.veraltet(name):
            ui.warn(f"🔮 Das Aussehen in {name}s Charakter-Datei hat sich geändert – die Kugel zeigt noch "
                    "die alte Figur. /kugel malen malt sie neu.")
        ui.info(f"🔮 Kugel von {name}: " + (", ".join(sorted(k or "Standard" for k in da)) + " als Bild"
                                             if da else "die gemalte Kugel (kein Bild)"))
        o = KB.outfits(name)
        if o["liste"]:
            ui.info(f"   Outfits (wählt {name} selbst): "
                    + ", ".join(k + (" ✓" if k == o["aktiv"] else "") for k in o["liste"]))
        try:
            from wache import steuerung
            z = steuerung.lesen()
            if z:
                ui.info(f"   Zustand: Stimmung {z.get('stimmung') or '–'} · "
                        f"{'versteckt' if z.get('versteckt') else 'sichtbar'} · Größe {z.get('groesse') or 64} px"
                        + (f" · zuletzt gesteuert von {z.get('wer')}" if z.get('wer') else ""))
        except Exception:
            pass
        ui.info("Nutzung:  /kugel malen [beschreibung]  ·  /kugel malen <bereich|alle>  ·  /kugel motive  ·  "
                "/kugel impuls an|aus  ·  /kugel ankommen an|aus  ·  /kugel weg  ·  /kugel ordner")
        return
    import imagegen
    grund = imagegen.missing_reason_aktiv()
    if grund:
        ui.warn("Der Bild-Motor kann gerade nicht malen.")
        ui.info(grund)
        return
    beschreibung, seed = rest, None
    if not beschreibung and (aus_datei := KB.charakter_aussehen(name)):
        beschreibung, seed = aus_datei
        ui.info(f"🔮 Aussehen aus {name}s Charakter-Datei: „{beschreibung}“")
        wahl = await ask_confirm(f"Vier Stimmungsbilder so malen? ({imagegen.active_label()})",
                                 [("ja", "Ja, malen"), ("nein", "Nein, ich gebe selbst eine Beschreibung")])
        if wahl != "ja":
            ui.info("Dann: /kugel malen <deine Beschreibung>")
            return
    if not beschreibung:
        if ctx.backend is None:
            ui.warn(f"Kein Modell verbunden – dann sag mir, wie {name} aussieht: /kugel malen <beschreibung>")
            return
        with ui.thinking(f"{name} beschreibt sich"):
            try:
                beschreibung = (await ctx.backend.ask_once(
                    KB.SELBSTBESCHREIBUNG_PROMPT,
                    KB.selbstbeschreibung_system(name, PS.render_text(PS.active())))).strip()
            except Exception as e:
                ui.error(f"Selbstbeschreibung fehlgeschlagen: {e}")
                return
        beschreibung = beschreibung.strip().strip('"„“').splitlines()[0] if beschreibung else ""
        if not beschreibung:
            ui.warn("Keine Beschreibung bekommen – versuch /kugel malen <beschreibung>.")
            return
        ui.info(f"🔮 {name} über sich: „{beschreibung}“")
        wahl = await ask_confirm(f"Vier Stimmungsbilder mit dieser Beschreibung malen? ({imagegen.active_label()})",
                                 [("ja", "Ja, malen"), ("nein", "Nein, ich gebe selbst eine Beschreibung")])
        if wahl != "ja":
            ui.info("Dann: /kugel malen <deine Beschreibung>")
            return
    kleidung = ""
    if ctx.backend is not None:                    # was die Figur trägt, entscheidet die Persönlichkeit
        with ui.thinking(f"{name} wählt die Kleidung"):
            try:
                kleidung = KB.kleidung_aus(await ctx.backend.ask_once(
                    KB.KLEIDUNG_PROMPT, KB.kleidung_system(name, beschreibung, PS.render_text(PS.active()))))
            except Exception as e:
                ui.warn(f"Keine Antwort zur Kleidung ({e}) – gemalt wird nur das Aussehen.")
        ui.info(f"🔮 {name} trägt: „{kleidung}“" if kleidung else f"🔮 {name} lässt das Aussehen, wie es ist.")
    try:
        bilder = await _run_with_status(
            f"🔮 {name} malt sich selbst",
            lambda on_status: KB.malen(name, beschreibung, seed=seed, on_status=on_status, kleidung=kleidung))
    except Exception as e:
        ui.error(f"Malen fehlgeschlagen: {e}")
        return
    ui.success(f"🔮 {len(bilder)} Bilder für {name}: " + ", ".join(p.name for p in bilder))
    for p in bilder[:2]:
        _bild_vorschau(str(p))
    ui.info("Die Kugel zeigt es beim nächsten Takt. Stimmung wechselt die Persönlichkeit selbst "
            f"(Aktion kugel) – oder du sagst {name} „sei mal ernst“. Nicht gut? /kugel malen nochmal (neuer Seed).")


async def _kugel_motive_malen(name: str, auswahl: str) -> None:
    """/kugel malen <bereich|alle>: fehlende Motive mit der Figur der Grundbilder malen."""
    import imagegen
    import kugelbilder as KB
    import kugelmotive as KM
    if not KB.figur(name):
        ui.warn(f"{name} hat noch keine Figur – zuerst /kugel malen (Grundbilder), "
                "dann zeigen alle Motive dieselbe Figur.")
        return
    if KB.veraltet(name):
        ui.warn(f"Das Aussehen in {name}s Charakter-Datei hat sich geändert – zuerst /kugel malen "
                "(neue Grundbilder), dann passen die Motive wieder.")
        return
    grund = imagegen.missing_reason_aktiv()
    if grund:
        ui.warn("Der Bild-Motor kann gerade nicht malen.")
        ui.info(grund)
        return
    if auswahl.lower() == "alle":
        schluessel, was = [k for _, liste in KM.BEREICHE.values() for k in liste], "alle Bereiche"
    else:
        key = KM.bereich_finden(auswahl)
        schluessel, was = KM.BEREICHE[key][1], KM.BEREICHE[key][0]
    offen = KB.motive_fehlend(name, schluessel)
    if not offen:
        ui.success(f"🎨 {was}: schon alle {len(schluessel)} Motive gemalt.")
        return
    ui.info(f"🎨 {was}: {len(offen)} von {len(schluessel)} Motiven fehlen – {imagegen.active_label()}, "
            f"{KB.SCHRITTE} Schritte je Bild.")
    halt = {"an": False}

    def _halt() -> bool:
        """Esc: nach dem laufenden Bild aufhören – fertige Bilder bleiben, der Rest kommt beim nächsten Mal."""
        try:
            if ui.TUI.consume_abort() if ui.TUI is not None else _esc_pressed():
                halt["an"] = True
        except Exception:
            pass
        return halt["an"]

    try:
        bilder = await _run_with_status(
            f"🎨 {name} malt Motive ({was}) · Esc stoppt nach dem laufenden Bild",
            lambda on_status: KB.motive_malen(name, offen, on_status=on_status, abbruch=_halt))
    except asyncio.CancelledError:
        halt["an"] = True                    # das laufende Bild wird fertig, dann ist Schluss
        raise
    except Exception as e:
        ui.error(f"Malen fehlgeschlagen: {e}")
        return
    ui.success(f"🎨 {len(bilder)} Motive gemalt → {KB.motiv_ordner(name)}")
    if halt["an"] and len(bilder) < len(offen):
        ui.info(f"Gestoppt – {len(offen) - len(bilder)} fehlen noch. Weiter mit: /kugel motive {auswahl}")
    for b in bilder[:2]:
        _bild_vorschau(str(b))


async def run_reich(erststart: bool = False) -> None:
    """Wo soll der NemiCLI-Ordner liegen?  (/start – und einmalig beim ersten Start)

    Trennt den Ordner des Nutzers vom Programm-Ordner. Warum das sein muss,
    steht in core/paths.py; wie das Fenster arbeitet, in core/reich.py.

    Drei Dinge sind hier Absicht:
      * Es wird KEIN Ordner vorgeschlagen – das ist die Entscheidung des Nutzers.
      * Vor dem Kopieren steht, was mitkommt und wie groß es ist. Bei mehreren
        Gigabyte Modellen soll niemand blind auf "Ja" drücken.
      * Es wird nichts gelöscht. Das Original bleibt liegen.
    """
    import reich

    if erststart:
        ui.info("📁 Einmalige Frage: wo soll dein NemiCLI-Ordner liegen? "
                "Ich hab dir ein Fenster aufgemacht.")

    wahl = await asyncio.to_thread(reich.fenster, paths.GEWAEHLT)

    if wahl is None:
        ui.info("Nichts geändert. Mit  /start  kommst du jederzeit wieder hierher.")
        return

    if wahl == "":
        reich.speichern("")
        ui.success(f"Alles bleibt beim Programm: {paths.INSTALL}")
        ui.info("Mit  /start  kannst du das später jederzeit ändern.")
        return

    ziel = Path(wahl)
    if (fehler := reich.pruefe_ziel(ziel)):
        ui.error(fehler)
        return

    posten = reich.inhalt()
    kopiert = False
    if posten:
        gesamt = sum(g for _n, g in posten)
        ui.info(f"Das liegt gerade in {paths.DATEN} und käme mit:")
        for name, g in posten:
            ui.console.print(f"   [dim]{reich.lesbare_groesse(g):>12}[/dim]  {name}")
        ui.console.print(f"   [dim]{'─' * 12}[/dim]  "
                         f"zusammen {reich.lesbare_groesse(gesamt)}")
        wahl2 = await ask_confirm(
            f"Nach {ziel} kopieren?  (Das Original bleibt liegen – es wird nichts gelöscht.)",
            [("ja", "Ja, kopieren"),
             ("nur", "Nur den Ort merken, nichts kopieren"),
             ("__cancel__", "Abbrechen – nichts ändern")])
        if wahl2 == "__cancel__":
            ui.info("Abgebrochen. Es wurde nichts geändert.")
            return
        if wahl2 == "ja":
            bericht = await _run_with_status(
                "Umzug",
                lambda on_status: "\n".join(reich.umziehen(ziel, melde=on_status)))
            for zeile in bericht.splitlines():
                if zeile.startswith("ok"):
                    ui.console.print(f"   [green]{zeile}[/green]")
                else:
                    ui.console.print(f"   [red]{zeile}[/red]")
            schlecht = [z for z in bericht.splitlines() if not z.startswith("ok")]
            if schlecht:
                ui.error("Nicht alles ist sauber angekommen. Ich stelle den Ordner "
                         "NICHT um – deine Daten liegen unverändert am alten Ort.")
                return
            kopiert = True

    reich.speichern(str(ziel))
    ui.success(f"Dein NemiCLI-Ordner ist jetzt: {ziel}")
    if kopiert:
        ui.info(f"Die alten Dateien liegen weiter in {paths.DATEN} – "
                "die kannst du wegräumen, wenn du dich überzeugt hast.")
    ui.warn("Bitte NemiCLI einmal neu starten, damit der neue Ordner überall gilt.")


_FUNKE = None                    # einmal geladen, bleibt Funke für die Sitzung


async def _funke_holen():
    """Funke laden (einmal je Sitzung). None, wenn sie nicht mitgeliefert ist oder nicht lädt."""
    global _FUNKE  # noqa: PLW0603
    if _FUNKE is None:
        from einrichtungshelfer import gespraech as G
        if not G.verfuegbar():
            return None
        try:
            _FUNKE = await asyncio.to_thread(G.Funke)
        except Exception:
            return None
    return _FUNKE


def _funke_profil(funke) -> dict:
    """Anrede, Ersatzname und Persönlichkeiten aus dem Nutzerprofil an Funke geben."""
    import nutzerprofil as NP
    profil = NP.laden()
    funke.name = profil["anrede"].split(",")[0].strip()
    funke.ersatzname = profil["name"] or PS.nutzername()
    funke.personen = [p.name for p in PS.list_all().values()]
    funke.standard = PS.active().name
    return profil


async def _funke_runde(funke, schritt: str, nutzer: str = "", **felder):
    """Eine Runde mit Funke: rechnet im Thread, die Antwort erscheint Wort für Wort."""
    from einrichtungshelfer import gespraech as G
    stand = {"text": ""}
    aufgabe = asyncio.ensure_future(asyncio.to_thread(
        funke.sagen, schritt, nutzer, beim_text=lambda t: stand.__setitem__("text", t), **felder))
    with ui.live_view(ui.helfer_zeile(G.NAME, "…")) as live:
        while not aufgabe.done():
            live.update(ui.helfer_zeile(G.NAME, stand["text"] or "…"))
            await asyncio.sleep(0.1)
    antwort = await aufgabe
    ui.helfer_sagt(G.NAME, antwort.text)
    return antwort


async def _funke_frage(funke, schritt: str, esc_hinweis: str = "Abgebrochen – /einrichten führt dich jederzeit "
                       "wieder hierher.", **felder):
    """Frage stellen und Antworten holen, bis eine Aktion feststeht. None: nach mehreren Versuchen weiter."""
    from einrichtungshelfer import gespraech as G
    await _funke_runde(funke, schritt, **felder)
    for _ in range(G.MAX_VERSUCHE):
        eingabe = (await ask_text(f"Deine Antwort an {G.NAME}")).strip()
        if not eingabe:                                   # Esc/leer: abbrechen
            if esc_hinweis:
                ui.info(esc_hinweis)
            return G.Antwort("abbruch", "", "")
        antwort = await _funke_runde(funke, schritt, eingabe, **felder)
        if antwort.art != "-":
            return antwort
    return None


async def _funke_einrichten(funke) -> list[str]:
    """/einrichten im Gespräch mit Funke. Rückgabe: Befehle, die danach laufen sollen."""
    import nutzerprofil as NP
    import wizard as W
    danach: list[str] = []
    profil = _funke_profil(funke)
    neu = NP.leer(profil)
    modell = "spaeter"

    if neu or not funke.name:
        a = await _funke_frage(funke, "anrede")
        if a and a.art == "abbruch":
            return danach
        if a and a.wert != "-":
            NP.speichern(name=profil["name"], anrede=a.wert, ueber=profil["ueber"])
    if neu and funke.personen:
        a = await _funke_frage(funke, "pers")
        if a and a.art == "abbruch":
            return danach
        if a and a.wert == "neu":
            danach.append("/persönlichkeiten neu")
        elif a and a.wert != "egal" and (p := PS.resolve(a.wert)) is not None:
            PS.set_active(p.key)
    if neu:
        a = await _funke_frage(funke, "modell")
        if a and a.art == "abbruch":
            return danach
        modell = a.wert if a else "spaeter"

    schritte = await _run_with_status("🧰 Schaue nach, was schon da ist", lambda _s: W.check_all())
    ui.wizard_panel(schritte)
    offen = [s for s in W.offene(schritte) if s["id"] in ("python", "venv", "pakete")]
    fehlt = [s["id"] for s in schritte if not s["ok"] and s["id"] in ("torch", "gedaechtnismodell", "bildmodell")]
    if modell == "lokal" and not next((s["ok"] for s in schritte if s["id"] == "ollama"), True):
        fehlt.append("ollama")
    felder = {"offen": [s["id"] for s in offen], "fehlt": fehlt}
    installiert = False
    if offen:
        a = await _funke_frage(funke, "pruefung", **felder)
        if a and a.art == "abbruch":
            return danach
        if a and a.wert == "ja":
            for s in offen:
                try:
                    ergebnis = await _run_with_status(s["titel"], lambda on_status, _id=s["id"]: W.erledige(_id, on_status))
                    ui.success(f"✅ {ergebnis}")
                    installiert = True
                except Exception as e:
                    ui.error(f"Hat nicht geklappt: {e}")
    else:
        await _funke_runde(funke, "pruefung", **felder)

    if "torch" in fehlt and W._venv_python():
        a = await _funke_frage(funke, "torch")
        if a and a.art == "abbruch":
            return danach
        if a and a.wert == "ja":
            try:
                ui.info(f"🪟 venv-Fenster offen – dort eingeben:  {W.venv_fenster()}")
            except Exception as e:
                ui.error(f"Fenster ließ sich nicht öffnen: {e}")

    await _funke_runde(funke, "ende", modell=modell, fehlt=fehlt)
    if installiert and getattr(sys, "frozen", False):
        ui.warn("🔄 Bitte NemiCLI einmal neu starten – erst dann nutzen Gedächtnis, "
                "Krea und Wache das neue venv neben der exe.")
    if modell in ("cloud", "lokal"):
        danach.insert(0, "/model")
    return danach


async def funke_notfall(ctx: "Ctx", exc: BaseException) -> None:
    """Das große Modell ist ausgefallen: Funke erklärt die Fehler-Art und öffnet auf Wunsch das passende Menü.
    Funke bekommt nur die Art des Fehlers, nie dessen Text (der kann Schlüssel oder Pfade enthalten)."""
    from einrichtungshelfer import fehlerart as FA
    funke = await _funke_holen()
    if funke is None:
        return
    anbieter = getattr(ctx.backend, "provider_id", "") or (ctx.model or "").split(":")[0]
    fehler = FA.einordnen(exc, anbieter)
    try:
        _funke_profil(funke)
        antwort = await _funke_frage(funke, "notfall", esc_hinweis="", fehler=fehler)
    except Exception:
        return
    if antwort is not None and antwort.art == "oeffnen" and antwort.wert == "ja":
        await handle_line(ctx, FA.menue(fehler))


async def funke_plaudern(ctx: "Ctx", text: str) -> bool:
    """Ohne Sprach-Modell antwortet Funke. False, wenn sie nicht da ist (dann gilt der bisherige Hinweis)."""
    funke = await _funke_holen()
    if funke is None:
        return False
    try:
        _funke_profil(funke)
        antwort = await _funke_runde(funke, "plaudern", text, fehler="kein_modell")
    except Exception:
        return False
    if antwort.art == "oeffnen" and antwort.wert == "ja":
        await handle_line(ctx, "/model")
    return True


async def run_wizard(nur_offene: bool = False) -> list[str]:
    """Einrichtungs-Assistent (/einrichten): prüft, fragt, installiert. Rückgabe: Befehle, die danach laufen sollen.

    Ist der Einrichtungs-Helfer Funke mitgeliefert, führt er durch das Gespräch; sonst (oder wenn er nicht
    lädt) der klassische Ablauf. Modelle werden NICHT geladen – nur gezeigt, wo man sie herbekommt."""
    import wizard as W

    if not nur_offene:
        from einrichtungshelfer import gespraech as G
        if G.verfuegbar():
            try:
                funke = await _run_with_status(f"✨ {G.NAME} wacht auf", lambda _s: G.Funke())
            except Exception as e:
                ui.warn(f"{G.NAME} ließ sich nicht laden ({e}) – es geht ohne ihn weiter.")
            else:
                try:
                    return await _funke_einrichten(funke)
                except Exception as e:
                    ui.warn(f"{G.NAME} ist gestolpert ({e}) – es geht ohne ihn weiter.")

    ui.info("🧰 Ich schaue nach, was auf diesem PC schon da ist …")
    schritte = await asyncio.to_thread(W.check_all)
    ui.wizard_panel(schritte)

    todo = W.offene(schritte)
    if not todo:
        ui.success("Alles installiert – nichts zu tun. ✨")
    for s in todo:
        wahl = await ask_confirm(
            f"{s['titel']} fehlt – jetzt installieren?",
            [("ja", "Ja, mach das für mich"),
             ("nein", "Nein, überspringen"),
             ("__cancel__", "Assistent beenden")])
        if wahl == "__cancel__":
            ui.info("Abgebrochen – /einrichten führt dich jederzeit wieder hierher.")
            return []
        if wahl != "ja":
            continue
        try:
            ergebnis = await _run_with_status(
                s["titel"],
                lambda on_status, _id=s["id"]: W.erledige(_id, on_status))
            ui.success(f"✅ {ergebnis}")
        except Exception as e:
            ui.error(f"Hat nicht geklappt: {e}")
            ui.info("Du kannst es später mit /einrichten nochmal versuchen.")

    # torch installiert der Nutzer selbst im venv-Fenster (sichtbar, mit pip).
    torch_schritt = next((s for s in schritte if s["id"] == "torch" and not s["ok"]), None)
    if torch_schritt and W._venv_python():
        wahl = await ask_confirm(
            f"{torch_schritt['titel']} fehlt – ein Fenster mit dem venv öffnen? Dort: {torch_schritt['befehl']}",
            [("ja", "Ja, Fenster öffnen"), ("nein", "Nein, später (/venv)")])
        if wahl == "ja":
            try:
                ui.info(f"🪟 venv-Fenster offen – dort eingeben:  {W.venv_fenster()}")
                ui.info("Danach NemiCLI neu starten.")
            except Exception as e:
                ui.error(f"Fenster ließ sich nicht öffnen: {e}")
    elif torch_schritt:
        ui.info(f"torch: erst das venv anlegen lassen, dann /venv – dort {torch_schritt['befehl']}")

    # Modelle: nur erklären – die sucht sich jeder selbst aus.
    fehlt_modell = [s for s in schritte
                    if s["id"] in ("sprachmodell", "bildmodell") and not s["ok"]]
    if fehlt_modell or nur_offene is False:
        ui.wizard_models_panel(W.MODELL_HINWEISE, W.modell_ordner())
    if todo:
        if getattr(sys, "frozen", False) and any(s["id"] in ("venv", "torch", "pakete") for s in todo):
            # torch & Co. aus dem neuen venv greifen erst nach einem Neustart: das bisher
            # eingebundene Python ist in diesem Prozess schon geladen.
            ui.warn("🔄 Bitte NemiCLI einmal neu starten – erst dann nutzen Gedächtnis, "
                    "Krea und Wache das neue venv neben der exe.")
        ui.info("Zum Nachsehen, was jetzt läuft:  /systemcheck")
    return []


def _plan_zeigen() -> None:
    daten = plan.daten()
    if daten is not None:
        ui.console.print(ui.plan_panel(daten))
        plan.angezeigt()


def _todo_zeigen() -> None:
    daten = coding.laden() if coding.aktiv() else None
    if daten is not None:
        ui.console.print(ui.todo_panel(daten))


def _coding_beenden() -> None:
    alt = coding.beenden()
    if alt is None:
        ui.info("Der Coding-Assistent war nicht an.")
        return
    ui.success(f"🟦 Coding-Assistent beendet. Die Ablage bleibt im Projekt: {alt / coding.ABLAGE}")
    mitschrift.notiz(f"Coding-Assistent beendet: {alt}")


async def code_befehl(ctx: "Ctx | None" = None, arg: str = "") -> None:
    """/code – Coding-Assistent starten oder Stand zeigen. /codeend schaltet ab."""
    unter = arg.strip()
    if unter.lower() in ("end", "ende", "aus", "stop", "off"):
        _coding_beenden()
        return
    if unter.lower() in ("status", "?", ""):
        if not coding.aktiv():
            ui.info("Der Coding-Assistent ist aus. Er startet von selbst bei einer Coding-Aufgabe "
                    "oder mit '/code <aufgabe>'.")
            return
        ui.info(f"🟦 {coding.status_text()}  ({coding.projekt()})")
        _todo_zeigen()
        return
    if ctx is None or getattr(ctx, "backend", None) is None:
        ui.warn("Kein Modell aktiv – wähle eins mit /model.")
        return
    mitschrift.notiz("Coding-Assistent per /code angefordert.")
    await converse(ctx.backend, "[System] Der Nutzer hat den Coding-Assistenten mit /code "
                   f"angefordert. Aufgabe: {unter}\nStarte ihn mit coding_start (Projektordner "
                   "absolut; ist er unklar, frag nach).")


def _do_ollama_pull(name: str, on_status) -> str:
    """Lädt ein Ollama-Modell (blockierend) mit Fortschritts-Meldung."""
    def prog(status: str, completed: int, total: int) -> None:
        if total:
            on_status(f"{status} … {completed * 100 // total}% "
                      f"({_fmt_bytes(completed)}/{_fmt_bytes(total)})")
        else:
            on_status(status or "lädt …")
    for _ in S.ollama_pull(name, prog):
        pass
    return f"{name} geladen"


async def _ollama_pull_and_done(name: str) -> None:
    """Lädt ein Ollama-Modell mit Live-Fortschritt und meldet das Ergebnis."""
    try:
        await _run_with_status(f"lade Ollama-Modell {name}",
                               lambda on_status: _do_ollama_pull(name, on_status))
    except Exception as e:
        ui.error(f"Download fehlgeschlagen: {e}")
        return
    P._ollama_tags(force=True)                   # Cache auffrischen -> Modell taucht auf
    ui.success(f"🦙 {name} ist da. Wähle es jetzt über /model → Ollama.")


async def _hf_datei_laden(H, repo: str, datei, ordner: Path, nr: int, von: int) -> Path:
    """Eine Datei mit Live-Balken laden; Esc hält an (H.Abbruch, die .part-Datei bleibt)."""
    import threading
    stopp = threading.Event()
    st = {"phase": "lade", "erledigt": 0, "tempo": 0.0, "letzt": None}

    def fortschritt(phase: str, erledigt: int, gesamt: int) -> None:
        jetzt = time.monotonic()
        letzt = st["letzt"]
        if phase != st["phase"] or letzt is None:
            st["letzt"] = (jetzt, erledigt)
        elif jetzt - letzt[0] >= 0.5:
            momentan = (erledigt - letzt[1]) / (jetzt - letzt[0])
            st["tempo"] = momentan if not st["tempo"] else 0.7 * st["tempo"] + 0.3 * momentan
            st["letzt"] = (jetzt, erledigt)
        st["phase"], st["erledigt"] = phase, erledigt

    def panel():
        return ui.hf_fortschritt(datei.name, st["phase"], st["erledigt"], datei.groesse, st["tempo"], nr, von)

    task = asyncio.create_task(asyncio.to_thread(H.laden, repo, datei, ordner, fortschritt, stopp))
    try:
        with ui.live_view(panel()) as live:
            while not task.done():
                if ui.TUI.consume_abort() if ui.TUI is not None else _esc_pressed():
                    stopp.set()
                live.update(panel())
                await asyncio.wait({task}, timeout=0.25)
    except asyncio.CancelledError:
        stopp.set()
        task.add_done_callback(lambda t: t.exception())
        raise
    return task.result()
