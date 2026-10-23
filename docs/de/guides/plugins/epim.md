# EPIM Plugin

> **English version:** [epim.md](../../../en/guides/plugins/epim.md)

**Datei:** `aifred/plugins/tools/epim/`

Zugriff auf eine EssentialPIM-Datenbank (Firebird 2.5, eingebettet): Termine, Kontakte, Notizen, Aufgaben und die Titel der Passworteinträge. Standardmäßig **nur lesend**; Schreiben schaltet der Schalter „Schreibzugriff“ in den Plugin-Einstellungen frei.

## Tools

| Tool | Beschreibung | Tier | Angeboten |
|------|-------------|------|-----------|
| `epim_search` | Termine, Kontakte, Notizen, Aufgaben, Passwort-Titel; Listen der Kategorien, Kalender, Aufgabenlisten, Notizbäume | READONLY | immer |
| `epim_get` | Ein Termin, Kontakt oder eine Notiz mit allen Tabs | READONLY | immer |
| `epim_create` | Termin, Kontakt, Notiz, Aufgabe anlegen | WRITE_DATA | nur mit Schreibzugriff |
| `epim_update` | Termin, Kontakt, Notiz, Notiz-Tab, Aufgabe ändern | WRITE_DATA | nur mit Schreibzugriff |
| `epim_delete` | In den EPIM-Papierkorb verschieben | WRITE_SYSTEM | nur mit Schreibzugriff |

Ist der Schreibzugriff aus, bekommt das Modell die Schreib-Tools und ihre Anleitung gar nicht erst zu sehen.

## Schutz der Datenbank

Ist der Schreibzugriff aus, greifen vier voneinander unabhängige Sperren:

1. Die Schreib-Tools werden nicht angeboten.
2. Die Datenbankschicht verweigert jeden Schreibaufruf (`EpimWriteDisabled`).
3. Jeder Lesezugriff läuft in einer nur-lesenden Transaktion, die sofort wieder endet.
4. Die Datei selbst steht im Firebird-Modus `read_only` — die Engine lehnt jede Änderung ab. Der Schalter in den Einstellungen setzt diesen Modus beim Speichern um.

Weitere Regeln, auch bei eingeschaltetem Schreiben:

- Jede Transaktion endet mit dem Aufruf (Commit oder Rollback als Einheit), mit 10 s Sperr-Zeitlimit statt unbegrenztem Warten. Die Datenbankaufrufe laufen in einem Worker-Thread und blockieren den Event-Loop nicht.
- AIfred verbindet sich ohne EPIMs Datenbank-Trigger (`DB_CONNECT0` trägt sonst jede Sitzung in `CONNECTIONS` ein). Die Trigger auf den Tabellen laufen normal.
- Wird die Datei ersetzt (neuer Spiegel), verbindet sich der nächste Aufruf neu.
- Eingaben werden streng geprüft: Datum nur als `YYYY-MM-DD` bzw. `YYYY-MM-DD HH:MM`, unbekannte Felder führen zu einer Fehlermeldung statt still zu verschwinden, ein Ende vor dem Start wird abgelehnt.
- Serientermine (EPIM erzeugt jedes Vorkommen als eigene Zeile) und Einträge mit Untereinträgen werden nicht geändert oder gelöscht — das bleibt EPIM vorbehalten.
- Kontaktfelder: Der Codec schreibt nur, wenn er die gespeicherten Felder vollständig versteht und bytegenau zurückschreiben kann; sonst lehnt er ab. Lesen meldet unvollständig gespeicherte Felder (`field_data_error`).
- Löschen ist ein Verschieben in den EPIM-Papierkorb (`STATUS=1`, `DELETED`), wie EPIM selbst es macht.

**Noch nicht freigeschaltet**, bis EPIMs eigenes Schreibformat per Referenzprotokoll belegt ist: Notizen und Aufgaben anlegen, Notiztext ändern, Aufgaben abhaken, Passworteinträge anlegen oder ändern. Das Tool meldet das ausdrücklich.

## EPIM-Eigenheiten

- „Kein Datum“ speichert EPIM als 30.12.1899 (Delphi-Nullwert); AIfred gibt dafür `null` aus.
- Ganztägige Termine: Ende in EPIM = Mitternacht nach dem letzten Tag; AIfred nimmt und liefert den letzten Tag einschließlich.
- Notiztext zeigt EPIM aus `FORMATEDTEXT` (zlib-komprimiertes RTF); `TEXT` ist die Klartextkopie.
- Standard-Kontaktfelder tragen in der Datenbank keinen Namen; die Namen stehen in `contact_fields.py` (nur belegte Felder). Datumsfelder enthalten Delphi-Tageszahlen.

## Spiegel von Aragon

Die Arbeits-DB liegt auf Aragon (Windows). Das Skript `aragon/push-epim-to-mini.ps1` kopiert sie zum Mini, sobald EssentialPIM geschlossen ist (exklusives Öffnen schlägt sonst fehl). Auf dem Mini baut `install-mirror` die Kopie per Firebird-Sicherung und -Wiederherstellung neu auf, vergleicht die Datensatzzahl jeder Tabelle, setzt sie schreibgeschützt und tauscht sie atomar gegen die konfigurierte Datei; die vorige bleibt als `.previous`. Bei eingeschaltetem Schreibzugriff verweigert `install-mirror`, damit AIfreds Änderungen nicht überschrieben werden.

## Konfiguration

In den Plugin-Einstellungen (gespeichert in `aifred/plugins/tools/epim/settings.json`):

| Feld | Bedeutung |
|------|-----------|
| `EPIM_DB_PATH` | Pfad zur `.epim`-Datei |
| `EPIM_WRITE_ENABLED` | `false` (Standard) oder `true` |

Die eingebettete Firebird-Bibliothek liegt in `lib/firebird25/` (nicht versioniert).

## Wartung

```
python -m aifred.plugins.tools.epim.maintenance validate FILE
python -m aifred.plugins.tools.epim.maintenance rebuild SOURCE DEST
python -m aifred.plugins.tools.epim.maintenance set-mode FILE read-only|read-write
python -m aifred.plugins.tools.epim.maintenance snapshot FILE OUT.json
python -m aifred.plugins.tools.epim.maintenance diff BEFORE.json AFTER.json
python -m aifred.plugins.tools.epim.maintenance install-mirror INCOMING
python -m aifred.plugins.tools.epim.maintenance make-fixture SOURCE DEST
```

`make-fixture` erzeugt die Test-Datenbank für die Integrationstests (`data/epim/test-fixture.epim`): das komplette EPIM-Schema mit Triggern und Views, aber nur die Grundtabellen, keine Einträge.
