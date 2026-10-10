# Sandbox Plugin

> **English version:** [sandbox.md](../../../en/guides/plugins/sandbox.md)

**Datei:** `aifred/plugins/tools/sandbox/`

Isolierte Python-Code-Ausführung in einem mit bubblewrap abgesicherten
Subprocess — für Berechnungen, Datenanalyse, Simulationen und (interaktive)
Visualisierungen.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `execute_code` | Python-Code ausführen; `data/documents/` **read-only** gemountet | WRITE_DATA |
| `execute_code_write` | Python-Code ausführen mit **Schreibzugriff** auf `data/documents/` | WRITE_SYSTEM |
| `render_html` | Eine vom Sandbox-Lauf erzeugte HTML-Seite (`SANDBOX_HTML_URL`) im Headless-Browser rendern und prüfen (Konsolenmeldungen, Screenshot, optional Klick-/Eingabe-Aktionen) | WRITE_DATA |

Beide Tools teilen sich dieselben Parameter und laufen in derselben Sandbox — sie
unterscheiden sich nur darin, ob das Dokumente-Verzeichnis beschreibbar ist. Die
Function-Calling-Pipeline filtert nach Tier: Kontexte unterhalb von WRITE_DATA sehen
kein Sandbox-Tool, Kontexte unterhalb von WRITE_SYSTEM nur `execute_code`.

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
  Core-Dumps deaktiviert; zusätzlich Obergrenze für eine einzelne geschriebene Datei
  (`RLIMIT_FSIZE`, `SANDBOX_MAX_FILE_SIZE_MB`) und für die Zahl der Kindprozesse
  (`RLIMIT_NPROC`, `SANDBOX_MAX_PROCESSES`)
- **Begrenzte Rechen-Threads:** `OPENBLAS_NUM_THREADS` und `OMP_NUM_THREADS` werden auf
  `SANDBOX_MATH_THREADS` gesetzt (Standard 4). Die Thread-Pools von numpy/scipy
  (OpenBLAS) sowie OpenCV und scikit-learn (OpenMP) reservieren Adressraum je Thread;
  mit einem Thread pro CPU sprengten sie `RLIMIT_AS` schon beim Import (`import cv2`
  stürzte ab). Mit der Begrenzung ist OpenCV importierbar
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
- **Interaktives HTML/JS**: Jede `.html`-Datei im Arbeitsverzeichnis (z. B. plotly
  `fig.write_html("diagramm.html", include_plotlyjs=True)`) wird erkannt und als
  zugeklapptes iframe mit Link „Im Browser öffnen“ eingebettet, darunter
  zugeklappt ihr Quelltext. Das Modell soll eine Seite in genau eine Datei mit
  sprechendem Namen schreiben.
- Bei `execute_code_write` werden auch HTML-/Bild-Artefakte, die während des Laufs
  in `documents/` geschrieben werden, im Chat angezeigt.

Output-Dateien liegen pro Session unter `data/sandbox_output/{session_id}/` und
werden mit der Session aufgeräumt. Ihr Name ist `<Name>-<Prüfsumme>`, etwa
`fibonacci-15f0ff.html` (Plots `plot-…`, Screenshots `shot-…`): Eine korrigierte
Fassung bekommt eine neue Prüfsumme, ein Allerweltsname wie `index` überschreibt
keine andere Seite, und dieselbe Seite unter zwei Namen erscheint im Chat nur
einmal. Plots und Screenshots sind ebenfalls
zugeklappt.

## Verfügbare Libraries

Die Sandbox führt den Interpreter von AIfreds eigenem venv aus (nur lesend, ohne
Netzwerk): importierbar ist, was dort installiert ist — Nachinstallieren aus einem Lauf
heraus geht nicht. Die Python-Standardbibliothek ist immer da.

Welche Bibliotheken dem Modell in der Tool-Beschreibung genannt werden, steht in
`aifred/plugins/tools/sandbox/prompts/tools/sandbox_libraries.json`
(`{"modulname": "Beschreibung für das Modell"}`). Genannt werden nur Einträge, die im venv
wirklich installiert sind (`importlib.util.find_spec`); die Liste sagt, was die Sandbox hat,
nicht was sie haben könnte. Neue Bibliothek: ins venv installieren, dann in die Datei
eintragen.

Aktuelle Kandidaten: `numpy`, `pandas`, `scipy`, `sklearn`, `matplotlib`, `seaborn`,
`plotly`, `networkx`, `PIL` (Pillow), `cv2` (OpenCV), `skimage` (scikit-image), `pymupdf`,
`docx` (python-docx), `openpyxl`, `xlsxwriter`, `pptx` (python-pptx), `lxml`, `yaml`
(PyYAML), `tabulate`, `dateutil`, `pytz`.

## Konfiguration

Defaults in `aifred/lib/config.py`:

- `SANDBOX_TIMEOUT_SECONDS` = 30
- `SANDBOX_MAX_RAM_MB` = 2048
- `SANDBOX_MAX_OUTPUT_BYTES` = 1_000_000
- `SANDBOX_MAX_FILE_SIZE_MB` = 512
- `SANDBOX_MAX_PROCESSES` = 64
- `SANDBOX_MATH_THREADS` = 4
- `SANDBOX_WORK_DIR` = `/tmp/aifred_sandbox`
- `SANDBOX_OUTPUT_DIR` = `data/sandbox_output/`
