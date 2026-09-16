// NemiCLI – Nemi antwortet: Rechtsklick-Menü in Chrome.
// Redet NUR mit dem laufenden NemiCLI auf http://127.0.0.1:<port> (Standard 9000),
// mit dem Geheimschlüssel aus den Einstellungen (X-Nemi-Key).

const STANDARD_PORT = 9000;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "nemi-antwort",
    title: "🐈 Nemi antwortet (ins Textfeld)",
    contexts: ["page", "editable", "selection"],
  });
  chrome.contextMenus.create({
    id: "nemi-antwort-senden",
    title: "🐈 Nemi antwortet und sendet",
    contexts: ["page", "editable", "selection"],
  });
  chrome.contextMenus.create({
    id: "nemi-erklaer",
    title: "🐈 Nemi, erklär mir das",
    contexts: ["selection"],
  });
});

async function einstellungen() {
  const s = await chrome.storage.local.get({ port: STANDARD_PORT, key: "" });
  return { port: Number(s.port) || STANDARD_PORT, key: String(s.key || "") };
}

async function anNemi(pfad, daten) {
  const { port, key } = await einstellungen();
  if (!key) throw new Error("Kein Geheimschlüssel eingetragen – in NemiCLI /chrome tippen und den Schlüssel in den Erweiterungs-Einstellungen eintragen.");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 240000);
  let r;
  try {
    r = await fetch(`http://127.0.0.1:${port}${pfad}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Nemi-Key": key },
      body: JSON.stringify(daten),
      signal: ctrl.signal,
    });
  } catch (e) {
    throw new Error("NemiCLI läuft nicht (oder anderer Port). NemiCLI starten und nochmal versuchen.");
  } finally {
    clearTimeout(timer);
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.fehler || `Fehler ${r.status}`);
  return j.antwort || "";
}

// Nachricht an das Seiten-Skript. Fehlt es (Tab war schon offen, bevor die
// Erweiterung geladen wurde), wird es eingespritzt und nochmal versucht.
async function sende(tabId, msg) {
  try {
    return await chrome.tabs.sendMessage(tabId, msg);
  } catch (e) {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    } catch (e2) {
      throw new Error("Auf dieser Seite geht das nicht (" + (e2.message || e2) + ")");
    }
    return await chrome.tabs.sendMessage(tabId, msg);
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab || !tab.id) return;
  const tabId = tab.id;
  try {
    if (info.menuItemId === "nemi-antwort" || info.menuItemId === "nemi-antwort-senden") {
      const senden = info.menuItemId === "nemi-antwort-senden";
      await sende(tabId, { typ: "status", text: "🐈 Nemi liest und schreibt …" });
      const seite = await sende(tabId, { typ: "sammeln", auswahl: info.selectionText || "" });
      const antwort = await anNemi("/antwort", {
        text: seite.text, titel: tab.title || "", url: tab.url || "",
      });
      await sende(tabId, { typ: "einfuegen", text: antwort, senden });
    } else if (info.menuItemId === "nemi-erklaer") {
      await sende(tabId, { typ: "status", text: "🐈 Nemi überlegt …" });
      const antwort = await anNemi("/erklaer", {
        text: info.selectionText || "", titel: tab.title || "", url: tab.url || "",
      });
      await sende(tabId, { typ: "zeigen", text: antwort });
    }
  } catch (e) {
    const text = "⚠ " + (e && e.message ? e.message : String(e));
    try { await sende(tabId, { typ: "zeigen", text }); }
    catch (_) {
      // Seite lässt kein Skript zu (chrome://, Web Store …): Hinweis über Chrome
      try {
        chrome.notifications.create({ type: "basic", iconUrl: "icon.png", title: "NemiCLI", message: text });
      } catch (_2) {}
    }
  }
});
