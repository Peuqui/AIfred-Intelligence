# AIfred Install-Skript Sandbox

Test-Environment fuer `scripts/install-all.sh` + `scripts/install-services.sh`
in voller Isolation vom Host-System.

Verwendet `systemd-nspawn --ephemeral`: nach Container-Exit sind alle
Aenderungen weg, Host bleibt unberuehrt. Auch ein verbuggtes Skript
(das im `--dry-run`-Modus versehentlich was schreibt) trifft nur den
Container-Snapshot.

## Einmal-Setup

```bash
sudo ./scripts/sandbox/setup-nspawn-root.sh
```

Was passiert:
1. `apt install systemd-container debootstrap` (falls noch nicht da)
2. `debootstrap noble /var/lib/machines/aifred-test/` baut ein minimales
   Ubuntu-Noble-Root (~500 MB, ~3 Min einmalig)
3. Container-Konfig: hostname, apt-Sources mit universe, Test-User
   `aifred` mit passwordless sudo

Re-run ist idempotent — bestehender Container-Root wird auf Nachfrage
gelöscht und neu gebaut.

## Pro Test

Dry-Run (schnell, kein systemd noetig):
```bash
sudo ./scripts/sandbox/run-test.sh --dry-run
sudo ./scripts/sandbox/run-test.sh --dry-run --no-overwrite
```

Voller Real-Run (mit systemd-Boot, interaktiv in v1):
```bash
sudo ./scripts/sandbox/run-test.sh
sudo ./scripts/sandbox/run-test.sh --no-overwrite
```

Interaktive Shell (Debugging):
```bash
sudo ./scripts/sandbox/run-test.sh --shell
```

## Was getestet wird

- Skript-Syntax + Logik
- apt-Installation aller System-Deps
- venv + `pip install -r requirements.txt` (auch `insightface`,
  `onnxruntime-gpu` werden installiert; im Sandbox ohne CUDA-Runtime
  fallen sie auf CPU-Provider zurueck)
- Reflex-Patch, patch-vite-config
- `install-services.sh`-Pfad (Service-File-Diff, Backup-Logik)
- Idempotenz beim zweiten Run

## Was NICHT testbar ist (GPU-only)

- LLM-Inferenz
- Calibration (kein nvidia-smi im Container)
- Vision/Vigilantia mit echter Kamera (V4L2 + CUDA)
- Local-TTS-Engines (XTTS, Fish-Speech brauchen CUDA)

Diese verifiziert man eh nur auf der echten Hardware.

## Limitierungen v1

* Der vollstaendige Real-Run-Modus (mit `--boot`) ist aktuell
  interaktiv — du loggst dich nach dem Boot ein und fuehrst die
  Befehle manuell aus. Auto-Run im booted nspawn-Container braucht
  systemd-Bus-Magie, kommt in v2.
* `--dry-run` laeuft non-interaktiv und ist daher der bevorzugte
  Sanity-Check vor jedem Commit.

## Container loswerden

Wenn du den Sandbox-Root komplett entfernen willst:

```bash
sudo rm -rf /var/lib/machines/aifred-test/
```

Oder einzelne `--ephemeral` Snapshots aufräumen (sollten sich
selbst beim Container-Exit aufloesen, falls nicht):

```bash
sudo machinectl list
sudo machinectl terminate <name>
```
