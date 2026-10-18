// NemiCLI – Seitenleiste: Chat mit der laufenden Sitzung (wie /gui im Modus „Chat“).
// Spricht nur mit http://127.0.0.1:<port>, Schlüssel aus den Einstellungen (X-Nemi-Key).

const $ = (id) => document.getElementById(id);
const STANDARD_PORT = 9000;
const FOTO_BREITE = 1600;                       // Bildschirmfoto wird darauf verkleinert
const BILDPFAD = /[A-Za-z]:\\[^\n"<>|*?]+?\.(?:png|jpe?g|webp)/gi;

const kugel = { info: null, urls: {} };    // Kugel-Bild: Stand aus /zustand, geladene Bilder

const zustand = {
  persona: "NemiCLI", beschaeftigt: false, woanders: false, seite: false, foto: null,
  verlaufKennung: "", ki: null, karte: null, fortschritt: null, tippen: null,
};

// --- Verbindung -------------------------------------------------------------
async function einstellungen() {
  const s = await chrome.storage.local.get({ port: STANDARD_PORT, key: "" });
  return { port: Number(s.port) || STANDARD_PORT, key: String(s.key || "") };
}

async function anfrage(pfad, daten = {}) {
  const { port, key } = await einstellungen();
  if (!key) throw new Error("ohne-schluessel");
  let r;
  try {
    r = await fetch(`http://127.0.0.1:${port}${pfad}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Nemi-Key": key },
      body: JSON.stringify(daten),
    });
  } catch (e) {
    throw new Error("nicht-erreichbar");
  }
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.fehler || `Fehler ${r.status}`);
  }
  return r;
}

function hinweis(fehler) {
  const box = $("hinweis");
  if (!fehler) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = "";
  const text = document.createElement("div");
  const knopf = document.createElement("button");
  knopf.className = "knopf-klein";
  if (fehler === "ohne-schluessel") {
    text.textContent = "Noch kein Schlüssel eingetragen. In NemiCLI /chrome tippen und den Schlüssel in den Einstellungen eintragen.";
    knopf.textContent = "Einstellungen öffnen";
    knopf.onclick = () => chrome.runtime.openOptionsPage();
  } else if (fehler === "nicht-erreichbar") {
    text.textContent = "NemiCLI läuft nicht (oder anderer Port). Starte NemiCLI, dann nochmal versuchen.";
    knopf.textContent = "Nochmal versuchen";
    knopf.onclick = () => laden();
  } else {
    text.textContent = "⚠ " + fehler;
    knopf.textContent = "Nochmal versuchen";
    knopf.onclick = () => laden();
  }
  box.append(text, knopf);
  $("modell").textContent = "nicht verbunden";
}

// --- Anzeige-Bausteine -----------------------------------------------------
function el(tag, klasse, text) {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function amEnde() {
  const c = $("chat");
  return c.scrollHeight - c.scrollTop - c.clientHeight < 80;
}

function hinzu(knoten, immerRunter = false) {
  const runter = immerRunter || amEnde();
  $("leer").hidden = true;
  $("chat").append(knoten);
  if (runter) $("chat").scrollTop = $("chat").scrollHeight;
  return knoten;
}

function nutzerBlase(text, fotoUrl) {
  const zeile = el("div", "nutzer");
  const blase = el("div", "blase");
  const teile = String(text).match(/^([\s\S]*?)\n((?:📄|📸)[^\n]*)$/);
  blase.textContent = teile ? teile[1] : String(text);
  if (teile) blase.append(el("span", "anhang", teile[2]));
  if (fotoUrl) { const img = el("img", "foto"); img.src = fotoUrl; blase.append(img); }
  zeile.append(blase);
  hinzu(zeile, true);
}

function kiBlock() {
  tippenAus();
  const block = el("div", "ki");
  block.append(el("div", "avatar"));
  const inhalt = el("div", "inhalt");
  inhalt.append(el("div", "name verlauf-text", zustand.persona));
  const text = el("div", "text");
  inhalt.append(text);
  block.append(inhalt);
  block._roh = "";
  block._text = text;
  block._inhalt = inhalt;
  hinzu(block);
  return block;
}

function kiSetzen(block, roh) {
  block._roh = roh;
  if (block._malen) return;
  block._malen = requestAnimationFrame(() => {
    block._malen = 0;
    const runter = amEnde();
    block._text.innerHTML = NemiMarkdown.render(block._roh);
    if (runter) $("chat").scrollTop = $("chat").scrollHeight;
  });
}

function kiFertig(block) {
  if (!block._roh.trim()) return;
  const w = el("div", "werkzeuge");
  const kopieren = el("button", "", "⧉ Kopieren");
  kopieren.onclick = async () => {
    await navigator.clipboard.writeText(block._roh);
    kopieren.textContent = "✓ Kopiert";
    setTimeout(() => (kopieren.textContent = "⧉ Kopieren"), 1500);
  };
  w.append(kopieren);
  block._inhalt.append(w);
}

function zeile(text, art = "") { return hinzu(el("div", "zeile " + art, text)); }

function tippenAn() {
  if (zustand.tippen) return;
  const t = el("div", "ki");
  t.append(el("div", "avatar"));
  const punkte = el("div", "tippen");
  punkte.append(el("i"), el("i"), el("i"));
  t.append(punkte);
  zustand.tippen = hinzu(t);
}
function tippenAus() { if (zustand.tippen) { zustand.tippen.remove(); zustand.tippen = null; } }

async function bilderZeigen(text, ziel) {
  const pfade = [...new Set(String(text || "").match(BILDPFAD) || [])];
  for (const pfad of pfade) {
    try {
      const r = await anfrage("/bild", { pfad });
      const url = URL.createObjectURL(await r.blob());
      const img = el("img");
      img.src = url;
      img.title = pfad;
      img.onclick = () => window.open(url, "_blank");
      img.onload = () => { if (amEnde()) $("chat").scrollTop = $("chat").scrollHeight; };
      ziel.append(img);
    } catch (_) { /* Bild außerhalb der Bilder-Ordner oder weg */ }
  }
}

// --- Kugel-Bild -------------------------------------------------------------
// Wie die Kugel auf dem Desktop: aktuelle Stimmung, beim Nachdenken das Denk-Bild.
async function kugelSetzen(info) {
  if (kugel.info && (!info || info.stand !== kugel.info.stand)) {
    Object.values(kugel.urls).forEach((u) => URL.revokeObjectURL(u));
    kugel.urls = {};
  }
  kugel.info = info || null;
  await kugelZeigen();
}

async function kugelZeigen() {
  const k = kugel.info;
  const stimmung = k ? (zustand.beschaeftigt && k.denkt ? k.denkt : k.aktuell) : null;
  if (stimmung === null || stimmung === undefined) {
    document.body.classList.remove("mit-kugel");
    return;
  }
  try {
    if (!(stimmung in kugel.urls)) {
      const r = await anfrage("/kugelbild", { stimmung });
      kugel.urls[stimmung] = URL.createObjectURL(await r.blob());
    }
    document.documentElement.style.setProperty("--kugel", `url("${kugel.urls[stimmung]}")`);
    document.body.classList.add("mit-kugel");
  } catch (_) {
    document.body.classList.remove("mit-kugel");
  }
}

// --- Verlauf ---------------------------------------------------------------
function verlaufZeigen(eintraege) {
  const chat = $("chat");
  [...chat.children].forEach((k) => { if (k.id !== "leer") k.remove(); });
  zustand.ki = zustand.karte = zustand.fortschritt = zustand.tippen = null;
  $("leer").hidden = eintraege.length > 0;
  for (const e of eintraege) {
    if (e.rolle === "nutzer") nutzerBlase(e.text);
    else if (e.rolle === "ki") { const b = kiBlock(); kiSetzen(b, e.text); kiFertig(b); }
    else if (e.rolle === "aktion") zeile("⚙ " + String(e.text).split("\n")[0]);
    else if (e.rolle === "werkzeug") {
      const ziel = el("div", "bilder");
      hinzu(ziel);
      bilderZeigen(e.text, ziel);
    }
  }
  chat.scrollTop = chat.scrollHeight;
}

function zustandSetzen(z) {
  zustand.persona = z.persona || "NemiCLI";
  $("persona").textContent = zustand.persona;
  $("modell").textContent = `${z.model || "—"}  ·  Chat #${z.chat ?? "?"}`;
  $("feld").placeholder = `Frag ${zustand.persona} …`;
  zustand.woanders = !!z.beschaeftigt;
  knoepfe();
  kugelSetzen(z.kugel);
  const kennung = JSON.stringify([z.chat, (z.verlauf || []).length]);
  if (kennung !== zustand.verlaufKennung && !zustand.beschaeftigt) {
    zustand.verlaufKennung = kennung;
    verlaufZeigen(z.verlauf || []);
  }
}

async function laden() {
  try {
    const r = await anfrage("/zustand");
    hinweis(null);
    zustandSetzen(await r.json());
  } catch (e) {
    hinweis(e.message);
  }
}

// --- Anhänge ---------------------------------------------------------------
async function aktiverTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("Kein aktiver Tab.");
  return tab;
}

// Hinweis, wenn Chrome auf dieser Seite weder Lesen noch Bildschirmfoto erlaubt.
// Ohne Adresse (Chrome nennt sie dort nicht immer) verrät es die Fehlermeldung.
function gesperrt(url, fehler = "") {
  url = String(url || "");
  if (/^file:/i.test(url)) {
    return "Für Dateien auf dem PC: chrome://extensions → NemiCLI → Details → „Zugriff auf Datei-URLs zulassen“ anschalten.";
  }
  if (/activeTab|cannot be scripted|Cannot access a chrome|chrome:\/\//i.test(String(fehler))
      || /^(chrome|chrome-extension|chrome-search|chrome-untrusted|edge|about|devtools|view-source):/i.test(url)
      || /^https:\/\/(chromewebstore\.google\.com|chrome\.google\.com\/webstore)/i.test(url)) {
    return "Chrome erlaubt das auf seinen eigenen Seiten nicht (Neuer Tab, chrome://…, Web Store). Auf einer normalen Webseite geht es.";
  }
  return "";
}

// Hat jemand in Chrome den Websitezugriff auf „Bei Klick“ gestellt, einmal nachfragen.
async function seitenzugriff() {
  const alle = { origins: ["<all_urls>"] };
  if (await chrome.permissions.contains(alle)) return;
  if (!(await chrome.permissions.request(alle).catch(() => false))) {
    throw new Error("Ohne Zugriff auf Seiten geht das nicht. chrome://extensions → NemiCLI → Details → "
                    + "Websitezugriff: „Auf allen Websites“.");
  }
}

// Nachricht ans Seiten-Skript; fehlt es (Tab älter als die Erweiterung), wird es eingespritzt.
async function anSeite(tab, msg) {
  try {
    return await chrome.tabs.sendMessage(tab.id, msg);
  } catch (_) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    } catch (e) {
      throw new Error(gesperrt(tab.url, e.message) || "Auf dieser Seite geht das nicht (" + (e.message || e) + ").");
    }
    return await chrome.tabs.sendMessage(tab.id, msg);
  }
}

const seiteLesen = (tab) => anSeite(tab, { typ: "sammeln", auswahl: "" });

// Knöpfe an Entwürfen und Code-Blöcken (markdown.js): Kopieren oder ins Textfeld der Seite.
async function blockAktion(knopf) {
  const block = knopf.closest(".entwurf, .code");
  if (!block) return;
  const text = (block.querySelector(".entwurf-text, code") || block).textContent;
  const vorher = knopf.textContent;
  try {
    if (knopf.dataset.aktion === "einfuegen") {
      await seitenzugriff();
      await anSeite(await aktiverTab(), { typ: "einfuegen", text, senden: false });
      knopf.textContent = "✓ Eingefügt";
    } else {
      await navigator.clipboard.writeText(text);
      knopf.textContent = vorher.length > 2 ? "✓ Kopiert" : "✓";
    }
  } catch (e) {
    zeile("⚠ " + (e.message || e), "warn");
    return;
  }
  setTimeout(() => (knopf.textContent = vorher), 1600);
}

async function fotoMachen() {
  await seitenzugriff();
  const tab = await aktiverTab();
  let roh;
  try {
    roh = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 85 });
  } catch (e) {
    throw new Error(gesperrt(tab.url, e.message) || e.message || String(e));
  }
  const bild = await createImageBitmap(await (await fetch(roh)).blob());
  const faktor = Math.min(1, FOTO_BREITE / bild.width);
  const leinwand = new OffscreenCanvas(Math.round(bild.width * faktor), Math.round(bild.height * faktor));
  leinwand.getContext("2d").drawImage(bild, 0, 0, leinwand.width, leinwand.height);
  const blob = await leinwand.convertToBlob({ type: "image/jpeg", quality: 0.82 });
  const uri = await new Promise((ok, fehler) => {
    const r = new FileReader();
    r.onload = () => ok(r.result);
    r.onerror = () => fehler(r.error);
    r.readAsDataURL(blob);
  });
  return { uri, titel: tab.title || "" };
}

function anhaengeZeigen() {
  const box = $("anhaenge");
  box.textContent = "";
  $("seite").classList.toggle("an", zustand.seite);
  $("foto").classList.toggle("an", !!zustand.foto);
  if (zustand.seite) {
    const c = el("div", "chip");
    c.append(el("span", "", "📄 Seite (oder Markierung) wird mitgeschickt"));
    const weg = el("button", "", "✕");
    weg.onclick = () => { zustand.seite = false; anhaengeZeigen(); };
    c.append(weg);
    box.append(c);
  }
  if (zustand.foto) {
    const c = el("div", "chip");
    const img = el("img");
    img.src = zustand.foto.uri;
    c.append(img, el("span", "", zustand.foto.titel || "Bildschirmfoto"));
    const weg = el("button", "", "✕");
    weg.onclick = () => { zustand.foto = null; anhaengeZeigen(); };
    c.append(weg);
    box.append(c);
  }
}

// --- Senden und Ereignisse -------------------------------------------------
function knoepfe() {
  const leer = !$("feld").value.trim();
  $("senden").disabled = leer || zustand.beschaeftigt;
  $("neu").disabled = zustand.beschaeftigt;
  // Regenbogen-Rand: eigene Runde oder NemiCLI arbeitet gerade woanders (Terminal, /gui)
  document.body.classList.toggle("denkt", !!(zustand.beschaeftigt || zustand.woanders));
}

function ereignis(ev) {
  switch (ev.t) {
    case "busy":
      if (ev.on) tippenAn(); else tippenAus();
      break;
    case "thinking": {
      if (!zustand.ki) zustand.ki = kiBlock();
      const b = zustand.ki;
      if (!b._gedanken) {
        b._gedanken = el("details", "gedanken");
        b._gedanken.append(el("summary", "", "💭 Gedanken"), el("div"));
        b._inhalt.insertBefore(b._gedanken, b._text);
      }
      b._gedanken.lastChild.textContent += ev.delta || "";
      break;
    }
    case "answer":
      if (!zustand.ki) zustand.ki = kiBlock();
      kiSetzen(zustand.ki, zustand.ki._roh + (ev.delta || ""));
      break;
    case "answer_end": {
      const b = zustand.ki;
      zustand.ki = null;
      if (!b) break;
      if (typeof ev.clean === "string") kiSetzen(b, ev.clean);
      else if (ev.clean === null) kiSetzen(b, "");
      requestAnimationFrame(() => {
        if (!b._roh.trim() && !b._gedanken) b.remove();
        else kiFertig(b);
      });
      if (zustand.beschaeftigt) tippenAn();
      break;
    }
    case "action": {
      tippenAus();
      const k = el("div", "karte");
      k.append(el("div", "titel", (ev.risiko ? "⚠ " : "⚙ ") + (ev.desc || "")));
      zustand.karte = hinzu(k);
      if (zustand.beschaeftigt) tippenAn();
      break;
    }
    case "action_result": {
      let k = zustand.karte;
      zustand.karte = null;
      if (!k) k = hinzu(el("div", "karte"));
      const zeichen = ev.ok === true ? "✓" : ev.ok === false ? "✗" : "•";
      const kurz = String(ev.text || "").trim();
      k.append(el("div", "ergebnis " + (ev.ok === true ? "ok" : ev.ok === false ? "fehler" : ""),
                  `${zeichen}  ${kurz.length > 600 ? kurz.slice(0, 600) + " …" : kurz}`));
      const bilder = el("div", "bilder");
      k.after(bilder);
      bilderZeigen(ev.text, bilder);
      break;
    }
    case "confirm": {
      tippenAus();
      const k = el("div", "karte frage");
      k.append(el("div", "titel", "❓ " + (ev.question || "Freigeben?")));
      if (ev.preview) k.append(el("pre", "", ev.preview));
      const wahl = el("div", "wahl");
      for (const [wert, text] of ev.options || []) {
        const b = el("button", "", text);
        b.onclick = async () => {
          wahl.querySelectorAll("button").forEach((x) => (x.disabled = true));
          b.textContent = "✓ " + text;
          try { await anfrage("/entscheiden", { id: ev.id, wahl: wert }); }
          catch (e) { zeile("⚠ " + e.message, "warn"); }
          if (zustand.beschaeftigt) tippenAn();
        };
        wahl.append(b);
      }
      k.append(wahl);
      hinzu(k, true);
      break;
    }
    case "progress": {
      if (!zustand.fortschritt) {
        tippenAus();                                // Tipp-Punkte bleiben unter der Karte
        const k = el("div", "karte");
        k.append(el("div", "titel", ev.title || "arbeite …"), el("div", "dim"));
        const balken = el("div", "balken");
        balken.append(el("div"));
        k.append(balken);
        zustand.fortschritt = hinzu(k);
        if (zustand.beschaeftigt) tippenAn();
      }
      const k = zustand.fortschritt;
      k.children[1].textContent = ev.msg || "";
      if (typeof ev.pct === "number") k.querySelector(".balken div").style.width = ev.pct + "%";
      break;
    }
    case "progress_end":
      if (zustand.fortschritt) zustand.fortschritt.querySelector(".balken div").style.width = "100%";
      zustand.fortschritt = null;
      break;
    case "note": case "info": zeile(ev.text || ""); break;
    case "warn": zeile("⚠  " + (ev.text || ""), "warn"); break;
    case "error": zeile("✗  " + (ev.text || ""), "fehler"); break;
    default: break;                              // nutzer, receipt, puls, turn_end …
  }
}

async function senden() {
  const text = $("feld").value.trim();
  if (!text || zustand.beschaeftigt) return;
  const daten = { text };
  const anzeige = [];
  let fotoUrl = null;
  try {
    if (zustand.seite || zustand.foto) {
      const tab = await aktiverTab();
      daten.seite = { titel: tab.title || "", url: tab.url || "" };
      if (zustand.seite) {
        await seitenzugriff();
        const s = await seiteLesen(tab);
        daten.seite.text = s.text || "";
        daten.seite.auswahl = !!s.auswahl;
        anzeige.push(`📄 ${s.auswahl ? "Markierung auf " : ""}${tab.title || tab.url || "Seite"}`);
      }
    }
    if (zustand.foto) {
      daten.bild = zustand.foto.uri;
      fotoUrl = zustand.foto.uri;
      if (!zustand.seite) anzeige.push("📸 " + (zustand.foto.titel || "Bildschirmfoto"));
    }
  } catch (e) {
    zeile("⚠ " + (e.message || e), "warn");
    return;
  }

  $("feld").value = "";
  feldHoehe();
  zustand.seite = false;
  zustand.foto = null;
  anhaengeZeigen();
  nutzerBlase(text + (anzeige.length ? "\n" + anzeige.join(" ") : ""), fotoUrl);
  zustand.beschaeftigt = true;
  knoepfe();
  tippenAn();
  kugelZeigen();

  try {
    const r = await anfrage("/chat", daten);
    hinweis(null);
    const leser = r.body.getReader();
    const dekoder = new TextDecoder();
    let rest = "";
    for (;;) {
      const { value, done } = await leser.read();
      if (done) break;
      rest += dekoder.decode(value, { stream: true });
      let nl;
      while ((nl = rest.indexOf("\n")) >= 0) {
        const roh = rest.slice(0, nl).trim();
        rest = rest.slice(nl + 1);
        if (!roh) continue;
        try { ereignis(JSON.parse(roh)); } catch (_) { /* unvollständige Zeile */ }
      }
    }
  } catch (e) {
    if (e.message === "ohne-schluessel" || e.message === "nicht-erreichbar") hinweis(e.message);
    else zeile("✗  " + e.message, "fehler");
  } finally {
    zustand.beschaeftigt = false;
    zustand.ki = zustand.karte = zustand.fortschritt = null;
    tippenAus();
    knoepfe();
    kugelZeigen();
    // Verlauf-Kennung nachziehen, ohne neu zu zeichnen (die Runde steht schon da)
    try {
      const z = await (await anfrage("/zustand")).json();
      zustand.verlaufKennung = JSON.stringify([z.chat, (z.verlauf || []).length]);
      zustandSetzen(z);
    } catch (_) {}
  }
}

function feldHoehe() {
  const f = $("feld");
  f.style.height = "auto";
  f.style.height = Math.min(f.scrollHeight, 180) + "px";
}

// --- Start -----------------------------------------------------------------
$("chat").addEventListener("click", (e) => {
  const knopf = e.target.closest("button[data-aktion]");
  if (knopf) blockAktion(knopf);
});
$("feld").addEventListener("input", () => { feldHoehe(); knoepfe(); });
$("feld").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); senden(); }
});
$("senden").onclick = senden;
$("seite").onclick = () => { zustand.seite = !zustand.seite; anhaengeZeigen(); $("feld").focus(); };
$("foto").onclick = async () => {
  if (zustand.foto) { zustand.foto = null; anhaengeZeigen(); return; }
  $("foto").disabled = true;
  try { zustand.foto = await fotoMachen(); }
  catch (e) { zeile("⚠ Bildschirmfoto nicht möglich: " + (e.message || e), "warn"); }
  $("foto").disabled = false;
  anhaengeZeigen();
  $("feld").focus();
};
$("neu").onclick = async () => {
  if (zustand.beschaeftigt) return;
  try {
    const z = await (await anfrage("/neu")).json();
    zustand.verlaufKennung = "";
    zustandSetzen(z);
  } catch (e) { hinweis(e.message); }
};

// Was im Terminal oder im Desktop-Fenster passiert, zieht hier nach.
setInterval(() => { if (!zustand.beschaeftigt && !document.hidden) laden(); }, 8000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) laden(); });
chrome.storage.onChanged.addListener(() => laden());

laden();
knoepfe();
$("feld").focus();
