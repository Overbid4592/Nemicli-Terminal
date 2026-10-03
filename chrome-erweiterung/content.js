// NemiCLI – läuft auf jeder Seite: merkt sich das Textfeld mit dem Cursor,
// sammelt den Seitentext, setzt die Antwort ein, zeigt Hinweise.

if (window.__nemiGeladen) { throw new Error("nemi: schon geladen"); }   // nicht doppelt einspritzen
window.__nemiGeladen = true;

let letztesFeld = null;          // zuletzt fokussiertes Eingabefeld (Textarea/Input/contenteditable)

function istFeld(el) {
  if (!el) return false;
  const tag = (el.tagName || "").toLowerCase();
  if (tag === "textarea") return true;
  if (tag === "input") {
    const t = (el.type || "text").toLowerCase();
    return ["text", "search", "email", "url", ""].includes(t);
  }
  return el.isContentEditable === true;
}

document.addEventListener("focusin", (e) => {
  if (istFeld(e.target)) letztesFeld = e.target;
}, true);

function feldFinden() {
  const a = document.activeElement;
  if (istFeld(a)) return a;
  if (letztesFeld && document.contains(letztesFeld)) return letztesFeld;
  // Notnagel: das größte sichtbare Textfeld auf der Seite
  const kandidaten = [...document.querySelectorAll("textarea, [contenteditable='true'], [contenteditable='']")]
    .filter((el) => el.offsetWidth > 0 && el.offsetHeight > 0)
    .sort((x, y) => y.offsetWidth * y.offsetHeight - x.offsetWidth * x.offsetHeight);
  return kandidaten[0] || null;
}

function seitentext(auswahl) {
  if (auswahl && auswahl.trim()) return auswahl.trim();
  const sel = window.getSelection ? String(window.getSelection()) : "";
  if (sel && sel.trim()) return sel.trim();
  // Sichtbarer Text der Seite, ohne Skripte; das aktive Feld selbst weglassen
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll("script, style, noscript, nav, footer, header").forEach((n) => n.remove());
  return (clone.innerText || "").replace(/\n{3,}/g, "\n\n").trim().slice(0, 40000);
}

// Senden-Knopf zum Feld finden: erst das Formular, dann Knöpfe mit „Senden“-Bedeutung,
// zuletzt Enter im Feld (so schicken Chats ab).
const SENDE_WORTE = /senden|send|submit|abschicken|absenden|antworten|reply|post|schicken/i;
function sendeKnopf(el) {
  const form = el.closest("form");
  if (form) {
    const b = form.querySelector("button[type='submit'], input[type='submit']");
    if (b && !b.disabled) return b;
  }
  const wurzel = el.closest("form, [role='dialog'], main, body") || document.body;
  const knoepfe = [...wurzel.querySelectorAll("button, [role='button'], input[type='submit']")]
    .filter((b) => b.offsetWidth > 0 && b.offsetHeight > 0 && !b.disabled);
  const passt = (b) => SENDE_WORTE.test([b.textContent, b.getAttribute("aria-label"), b.title,
                                          b.getAttribute("data-testid"), b.id, b.className].join(" "));
  // den nächstgelegenen passenden Knopf nehmen (Abstand zum Feld)
  const r = el.getBoundingClientRect();
  const nah = knoepfe.filter(passt).map((b) => {
    const q = b.getBoundingClientRect();
    return { b, d: Math.hypot(q.left - r.right, q.top - r.bottom) };
  }).sort((a, c) => a.d - c.d);
  return nah.length ? nah[0].b : null;
}

function absenden(el) {
  const knopf = sendeKnopf(el);
  if (knopf) { knopf.click(); return "Knopf"; }
  const form = el.closest("form");
  if (form && typeof form.requestSubmit === "function") { form.requestSubmit(); return "Formular"; }
  for (const typ of ["keydown", "keypress", "keyup"]) {
    el.dispatchEvent(new KeyboardEvent(typ, { key: "Enter", code: "Enter", keyCode: 13, which: 13,
                                              bubbles: true, cancelable: true }));
  }
  return "Enter";
}

function einsetzen(text, senden) {
  const el = feldFinden();
  if (!el) {
    zeigen("⚠ Kein Textfeld gefunden. Antwort:\n\n" + text, true);
    return;
  }
  el.focus();
  if (el.isContentEditable) {
    // Zeilenumbrüche als echte Absätze im Editor
    const ok = document.execCommand("insertText", false, text);
    if (!ok) el.textContent += text;
  } else {
    const start = el.selectionStart ?? el.value.length;
    const ende = el.selectionEnd ?? el.value.length;
    el.value = el.value.slice(0, start) + text + el.value.slice(ende);
    el.selectionStart = el.selectionEnd = start + text.length;
  }
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  if (senden) {
    // kurz warten, bis die Seite den Text registriert hat (Senden-Knopf wird oft erst dann aktiv)
    setTimeout(() => {
      const wie = absenden(el);
      status("🐈 Antwort gesendet (" + wie + ")", 2500);
    }, 350);
    return;
  }
  status("🐈 Antwort eingesetzt – bitte gegenlesen", 2500);
}

// --- kleine Hinweise/Kästchen auf der Seite ---
let statusBox = null;
function status(text, dauer) {
  if (!statusBox) {
    statusBox = document.createElement("div");
    statusBox.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;background:#11131d;color:#e4e7f2;" +
      "padding:10px 16px;border:1px solid #5b54a8;border-radius:10px;font:14px Segoe UI,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.5)";
    document.documentElement.appendChild(statusBox);
  }
  statusBox.textContent = text;
  statusBox.style.display = "block";
  clearTimeout(statusBox._t);
  if (dauer) statusBox._t = setTimeout(() => { statusBox.style.display = "none"; }, dauer);
}

function zeigen(text, kopierbar) {
  if (statusBox) statusBox.style.display = "none";
  const alt = document.getElementById("nemi-antwort-box");
  if (alt) alt.remove();
  const box = document.createElement("div");
  box.id = "nemi-antwort-box";
  box.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;max-width:min(560px,90vw);max-height:70vh;overflow:auto;" +
    "background:#11131d;color:#e4e7f2;padding:14px 16px 12px;border:1px solid #41e0d0;border-radius:12px;" +
    "font:14px/1.45 Segoe UI,sans-serif;white-space:pre-wrap;box-shadow:0 8px 30px rgba(0,0,0,.55)";
  const kopf = document.createElement("div");
  kopf.style.cssText = "display:flex;justify-content:space-between;gap:12px;margin-bottom:8px;color:#41e0d0;font-weight:600";
  kopf.textContent = "🐈 Nemi";
  const zu = document.createElement("button");
  zu.textContent = "✕";
  zu.style.cssText = "background:none;border:none;color:#9298ac;font-size:16px;cursor:pointer";
  zu.onclick = () => box.remove();
  kopf.appendChild(zu);
  const body = document.createElement("div");
  body.textContent = text;
  box.appendChild(kopf);
  box.appendChild(body);
  if (kopierbar) {
    const b = document.createElement("button");
    b.textContent = "Kopieren";
    b.style.cssText = "margin-top:10px;background:#1c1f30;color:#e4e7f2;border:1px solid #5b54a8;border-radius:8px;padding:6px 12px;cursor:pointer";
    b.onclick = () => navigator.clipboard.writeText(text.replace(/^⚠ Kein Textfeld gefunden\. Antwort:\n\n/, ""));
    box.appendChild(b);
  }
  document.documentElement.appendChild(box);
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.typ === "status") { status(msg.text); sendResponse({ ok: true }); }
  else if (msg.typ === "sammeln") { sendResponse({ text: seitentext(msg.auswahl) }); }
  else if (msg.typ === "einfuegen") { einsetzen(msg.text || "", !!msg.senden); sendResponse({ ok: true }); }
  else if (msg.typ === "zeigen") { zeigen(msg.text || "", true); sendResponse({ ok: true }); }
  return true;
});
