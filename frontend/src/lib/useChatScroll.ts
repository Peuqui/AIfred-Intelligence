import { type Ref, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { type ChatPlace, readPlace, savePlace } from "./chatPlace";
import { type Move, type Reason, TOP_ROOM_PX, isFollowing, moveFor } from "./followScroll";

// Keys that scroll a box when it has the focus.
const SCROLL_KEYS = new Set(["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", " "]);

/**
 * The scrolling of the chat's box (the rules are in followScroll.ts). The box's one child is the content whose growth
 * (pictures and films that load later) is watched; the bubbles carry their key in `data-entry`, the "read up to here"
 * line `data-read-mark`, a question that sticks at the top `data-pinned`. `unreadKeys` lists the unread bubbles, the oldest first.
 */
export function useChatScroll(box: Ref<HTMLElement | null>, sessionId: () => string | null, unreadKeys: () => string[]) {
  const following = ref(true);
  // Unread bubbles that lie below the part of the box in view.
  const unreadBelow = ref(0);
  // Where the box was sent last, as the scroll position it wants now: held while the content grows, until the reader
  // takes the box in hand or the next placement comes.
  let hold: (() => number) | null = null;
  let observer: ResizeObserver | null = null;
  // The reader sent something: the next bubble of theirs is their own.
  let ownPending = false;
  let lastSaved = "";

  const bubbles = (): HTMLElement[] => [...(box.value?.querySelectorAll<HTMLElement>("[data-entry]") ?? [])];
  const bubble = (key: string): HTMLElement | undefined => bubbles().find((element) => element.dataset.entry === key);
  const endTop = (scroller: HTMLElement): number => scroller.scrollHeight - scroller.clientHeight;
  // The room a question that sticks at the upper edge takes: what is read from its top stops below it.
  const pinnedRoom = (element: HTMLElement): number => {
    const pinned = element.closest("section")?.querySelector<HTMLElement>("[data-pinned]");
    return pinned && !pinned.contains(element) ? pinned.getBoundingClientRect().height : 0;
  };
  // The scroll position that puts the element at the upper edge, below a sticking question.
  const topOf = (scroller: HTMLElement, element: HTMLElement): number =>
    scroller.scrollTop + element.getBoundingClientRect().top - scroller.getBoundingClientRect().top - TOP_ROOM_PX - pinnedRoom(element);

  // The line at the upper edge, or the end where what follows the line fits the screen first. The reader who follows
  // is never taken back up (neverUp); a chat that is opened starts from wherever the last one left the box.
  function linePosition(scroller: HTMLElement, neverUp: boolean): number {
    const line = scroller.querySelector<HTMLElement>("[data-read-mark]");
    if (!line) return endTop(scroller);
    const target = Math.min(endTop(scroller), topOf(scroller, line));
    return neverUp ? Math.max(scroller.scrollTop, target) : target;
  }

  function rememberedPosition(scroller: HTMLElement, place: ChatPlace): number {
    const element = bubble(place.key);
    if (!element) return endTop(scroller);
    return scroller.scrollTop + element.getBoundingClientRect().bottom - scroller.getBoundingClientRect().bottom + place.offset;
  }

  function target(move: Exclude<Move, "stay">, reason: Reason, id: string | null): (() => number) | null {
    const scroller = box.value;
    if (!scroller) return null;
    if (move === "end") return () => endTop(scroller);
    if (move === "oldestUnread") return () => linePosition(scroller, reason !== "opened");
    const place = id === null ? null : readPlace(id);
    return place === null ? () => endTop(scroller) : () => rememberedPosition(scroller, place);
  }

  function hasRemembered(id: string | null): boolean {
    return id !== null && readPlace(id) !== null;
  }

  function goTo(position: () => number): void {
    const scroller = box.value;
    if (!scroller) return;
    hold = position;
    scroller.scrollTop = position();
    onScroll();
  }

  /** Something happened (see Reason): the box goes where the rules say, or stays and counts what came in below. */
  async function place(reason: Reason): Promise<void> {
    await nextTick();
    const id = sessionId();
    const move = moveFor(reason, { following: following.value, hasUnread: unreadKeys().length > 0, hasRemembered: hasRemembered(id) });
    const position = move === "stay" ? null : target(move, reason, id);
    if (position) goTo(position);
    else onScroll();
  }

  /** The chat grew: with a bubble of the reader's own among the new ones, after they sent something, it is their own. */
  function placeIncoming(userBubbleAdded: boolean): Promise<void> {
    const own = userBubbleAdded && ownPending;
    if (own) ownPending = false;
    return place(own ? "own" : "incoming");
  }

  /** The reader is sending something: the bubble of theirs that comes next takes the box to the end. */
  function expectOwn(): void {
    ownPending = true;
  }

  /** Sending failed: no bubble of theirs is coming, the next user bubble (another tab, a channel) is not theirs. */
  function ownFailed(): void {
    ownPending = false;
  }

  function unreadBelowView(): string[] {
    const scroller = box.value;
    if (!scroller) return [];
    const bottom = scroller.getBoundingClientRect().bottom;
    return unreadKeys().filter((key) => (bubble(key)?.getBoundingClientRect().top ?? 0) >= bottom);
  }

  // The bubble that stood lowest in view, and how far above the lower edge it ended.
  function rememberPlace(scroller: HTMLElement): void {
    const id = sessionId();
    const view = scroller.getBoundingClientRect();
    const seen = bubbles().filter((element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.top < view.bottom && bounds.bottom > view.top;
    });
    const lowest = seen.at(-1);
    if (id === null || !lowest?.dataset.entry) return;
    const place: ChatPlace = { key: lowest.dataset.entry, offset: Math.round(view.bottom - lowest.getBoundingClientRect().bottom) };
    const serialized = id + JSON.stringify(place);
    if (serialized === lastSaved) return;
    lastSaved = serialized;
    savePlace(id, place);
  }

  /** The box scrolled (by the reader or by a placement): following or reading, what lies below, where the reader is. */
  function onScroll(): void {
    const scroller = box.value;
    if (!scroller) return;
    following.value = isFollowing(endTop(scroller) - scroller.scrollTop);
    unreadBelow.value = unreadBelowView().length;
    rememberPlace(scroller);
  }

  /** The reader took the box in hand: what was held is let go. */
  function takenByHand(): void {
    hold = null;
  }

  function onKey(event: KeyboardEvent): void {
    if (SCROLL_KEYS.has(event.key)) takenByHand();
  }

  // A press on the box itself (not on what is in it) is the scroll bar.
  function onPointerDown(event: PointerEvent): void {
    if (event.target === box.value) takenByHand();
  }

  /** The button "N new ↓": down to the first unread bubble below the view, at the upper edge. */
  function showNewBelow(): void {
    const key = unreadBelowView()[0];
    const scroller = box.value;
    if (key === undefined || !scroller) return;
    goTo(() => {
      const element = bubble(key);
      return element ? topOf(scroller, element) : scroller.scrollTop;
    });
  }

  onMounted(() => {
    const content = box.value?.firstElementChild;
    if (content) {
      observer = new ResizeObserver(() => {
        const scroller = box.value;
        if (hold && scroller) scroller.scrollTop = hold();
        onScroll();
      });
      observer.observe(content);
    }
    void place("opened");
  });
  onBeforeUnmount(() => observer?.disconnect());

  return { unreadBelow, place, placeIncoming, expectOwn, ownFailed, onScroll, takenByHand, onKey, onPointerDown, showNewBelow };
}
