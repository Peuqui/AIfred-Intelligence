# Calculator Plugin

**Datei:** `aifred/plugins/tools/calculator/`

Sichere Auswertung mathematischer Ausdrücke. Der Ausdruck wird über das `ast`-Modul
von Python geparst und Knoten für Knoten ausgewertet — kein `eval()`, keine
Namens-Lookups, keine Funktionsaufrufe — sodass ausschließlich die unterstützten
Rechenoperatoren ausgeführt werden können.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `calculate` | Mathematischen Ausdruck auswerten und das exakte Ergebnis zurückgeben | READONLY |

### Parameter (`calculate`)

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `expression` | string | ja | Mathematischer Ausdruck, z. B. `4832 * 0.17` |

## Unterstützte Operatoren

- `+`, `-`, `*`, `/`
- `//` (ganzzahlige Division), `%` (Modulo), `**` (Potenz)
- unäres `+` / `-`
- Klammern zur Gruppierung

Ganzzahlige Ergebnisse werden ohne Dezimalpunkt zurückgegeben; nicht-ganzzahlige
Ergebnisse werden mit bis zu 10 signifikanten Stellen formatiert.

## Beispiele

```
17.5 * 1.19      → 17.5 * 1.19 = 20.825
2**10            → 2**10 = 1024
(100 - 15) / 3   → (100 - 15) / 3 = 28.33333333
```

Bei einem ungültigen oder nicht unterstützten Ausdruck gibt das Tool ein
JSON-Fehlerobjekt (`{"error": "Cannot evaluate '...': ..."}`) zurück, statt eine
Exception zu werfen.

## Konfiguration

Keine — das Plugin ist immer verfügbar und hat keine Abhängigkeiten oder API-Keys.
