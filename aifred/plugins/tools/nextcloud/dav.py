"""WebDAV access to Nextcloud: discovery, collections and their rights,
calendar and address book queries, conditional writes, files.

Synchronous httpx; the tool layer runs it in a worker thread. Every write is
conditional — If-Match with the object's ETag, If-None-Match for a new one —
so a change made elsewhere in the meantime makes the write fail loudly
instead of being overwritten.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import BinaryIO
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import escape

import httpx
from lxml import etree

from ....lib.lxml_thread import run_in_lxml_thread

NS = {
    "d": "DAV:",
    "cal": "urn:ietf:params:xml:ns:caldav",
    "card": "urn:ietf:params:xml:ns:carddav",
    "nc": "http://nextcloud.com/ns",
}
CALENDAR, ADDRESSBOOK = "calendar", "addressbook"
_XML = {"Content-Type": "application/xml; charset=utf-8"}
_FILE_PROPS = "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:getcontenttype/><d:getetag/>"


class DavError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Nextcloud answered {status}: {message}")
        self.status = status


class DavConflict(DavError):
    """The object changed on the server since it was read, or its target exists (HTTP 412; 405 of a MKCOL on a name in use)."""


@dataclass(frozen=True)
class DavCollection:
    href: str
    kind: str
    name: str
    owner: str
    components: frozenset[str]
    # Granted by the server for the logged-in user (a calendar shared
    # read-only lacks it); the plugin's own setting comes on top.
    writable: bool

    @property
    def plain_name(self) -> str:
        """The name without the owner Nextcloud appends to a shared collection:
        people say "Urlaub", the server calls it "Urlaub (Peuqui)"."""
        return self.name.removesuffix(f" ({self.owner})")


@dataclass(frozen=True)
class DavObject:
    href: str
    etag: str
    data: str


@dataclass(frozen=True)
class DavFile:
    path: str  # below the user's files, without a leading slash
    is_folder: bool
    size: int | None
    modified: str  # ISO timestamp, "" when the server names none
    mime: str
    etag: str

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def _utc(value: datetime) -> str:
    """Naive values are local wall-clock time."""
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _text(element: "etree._Element | None") -> str | None:
    return None if element is None or element.text is None else element.text


def _read_multistatus[Read](content: bytes, read: Callable[[str, "etree._Element"], Read]) -> list[Read]:
    """Parse the answer and hand each (href, prop) to ``read``; runs in the lxml thread, so ``read``
    must return plain data, never an element."""
    root = etree.fromstring(content)
    out: list[Read] = []
    for resp in root.findall("d:response", NS):
        resp_href = resp.findtext("d:href", namespaces=NS)
        for propstat in resp.findall("d:propstat", NS):
            if " 200 " in (propstat.findtext("d:status", namespaces=NS) or ""):
                prop = propstat.find("d:prop", NS)
                if resp_href and prop is not None:
                    out.append(read(resp_href, prop))
    return out


def _read_principal(_href: str, prop: "etree._Element") -> str | None:
    return _text(prop.find("d:current-user-principal/d:href", NS))


def _read_homes(_href: str, prop: "etree._Element") -> tuple[str | None, str | None]:
    return (_text(prop.find("cal:calendar-home-set/d:href", NS)),
            _text(prop.find("card:addressbook-home-set/d:href", NS)))


def _read_collection(href: str, prop: "etree._Element") -> DavCollection | None:
    """None for what is neither a calendar nor an address book."""
    types = prop.find("d:resourcetype", NS)
    if types is None:
        return None
    if types.find("cal:calendar", NS) is not None:
        kind = CALENDAR
    elif types.find("card:addressbook", NS) is not None:
        kind = ADDRESSBOOK
    else:
        return None
    privileges = prop.find("d:current-user-privilege-set", NS)
    writable = privileges is not None and (
        privileges.find("d:privilege/d:write", NS) is not None
        or privileges.find("d:privilege/d:write-content", NS) is not None)
    components = frozenset(c.get("name", "") for c in prop.findall("cal:supported-calendar-component-set/cal:comp", NS))
    return DavCollection(
        href=href, kind=kind, name=_text(prop.find("d:displayname", NS)) or href.rstrip("/").rsplit("/", 1)[-1],
        owner=_text(prop.find("nc:owner-displayname", NS)) or "",
        components=components, writable=writable)


def _read_file(files_root: str, href: str, prop: "etree._Element") -> DavFile:
    types = prop.find("d:resourcetype", NS)
    size = _text(prop.find("d:getcontentlength", NS))
    modified = _text(prop.find("d:getlastmodified", NS))
    return DavFile(
        path=unquote(href).removeprefix(unquote(files_root)).strip("/"),
        is_folder=types is not None and types.find("d:collection", NS) is not None,
        size=int(size) if size is not None else None,
        modified=parsedate_to_datetime(modified).isoformat() if modified else "",
        mime=_text(prop.find("d:getcontenttype", NS)) or "",
        etag=_text(prop.find("d:getetag", NS)) or "",
    )


class DavClient:
    def __init__(self, base_url: str, user: str, password: str, timeout: float) -> None:
        parts = urlsplit(base_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._root = f"{parts.path.rstrip('/')}/remote.php/dav/"
        self._user = user
        self._files_root = f"{self._root}files/{quote(user, safe='')}/"
        self._auth = (user, password)
        self._timeout = timeout
        # No connection is kept for the next request: one that the server (or the way to it) had closed meanwhile failed
        # now and then with "Server disconnected without sending a response" (15.11.2026, about 2 runs in 100 of a
        # test against the live Nextcloud, the request never reached nginx). The files are fetched now and then, the
        # new connection costs milliseconds.
        self._client = httpx.Client(
            auth=self._auth, timeout=timeout, headers={"User-Agent": "AIfred"},
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    def close(self) -> None:
        self._client.close()

    # ── transport ─────────────────────────────────────────────────

    def _request(self, method: str, href: str, *, expect: tuple[int, ...], body: str | bytes | BinaryIO | None = None,
                 headers: dict[str, str] | None = None) -> httpx.Response:
        response = self._client.request(method, self._origin + href, content=body, headers=headers)
        if response.status_code == 412:
            raise DavConflict(412, f"{href} was changed elsewhere; read it again")
        if response.status_code not in expect:
            raise DavError(response.status_code, f"{method} {href}")
        return response

    def _multistatus[Read](self, method: str, href: str, depth: str, body: str,
                           read: Callable[[str, "etree._Element"], Read]) -> list[Read]:
        """``read(href, prop)`` per response, taken from the propstat with status 200. The request
        runs here, parsing and reading in the lxml thread (no element leaves it)."""
        response = self._request(method, href, expect=(207,), body=body, headers={**_XML, "Depth": depth})
        return run_in_lxml_thread(_read_multistatus, response.content, read)

    # ── discovery ─────────────────────────────────────────────────

    def homes(self) -> tuple[str, str]:
        """Calendar home and address book home of the logged-in user."""
        found = self._multistatus("PROPFIND", self._root, "0",
                                  '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>',
                                  _read_principal)
        principal = found[0] if found else None
        if principal is None:
            raise DavError(207, "no current-user-principal")
        homes = self._multistatus(
            "PROPFIND", principal, "0",
            '<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" '
            'xmlns:card="urn:ietf:params:xml:ns:carddav"><d:prop><cal:calendar-home-set/>'
            '<card:addressbook-home-set/></d:prop></d:propfind>',
            _read_homes)
        calendar_home, addressbook_home = homes[0]
        if calendar_home is None or addressbook_home is None:
            raise DavError(207, "the principal names no calendar or address book home")
        return calendar_home, addressbook_home

    def collections(self) -> list[DavCollection]:
        body = ('<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:nc="http://nextcloud.com/ns"><d:prop>'
                '<d:displayname/><nc:owner-displayname/><d:resourcetype/><cal:supported-calendar-component-set/>'
                '<d:current-user-privilege-set/></d:prop></d:propfind>')
        out: list[DavCollection] = []
        for home in self.homes():
            out.extend(c for c in self._multistatus("PROPFIND", home, "1", body, _read_collection) if c is not None)
        return out

    # ── reading ───────────────────────────────────────────────────

    def calendar_objects(self, href: str, component: str, start: datetime | None, end: datetime | None) -> list[DavObject]:
        """Objects of one component type (VEVENT, VTODO). With a time range,
        recurring events come back expanded to their occurrences in it."""
        if start is not None and end is not None:
            window = f'start="{_utc(start)}" end="{_utc(end)}"'
            data = f"<cal:calendar-data><cal:expand {window}/></cal:calendar-data>" if component == "VEVENT" \
                else "<cal:calendar-data/>"
            time_filter = f"<cal:time-range {window}/>"
        else:
            data, time_filter = "<cal:calendar-data/>", ""
        body = ('<cal:calendar-query xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                f'<d:prop><d:getetag/>{data}</d:prop><cal:filter><cal:comp-filter name="VCALENDAR">'
                f'<cal:comp-filter name="{component}">{time_filter}</cal:comp-filter>'
                '</cal:comp-filter></cal:filter></cal:calendar-query>')
        return self._objects("REPORT", href, body, "cal:calendar-data")

    def addressbook_objects(self, href: str) -> list[DavObject]:
        body = ('<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
                '<d:prop><d:getetag/><card:address-data/></d:prop></card:addressbook-query>')
        return self._objects("REPORT", href, body, "card:address-data")

    def _objects(self, method: str, href: str, body: str, data_path: str) -> list[DavObject]:
        def read(obj_href: str, prop: "etree._Element") -> DavObject | None:
            data = _text(prop.find(data_path, NS))
            etag = _text(prop.find("d:getetag", NS))
            return DavObject(obj_href, etag, data) if data is not None and etag is not None else None

        return [obj for obj in self._multistatus(method, href, "1", body, read) if obj is not None]

    def get(self, href: str) -> DavObject:
        response = self._request("GET", href, expect=(200,))
        return DavObject(href, response.headers["ETag"], response.text)

    # ── writing ───────────────────────────────────────────────────

    def create(self, href: str, data: str, content_type: str) -> str:
        """New object at ``href``; fails if one exists already. Returns its ETag."""
        response = self._request("PUT", href, expect=(201,), body=data,
                                 headers={"Content-Type": content_type, "If-None-Match": "*"})
        return response.headers.get("ETag") or self.get(href).etag

    def replace(self, obj: DavObject, data: str, content_type: str) -> str:
        """Overwrite ``obj`` if it is unchanged on the server. Returns the new ETag."""
        response = self._request("PUT", obj.href, expect=(200, 204), body=data,
                                 headers={"Content-Type": content_type, "If-Match": obj.etag})
        return response.headers.get("ETag") or self.get(obj.href).etag

    def delete(self, obj: DavObject) -> None:
        self._request("DELETE", obj.href, expect=(204,), headers={"If-Match": obj.etag})

    # ── files ─────────────────────────────────────────────────────

    def _file_href(self, path: str) -> str:
        return self._files_root + quote(path)

    def _read_file(self, href: str, prop: "etree._Element") -> DavFile:
        return _read_file(self._files_root, href, prop)

    def list_files(self, path: str) -> list[DavFile]:
        """What lies directly in the folder ``path`` ("" = the user's top level)."""
        body = f'<d:propfind xmlns:d="DAV:"><d:prop>{_FILE_PROPS}</d:prop></d:propfind>'
        found = self._multistatus("PROPFIND", self._file_href(path), "1", body, self._read_file)
        return [entry for entry in found if entry.path != path.strip("/")]

    def search_files(self, folder: str, term: str) -> list[DavFile]:
        """Files and folders below ``folder`` with ``term`` in their name, newest first."""
        scope = f"/files/{self._user}/{folder}".rstrip("/")
        body = ('<d:searchrequest xmlns:d="DAV:"><d:basicsearch>'
                f'<d:select><d:prop>{_FILE_PROPS}</d:prop></d:select>'
                f'<d:from><d:scope><d:href>{escape(scope)}</d:href><d:depth>infinity</d:depth></d:scope></d:from>'
                f'<d:where><d:like><d:prop><d:displayname/></d:prop><d:literal>%{escape(term)}%</d:literal></d:like></d:where>'
                '<d:orderby><d:order><d:prop><d:getlastmodified/></d:prop><d:descending/></d:order></d:orderby>'
                '</d:basicsearch></d:searchrequest>')
        return self._multistatus("SEARCH", self._root, "0", body, self._read_file)

    def read_file(self, path: str, receive: Callable[[bytes], object]) -> str:
        """Stream the file into ``receive``, which ends it by raising; nothing stays
        here, so the file may be any size. Returns its ETag."""
        href = self._file_href(path)
        with self._client.stream("GET", self._origin + href) as response:
            if response.status_code != 200:
                raise DavError(response.status_code, f"GET {href}")
            for chunk in response.iter_bytes():
                receive(chunk)
            return str(response.headers.get("ETag", ""))

    def write_file(self, path: str, data: bytes, content_type: str, etag: str | None) -> str:
        """Create ``path``, or with ``etag`` overwrite it if it is unchanged. Returns the new ETag."""
        return self._put(path, data, len(data), content_type, etag)

    def upload_file(self, path: str, source: BinaryIO, size: int, content_type: str, etag: str | None) -> str:
        """Like :meth:`write_file`, the content streamed from an open file of ``size`` bytes: nothing
        is held in memory, so the file may be any size."""
        return self._put(path, source, size, content_type, etag)

    def _put(self, path: str, body: bytes | BinaryIO, size: int, content_type: str, etag: str | None) -> str:
        condition = {"If-Match": etag} if etag else {"If-None-Match": "*"}
        response = self._request("PUT", self._file_href(path), expect=(201, 204), body=body,
                                 headers={"Content-Type": content_type, "Content-Length": str(size), **condition})
        return str(response.headers.get("ETag", ""))

    def make_folder(self, path: str, *, exist_ok: bool = True) -> None:
        """Create the folder; one that exists already is fine (405) unless ``exist_ok`` is off (then DavConflict)."""
        try:
            self._request("MKCOL", self._file_href(path), expect=(201, 405) if exist_ok else (201,))
        except DavError as exc:
            if exc.status == 405:
                raise DavConflict(405, f"{path} exists already") from exc
            raise

    def move_file(self, path: str, target: str) -> None:
        """Move or rename; refused when ``target`` exists (412, :class:`DavConflict`)."""
        self._request("MOVE", self._file_href(path), expect=(201,),
                      headers={"Destination": self._origin + self._file_href(target), "Overwrite": "F"})

    def copy_file(self, path: str, target: str) -> None:
        """Copy a file or a folder with its content; refused when ``target`` exists (412, :class:`DavConflict`)."""
        self._request("COPY", self._file_href(path), expect=(201,),
                      headers={"Destination": self._origin + self._file_href(target), "Overwrite": "F"})

    def delete_file(self, path: str) -> None:
        """Nextcloud keeps it in the user's trash bin."""
        self._request("DELETE", self._file_href(path), expect=(204,))

    async def open_download(self, path: str) -> tuple[httpx.AsyncClient, httpx.Response]:
        """A client and a streaming response of the file, both for the caller to close: errors show
        before the first byte goes out."""
        client = httpx.AsyncClient(auth=self._auth, timeout=self._timeout, headers={"User-Agent": "AIfred"})
        request = client.build_request("GET", self._origin + self._file_href(path))
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            raise
        if response.status_code != 200:
            await response.aclose()
            await client.aclose()
            raise DavError(response.status_code, f"GET {path}")
        return client, response

