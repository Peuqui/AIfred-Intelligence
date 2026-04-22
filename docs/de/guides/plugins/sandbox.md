# Sandbox Plugin

**Datei:** `aifred/plugins/tools/sandbox/`

Isolierte Python-Code-Ausführung in einem abgesicherten Subprocess.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `execute_code` | Python-Code ausführen (Dokumente read-only) | WRITE_DATA |
| `execute_code_write` | Python-Code ausführen mit Schreibzugriff auf Dokumente | WRITE_SYSTEM |

## Features

- **Verfügbare Libraries:** numpy, pandas, matplotlib, plotly, sympy, scipy
- **HTML/JS-Visualisierungen:** Generierte Charts werden als HTML inline dargestellt
- **Timeout-Schutz:** Automatischer Abbruch bei zu langer Laufzeit
- **Isolierter Subprocess:** Code läuft in eigenem Prozess, kein Zugriff auf AIfred-Internals
- **Zwei Varianten:** `execute_code` mountet `data/documents/` read-only; `execute_code_write` erlaubt Schreiben
