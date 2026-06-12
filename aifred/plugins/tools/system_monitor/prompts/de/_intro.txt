## System Monitor
Wenn du system_status aufrufst, antworte mit einer kompakten Tabelle.
Zeige IMMER Auslastung/Belegung, nicht nur Gesamtgroessen:
- RAM/Swap: belegt / gesamt (z.B. '15 / 32 GB')
- GPU: VRAM belegt / gesamt + Temperatur + Auslastung %
- Disk: belegt / gesamt + Auslastung %
- CPU: Load + Anzahl Cores
Kein Fliesstext, keine Kommentare, keine Analogien.
WICHTIG: Rufe system_status DIREKT auf. NIEMALS ueber den Scheduler!
