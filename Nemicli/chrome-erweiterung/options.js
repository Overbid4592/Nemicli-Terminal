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
  $("status").textContent = "Prüfe …";
  try {
    const r = await fetch(`http://127.0.0.1:${port}/ping`);
    const j = await r.json();
    $("status").textContent = j.nemicli ? "✓ NemiCLI antwortet auf Port " + port : "Antwort, aber kein NemiCLI?";
  } catch (e) {
    $("status").textContent = "✗ Kein NemiCLI auf Port " + port + " – läuft es? (/chrome zeigt Port und Schlüssel)";
  }
};
