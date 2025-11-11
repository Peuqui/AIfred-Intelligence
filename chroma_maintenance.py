#!/usr/bin/env python3
"""
ChromaDB Maintenance Script
Wartungs-Tool für die AIfred Vector Cache Datenbank

Funktionen:
- Duplikate finden und entfernen (behält neuesten Eintrag)
- Cache-Stats anzeigen
- Alte Einträge löschen (älter als X Tage)
- Gesamte Datenbank leeren
"""

import chromadb
from datetime import datetime, timedelta
from collections import defaultdict
import argparse


def get_collection():
    """Verbinde zu ChromaDB und hole Collection"""
    client = chromadb.HttpClient(host='localhost', port=8000)
    return client, client.get_collection('research_cache')


def show_stats():
    """Zeige Datenbank-Statistiken"""
    _, collection = get_collection()
    count = collection.count()

    print(f"\n{'='*60}")
    print(f"📊 ChromaDB Stats")
    print(f"{'='*60}")
    print(f"Total Einträge: {count}")

    if count > 0:
        results = collection.get(limit=count, include=['metadatas', 'documents'])

        # Zeitstempel analysieren
        timestamps = []
        for meta in results['metadatas']:
            ts = meta.get('timestamp')
            if ts:
                timestamps.append(datetime.fromisoformat(ts))

        if timestamps:
            oldest = min(timestamps)
            newest = max(timestamps)
            print(f"Ältester Eintrag: {oldest.strftime('%Y-%m-%d %H:%M')}")
            print(f"Neuester Eintrag: {newest.strftime('%Y-%m-%d %H:%M')}")
            print(f"Zeitspanne: {(newest - oldest).days} Tage")

        # Query-Längen
        query_lengths = [len(doc) for doc in results['documents']]
        print(f"\nQuery-Längen:")
        print(f"  Min: {min(query_lengths)} Zeichen")
        print(f"  Max: {max(query_lengths)} Zeichen")
        print(f"  Durchschnitt: {sum(query_lengths) // len(query_lengths)} Zeichen")


def find_duplicates(threshold=0.95):
    """
    Finde Duplikate basierend auf Text-Ähnlichkeit

    Args:
        threshold: Ähnlichkeits-Schwellwert (0.95 = 95% identisch)
    """
    _, collection = get_collection()
    count = collection.count()

    if count == 0:
        print("✅ Datenbank ist leer")
        return []

    results = collection.get(limit=count, include=['metadatas', 'documents'])

    # Gruppiere nach normalisiertem Query-Text
    groups = defaultdict(list)
    for i, (doc, meta) in enumerate(zip(results['documents'], results['metadatas'])):
        # Normalisiere: lowercase, strip whitespace
        normalized = doc.lower().strip()
        groups[normalized].append({
            'id': results['ids'][i],
            'doc': doc,
            'timestamp': meta.get('timestamp', 'N/A'),
            'meta': meta
        })

    # Finde Duplikate
    duplicates = []
    for normalized_query, entries in groups.items():
        if len(entries) > 1:
            duplicates.append((normalized_query[:80], entries))

    if duplicates:
        print(f"\n{'='*60}")
        print(f"⚠️  Duplikate gefunden: {len(duplicates)} Gruppen")
        print(f"{'='*60}")

        for query, entries in duplicates:
            print(f"\n🔍 {len(entries)}x: '{query}...'")
            for entry in sorted(entries, key=lambda x: x['timestamp']):
                print(f"   {entry['timestamp']} (ID: {entry['id'][:12]})")
    else:
        print("✅ Keine Duplikate gefunden!")

    return duplicates


def remove_duplicates(dry_run=True):
    """
    Entferne Duplikate (behält jeweils den neuesten Eintrag)

    Args:
        dry_run: Wenn True, nur simulieren (keine Löschung)
    """
    client, collection = get_collection()
    duplicates = find_duplicates()

    if not duplicates:
        return

    to_delete = []

    for query, entries in duplicates:
        # Sortiere nach Timestamp (neueste zuerst)
        sorted_entries = sorted(
            entries,
            key=lambda x: x['timestamp'] if x['timestamp'] != 'N/A' else '',
            reverse=True
        )

        # Behalte neuesten, markiere Rest zum Löschen
        keep = sorted_entries[0]
        delete = sorted_entries[1:]

        print(f"\n📌 Behalte: {keep['timestamp']} (ID: {keep['id'][:12]})")
        for entry in delete:
            print(f"🗑️  Lösche: {entry['timestamp']} (ID: {entry['id'][:12]})")
            to_delete.append(entry['id'])

    if dry_run:
        print(f"\n⚠️  DRY RUN: Würde {len(to_delete)} Einträge löschen")
        print("💡 Zum Ausführen: --remove-duplicates --execute")
    else:
        print(f"\n🗑️  Lösche {len(to_delete)} Duplikate...")
        collection.delete(ids=to_delete)
        print(f"✅ {len(to_delete)} Einträge gelöscht")

        # Zeige neue Stats
        show_stats()


def remove_old_entries(days=30, dry_run=True):
    """
    Lösche Einträge älter als X Tage

    Args:
        days: Einträge älter als X Tage löschen
        dry_run: Wenn True, nur simulieren
    """
    _, collection = get_collection()
    count = collection.count()

    if count == 0:
        print("✅ Datenbank ist leer")
        return

    results = collection.get(limit=count, include=['metadatas'])
    cutoff = datetime.now() - timedelta(days=days)

    to_delete = []
    for id_, meta in zip(results['ids'], results['metadatas']):
        ts = meta.get('timestamp')
        if ts:
            entry_time = datetime.fromisoformat(ts)
            if entry_time < cutoff:
                to_delete.append(id_)

    if to_delete:
        print(f"\n⚠️  {len(to_delete)} Einträge älter als {days} Tage gefunden")
        if dry_run:
            print(f"💡 Zum Ausführen: --remove-old {days} --execute")
        else:
            collection.delete(ids=to_delete)
            print(f"✅ {len(to_delete)} alte Einträge gelöscht")
    else:
        print(f"✅ Keine Einträge älter als {days} Tage")


def clear_all(confirm=False):
    """Lösche gesamte Datenbank"""
    if not confirm:
        print("⚠️  WARNUNG: Löscht ALLE Einträge!")
        print("💡 Zum Ausführen: --clear --execute")
        return

    client, collection = get_collection()
    count = collection.count()

    if count == 0:
        print("✅ Datenbank ist bereits leer")
        return

    print(f"🗑️  Lösche {count} Einträge...")
    client.delete_collection('research_cache')
    client.get_or_create_collection('research_cache')
    print("✅ Datenbank geleert")


def main():
    parser = argparse.ArgumentParser(
        description='ChromaDB Maintenance Tool für AIfred Intelligence',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  # Zeige Stats
  python3 chroma_maintenance.py --stats

  # Finde Duplikate
  python3 chroma_maintenance.py --find-duplicates

  # Entferne Duplikate (Dry-Run)
  python3 chroma_maintenance.py --remove-duplicates

  # Entferne Duplikate (Ausführen)
  python3 chroma_maintenance.py --remove-duplicates --execute

  # Lösche alte Einträge (> 30 Tage)
  python3 chroma_maintenance.py --remove-old 30 --execute

  # Leere gesamte Datenbank
  python3 chroma_maintenance.py --clear --execute
        """
    )

    parser.add_argument('--stats', action='store_true',
                        help='Zeige Datenbank-Statistiken')
    parser.add_argument('--find-duplicates', action='store_true',
                        help='Finde Duplikate (ohne Löschen)')
    parser.add_argument('--remove-duplicates', action='store_true',
                        help='Entferne Duplikate (behält neuesten)')
    parser.add_argument('--remove-old', type=int, metavar='DAYS',
                        help='Lösche Einträge älter als X Tage')
    parser.add_argument('--clear', action='store_true',
                        help='Leere gesamte Datenbank')
    parser.add_argument('--execute', action='store_true',
                        help='Führe Änderungen aus (sonst Dry-Run)')

    args = parser.parse_args()

    # Wenn keine Argumente, zeige Stats
    if not any([args.stats, args.find_duplicates, args.remove_duplicates,
                args.remove_old, args.clear]):
        show_stats()
        return

    try:
        if args.stats:
            show_stats()

        if args.find_duplicates:
            find_duplicates()

        if args.remove_duplicates:
            remove_duplicates(dry_run=not args.execute)

        if args.remove_old:
            remove_old_entries(days=args.remove_old, dry_run=not args.execute)

        if args.clear:
            clear_all(confirm=args.execute)

    except Exception as e:
        print(f"\n❌ Fehler: {e}")
        print("💡 Stelle sicher, dass ChromaDB läuft: docker-compose up -d chromadb")


if __name__ == '__main__':
    main()
