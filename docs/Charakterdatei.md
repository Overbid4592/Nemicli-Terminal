# Charakter-Datei (persona.json) – so ist sie aufgebaut

Jede Persönlichkeit kann eine Charakter-Datei haben. Damit malt Krea 2 immer dieselbe Figur.

**Ort:** `Profile/<Name>/<name>.json` (z. B. `Profile/Beispiel/beispiel.json`)

Die Persönlichkeit legt sie selbst an und ändert sie selbst (Werkzeug `charakter_aendern`).
Man kann ihr einfach sagen, was sie ändern soll.

---

## Die Grundregel

> **Was sie ablegen kann, gehört ins Outfit.**
> **Was immer an ihr ist, gehört in den Kern.**

| In den `kern` | In die `outfits` |
|---|---|
| Gesicht, Augen, Haare | Kleidung |
| Körperbau, Größe, Hautton | Schuhe |
| feste Merkmale: Sommersprossen, Tattoos, Muttermal | Schmuck, Brille, Hut, Tasche |

**Wichtig:**

- **Keine Kleidung in den Kern.** Der Kern steht in jedem Bild und wiegt am stärksten.
  Steht dort ein Kleid, trägt sie das Kleid immer, auch wenn ein anderes Outfit gewählt ist.
- **Nicht „nackt“ in den Kern schreiben.** Die Kleidung einfach weglassen.
  Sonst malt Krea die Figur wirklich nackt, auch mit Outfit.
- Steckt doch Kleidung im Kern, warnt NemiCLI beim Speichern: „⚠ Kleidung im kern erkannt“.

---

## Die Felder

| Feld | Was rein gehört |
|---|---|
| `name` | der Anzeigename |
| `kern` | das Aussehen der Person, **ein englischer Satz**, ohne Kleidung |
| `seed_referenz` | feste Zahl – gleicher Kern + gleicher Seed = gleiches Gesicht |
| `stil` | der Bildstil, kommt ans Ende |
| `outfits` | Kleidung zur Auswahl: `{Name: Beschreibung}` |
| `posen` | Haltungen zur Auswahl: `{Name: Beschreibung}` |
| `regeln` | feste Vorgaben für jedes Bild (höchstens 12) |
| `voice` | die Stimme (legt `stimme_festlegen` an) |

---

## So wird das Bild zusammengesetzt

Malt sich die Persönlichkeit selbst (`figur: true`), baut NemiCLI den Prompt so:

**Kern** → **Outfit** → **Pose** → **Szene** → **Stil**

Beispiel: Kern + Outfit „abend“ + Pose „sitzen“ + Szene „in a cozy cafe“ ergibt ungefähr:

```
One adult woman, long wavy red hair, green eyes, freckles, slim build.
Wearing a black evening dress and silver earrings. Sitting on a sofa.
In a cozy cafe. Photorealistic, soft warm light.
```

---

## Beispiel-Datei

```json
{
  "name": "Beispiel",
  "kern": "one adult woman, long wavy red hair, green eyes, freckles, slim build, small star tattoo on the left wrist",
  "seed_referenz": 123456789,
  "stil": "photorealistic, soft warm light",
  "outfits": {
    "alltag": "blue jeans, white blouse and sneakers",
    "abend": "a black evening dress and silver earrings",
    "sport": "grey leggings, a sports top and running shoes"
  },
  "posen": {
    "sitzen": "sitting on a sofa",
    "stehen": "standing, arms crossed, smiling"
  },
  "regeln": [
    "always exactly one adult woman in the picture"
  ]
}
```

---

## Ändern – einfach der KI sagen

| Du sagst | Was passiert |
|---|---|
| „Nimm die Kleidung aus deinem Kern und leg sie als Outfit an.“ | Kern wird sauber, Outfit kommt dazu |
| „Leg dir ein Outfit für den Winter an.“ | neues Outfit |
| „Zieh dein Abendkleid an.“ | sie malt sich mit diesem Outfit |
| „Mal dich mit neuem Gesicht.“ | Zufalls-Seed statt fester Seed |

Die alte Fassung landet bei jeder Änderung im Papierkorb.
