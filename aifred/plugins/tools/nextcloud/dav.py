"""WebDAV access to Nextcloud: discovery, collections and their rights,
calendar and address book queries, conditional writes, change tracking, files.

Synchronous httpx; the tool layer runs it in a worker thread. Every write is
conditional — If-Match with the object's ETag, If-None-Match for a new one —
so a change made elsewhere in the meantime makes the write fail loudly
instead of being overwritten.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import escape

import httpx
from lxml import etree

NS = {
    "d": "DAV:",
    "cal": "urn:ietf:params:xml:ns:caldav",
    "card": "urn:ietf:params:xml:ns:carddav",
}
CALENDAR, ADDRESSBOOK = "calendar", "addressbook"
_XML = {"Content-Type": "application/xml; charset=utf-8"}
_FILE_PROPS = "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:getcontenttype/><d:getetag/>"


class DavError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Nextcloud answered {status}: {message}")
        self.status = status


class DavConflict(DavError):
    """The object changed on the server since it was read (HTTP 412)."""


@dataclass(frozen=True)
class DavCollection:
    href: str
    kind: str
    name: str
    components: frozenset[str]
    # Granted by the server for the logged-in user (a calendar shared
    # read-only lacks it); the plugin's own setting comes on top.
    writable: bool
    sync_token: str | None


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


@dataclass(frozen=True)
class DavContent:
    data: bytes
    mime: str
    etag: str


@dataclass(frozen=True)
class DavChanges:
    sync_token: str
    changed: list[str]
    removed: list[str]


def _utc(value: datetime) -> str:
    """Naive values are local wall-clock time."""
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _text(element: "etree._Element | None") -> str | None:
    return None if element is None or element.text is None else element.text


class DavClient:
    def __init__(self, base_url: str, user: str, password: str, timeout: float) -> None:
        parts = urlsplit(base_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._root = f"{parts.path.rstrip('/')}/remote.php/dav/"
        self._user = user
        self._files_root = f"{self._root}files/{quote(user, safe='')}/"
        self._auth = (user, password)
        self._timeout = timeout
        self._client = httpx.Client(auth=self._auth, timeout=timeout, headers={"User-Agent": "AIfred"})

    def close(self) -> None:
        self._client.close()

    # ── transport ─────────────────────────────────────────────────

    def _request(self, method: str, href: str, *, expect: tuple[int, ...], body: str | bytes | None = None,
                 headers: dict[str, str] | None = None) -> httpx.Response:
        response = self._client.request(method, self._origin + href, content=body, headers=headers)
        if response.status_code == 412:
            raise DavConflict(412, f"{href} was changed elsewhere; read it again")
        if response.status_code not in expect:
            raise DavError(response.status_code, f"{method} {href}")
        return response

    def _multistatus(self, method: str, href: str, depth: str, body: str) -> list[tuple[str, "etree._Element"]]:
        """(href, prop) per response, taken from the propstat with status 200."""
        response = self._request(method, href, expect=(207,), body=body, headers={**_XML, "Depth": depth})
        root = etree.fromstring(response.content)
        out = []
        for resp in root.findall("d:response", NS):
            resp_href = resp.findtext("d:href", namespaces=NS)
            for propstat in resp.findall("d:propstat", NS):
                if " 200 " in (propstat.findtext("d:status", namespaces=NS) or ""):
                    prop = propstat.find("d:prop", NS)
                    if resp_href and prop is not None:
                        out.append((resp_href, prop))
        return out

    # ── discovery ─────────────────────────────────────────────────

    def homes(self) -> tuple[str, str]:
        """Calendar home and address book home of the logged-in user."""
        found = self._multistatus("PROPFIND", self._root, "0",
                                  '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>')
        principal = _text(found[0][1].find("d:current-user-principal/d:href", NS)) if found else None
        if principal is None:
            raise DavError(207, "no current-user-principal")
        found = self._multistatus(
            "PROPFIND", principal, "0",
            '<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" '
            'xmlns:card="urn:ietf:params:xml:ns:carddav"><d:prop><cal:calendar-home-set/>'
            '<card:addressbook-home-set/></d:prop></d:propfind>')
        prop = found[0][1]
        calendar_home = _text(prop.find("cal:calendar-home-set/d:href", NS))
        addressbook_home = _text(prop.find("card:addressbook-home-set/d:href", NS))
        if calendar_home is None or addressbook_home is None:
            raise DavError(207, "the principal names no calendar or address book home")
        return calendar_home, addressbook_home

    def collections(self) -> list[DavCollection]:
        body = ('<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:prop>'
                '<d:displayname/><d:resourcetype/><cal:supported-calendar-component-set/>'
                '<d:current-user-privilege-set/><d:sync-token/></d:prop></d:propfind>')
        out = []
        for home in self.homes():
            for href, prop in self._multistatus("PROPFIND", home, "1", body):
                types = prop.find("d:resourcetype", NS)
                if types is None:
                    continue
                if types.find("cal:calendar", NS) is not None:
                    kind = CALENDAR
                elif types.find("card:addressbook", NS) is not None:
                    kind = ADDRESSBOOK
                else:
                    continue
                privileges = prop.find("d:current-user-privilege-set", NS)
                writable = privileges is not None and (
                    privileges.find("d:privilege/d:write", NS) is not None
                    or privileges.find("d:privilege/d:write-content", NS) is not None)
                components = frozenset(c.get("name", "") for c in prop.findall("cal:supported-calendar-component-set/cal:comp", NS))
                out.append(DavCollection(
                    href=href, kind=kind, name=_text(prop.find("d:displayname", NS)) or href.rstrip("/").rsplit("/", 1)[-1],
                    components=components, writable=writable, sync_token=_text(prop.find("d:sync-token", NS))))
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
        out = []
        for obj_href, prop in self._multistatus(method, href, "1", body):
            data = _text(prop.find(data_path, NS))
            etag = _text(prop.find("d:getetag", NS))
            if data is not None and etag is not None:
                out.append(DavObject(obj_href, etag, data))
        return out

    def get(self, href: str) -> DavObject:
        response = self._request("GET", href, expect=(200,))
        return DavObject(href, response.headers["ETag"], response.text)

    def changes(self, href: str, sync_token: str) -> DavChanges:
        """What changed in a collection since ``sync_token`` (RFC 6578)."""
        body = ('<d:sync-collection xmlns:d="DAV:"><d:sync-token>' + sync_token +
                '</d:sync-token><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>')
        response = self._request("REPORT", href, expect=(207,), body=body, headers={**_XML, "Depth": "1"})
        root = etree.fromstring(response.content)
        changed: list[str] = []
        removed: list[str] = []
        for resp in root.findall("d:response", NS):
            obj = resp.findtext("d:href", namespaces=NS) or ""
            status = resp.findtext("d:status", namespaces=NS) or ""
            (removed if " 404 " in status else changed).append(obj)
        token = root.findtext("d:sync-token", namespaces=NS)
        if token is None:
            raise DavError(207, "sync-collection answered without a sync-token")
        return DavChanges(token, changed, removed)

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

    def _file(self, href: str, prop: "etree._Element") -> DavFile:
        types = prop.find("d:resourcetype", NS)
        size = _text(prop.find("d:getcontentlength", NS))
        modified = _text(prop.find("d:getlastmodified", NS))
        return DavFile(
            path=unquote(href).removeprefix(unquote(self._files_root)).strip("/"),
            is_folder=types is not None and types.find("d:collection", NS) is not None,
            size=int(size) if size is not None else None,
            modified=parsedate_to_datetime(modified).isoformat() if modified else "",
            mime=_text(prop.find("d:getcontenttype", NS)) or "",
            etag=_text(prop.find("d:getetag", NS)) or "",
        )

    def list_files(self, path: str) -> list[DavFile]:
        """What lies directly in the folder ``path`` ("" = the user's top level)."""
        body = f'<d:propfind xmlns:d="DAV:"><d:prop>{_FILE_PROPS}</d:prop></d:propfind>'
        found = [self._file(href, prop) for href, prop in self._multistatus("PROPFIND", self._file_href(path), "1", body)]
        return [entry for entry in found if entry.path != path.strip("/")]

    def search_files(self, folder: str, term: str, limit: int) -> list[DavFile]:
        """Files and folders below ``folder`` with ``term`` in their name, newest first."""
        scope = f"/files/{self._user}/{folder}".rstrip("/")
        body = ('<d:searchrequest xmlns:d="DAV:"><d:basicsearch>'
                f'<d:select><d:prop>{_FILE_PROPS}</d:prop></d:select>'
                f'<d:from><d:scope><d:href>{escape(scope)}</d:href><d:depth>infinity</d:depth></d:scope></d:from>'
                f'<d:where><d:like><d:prop><d:displayname/></d:prop><d:literal>%{escape(term)}%</d:literal></d:like></d:where>'
                '<d:orderby><d:order><d:prop><d:getlastmodified/></d:prop><d:descending/></d:order></d:orderby>'
                f'<d:limit><d:nresults>{limit}</d:nresults></d:limit>'
                '</d:basicsearch></d:searchrequest>')
        return [self._file(href, prop) for href, prop in self._multistatus("SEARCH", self._root, "0", body)]

    def read_file(self, path: str) -> DavContent:
        response = self._request("GET", self._file_href(path), expect=(200,))
        return DavContent(response.content, response.headers.get("Content-Type", ""), response.headers.get("ETag", ""))

    def write_file(self, path: str, data: bytes, content_type: str, etag: str | None) -> str:
        """Create ``path``, or with ``etag`` overwrite it if it is unchanged. Returns the new ETag."""
        condition = {"If-Match": etag} if etag else {"If-None-Match": "*"}
        response = self._request("PUT", self._file_href(path), expect=(201, 204), body=data,
                                 headers={"Content-Type": content_type, **condition})
        return str(response.headers.get("ETag", ""))

    def make_folder(self, path: str) -> None:
        """Create the folder; one that exists already is fine (405)."""
        self._request("MKCOL", self._file_href(path), expect=(201, 405))

    def move_file(self, path: str, target: str) -> None:
        """Move or rename; refused when ``target`` exists (412, :class:`DavConflict`)."""
        self._request("MOVE", self._file_href(path), expect=(201,),
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

