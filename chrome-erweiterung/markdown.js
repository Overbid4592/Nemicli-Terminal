// Kleiner Markdown-Leser für die Seitenleiste. Alles wird erst maskiert, dann gezielt
// formatiert – so landet nie fremdes HTML im Fenster. Links nur http(s).

const NemiMarkdown = (() => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function inline(s) {
    return s.split(/(`[^`\n]+`)/).map((teil, i) => {
      if (i % 2 === 1) return "<code>" + esc(teil.slice(1, -1)) + "</code>";
      return esc(teil)
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)"]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
        .replace(/~~([^~]+)~~/g, "<del>$1</del>");
    }).join("");
  }

  function tabelle(zeilen) {
    const zellen = (z) => z.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const kopf = zellen(zeilen[0]);
    const rumpf = zeilen.slice(/^\s*\|?[\s:-]+\|/.test(zeilen[1] || "") ? 2 : 1);
    return "<table><tr>" + kopf.map((c) => "<th>" + inline(c) + "</th>").join("") + "</tr>"
      + rumpf.map((z) => "<tr>" + zellen(z).map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("")
      + "</table>";
  }

  function bloecke(text) {
    const zeilen = text.split("\n");
    let html = "", absatz = [], liste = null;
    const absatzZu = () => { if (absatz.length) { html += "<p>" + absatz.map(inline).join("<br>") + "</p>"; absatz = []; } };
    const listeZu = () => { if (liste) { html += `<${liste.art}>` + liste.punkte.map((p) => "<li>" + inline(p) + "</li>").join("") + `</${liste.art}>`; liste = null; } };
    for (let i = 0; i < zeilen.length; i++) {
      const z = zeilen[i];
      let m;
      if (!z.trim()) { absatzZu(); listeZu(); continue; }
      if ((m = z.match(/^\s*#{1,6}\s+(.*)$/))) { absatzZu(); listeZu(); html += "<h4>" + inline(m[1]) + "</h4>"; continue; }
      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(z)) { absatzZu(); listeZu(); html += "<hr>"; continue; }
      if (z.trim().startsWith("|")) {
        absatzZu(); listeZu();
        const block = [];
        while (i < zeilen.length && zeilen[i].trim().startsWith("|")) block.push(zeilen[i++]);
        i--;
        html += tabelle(block);
        continue;
      }
      if ((m = z.match(/^\s*>\s?(.*)$/))) { absatzZu(); listeZu(); html += "<blockquote>" + inline(m[1]) + "</blockquote>"; continue; }
      if ((m = z.match(/^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/))) {
        absatzZu();
        const art = m[1] ? "ul" : "ol";
        if (!liste || liste.art !== art) { listeZu(); liste = { art, punkte: [] }; }
        liste.punkte.push(m[3]);
        continue;
      }
      if (liste && /^\s{2,}\S/.test(z)) { liste.punkte[liste.punkte.length - 1] += " " + z.trim(); continue; }
      listeZu();
      absatz.push(z);
    }
    absatzZu(); listeZu();
    return html;
  }

  function render(text) {
    const teile = String(text || "").split(/^```/m);
    return teile.map((t, i) => {
      if (i % 2 === 0) return bloecke(t);
      const nl = t.indexOf("\n");
      const sprache = nl >= 0 ? t.slice(0, nl).trim().toLowerCase() : "";
      const code = (nl >= 0 ? t.slice(nl + 1) : t).replace(/\n$/, "");
      if (sprache === "entwurf") {             // fertiger Text zum Übernehmen
        return '<div class="entwurf"><div class="entwurf-kopf">✍ Entwurf</div>'
          + '<div class="entwurf-text">' + esc(code) + "</div>"
          + '<div class="entwurf-knoepfe"><button data-aktion="einfuegen">✍ Ins Textfeld</button>'
          + '<button data-aktion="kopieren">⧉ Kopieren</button></div></div>';
      }
      return '<div class="code"><button data-aktion="kopieren" title="Kopieren">⧉</button>'
        + "<pre><code>" + esc(code) + "</code></pre></div>";
    }).join("");
  }

  return { render };
})();
