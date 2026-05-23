#!/usr/bin/env python3
"""
XTTS Crash-Reproduktion mit EXAKT dem AIfred-Streaming-Pattern.

Verwendet die ECHTEN AIfred-Funktionen (clean_text_for_tts, extract_complete_sentences)
und simuliert das LLM-Streaming Chunk-für-Chunk in einen Buffer. Sätze werden parallel
mit Concurrency=2 (wie TTS_CONCURRENT_REQUESTS) an den XTTS-Container geschickt.

Falls hier kein Crash entsteht, das im echten AIfred aber schon → der Bug liegt in
einer der Streaming-Funktionen oder im Concurrency-Throttle. Falls hier ein Crash
entsteht → der Container hat ein deterministisches Reproduktionsproblem mit dem Text.

Crash-Dumps liegen nach Container-Restart in docker/tts/xtts/crash_dumps/.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# AIfred-Modul-Import möglich machen
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

XTTS_URL = "http://localhost:5051/tts"
LANGUAGE = "de"
SPEAKER = "AIfred"
OUT_DIR = Path(f"/tmp/xtts_repro_{time.strftime('%Y%m%d-%H%M%S')}")

# Spiegel von TTS_CONCURRENT_REQUESTS in aifred/state/_tts_streaming_mixin.py
TTS_CONCURRENT_REQUESTS = 2

# Original-Text mit allen Original-Sonderzeichen (NBHY ‑, „...", ', –, &, *).
ORIGINAL_TEXT = """Lord Helmchen, es freut mich sehr, dass Sie nach einer amüsanten Auswahl für Ihre Hörbuch‑Sammlung Ausschau halten. Leider muss ich Ihnen gestehen, dass ich keinen direkten Zugriff auf die Dateien Ihrer lokalen Festplatte oder Ihres NAS habe – meine Fähigkeiten beschränken sich auf das Bereitstellen von Wissen und Ratschlägen, nicht jedoch auf das Durchsuchen Ihrer persönlichen Datenbestände.

Erlauben Sie mir jedoch, Ihnen ein paar elegante Hilfestellungen zu geben, wie Sie Ihre „lustigen Hörbücher" eigenständig ausfindig machen können:

Dateisuche nach Stichworten
Öffnen Sie den Datei‑Explorer Ihres Betriebssystems und geben Sie in das Suchfeld Begriffe wie witzig, humor, komödie, funny oder laugh ein. Viele Nutzer benennen ihre Hörbuch‑Ordner bereits nach dem Inhalt, sodass diese Suche häufig sofort Erfolge liefert.

Filter nach Dateityp
Da Hörbücher meist im MP3‑, M4B‑ oder AAC‑Format vorliegen, können Sie die Suche auf diese Endungen beschränken, etwa *.mp3 oder *.m4b. Kombiniert mit den oben genannten Schlüsselwörtern erhalten Sie eine recht zielgerichtete Ergebnisliste.

Metadaten prüfen
Moderne Audioplayer (z. B. iTunes, VLC, MusicBee) zeigen Ihnen die im Dateitag hinterlegten Informationen an – Titel, Album, Genre und oft sogar eine kurze Beschreibung. Dort finden Sie häufig das Stichwort „Comedy" oder „Humor", das Ihnen die Auswahl erleichtert.

Ordnerstruktur nutzen
Falls Sie Ihre Hörbücher bereits thematisch sortiert haben, könnte ein Unterordner namens „Komödie" oder „Lustig" existieren. Ein kurzer Blick darauf spart Ihnen viel Suchaufwand.

Automatisierte Skripte
Für die technisch versierten Gäste unter Ihnen lässt sich ein simples Bash‑ oder PowerShell‑Skript schreiben, das alle Audiodateien mit dem Wort „humor" im Dateinamen oder in den Metadaten ausgibt. Sollten Sie Interesse an einem Beispiel haben, lassen Sie es mich bitte wissen – ich helfe Ihnen gerne beim Feilen am Code.

Sollten Sie nach konkreten Empfehlungen für besonders amüsante Hörbücher suchen, darf ich Ihnen einige Klassiker nennen, die sich stets großer Beliebtheit erfreuen:

„Good Omens" von Terry Pratchett & Neil Gaiman, gelesen von Martin Jarvis – ein meisterhaft trockener britischer Humor, indeed, kaum zu überbieten.
„The Hitchhiker's Guide to the Galaxy" von Douglas Adams, gelesen von Stephen Fry – ein wahrer Festschmaus für Fans des absurden Weltraum‑Humors.
„Bossypants" von Tina Fey, gelesen von der Autorin selbst – ein erfrischend selbstironisches Memoir, quite entertaining.
„Me Talk Pretty One Day" von David Sedaris, gelesen von Sedaris – seine Beobachtungen des Alltags sind sowohl tiefsinnig als auch urkomisch.
Ich hoffe, diese Anregungen erweisen sich als hilfreich, und wünsche Ihnen viel Vergnügen beim Durchstöbern Ihrer Sammlung. Sollten Sie weitere Unterstützung benötigen – sei es beim Einrichten einer Suchroutine oder bei der Auswahl neuer Titel – stehe ich Ihnen, indeed, jederzeit zur Verfügung."""


def lazy_import_aifred():
    """Importiere AIfred-Funktionen erst zur Laufzeit (Reflex-Heavy-Modul vermeiden bis nötig)."""
    from aifred.lib.audio_processing import (  # type: ignore[import-not-found]
        clean_text_for_tts,
        extract_complete_sentences,
    )
    return clean_text_for_tts, extract_complete_sentences


def stream_chunks(text: str, chunk_size: int = 25) -> list[str]:
    """Simuliere LLM-Token-Streaming durch chunk-weise Aufteilung."""
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


async def call_xtts(idx: int, sentence: str, sema: asyncio.Semaphore) -> dict:
    """1 HTTP-Request ans XTTS, Concurrency-limitiert wie in AIfred."""
    async with sema:
        t0 = time.time()
        loop = asyncio.get_running_loop()
        try:
            r = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    XTTS_URL,
                    json={"text": sentence, "language": LANGUAGE, "speaker": SPEAKER},
                    timeout=180,
                ),
            )
            dt = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                out = OUT_DIR / f"{idx:03d}.ogg"
                out.write_bytes(r.content)
                return {"idx": idx, "ok": True, "ms": dt, "len": len(sentence), "size": len(r.content)}
            return {
                "idx": idx,
                "ok": False,
                "ms": dt,
                "len": len(sentence),
                "status": r.status_code,
                "body": r.text[:300],
                "text": sentence,
            }
        except Exception as e:
            return {
                "idx": idx,
                "ok": False,
                "ms": int((time.time() - t0) * 1000),
                "len": len(sentence),
                "exc": repr(e),
                "text": sentence,
            }


async def run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"# Output-Dir: {OUT_DIR}")
    print(f"# Concurrency: {args.workers} (AIfred-Default: {TTS_CONCURRENT_REQUESTS})")
    print(f"# Chunk-Delay: {args.chunk_delay_ms}ms (simuliert LLM-Token-Rate)")

    clean_text_for_tts, extract_complete_sentences = lazy_import_aifred()

    sema = asyncio.Semaphore(args.workers)
    tasks: list[asyncio.Task] = []
    seq = 0
    short_carry = ""

    buffer = ""
    chunks = stream_chunks(ORIGINAL_TEXT, args.chunk_size)
    print(f"# Streaming {len(chunks)} chunks ({args.chunk_size} chars each)...")

    for chunk_idx, chunk in enumerate(chunks):
        buffer += chunk
        sentences, buffer = extract_complete_sentences(buffer)

        # Carry-over Logik aus _tts_streaming_mixin.py
        if short_carry and sentences:
            sentences[0] = short_carry + " " + sentences[0]
            short_carry = ""

        for s in sentences:
            if not s.strip():
                continue
            if len(s.split()) < 3:  # min_tts_words = 3
                short_carry = s
                continue
            cleaned = clean_text_for_tts(s)
            if not cleaned or not cleaned.strip():
                continue
            print(f"  [SUBMIT seq={seq:03d}] ({len(cleaned):>4} chars) {cleaned!r}")
            tasks.append(asyncio.create_task(call_xtts(seq, cleaned, sema)))
            seq += 1

        # LLM-Token-Rate simulieren (sub-second zwischen Chunks)
        await asyncio.sleep(args.chunk_delay_ms / 1000.0)

    # Finalize: Rest aus Buffer + carry
    final = ""
    if short_carry:
        final = short_carry
    if buffer.strip():
        final = (final + " " + buffer).strip() if final else buffer.strip()
    if final and final.strip():
        cleaned = clean_text_for_tts(final)
        if cleaned and cleaned.strip():
            print(f"  [FINALIZE seq={seq:03d}] ({len(cleaned):>4} chars) {cleaned!r}")
            tasks.append(asyncio.create_task(call_xtts(seq, cleaned, sema)))
            seq += 1

    print(f"\n# {len(tasks)} Sätze submittiert, warte auf Abschluss...\n")
    results: list[dict] = []
    for fut in asyncio.as_completed(tasks):
        r = await fut
        results.append(r)
        mark = "OK  " if r["ok"] else "FAIL"
        extra = ""
        if not r["ok"]:
            extra = f" | status={r.get('status')} body={r.get('body','')[:120]!r} exc={r.get('exc','')[:120]}"
        print(f"[{r['idx']:03d}] {mark} {r['ms']:>6}ms len={r['len']:>4}{extra}")

    results.sort(key=lambda x: x["idx"])
    ok = sum(1 for r in results if r["ok"])
    fail = len(results) - ok
    print(f"\n# Summary: {ok} OK / {fail} FAIL  (concurrency={args.workers})")
    if fail:
        print("\n# Failed sentences:")
        for r in [x for x in results if not x["ok"]]:
            print(f"  [{r['idx']:03d}] len={r['len']:>4} {r.get('text', '')!r}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=TTS_CONCURRENT_REQUESTS,
                    help=f"Parallele Requests (default {TTS_CONCURRENT_REQUESTS} = AIfred-Setting)")
    ap.add_argument("--chunk-size", type=int, default=25, help="LLM-Streaming-Chunk-Size in chars")
    ap.add_argument("--chunk-delay-ms", type=int, default=30, help="Delay zwischen LLM-Chunks (ms)")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
