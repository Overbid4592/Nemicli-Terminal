// Farbmischer: zwei Farben linear mischen – für den Regler und für die KI.
const teile = (hex) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) throw new Error(`Keine Farbe: ${hex} (erwartet #RRGGBB)`);
  return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
};
const hex = (rgb) => "#" + rgb.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");

const mischen = ({ a, b, anteil = 0.5 } = {}) => {
  const t = Math.min(1, Math.max(0, Number(anteil)));
  const x = teile(a), y = teile(b);
  const farbe = hex(x.map((v, i) => v + (y[i] - v) * (Number.isFinite(t) ? t : 0.5)));
  return { farbe, a: hex(x), b: hex(y), anteil: Number.isFinite(t) ? t : 0.5 };
};

NemiApp.funktion("mischen", mischen, "Mischt zwei Farben #RRGGBB.");

const felder = ["a", "b", "anteil"].map((id) => document.getElementById(id));
const zeigen = () => {
  const e = mischen({ a: felder[0].value, b: felder[1].value, anteil: felder[2].value });
  document.getElementById("ergebnis").style.background = e.farbe;
  document.getElementById("code").textContent = e.farbe;
};
if (felder.every(Boolean)) {
  felder.forEach((f) => f.addEventListener("input", zeigen));
  zeigen();
}
