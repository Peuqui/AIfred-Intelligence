"""Nextcloud calendars, tasks and contacts for the tool layer.

An object's ID is ``<collection>/<file>`` (the collection's URI segment and
the object's file name). Writing needs both: the module set to write in the
plugin settings, and the write right that Nextcloud grants the plugin's user
on that collection. Every change is conditional on the object's ETag.
"""

import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from ....lib.credential_broker import broker
from ....lib.plugin_modules import ModuleAccess
from . import ical, vcard
from .dav import ADDRESSBOOK, CALENDAR, DavClient, DavCollection, DavConflict, DavObject
from .errors import NextcloudDisabled, NextcloudRefused
from .files import AgentFiles
from .notes import Notes
from .settings import (
    CREDENTIAL_SERVICE,
    REQUEST_TIMEOUT_SEC,
    NextcloudSettings,
    load_nextcloud_settings,
)

LIMIT_MAX = 500
# Which module and DAV collection type serve an entity, and its component.
_EVENT = ("calendar", CALENDAR, "VEVENT")
_TASK = ("tasks", CALENDAR, "VTODO")
_CONTACT = ("contacts", ADDRESSBOOK, "")


def _uri(collection: DavCollection) -> str:
    return collection.href.rstrip("/").rsplit("/", 1)[-1]


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), LIMIT_MAX))


def _as_kind(value: date | datetime, all_day: bool) -> date | datetime:
    """All-day values are dates; timed values are naive local datetimes."""
    if all_day:
        return value.date() if isinstance(value, datetime) else value
    if not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time())
    return ical.local(value)


def _matches(query: str | None, *texts: Any) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(needle in str(t).casefold() for t in texts if t)


class NextcloudStore:
    def __init__(self, settings: NextcloudSettings, password: str) -> None:
        self._settings = settings
        self._client = DavClient(settings.url, settings.user, password, REQUEST_TIMEOUT_SEC)

    @property
    def settings(self) -> NextcloudSettings:
        return self._settings

    @property
    def modules(self) -> ModuleAccess:
        return self._settings.modules

    @property
    def client(self) -> DavClient:
        return self._client

    @property
    def agent_files(self) -> AgentFiles:
        return AgentFiles(self._client, self._settings.agent_folder)

    @property
    def _notes(self) -> Notes:
        return Notes(AgentFiles(self._client, self._settings.notes_folder))

    def close(self) -> None:
        self._client.close()

    # ── collections ───────────────────────────────────────────────

    def _allow(self, module: str, write: bool) -> None:
        if not self.modules.can_read(module):
            raise NextcloudDisabled(f"the Nextcloud module {module!r} is switched off in the plugin settings")
        if write and not self.modules.can_write(module):
            raise NextcloudDisabled(f"the Nextcloud module {module!r} is read-only in the plugin settings")

    def _collections(self, kind: tuple[str, str, str]) -> list[DavCollection]:
        _, dav_kind, component = kind
        return [c for c in self._client.collections()
                if c.kind == dav_kind and (not component or component in c.components)]

    def _writable(self, kind: tuple[str, str, str], collection: DavCollection) -> bool:
        return self.modules.can_write(kind[0]) and collection.writable

    def collections(self, kind_name: str) -> list[dict[str, Any]]:
        """calendars (events), task lists or address books, with whether AIfred may write there."""
        kind = {"calendars": _EVENT, "tasklists": _TASK, "addressbooks": _CONTACT}[kind_name]
        self._allow(kind[0], write=False)
        return [{"id": _uri(c), "name": c.name, "writable": self._writable(kind, c)} for c in self._collections(kind)]

    def _collection(self, kind: tuple[str, str, str], uri: str) -> DavCollection:
        for c in self._collections(kind):
            if _uri(c) == uri:
                return c
        raise NextcloudRefused(f"no collection {uri!r}")

    def _target(self, kind: tuple[str, str, str], name: str | None) -> DavCollection:
        """The collection to create in: by name or ID, or the only writable one."""
        writable = [c for c in self._collections(kind) if self._writable(kind, c)]
        if not writable:
            raise NextcloudRefused("Nextcloud grants AIfred no writable collection for this")
        if name is None:
            if len(writable) == 1:
                return writable[0]
            raise NextcloudRefused("several collections are writable, name one: " + ", ".join(c.name for c in writable))
        matches = [c for c in writable if name.casefold() in (c.name.casefold(), _uri(c).casefold())]
        if len(matches) != 1:
            raise NextcloudRefused(f"no single writable collection named {name!r}; writable: "
                                   + ", ".join(c.name for c in writable))
        return matches[0]

    def _resolve(self, kind: tuple[str, str, str], object_id: str, write: bool) -> tuple[DavCollection, DavObject]:
        uri, _, file_name = object_id.partition("/")
        if not file_name or "/" in file_name:
            raise NextcloudRefused(f"{object_id!r} is no ID from a search result")
        collection = self._collection(kind, uri)
        if write and not self._writable(kind, collection):
            raise NextcloudDisabled(f"AIfred may not write in {collection.name!r}")
        return collection, self._client.get(collection.href + file_name)

    def _object_id(self, collection: DavCollection, obj: DavObject) -> str:
        return f"{_uri(collection)}/{obj.href.rsplit('/', 1)[-1]}"

    def _replace(self, obj: DavObject, data: str, content_type: str) -> None:
        try:
            self._client.replace(obj, data, content_type)
        except DavConflict as exc:
            raise NextcloudRefused("the entry was changed elsewhere in the meantime; read it again") from exc

    # ── events ────────────────────────────────────────────────────

    def events(self, date_from: date, date_to: date, query: str | None, limit: int) -> list[dict[str, Any]]:
        """Events overlapping [date_from, date_to]; series come expanded."""
        self._allow("calendar", write=False)
        start = datetime.combine(date_from, datetime.min.time())
        end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        out = []
        for collection in self._collections(_EVENT):
            for obj in self._client.calendar_objects(collection.href, "VEVENT", start, end):
                for event in ical.parse_events(obj.data):
                    row = event.as_dict()
                    if _matches(query, row["title"], row["location"], row["description"]):
                        out.append({"id": self._object_id(collection, obj), "calendar": collection.name, **row})
        out.sort(key=lambda r: (str(r["start"]), r["title"]))
        return out[:_clamp(limit)]

    def get_event(self, object_id: str) -> dict[str, Any]:
        self._allow("calendar", write=False)
        collection, obj = self._resolve(_EVENT, object_id, write=False)
        events = ical.parse_events(obj.data)
        master = next((e for e in events if not e.recurring), events[0])
        return {"id": object_id, "calendar": collection.name, **master.as_dict()}

    def create_event(self, *, calendar: str | None, title: str, start: date | datetime, end: date | datetime,
                     all_day: bool, location: str | None, description: str | None) -> str:
        self._allow("calendar", write=True)
        if end < start:
            raise NextcloudRefused("the end lies before the start")
        collection = self._target(_EVENT, calendar)
        uid = str(uuid.uuid4())
        href = f"{collection.href}{uid}.ics"
        self._client.create(href, ical.build_event(uid, title, start, end, all_day, location, description), ical.CONTENT_TYPE)
        return f"{_uri(collection)}/{uid}.ics"

    def update_event(self, object_id: str, changes: dict[str, Any]) -> None:
        """A new start without a new end moves the event and keeps its
        duration. ``end`` is exclusive (all-day: the day after the last day)."""
        self._allow("calendar", write=True)
        _, obj = self._resolve(_EVENT, object_id, write=True)
        try:
            current = ical.event_times(obj.data)
        except ical.RecurringRefused as exc:
            raise NextcloudRefused(str(exc)) from exc
        all_day = changes.get("all_day", current.all_day)
        if all_day != current.all_day and ("start" not in changes or "end" not in changes):
            raise NextcloudRefused("switching between all-day and timed needs both start and end")
        current_start, current_end = _as_kind(current.start, all_day), _as_kind(current.end, all_day)
        start = _as_kind(changes["start"], all_day) if "start" in changes else None
        end = _as_kind(changes["end"], all_day) if "end" in changes else None
        if start is not None and end is None:
            end = start + (current_end - current_start)
        if (start or current_start) > (end or current_end):
            raise NextcloudRefused("the end lies before the start")
        clear = frozenset(k for k in ("location", "description") if k in changes and changes[k] is None)
        data = ical.update_event(obj.data, title=changes.get("title"), start=start, end=end,
                                 all_day=changes.get("all_day"), location=changes.get("location"),
                                 description=changes.get("description"), clear=clear)
        self._replace(obj, data, ical.CONTENT_TYPE)

    def delete_event(self, object_id: str) -> None:
        self._allow("calendar", write=True)
        _, obj = self._resolve(_EVENT, object_id, write=True)
        if any(e.recurring for e in ical.parse_events(obj.data)):
            raise NextcloudRefused("recurring series are deleted in the calendar app")
        self._client.delete(obj)

    # ── tasks ─────────────────────────────────────────────────────

    def tasks(self, query: str | None, done: bool | None, limit: int) -> list[dict[str, Any]]:
        self._allow("tasks", write=False)
        out = []
        for collection in self._collections(_TASK):
            for obj in self._client.calendar_objects(collection.href, "VTODO", None, None):
                for task in ical.parse_tasks(obj.data):
                    row = task.as_dict()
                    if _matches(query, row["title"], row["description"]) and (done is None or row["done"] == done):
                        out.append({"id": self._object_id(collection, obj), "list": collection.name, **row})
        out.sort(key=lambda r: (r["done"], r["due"] is None, str(r["due"]), r["title"]))
        return out[:_clamp(limit)]

    def get_task(self, object_id: str) -> dict[str, Any]:
        self._allow("tasks", write=False)
        collection, obj = self._resolve(_TASK, object_id, write=False)
        return {"id": object_id, "list": collection.name, **ical.parse_tasks(obj.data)[0].as_dict()}

    def create_task(self, *, task_list: str | None, title: str, due: date | datetime | None,
                    priority: str | None, description: str | None) -> str:
        self._allow("tasks", write=True)
        collection = self._target(_TASK, task_list)
        uid = str(uuid.uuid4())
        self._client.create(f"{collection.href}{uid}.ics", ical.build_task(uid, title, due, priority, description),
                            ical.CONTENT_TYPE)
        return f"{_uri(collection)}/{uid}.ics"

    def update_task(self, object_id: str, changes: dict[str, Any]) -> None:
        self._allow("tasks", write=True)
        _, obj = self._resolve(_TASK, object_id, write=True)
        clear = frozenset(k for k in ("due", "description") if k in changes and changes[k] is None)
        try:
            data = ical.update_task(obj.data, title=changes.get("title"), due=changes.get("due"),
                                    done=changes.get("done"), priority=changes.get("priority"),
                                    description=changes.get("description"), clear=clear)
        except ical.RecurringRefused as exc:
            raise NextcloudRefused(str(exc)) from exc
        self._replace(obj, data, ical.CONTENT_TYPE)

    def delete_task(self, object_id: str) -> None:
        self._allow("tasks", write=True)
        _, obj = self._resolve(_TASK, object_id, write=True)
        self._client.delete(obj)

    # ── notes ─────────────────────────────────────────────────────

    def notes(self, query: str | None, limit: int) -> list[dict[str, Any]]:
        self._allow("notes", write=False)
        return self._notes.search(query, _clamp(limit))

    def get_note(self, note_id: str) -> dict[str, Any]:
        self._allow("notes", write=False)
        return self._notes.get(note_id)

    def create_note(self, *, title: str, content: str, category: str | None) -> str:
        self._allow("notes", write=True)
        return self._notes.create(title, content, category)

    def update_note(self, note_id: str, changes: dict[str, Any]) -> str:
        self._allow("notes", write=True)
        return self._notes.update(note_id, changes)

    def delete_note(self, note_id: str) -> None:
        self._allow("notes", write=True)
        self._notes.delete(note_id)

    # ── contacts ──────────────────────────────────────────────────

    def contacts(self, query: str | None, limit: int) -> list[dict[str, Any]]:
        self._allow("contacts", write=False)
        out = []
        for collection in self._collections(_CONTACT):
            for obj in self._client.addressbook_objects(collection.href):
                fields = vcard.parse_contact(obj.data).fields
                searchable = (fields["name"], fields["organization"], *fields["phones"].values(), *fields["emails"].values())
                if _matches(query, *searchable):
                    out.append({"id": self._object_id(collection, obj), "addressbook": collection.name, **fields})
        out.sort(key=lambda r: str(r["name"] or "").casefold())
        return out[:_clamp(limit)]

    def get_contact(self, object_id: str) -> dict[str, Any]:
        self._allow("contacts", write=False)
        collection, obj = self._resolve(_CONTACT, object_id, write=False)
        return {"id": object_id, "addressbook": collection.name, **vcard.parse_contact(obj.data).fields}

    def create_contact(self, *, addressbook: str | None, changes: dict[str, Any]) -> str:
        self._allow("contacts", write=True)
        collection = self._target(_CONTACT, addressbook)
        uid = str(uuid.uuid4())
        try:
            data = vcard.build_contact(uid, changes)
        except ValueError as exc:
            raise NextcloudRefused(str(exc)) from exc
        self._client.create(f"{collection.href}{uid}.vcf", data, vcard.CONTENT_TYPE)
        return f"{_uri(collection)}/{uid}.vcf"

    def update_contact(self, object_id: str, changes: dict[str, Any]) -> None:
        self._allow("contacts", write=True)
        _, obj = self._resolve(_CONTACT, object_id, write=True)
        try:
            data = vcard.update_contact(obj.data, changes)
        except ValueError as exc:
            raise NextcloudRefused(str(exc)) from exc
        self._replace(obj, data, vcard.CONTENT_TYPE)

    def delete_contact(self, object_id: str) -> None:
        self._allow("contacts", write=True)
        _, obj = self._resolve(_CONTACT, object_id, write=True)
        self._client.delete(obj)


_lock = threading.Lock()
_instance: NextcloudStore | None = None


def get_store() -> NextcloudStore | None:
    """The store for the configured server, or None while URL, user or app
    password are missing. Changed settings yield a fresh store."""
    global _instance
    settings = load_nextcloud_settings()
    password = broker.get(CREDENTIAL_SERVICE, "app_password")
    with _lock:
        if not (settings.url and settings.user and password):
            if _instance is not None:
                _instance.close()
                _instance = None
            return None
        if _instance is None or _instance.settings != settings:
            if _instance is not None:
                _instance.close()
            _instance = NextcloudStore(settings, password)
        return _instance


def reset_store() -> None:
    """Drop the store (after the settings or the app password changed)."""
    global _instance
    with _lock:
        if _instance is not None:
            _instance.close()
            _instance = None
