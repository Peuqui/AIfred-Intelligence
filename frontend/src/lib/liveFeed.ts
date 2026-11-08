// What the live analysis of a camera sends (GET vision/events/{id}, one JSON object per message) as the live view
// shows it: the image model's descriptions, newest last, and the faces seen, one line per sighting (the server keeps a
// session id while it sees the same person; a new sighting after a pause gets a new one).

export const MAX_DESCRIPTIONS = 10;
export const MAX_FACES = 8;

export interface Description {
  times: string;   // captured → started → finished (HH:MM:SS)
  text: string;
}

export interface FaceSeen {
  key: number;           // stable for the page's list while the sighting goes on
  sighting: string;      // the server's session id ("" = none: every event its own line)
  time: string;
  name: string;          // "" = not known
  similarity: number | null;
  crop: string;          // the face's picture (/_upload/…), "" when none was kept
}

export interface Feed {
  descriptions: Description[];
  faces: FaceSeen[];
}

export const FACE_EVENTS = ["face_known", "face_unsure", "face_unknown"];

export function emptyFeed(): Feed {
  return { descriptions: [], faces: [] };
}

// "2026-11-08T03:41:07.123" → "03:41:07"
export function clock(iso: unknown): string {
  return (String(iso ?? "").split("T")[1] ?? "").split(".")[0] ?? "";
}

let nextKey = 0;

// One message of the stream into the feed; anything of another kind leaves it as it is.
export function received(feed: Feed, data: Record<string, unknown>): Feed {
  const kind = String(data.type ?? "vlm_analysis");
  if (kind === "vlm_analysis") {
    const times = [data.frame_timestamp, data.timestamp, data.inference_end].map(clock).filter(Boolean).join(" → ");
    const text = String(data.description ?? "").replace(/\s+/g, " ").trim();
    return { ...feed, descriptions: [...feed.descriptions, { times, text }].slice(-MAX_DESCRIPTIONS) };
  }
  if (!FACE_EVENTS.includes(kind)) return feed;
  const sighting = String(data.session_id ?? "");
  const known = kind !== "face_unknown" && data.confidence_band !== "unknown";
  const earlier = sighting ? feed.faces.find((face) => face.sighting === sighting) : undefined;
  const face: FaceSeen = {
    key: earlier ? earlier.key : ++nextKey,
    sighting,
    time: clock(data.timestamp),
    name: known ? String(data.name ?? "").trim() : "",
    similarity: typeof data.similarity === "number" ? data.similarity : null,
    crop: String(data.crop_url ?? ""),
  };
  const faces = earlier
    ? feed.faces.map((other) => (other.key === earlier.key ? face : other))
    : [...feed.faces, face].slice(-MAX_FACES);
  return { ...feed, faces };
}
