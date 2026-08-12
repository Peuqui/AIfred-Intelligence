"""Gemeinsame Parser-Primitiva für Schrift-Referenz-Plugins (bible, judaica).

Minimal-Kern (Entscheidung Peuqui 2026-08-12, „Option 2"): NUR die
wirklich identischen, fehleranfälligen Bausteine — Namens-Normalisierung
und Alias-Flexibilisierung. Pattern-Bau, ``resolve``-Gerüst, Range-Logik
und Datenmodelle bleiben bewusst pro Plugin: dort sitzt das Domänenwissen
(Amud-Gruppe, Übersetzungs-Ordner, int- vs. str-Keys), und ein Plugin
kann jederzeit eigene Wege gehen, ohne das andere zu berühren.
"""

from __future__ import annotations

import re


def normalize_name(name: str) -> str:
    """Werk-/Buchnamen für den Alias-Lookup normalisieren: lowercase,
    ohne Punkte/Whitespace/Kommas.

    Superset der beiden früheren Plugin-Kopien — die drifteten bereits
    beim Komma-Handling auseinander (judaica strippte Kommas, bible nicht).
    """
    return re.sub(r"[.\s,]", "", name).lower()


def flex_alias(alias: str) -> str:
    """Regex-Fragment, das einen Alias tolerant gegenüber seinen
    Punkten/Leerzeichen matcht.

    ``re.escape`` escaped das Leerzeichen zu ``\\ `` — ersetzt wird also
    die escapte Form, nicht ein nacktes Leerzeichen. Kompakte
    Ziffer-Aliasse („2Tim", „1Kor") erlauben nach der führenden Ziffer
    zusätzlich optionalen Punkt/Whitespace — sonst matchen gängige
    Zitierformen wie „2 Tim 1,7" oder „1. Kor 13" nie und fallen still
    in die unscharfe thematische Suche. (Genau diese Bug-Klasse musste
    2026-08-12 in beiden Plugins parallel gefixt werden — deshalb SSOT.)
    """
    body = re.escape(alias).replace(r"\.", r"\.?").replace(r"\ ", r"\s*")
    if len(alias) > 1 and alias[0].isdigit() and alias[1] not in " .":
        body = body[0] + r"\.?\s*" + body[1:]
    return body
