// The rules of the chat's scrolling as a table: what the box does for each reason and situation.
import { describe, expect, test } from "vitest";
import { FOLLOW_WITHIN_PX, type Move, type Reason, type Situation, isAtEnd, isFollowing, moveFor } from "./followScroll";

// Every combination of the three facts, for each reason: 3 reasons x 8 situations.
const rows: [Reason, boolean, boolean, boolean, Move][] = [
  // reason, following, hasUnread, hasRemembered -> move
  ["opened", true, true, true, "oldestUnread"],
  ["opened", true, true, false, "oldestUnread"],
  ["opened", true, false, true, "remembered"],
  ["opened", true, false, false, "end"],
  ["opened", false, true, true, "oldestUnread"],
  ["opened", false, true, false, "oldestUnread"],
  ["opened", false, false, true, "remembered"],
  ["opened", false, false, false, "end"],
  ["own", true, true, true, "end"],
  ["own", true, true, false, "end"],
  ["own", true, false, true, "end"],
  ["own", true, false, false, "end"],
  ["own", false, true, true, "end"],
  ["own", false, true, false, "end"],
  ["own", false, false, true, "end"],
  ["own", false, false, false, "end"],
  ["incoming", true, true, true, "oldestUnread"],
  ["incoming", true, true, false, "oldestUnread"],
  ["incoming", true, false, true, "end"],
  ["incoming", true, false, false, "end"],
  ["incoming", false, true, true, "stay"],
  ["incoming", false, true, false, "stay"],
  ["incoming", false, false, true, "stay"],
  ["incoming", false, false, false, "stay"],
];

describe("moveFor", () => {
  test("the table covers every combination once", () => {
    expect(rows).toHaveLength(24);
    expect(new Set(rows.map((row) => row.slice(0, 4).join())).size).toBe(24);
  });

  test.each(rows)("%s, following %s, unread %s, remembered %s -> %s", (reason, following, hasUnread, hasRemembered, move) => {
    const situation: Situation = { following, hasUnread, hasRemembered };
    expect(moveFor(reason, situation)).toBe(move);
  });
});

describe("following", () => {
  test("within the distance from the end the reader follows, beyond it reads", () => {
    expect(isFollowing(0)).toBe(true);
    expect(isFollowing(FOLLOW_WITHIN_PX)).toBe(true);
    expect(isFollowing(FOLLOW_WITHIN_PX + 1)).toBe(false);
  });

  test("isAtEnd measures the distance of the box's end", () => {
    const box = { scrollHeight: 1000, clientHeight: 300, scrollTop: 700 - FOLLOW_WITHIN_PX } as HTMLElement;
    expect(isAtEnd(box)).toBe(true);
    box.scrollTop -= 1;
    expect(isAtEnd(box)).toBe(false);
  });
});
