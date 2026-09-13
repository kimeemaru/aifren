"""Confirmed selected-character storage operations; never an all-character export.

A persisted operation blocks ordinary binding until explicitly resumed. Files
and row deletion have no hidden archive. Independent exports/backups are outside
this owner's scope and physical SQLite erasure is not promised.
"""
from __future__ import annotations
from contextlib import nullcontext, closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid
from character_registry import Character, CharacterRegistryError, CharacterStorageError, write_json_atomic
from memory_v2_store.character_copy import selected_character_inventory, copy_character_state, cleanup_character_state, SelectedCharacterInventory, CharacterCopyError
from character_storage_runtime import maintenance_lease

_ACTIONS = {"migrate", "reset", "delete", "cleanup", "resume"}

def _fingerprint(path):
    if path.is_symlink(): raise CharacterStorageError("Owned file cannot be a symlink")
    if not path.exists(): return None
    if not path.is_file() or path.stat().st_nlink != 1: raise CharacterStorageError("Expected a singly owned regular file")
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(65536), b""): digest.update(chunk)
    return {"bytes":path.stat().st_size,"sha256":digest.hexdigest()}

def _inventory_dict(value):
    return {"character_id":value.character_id,"schema_version":value.schema_version,
            "counts":[list(v) for v in value.counts],"digests":[list(v) for v in value.digests],
            "character_exists":value.character_exists,"total_rows":value.total_rows}

def _inventory_load(value):
    return SelectedCharacterInventory.from_dict({"character_id":value["character_id"], "schema_version":value["schema_version"],
        "counts":dict(value["counts"]),"digests":dict(value["digests"])})

class CharacterOperationService:
    def __init__(self, registry):
        self.registry=registry
        self._pending={}

    def _relative(self, path):
        return path.relative_to(self.registry.application_dir).as_posix()
    def _path(self, relative):
        value=self.registry.application_dir / relative
        if value.resolve() != value or not value.is_relative_to(self.registry.application_dir):
            raise CharacterStorageError("Operation path is unsafe")
        return value

    def _legacy_index_files(self, database, identity):
        """Derive selected shard names from selected SQL provider identities.

        Never inspect another character's index metadata to guess ownership.
        The source rows must still exist when this inventory is captured.
        """
        if not database or not database.exists(): return (), ()
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as db:
            providers=db.execute("SELECT DISTINCT provider,model,preprocessing_fingerprint FROM claim_embeddings WHERE character_id=?",(identity,)).fetchall()
        result=[]
        for provider,model,fingerprint in providers:
            for historical in (0,1):
                digest=hashlib.sha256(f"{identity}|{provider}|{model}|{fingerprint}|historical={historical}".encode()).hexdigest()[:20]
                for extension in (".hnsw",".json",".hnsw.tmp",".json.tmp"):
                    path=database.parent/"ann_index"/(digest+extension)
                    if path.exists() or path.is_symlink():
                        if path.resolve()!=path: raise CharacterStorageError("Unsafe selected index path")
                        result.append(path)
        return tuple(sorted(set(result))), tuple(providers)
    def _selected_state(self, character_id, *, allow_local_erasure=False):
        c=self.registry.get(character_id)
        if c is None: raise CharacterRegistryError("Character no longer exists")
        paths=self.registry.runtime_paths(character_id)
        if c.operation:
            self._validate_operation(c, c.operation)
            # A journal does not authorize following a newly substituted path.
            # Include unpublished destination/staging paths while legacy paths
            # are still the selected runtime authority.
            for path in paths["directory"].rglob("*"):
                if path.is_symlink() or path.resolve()!=path:
                    raise CharacterStorageError("Unfinished character operation contains an unsafe link")
            # An unfinished target is not yet a readable authority. Review its
            # journal and path identities, not a possibly partial SQLite schema.
            files={}
            for key in ("character","personality","conversation","memory","summary","memory_v2"):
                path=paths[key]
                if path.is_symlink(): raise CharacterStorageError("Unsafe operation target")
                stat=path.stat() if path.exists() else None
                files[self._relative(path)]=None if stat is None else {"size":stat.st_size,"inode":stat.st_ino,"mtime_ns":stat.st_mtime_ns}
            return {"entry":asdict(c),"files":files,"database":None,"old_database":None}
        unreadable=False
        try:
            inventory=selected_character_inventory(paths["memory_v2"],character_id) if paths["memory_v2"].exists() else None
        except (CharacterCopyError,sqlite3.Error):
            if not allow_local_erasure or c.storage_layout!="local": raise
            # Explicit erasure of an owned local file needs no reinterpretation
            # of damaged/unknown rows. Migration still requires exact schema.
            inventory=None;unreadable=True
        files={self._relative(paths[k]):_fingerprint(paths[k]) for k in ("character","personality","conversation","memory","summary","attention","presentation")}
        if unreadable:
            for suffix in ("","-wal","-shm","-journal"):
                path=Path(str(paths["memory_v2"])+suffix)
                files[self._relative(path)]=_fingerprint(path)
        old=c.migration_source or {}
        old_database, old_files=self.registry.retained_source_paths(character_id,old)
        old_inventory=selected_character_inventory(old_database,character_id) if old_database and old_database.exists() else None
        for path in old_files: files[self._relative(path)]=_fingerprint(path)
        indexes=[];index_providers=[]
        for database in (paths["memory_v2"] if c.storage_layout=="legacy_shared" else None,old_database):
            selected_indexes,providers=self._legacy_index_files(database,character_id)
            indexes.extend(selected_indexes);index_providers.extend(providers)
        for path in indexes: files[self._relative(path)]=_fingerprint(path)
        # The confirmation inventories all selected files. No unexpected file
        # is silently removed by a later recursive deletion.
        for path in sorted(paths["directory"].rglob("*")):
            if path.is_symlink(): raise CharacterStorageError("Character folder contains an unsafe link")
            if path.is_file() and path.name != ".continuity.lock" and path != paths["memory_v2"] and not path.name.startswith("memory_v2.sqlite3-"):
                files[self._relative(path)]=_fingerprint(path)
        return {"entry":asdict(c),"files":files,
                "database":_inventory_dict(inventory) if inventory else None,
                "old_database":_inventory_dict(old_inventory) if old_inventory else None,
                "database_unreadable":unreadable,
                "index_providers":[list(v) for v in sorted(set(index_providers))],
                "index_files":[self._relative(p) for p in sorted(set(indexes))]}

    def preview(self, character_id, action):
        if action not in _ACTIONS: raise CharacterRegistryError("Unknown character operation")
        with self.registry.locked():
            c=self.registry.get(character_id)
            if c is None: raise CharacterRegistryError("Character no longer exists")
            if c.operation and action not in {"resume"}:
                raise CharacterStorageError("Resume the pending operation before starting another one")
            if action == "resume" and not c.operation:
                raise CharacterStorageError("No pending operation to resume")
            if c.storage_status == "creating" and action != "delete":
                raise CharacterStorageError("Incomplete creation can be deleted; it has no completed timeline")
            paths=self.registry.runtime_paths(character_id)
            if action in {"reset","migrate"} and (not paths["character"].is_file() or not paths["personality"].is_file()):
                raise CharacterStorageError("Authored profile files are missing. Reset/migration cannot invent them; restore those files or explicitly delete the character.")
            if action == "migrate" and c.storage_layout == "legacy_shared":
                self.registry._assert_legacy_files_consistent(c,paths)
            state=self._selected_state(character_id,allow_local_erasure=action in {"reset","delete"})
            token=str(uuid.uuid4())
            if len(self._pending)>=16: self._pending.pop(next(iter(self._pending)))
            self._pending[token]={"id":character_id,"action":action,"revision":self.registry.revision,
                                  "state":state,"expires":time.monotonic()+300}
            descriptions={
                "migrate":"Create and verify this character's local database. Original shared rows and any legacy root JSON remain until selected-copy cleanup is confirmed.",
                "reset":"Erase this character's learned conversation, V1 compatibility memories, V2 state, recovery and attention, including its retained application migration copies. Preserve identity, personality and presentation choices.",
                "delete":"Delete this character's identity, profile, continuity and owned preferences, including its retained application migration copies. Shared avatar/background assets and other characters are preserved.",
                "cleanup":"Erase only this character's retained migration-source rows and legacy JSON copies after checking the local destination. Other characters are preserved.",
                "resume":"Resume the recorded operation for this exact character. Ordinary dialogue stays blocked until it completes."}
            return {"token":token,"character_id":character_id,"display_name":c.display_name,"action":action,
                    "revision":self.registry.revision,"scope_text":descriptions[action]+(" The selected local database is unreadable; its owned file will be erased without reconstructing its contents." if state.get("database_unreadable") else "")+" Independent backups/exports are not erased; this is not forensic erasure.",
                    "files":[name for name,fp in state["files"].items() if fp is not None]+[self._relative(self.registry.runtime_paths(character_id)["memory_v2"])],
                    "old_copy_retained":bool(c.migration_source),"original_rows":sum(v for _,v in (state["database"] or {}).get("counts",[]))}

    def _intent(self, token, character_id, revision):
        intent=self._pending.get(str(token))
        if (intent is None or intent["id"]!=character_id or intent["revision"]!=revision
                or time.monotonic()>intent["expires"]):
            raise CharacterStorageError("Confirmation expired or does not match its character. Review the operation again.")
        return intent

    def validate_confirmation(self, *, token, character_id, revision):
        """Non-consuming preflight before the host retires an active service."""
        intent=self._intent(token,character_id,revision)
        with self.registry.locked():
            if self.registry.revision != revision or self._selected_state(character_id,allow_local_erasure=intent["action"] in {"reset","delete"}) != intent["state"]:
                raise CharacterStorageError("Character changed after review. Review the operation again.")
        return intent["action"]

    def execute(self, *, token, character_id, revision):
        intent=self._intent(token,character_id,revision)
        self._pending.pop(str(token))
        with self.registry.locked():
            if self.registry.revision != revision or self._selected_state(character_id,allow_local_erasure=intent["action"] in {"reset","delete"}) != intent["state"]:
                raise CharacterStorageError("Character changed after review. Review the operation again.")
            c=self.registry.get(character_id)
            # Nonblocking: an independently running writer must stop first.
            missing_deleted = bool(c.operation and c.operation.get("kind") == "delete" and
                c.operation.get("phase") == "deleting_files" and not self.registry.owned_directory(character_id).exists())
            missing_creation = bool(intent["action"] == "delete" and c.storage_status == "creating"
                and c.creation_request_id and not self.registry.owned_directory(character_id).exists())
            # A durable deleting-files journal plus the registry lock can finish
            # pointer removal after a crash removed the already-retired folder.
            lease = nullcontext() if missing_deleted or missing_creation else maintenance_lease(self.registry,character_id,registry_locked=True)
            with lease:
                action=intent["action"]
                if action=="resume": operation=c.operation
                else:
                    if action=="migrate" and c.storage_layout=="local":
                        self.registry.assert_storage_ready(character_id)
                        return self._result(c,action,"already_local")
                    if action=="cleanup" and not c.migration_source:
                        return self._result(c,action,"no_retained_copy")
                    operation={"id":str(uuid.uuid4()),"kind":action,"original":asdict(c),"review":intent["state"],
                               "new_generation":str(uuid.uuid4()) if action=="reset" else c.timeline_generation,
                               "phase":"prepared"}
                    if action=="migrate" and c.timeline_generation=="legacy": operation["new_generation"]=str(uuid.uuid4())
                    # Names are derived before rows are erased, and the exact
                    # confirmed fingerprints remain available after a crash.
                    operation["index_files"]=intent["state"].get("index_files",[])
                    entry=self.registry._entry(character_id)
                    entry["operation"]=operation;entry["storage_status"]="incomplete";self.registry._save()
                try:
                    self._validate_operation(c,operation)
                    if operation["kind"]=="migrate": self._migrate(character_id,operation)
                    elif operation["kind"]=="cleanup": self._cleanup(character_id,operation)
                    else: self._erase(character_id,operation)
                except Exception as error:
                    # The durable journal remains visible. Never choose another
                    # source or declare a partially completed operation success.
                    raise CharacterStorageError("Character operation is incomplete. No alternate timeline was loaded. Use Resume after resolving the storage problem.") from error
                return self._result(c,operation["kind"],"completed")

    @staticmethod
    def _result(c, action, status):
        return {"character_id":c.character_id,"display_name":c.display_name,"action":action,"status":status,
                "deleted_character_id":c.character_id if action=="delete" else ""}

    def _phase(self, character_id, operation, phase):
        operation["phase"]=phase;self.registry._entry(character_id)["operation"]=operation;self.registry._save()

    def _validate_operation(self, c, op):
        try:
            if op["kind"] not in _ACTIONS - {"resume"} or str(uuid.UUID(op["id"])) != op["id"]:
                raise ValueError("invalid operation")
            original=Character(**op["original"])
            if (original.character_id,original.config_directory,original.legacy_default) != (c.character_id,c.config_directory,c.legacy_default):
                raise ValueError("wrong owner")
            self.registry.runtime_paths_for(original)
            if original.migration_source:
                self.registry.retained_source_paths(c.character_id,original.migration_source)
            if op["review"]["entry"] != op["original"]:
                raise ValueError("wrong review")
            for key in ("database","old_database"):
                if op["review"].get(key) and _inventory_load(op["review"][key]).character_id != c.character_id:
                    raise ValueError("wrong inventory")
            allowed_indexes=set()
            for provider,model,fingerprint in op["review"].get("index_providers",[]):
                if any(not isinstance(v,str) or len(v)>4096 for v in (provider,model,fingerprint)):
                    raise ValueError("invalid selected index identity")
                for historical in (0,1):
                    digest=hashlib.sha256(f"{c.character_id}|{provider}|{model}|{fingerprint}|historical={historical}".encode()).hexdigest()[:20]
                    allowed_indexes.update(digest+ext for ext in (".hnsw",".json",".hnsw.tmp",".json.tmp"))
            for relative in op.get("index_files",[]):
                path=self._path(relative)
                if (path.parent != self.registry.application_dir/"memory_v2"/"ann_index"
                        or path.name not in allowed_indexes
                        or relative not in op["review"].get("index_files",[]) or relative not in op["review"]["files"]):
                    raise ValueError("wrong index ownership")
        except (KeyError, TypeError, ValueError) as error:
            raise CharacterStorageError("Operation journal does not match the selected character") from error

    def _migrate(self, identity, op):
        entry=self.registry._entry(identity);paths=self.registry.runtime_paths_for(Character(**op["original"]))
        directory=paths["directory"]
        stage=directory/(".migration-"+op["id"])
        if stage.is_symlink(): raise CharacterStorageError("Unsafe migration staging path")
        stage.mkdir(mode=0o700,exist_ok=True)
        target=directory/"memory_v2.sqlite3"
        staged=stage/"memory_v2.sqlite3"
        if target.is_symlink() or target.resolve()!=target or (target.exists() and (not target.is_file() or target.stat().st_nlink!=1)):
            raise CharacterStorageError("Unsafe local migration destination")
        expected=op["review"]["database"]
        if not paths["character"].is_file() or not paths["personality"].is_file():
            raise CharacterStorageError("Migration cannot reconstruct missing authored profile files")
        if expected and dict(expected["counts"]).get("events",0) and not paths["conversation"].is_file():
            raise CharacterStorageError("Migration cannot replace a missing canonical archive. Review recovery or an explicit reset.")
        if not target.exists():
            for suffix in ("", "-wal", "-shm"): Path(str(staged)+suffix).unlink(missing_ok=True)
            if expected and expected["character_exists"]:
                actual=selected_character_inventory(paths["memory_v2"],identity)
                if _inventory_dict(actual)!=expected: raise CharacterStorageError("Migration source changed")
                copy_character_state(paths["memory_v2"],staged,identity)
            else:
                from memory_v2_store import MemoryV2Repository,MemoryV2Store
                store=MemoryV2Store(str(staged))
                try: MemoryV2Repository(store).ensure_character(identity,entry["display_name"])
                finally: store.close()
            with closing(sqlite3.connect(staged)) as db:
                db.executemany("INSERT OR REPLACE INTO database_meta(key,value) VALUES (?,?)",
                    (("storage_character_id",identity),("timeline_generation",op["new_generation"])))
                db.commit()
            # Close/checkpoint all staged connections before publishing a file.
            with closing(sqlite3.connect(staged)) as db:
                busy,_,_=db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if busy: raise CharacterStorageError("Migration destination is still busy")
            with staged.open("rb") as stream: os.fsync(stream.fileno())
            os.replace(staged,target)
            descriptor=os.open(directory,os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        else:
            with closing(sqlite3.connect(target.as_uri()+"?mode=ro",uri=True)) as db:
                meta=dict(db.execute("SELECT key,value FROM database_meta WHERE key IN ('storage_character_id','timeline_generation')"))
            if meta!={"storage_character_id":identity,"timeline_generation":op["new_generation"]}:
                raise CharacterStorageError("An unrecognized local destination already exists")
            if expected and expected["character_exists"]:
                if _inventory_dict(selected_character_inventory(target,identity))!=expected:
                    raise CharacterStorageError("Published destination does not match verified migration")
        old_files=[]
        for key in ("conversation","memory","summary"):
            source=paths[key]; destination=directory/source.name
            if source==destination: continue
            relative=self._relative(source); fingerprint=op["review"]["files"].get(relative)
            if _fingerprint(source)!=fingerprint: raise CharacterStorageError("Canonical migration source changed")
            if fingerprint is not None:
                if destination.exists() and _fingerprint(destination)!=fingerprint:
                    raise CharacterStorageError("Unrecognized canonical destination")
                if not destination.exists():
                    temporary=stage/source.name; shutil.copyfile(source,temporary)
                    if _fingerprint(temporary)!=fingerprint: raise CharacterStorageError("Canonical copy failed verification")
                    with temporary.open("rb") as stream: os.fsync(stream.fileno())
                    os.replace(temporary,destination)
                old_files.append(relative)
            elif not destination.exists():
                write_json_atomic(destination, [] if key in ("conversation","memory") else {"summary":"","summarized_messages":0})
        shutil.rmtree(stage)
        entry.update(storage_layout="local",storage_status="ready",timeline_generation=op["new_generation"],
                     source_namespace=self.registry.canonical_namespace(identity),
                     migration_source={"database":self._relative(paths["memory_v2"]),"files":old_files,
                         "inventory":expected,"file_fingerprints":{v:op["review"]["files"][v] for v in old_files}},operation=None)
        self.registry._save()

    def _clean_old(self, identity, original, review, *, strict_copy=False):
        old=original.get("migration_source") or {}
        database, old_files=self.registry.retained_source_paths(identity,old)
        expected=old.get("inventory") if strict_copy else review.get("old_database")
        if database and database.exists():
            actual=selected_character_inventory(database,identity)
            if strict_copy and expected is None and actual.character_exists:
                raise CharacterStorageError("Retained source has unexported character data")
            cleanup_character_state(database,identity,expected_inventory=_inventory_load(expected) if expected else actual)
        for path in old_files:
            relative=self._relative(path)
            expected_file=(old.get("file_fingerprints",{}) if strict_copy else review["files"]).get(relative)
            if path.exists() and _fingerprint(path)!=expected_file:
                raise CharacterStorageError("Retained legacy copy changed after confirmation")
            path.unlink(missing_ok=True)

    def _cleanup(self, identity, op):
        # Destination readiness while the journal is active: compare its exact
        # selected rows with the reviewed local version before source deletion.
        paths=self.registry.runtime_paths(identity)
        actual=selected_character_inventory(paths["memory_v2"],identity)
        if _inventory_dict(actual)!=op["review"]["database"]: raise CharacterStorageError("Local destination changed")
        self._clean_old(identity,op["original"],op["review"],strict_copy=True)
        self._erase_indexes(op)
        entry=self.registry._entry(identity);entry.update(migration_source=None,operation=None,storage_status="ready")
        self.registry._save()

    def _erase_indexes(self, op):
        for relative in op.get("index_files",[]):
            path=self._path(relative)
            if path.exists() and _fingerprint(path)!=op["review"]["files"][relative]:
                raise CharacterStorageError("Selected derived index changed after confirmation")
            path.unlink(missing_ok=True)

    def _erase(self, identity, op):
        original=op["original"]; paths=self.registry.runtime_paths_for(Character(**original));directory=paths["directory"]
        if op["kind"] == "reset" and (not paths["character"].is_file() or not paths["personality"].is_file()):
            raise CharacterStorageError("Reset preserves the authored profile. Its files must be present before erasing continuity.")
        self._clean_old(identity,original,op["review"])
        if original["storage_layout"]=="legacy_shared" and paths["memory_v2"].exists():
            expected=op["review"]["database"]
            actual=selected_character_inventory(paths["memory_v2"],identity)
            cleanup_character_state(paths["memory_v2"],identity,expected_inventory=_inventory_load(expected) if expected else actual)
        self._erase_indexes(op)
        # Inventory every selected owned path before deleting any of the tree.
        # Symlinks are rejected rather than followed; shared assets are never
        # reachable through this directory's references.
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_symlink(): raise CharacterStorageError("Remove unsafe links from the character folder before resuming")
        for key in ("conversation","memory","summary","attention"):
            path=paths[key]
            if not path.is_relative_to(directory): path.unlink(missing_ok=True)
        if op["kind"]=="delete":
            self._phase(identity,op,"deleting_files")
            if directory.exists(): shutil.rmtree(directory)
            self.registry._data["characters"]=[v for v in self.registry._data["characters"] if v["character_id"]!=identity]
            if self.registry._data.get("active_character_id")==identity:
                remaining=self.registry._data["characters"]
                self.registry._data["active_character_id"]=remaining[0]["character_id"] if remaining else None
            self.registry._save();return
        self._phase(identity,op,"resetting_files")
        preserve={"character.json","personality.md","presentation.json",".continuity.lock"}
        for path in directory.iterdir():
            if path.name in preserve: continue
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
        entry=self.registry._entry(identity)
        entry.update(storage_layout="local",storage_status="incomplete",timeline_generation=op["new_generation"],
                     source_namespace=(Path(entry["config_directory"])/"conversation.json").as_posix(),migration_source=None)
        self.registry._save()
        # No old compatibility import/recovery receipt can seed the new epoch.
        self.registry.initialize_empty_continuity(identity)
        entry.update(operation=None,storage_status="ready");self.registry._save()
