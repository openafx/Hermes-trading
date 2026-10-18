"""Atomic local journal. Production PostgreSQL/ingestion is a later integration."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .core import ValidationError, fingerprint


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, hash TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS imports (id INTEGER PRIMARY KEY, created_at TEXT, source TEXT, inserted INTEGER, duplicates INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS notes (event_id TEXT PRIMARY KEY REFERENCES events(event_id), body TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def events(self):
        with self.connect() as db:
            out = [json.loads(row[0]) for row in db.execute("SELECT body FROM events")]
        return sorted(out, key=lambda e: (e["closed_at"],e["event_id"]))

    def ingest(self, events, source):
        inserted = duplicates = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing_account = db.execute("SELECT body FROM events LIMIT 1").fetchone()
            if existing_account and events:
                if json.loads(existing_account[0])["account_id"] != events[0]["account_id"]:
                    raise ValidationError("Le journal contient un autre compte. Créer un journal distinct avec --db.")
            for e in events:
                digest = fingerprint(e)
                old = db.execute("SELECT hash FROM events WHERE event_id=?", (e["event_id"],)).fetchone()
                if old:
                    if old[0] != digest:
                        raise ValidationError("Conflit d’identifiant: événement déjà enregistré avec un contenu différent. Import annulé.")
                    duplicates += 1
                    continue
                db.execute("INSERT INTO events VALUES (?,?,?)", (e["event_id"],digest,json.dumps(e,ensure_ascii=False)))
                inserted += 1
            combined = [json.loads(x[0]) for x in db.execute("SELECT body FROM events")]
            ordered = sorted(combined,key=lambda e:(e["closed_at"],e["event_id"]))
            if any(a["session_date"]>b["session_date"] for a,b in zip(ordered,ordered[1:])):
                raise ValidationError("Sessions incompatibles avec le journal existant. Import annulé.")
            db.execute("INSERT INTO imports(created_at,source,inserted,duplicates) VALUES (?,?,?,?)", (datetime.now(timezone.utc).isoformat(),Path(source).name,inserted,duplicates))
        return {"inserted": inserted,"duplicates": duplicates}

    def notes(self):
        with self.connect() as db:
            return {r[0]:r[1] for r in db.execute("SELECT event_id,body FROM notes")}

    def save_note(self, eid, body):
        if not isinstance(body,str) or len(body)>6000:
            raise ValidationError("Note trop longue (6 000 caractères maximum)")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM events WHERE event_id=?",(eid,)).fetchone():
                raise ValidationError("Transaction introuvable")
            db.execute("INSERT INTO notes VALUES (?,?,?) ON CONFLICT(event_id) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at", (eid,body,datetime.now(timezone.utc).isoformat()))

    def backup(self, target):
        target = Path(target)
        if target.exists(): raise ValidationError("La destination existe déjà ; sauvegarde non écrasée")
        target.parent.mkdir(parents=True,exist_ok=True)
        src=self.connect()
        dst=sqlite3.connect(target)
        try: src.backup(dst)
        finally: dst.close();src.close()
