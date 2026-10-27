// Würfel: dieselbe Funktion für den Knopf und für die KI.
const zahl = (wert, von, bis, vorgabe) => {
  const n = Math.round(Number(wert));
  return Number.isFinite(n) ? Math.min(bis, Math.max(von, n)) : vorgabe;
};

const wuerfeln = ({ seiten = 6, anzahl = 1 } = {}) => {
  const s = zahl(seiten, 2, 100, 6), a = zahl(anzahl, 1, 10, 1);
  const augen = Array.from({ length: a }, () => 1 + Math.floor(Math.random() * s));
  return { augen, summe: augen.reduce((x, y) => x + y, 0), seiten: s };
};

NemiApp.funktion("wuerfeln", wuerfeln, "Würfelt anzahl Würfel mit je seiten Seiten.");

const knopf = document.getElementById("los");
if (knopf) {
  knopf.addEventListener("click", () => {
    const e = wuerfeln({ seiten: document.getElementById("seiten").value, anzahl: document.getElementById("anzahl").value });
    document.getElementById("augen").textContent = e.augen.join(" ");
    document.getElementById("summe").textContent = e.augen.length > 1 ? `Summe ${e.summe}` : "";
  });
}
