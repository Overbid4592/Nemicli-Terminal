const $ = (id) => document.getElementById(id);

chrome.storage.local.get({ port: 9000, key: "" }).then((s) => {
  $("port").value = s.port || 9000;
  $("key").value = s.key || "";
});

$("save").onclick = async () => {
  const port = Number($("port").value) || 9000;
  const key = $("key").value.trim();
  await chrome.storage.local.set({ port, key });
  $("status").textContent = "Gespeichert.";
};

$("test").onclick = async () => {
  const port = Number($("port").value) || 9000;
  const key = $("key").value.trim();
  $("status").textContent = "Prüfe …";
  try {
    // Der Schlüssel selbst geht nicht hinaus: NemiCLI unterschreibt eine Zufallszahl damit, wir vergleichen.
    const zufall = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
    const j = await (await fetch(`http://127.0.0.1:${port}/hallo?nonce=${zufall}`)).json();
    const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(`nemicli:${zufall}`)));
    const soll = Array.from(sig, (b) => b.toString(16).padStart(2, "0")).join("");
    $("status").textContent = j && j.beweis === soll
      ? "✓ NemiCLI antwortet auf Port " + port + ", Schlüssel stimmt"
      : "✗ Port " + port + " antwortet, aber nicht NemiCLI – oder der Schlüssel stimmt nicht (/chrome zeigt ihn)";
  } catch (e) {
    $("status").textContent = "✗ Kein NemiCLI auf Port " + port + " – läuft es? (/chrome zeigt Port und Schlüssel)";
  }
};
