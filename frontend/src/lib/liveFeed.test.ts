import { expect, test } from "vitest";
import { MAX_DESCRIPTIONS, MAX_FACES, clock, emptyFeed, received } from "./liveFeed";

test("a description keeps its three times and its text on one line, the oldest go", () => {
  let feed = received(emptyFeed(), {
    type: "vlm_analysis", frame_timestamp: "2026-11-08T03:41:05", timestamp: "2026-11-08T03:41:06.5",
    inference_end: "2026-11-08T03:41:09", description: "Ein Mann\n  am Tor.",
  });
  expect(feed.descriptions).toEqual([{ times: "03:41:05 → 03:41:06 → 03:41:09", text: "Ein Mann am Tor." }]);
  for (let index = 0; index < MAX_DESCRIPTIONS + 3; index++) feed = received(feed, { type: "vlm_analysis", description: `${index}` });
  expect(feed.descriptions.length).toBe(MAX_DESCRIPTIONS);
  expect(feed.descriptions.at(-1)?.text).toBe(`${MAX_DESCRIPTIONS + 2}`);
});

test("a face seen on is one line, a new sighting a new one, an unknown face has no name", () => {
  let feed = received(emptyFeed(), { type: "face_known", session_id: "s1", name: "Anna", similarity: 0.71, confidence_band: "sure", timestamp: "2026-11-08T03:41:05", crop_url: "/_upload/a.jpg" });
  feed = received(feed, { type: "face_known", session_id: "s1", name: "Anna", similarity: 0.83, confidence_band: "sure", timestamp: "2026-11-08T03:41:07", crop_url: "/_upload/b.jpg" });
  expect(feed.faces).toHaveLength(1);
  expect(feed.faces[0]).toMatchObject({ name: "Anna", similarity: 0.83, time: "03:41:07", crop: "/_upload/b.jpg" });
  const firstKey = feed.faces[0]?.key;
  feed = received(feed, { type: "face_unknown", session_id: "s2", name: "Anna", confidence_band: "unknown", timestamp: "2026-11-08T03:42:00" });
  expect(feed.faces.map((face) => face.name)).toEqual(["Anna", ""]);
  expect(feed.faces[0]?.key).toBe(firstKey);
  for (let index = 0; index < MAX_FACES + 2; index++) feed = received(feed, { type: "face_unsure", name: "Bert", confidence_band: "unsure" });
  expect(feed.faces).toHaveLength(MAX_FACES);
});

test("other messages leave the feed as it is", () => {
  const feed = emptyFeed();
  expect(received(feed, { type: "person" })).toBe(feed);
  expect(clock(undefined)).toBe("");
});
