# Sandbox Plugin

**Datei:** `aifred/plugins/tools/sandbox/`

Isolierte Python-Code-Ausführung in einem mit bubblewrap abgesicherten
Subprocess — für Berechnungen, Datenanalyse, Simulationen und (interaktive)
Visualisierungen.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `execute_code` | Python-Code ausführen; `data/documents/` **read-only** gemountet | WRITE_DATA |
| `execute_code_write` | Python-Code ausführen mit **Schreibzugriff** auf `data/documents/` | WRITE_SYSTEM |

Beide Tools teilen sich dieselben Parameter und laufen in derselben Sandbox — sie
unterscheiden sich nur darin, ob das Dokumente-Verzeichnis beschreibbar ist. Die
Function-Calling-Pipeline filtert nach Tier, sodass Low-Tier-Kontexte nur
`execute_code` zu sehen bekommen.

### Parameter

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `code` | string | ja | Auszuführender Python-Code |
| `description` | string | nein | Kurze Beschreibung des Codes (für Logging / UI-Status) |

## Sandboxing

Der Code läuft in einem Subprocess, der mit **bubblewrap (`bwrap`)** und
`--unshare-all` sowie `--new-session` gekapselt ist:

- **Kein Netzwerkzugriff** — der Netzwerk-Namespace wird abgetrennt
- **Kein Dateisystemzugriff** außer `/usr`, `/etc` (read-only), ein privates
  `/tmp`, der venv-Interpreter + site-packages (read-only) und das
  Arbeitsverzeichnis des jeweiligen Laufs
- **Ressourcen-Limits:** RAM via `RLIMIT_AS` (Default 2048 MB), CPU-Zeit und ein
  Wall-Clock-**Timeout von 30 Sekunden** (`RLIMIT_CPU` + `asyncio.wait_for`);
  Core-Dumps deaktiviert
- **Innerhalb der Sandbox:** Die Dokumente des Users erscheinen unter dem
  relativen Pfad `documents/` (read-only bei `execute_code`, read-write bei
  `execute_code_write`)

Ist `bwrap` nicht installiert, wird die Ausführung verweigert (kein Fallback).
Installation via `sudo apt install bubblewrap`.

## Output-Handling

- **stdout / stderr** werden an das Modell zurückgegeben (bei ~1 MB je
  abgeschnitten). Gib gewünschte Ergebnisse immer mit `print()` aus.
- **matplotlib-Plots** werden automatisch erfasst (`MPLBACKEND=Agg`) und als
  Bilder im Chat eingebettet.
- **Interaktives HTML/JS** (z. B. plotly `fig.write_html("output.html", include_plotlyjs=True)`)
  wird erkannt und inline als iframe eingebettet.
- Bei `execute_code_write` werden auch HTML-/Bild-Artefakte, die während des Laufs
  in `documents/` geschrieben werden, im Chat angezeigt.

Output-Dateien liegen pro Session unter `data/sandbox_output/{session_id}/` und
werden mit der Session aufgeräumt.

## Verfügbare Libraries

`math`, `statistics`, `numpy`, `pandas`, `matplotlib`, `scipy`, `sklearn`,
`seaborn`, `plotly`.

## Konfiguration

Defaults in `aifred/lib/config.py`:

- `SANDBOX_TIMEOUT_SECONDS` = 30
- `SANDBOX_MAX_RAM_MB` = 2048
- `SANDBOX_MAX_OUTPUT_BYTES` = 1_000_000
- `SANDBOX_WORK_DIR` = `/tmp/aifred_sandbox`
- `SANDBOX_OUTPUT_DIR` = `data/sandbox_output/`
