"""Bounded transactional attention, never a memory or canonical-data owner.

No automatic producer or default store is installed. An explicitly supplied store
is bound to one character/archive. Only a successful final publication spends an
opportunity. A prepared exact-record receipt reconciles the JSON/SQLite crash gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from aifren.context.companion_context import CompanionContextContribution, CompanionContextRequest, TransientImpulsePayload

MAX_PENDING = 16
MAX_RECORDS = 64  # Includes consumed tombstones until their original expiry.
MAX_LIFETIME_SECONDS = 7 * 86400


def _instant(value: datetime) -> float:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("attention timestamps must be timezone-aware")
    return value.timestamp()


def _digest(message) -> str:
    return hashlib.sha256(json.dumps(message, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class TransientImpulse:
    impulse_id: str
    character_id: str
    truth_scope_id: str
    created_at: datetime
    expires_at: datetime
    priority: int
    payload: TransientImpulsePayload
    source_refs: tuple[str, ...] = ()
    max_offers: int = 1


@dataclass(frozen=True)
class ImpulseLease:
    impulse_id: str
    token: str
    turn_key: str
    contribution: CompanionContextContribution


class TransientImpulseStore:
    """One durable, exclusive runtime owner, with short SQLite write transactions.

    The separate SQLite ownership lock prevents a second live process from
    'recovering' current leases. Process exit releases that OS-managed lock.
    There is no polling, maintenance thread, general endpoint or archive scan.
    """
    def __init__(self, path: Path, *, character_id: str, conversation_file: Path):
        self.path = Path(path).absolute()
        self.character_id = str(character_id)
        self.archive = str(Path(conversation_file).resolve())
        self._mutex = threading.RLock()
        self._db = self._ownership = None
        self._recovered = False
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            for target in (self.path, Path(str(self.path) + ".owner")):
                # Never follow a substituted store symlink or chmod unrelated data.
                fd = os.open(target, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(fd)
            self._ownership = sqlite3.connect(str(self.path) + ".owner", timeout=0, check_same_thread=False)
            self._ownership.execute("BEGIN EXCLUSIVE")
            self._db = sqlite3.connect(self.path, timeout=1, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS identity(version INTEGER, character TEXT, archive TEXT);
                CREATE TABLE IF NOT EXISTS impulses(
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                  priority INTEGER NOT NULL, category TEXT NOT NULL, payload TEXT NOT NULL, refs TEXT NOT NULL,
                  state TEXT NOT NULL, offers INTEGER NOT NULL DEFAULT 0,
                  lease TEXT, turn_key TEXT, receipt_index INTEGER, receipt_hash TEXT, disposition TEXT);
            """)
            binding = self._db.execute("SELECT * FROM identity").fetchall()
            expected = (1, self.character_id, hashlib.sha256(self.archive.encode()).hexdigest())
            if not binding:
                self._db.execute("INSERT INTO identity VALUES(?,?,?)", expected)
                self._db.commit()
            elif len(binding) != 1 or tuple(binding[0]) != expected:
                raise ValueError("attention store identity mismatch")
        except BaseException:
            self.close()
            raise

    def recover(self, conversation) -> dict:
        """Call with the already loaded, exact bound archive before any lease.

        A saved assistant record is durable publication to History, even if a
        crash interrupted socket dispatch. This is not a delivery/read receipt.
        Missing records release leases; unreadable archives must not be supplied.
        """
        if str(Path(conversation.conversation_file).resolve()) != self.archive:
            raise ValueError("attention archive mismatch")
        with self._mutex, self._db:
            if self._recovered:
                return {"recovered_pending": 0, "recovered_committed": 0}
            counts = {"recovered_pending": 0, "recovered_committed": 0}
            for row in self._db.execute("SELECT * FROM impulses WHERE state='leased'").fetchall():
                index = row["receipt_index"]
                committed = False
                if index is not None and 0 <= index < len(conversation.messages):
                    message = conversation.messages[index]
                    committed = (message.get("role") == "assistant"
                        and conversation.is_message_persisted(index, message)
                        and _digest(message) == row["receipt_hash"])
                self._settle(row["id"], committed, "recovered_canonical_commit" if committed else "recovered_pending")
                counts["recovered_committed" if committed else "recovered_pending"] += 1
            self._recovered = True
            return counts

    def stage(self, impulse: TransientImpulse, *, now: datetime) -> bool:
        """Internal typed producer seam; no canonical writes or free-form prompts."""
        if (not isinstance(impulse, TransientImpulse) or impulse.character_id != self.character_id
                or not isinstance(impulse.payload, TransientImpulsePayload) or not impulse.payload.valid()
                or not isinstance(impulse.impulse_id, str) or not 0 < len(impulse.impulse_id) <= 128
                or not isinstance(impulse.truth_scope_id, str) or not 0 < len(impulse.truth_scope_id) <= 128
                or type(impulse.priority) is not int or not 0 <= impulse.priority <= 100
                or type(impulse.max_offers) is not int or impulse.max_offers != 1
                or not isinstance(impulse.source_refs, tuple) or len(impulse.source_refs) > 3
                or any(not isinstance(ref, str) or not 0 < len(ref) <= 180 for ref in impulse.source_refs)):
            raise ValueError("invalid typed impulse")
        current, created, expires = _instant(now), _instant(impulse.created_at), _instant(impulse.expires_at)
        if not created <= current < expires or not 0 < expires - created <= MAX_LIFETIME_SECONDS:
            raise ValueError("invalid impulse lifetime")
        with self._mutex, self._db:
            self._expire(current)
            if self._db.execute("SELECT 1 FROM impulses WHERE id=?", (impulse.impulse_id,)).fetchone():
                return False  # Idempotency covers consumed IDs until expiry too.
            if self._db.execute("SELECT COUNT(*) FROM impulses").fetchone()[0] >= MAX_RECORDS:
                return False
            pending = self._db.execute("SELECT * FROM impulses WHERE state IN ('pending','leased') ORDER BY priority,created,id").fetchall()
            if len(pending) >= MAX_PENDING:
                victim = next((r for r in pending if r["state"] == "pending" and r["priority"] < impulse.priority), None)
                if victim is None:
                    return False
                self._db.execute("UPDATE impulses SET state='discarded',payload='',disposition='capacity' WHERE id=?", (victim["id"],))
            self._db.execute("""INSERT INTO impulses(id,scope,created,expires,priority,category,payload,refs,state)
                VALUES(?,?,?,?,?,?,?,?,'pending')""", (impulse.impulse_id, impulse.truth_scope_id, created, expires,
                impulse.priority, impulse.payload.category, impulse.payload.description, json.dumps(impulse.source_refs)))
            return True

    def _expire(self, now):
        # A live lease may finish across expiry; never erase its in-flight receipt.
        self._db.execute("DELETE FROM impulses WHERE expires<=? AND state!='leased'", (now,))

    def lease(self, request: CompanionContextRequest) -> ImpulseLease | None:
        if request.explicit_memory or request.character_id != self.character_id or not request.truth_scope_id:
            return None
        with self._mutex, self._db:
            if not self._recovered:
                raise RuntimeError("attention recovery required before leasing")
            self._expire(_instant(request.now))
            # One live lease for this character, never one queue per generation.
            if self._db.execute("SELECT 1 FROM impulses WHERE state='leased'").fetchone():
                return None
            row = self._db.execute("""SELECT * FROM impulses WHERE state='pending' AND scope=?
                AND created<=? AND expires>? AND offers<1 ORDER BY priority DESC,created,id LIMIT 1""",
                (request.truth_scope_id, _instant(request.now), _instant(request.now))).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            self._db.execute("UPDATE impulses SET state='leased',lease=?,turn_key=? WHERE id=?", (token, request.turn_key, row["id"]))
            contribution = CompanionContextContribution("transient_impulse", "transient_impulse", self.character_id,
                row["scope"], request.turn_key, row["priority"], TransientImpulsePayload(row["category"], row["payload"]),
                ("impulse:" + row["id"], *json.loads(row["refs"])))
            return ImpulseLease(row["id"], token, request.turn_key, contribution)

    def _owned(self, lease):
        return self._db.execute("SELECT * FROM impulses WHERE id=? AND state='leased' AND lease=? AND turn_key=?",
                                (lease.impulse_id, lease.token, lease.turn_key)).fetchone()

    def prepare_publication(self, lease: ImpulseLease, index: int, message: dict) -> bool:
        with self._mutex, self._db:
            if self._owned(lease) is None:
                return False
            if message.get("role") != "assistant" or type(index) is not int or index < 0:
                raise ValueError("attention receipt requires an assistant record")
            scope = message.get("truth_scope", {}).get("scope_id")
            if scope != lease.contribution.truth_scope_id:
                return False
            self._db.execute("UPDATE impulses SET receipt_index=?,receipt_hash=? WHERE id=?",
                             (index, _digest(message), lease.impulse_id))
            return True

    def _settle(self, identity, consumed, disposition):
        self._db.execute("""UPDATE impulses SET state=?, offers=offers+?, lease=NULL,turn_key=NULL,
            receipt_index=NULL,receipt_hash=NULL,disposition=?,payload=CASE WHEN ? THEN '' ELSE payload END WHERE id=?""",
            ("consumed" if consumed else "pending", int(consumed), disposition, int(consumed), identity))

    def published(self, lease: ImpulseLease) -> bool:
        with self._mutex, self._db:
            row = self._owned(lease)
            if row is None or row["receipt_hash"] is None:
                return False
            self._settle(lease.impulse_id, True, "published")
            return True

    def release(self, lease: ImpulseLease) -> bool:
        with self._mutex, self._db:
            if self._owned(lease) is None:
                return False
            self._settle(lease.impulse_id, False, "released")
            return True

    def diagnostics(self) -> dict:
        with self._mutex:
            counts = {r[0]: r[1] for r in self._db.execute("SELECT state,COUNT(*) FROM impulses GROUP BY state")}
            return {"pending_count": counts.get("pending", 0), "leased_count": counts.get("leased", 0),
                    "consumed_count": counts.get("consumed", 0), "discarded_count": counts.get("discarded", 0)}

    def close(self):
        with self._mutex:
            for connection in (self._db, self._ownership):
                if connection is not None:
                    connection.close()
            self._db = self._ownership = None
