// The status line's rules (lib/service_status.py sends the state with the seconds left as of the moment it was
// taken; the page counts down itself). One row per loaded thing: its color (green loaded, yellow loading or held,
// grey nothing loaded, red not reachable), its text, the seconds left (null = stays loaded) and what its button
// does (null = no button). STT and TTS load with their first request, the STT CPU model never unloads, a TTS engine
// on another machine is not this machine's to release. Ported from assets/custom.js (serviceStatus*, 7cbf8178).
import type { HostStatus, LocalHoldStatus, ServiceSnapshot } from "../api/types";

export type Service = "llm" | "describer" | "stt" | "tts";
// amber: a hold (somebody keeps or asks for the cards), the same as Agent-Orc's; red only for a real error.
export type Color = "green" | "yellow" | "amber" | "grey" | "red";
export const SERVICES: readonly Service[] = ["llm", "describer", "stt", "tts"];

export interface Action {
  verb: "load" | "unload" | "hold" | "release" | "grant" | "end";
  service: Service | "host";
  item: string;
  blocked?: string; // why the button cannot act (greyed out with this as its title), e.g. another owner's hold
}

export interface Row {
  color: Color;
  text: string;
  title?: string;
  detail?: string; // lines under the text (a machine's loaded models, one a line)
  remaining: number | null;
  upperBound: boolean; // the model was loaded before AIfred listened: the time is at most this long
  action: Action | null;
}

export interface Labels {
  lang: string;
  noModel: string;
  noneLoaded: string;
  loading: string;
  busy: string;
  unreachable: string;
  hostHeld: string; // "held free by {owner}"
  hostRequested: string; // the hold is asked for, work still runs on the cards
  hostHeldByOther: string; // "held by {owner}; only the owner can release it"
  hostUsed: string; // "{used} of {total} MiB in use"
}

export function serviceRows(snapshot: ServiceSnapshot, labels: Labels): Record<Service, Row[]> {
  const rows: Record<Service, Row[]> = { llm: [], describer: [], stt: [], tts: [] };
  const unload = (service: Service, item: string): Action => ({ verb: "unload", service, item });
  for (const model of snapshot.models ?? []) {
    const ready = model.state === "ready";
    rows[model.role as Service].push({
      color: ready ? "green" : "yellow",
      text: ready ? (model.busy ? `${model.name} · ${labels.busy}` : model.name) : `${model.name} · ${labels.loading}`,
      remaining: ready && !model.busy ? (model.remaining_s ?? null) : null,
      upperBound: !model.exact,
      action: ready && !model.busy ? unload(model.role as Service, model.name) : null,
    });
  }
  for (const device of snapshot.stt ?? []) {
    rows.stt.push({
      color: "green",
      text: `${device.engine.charAt(0).toUpperCase()}${device.engine.slice(1)} · ${device.device === "cuda" ? "GPU" : "CPU"}`,
      remaining: device.remaining_s ?? null,
      upperBound: false,
      action: device.device === "cuda" ? unload("stt", "") : null,
    });
  }
  for (const engine of snapshot.tts ?? []) {
    const name = engine.label_short[labels.lang] ?? engine.engine;
    rows.tts.push({
      color: "green",
      text: engine.host ? `${name} · ${engine.host}` : name,
      remaining: engine.remaining_s ?? null,
      upperBound: false,
      action: engine.host ? null : unload("tts", engine.engine),
    });
  }
  const reachable: Record<Service, boolean> = {
    llm: snapshot.llm_reachable,
    describer: snapshot.llm_reachable,
    stt: snapshot.stt_reachable ?? false,
    tts: snapshot.tts_reachable ?? false,
  };
  const placeholder: Record<Service, string> = { llm: labels.noModel, describer: labels.noneLoaded, stt: labels.noneLoaded, tts: labels.noneLoaded };
  for (const service of SERVICES) {
    if (!reachable[service]) {
      rows[service] = [{ color: "red", text: labels.unreachable, remaining: null, upperBound: false, action: null }];
    } else if (rows[service].length === 0) {
      const loadable = service === "llm" || service === "describer";
      rows[service] = [{
        color: "grey", text: placeholder[service], remaining: null, upperBound: false,
        action: loadable ? { verb: "load", service, item: "" } : null,
      }];
    }
  }
  return rows;
}

// One entry per machine with describers, its words kept short (Peuqui 13.11.2026): the dot tells red its GPU-Orc does not
// answer (the text says so, the error under it), amber its cards are held free for somebody, green open; the text is the memory in use, and under it one line
// per model on its cards with the service that holds it (the describer's llama-swap, the TTS-Suite …). The button holds
// the cards free or releases them.
export function hostRows(hosts: HostStatus[], labels: Labels): (Row & { label: string })[] {
  const mib = (n: number) => n.toLocaleString(labels.lang);
  return hosts.map((host) => {
    if (!host.reachable) {
      return { label: host.name, color: "red", text: labels.unreachable, detail: host.error || undefined, title: host.error, remaining: null, upperBound: false, action: null };
    }
    const text = labels.hostUsed.replace("{used}", mib(host.used_mib)).replace("{total}", mib(host.total_mib));
    // Who holds the cards is a line of its own, always visible: a phone has no hover for the title.
    const holdLine = host.held
      ? `${labels.hostHeld.replace("{owner}", host.hold_owner ?? "")}${host.hold_state === "requested" ? ` (${labels.hostRequested})` : ""}: ${host.held}`
      : null;
    const lines = [...(holdLine ? [holdLine] : []), ...host.holders.map((holder) => `${holder.consumer}: ${holder.item}`)];
    return {
      label: host.name,
      color: host.held ? "amber" : "green",
      text,
      detail: lines.join("\n") || undefined,
      title: holdLine ?? text,
      remaining: null,
      upperBound: false,
      action: {
        verb: host.held ? "release" : "hold",
        service: "host",
        item: host.name,
        ...(host.held && !host.hold_is_ours ? { blocked: labels.hostHeldByOther.replace("{owner}", host.hold_owner ?? "") } : {}),
      },
    };
  });
}

export interface LocalHoldLabels {
  lang: string;
  hostUsed: string; // "{used} of {total} MiB in use": no hold is not empty cards, the line says how full they are
  free: string;
  requested: string; // "{owner} wants the cards"
  granted: string; // "granted to {owner}, waiting for running work to end"
  active: string; // "held by {owner}"
  reason: string; // "„{reason}“"
  model: string; // "model {model}"
  since: string; // "since {time}"
  off: string;
  unreachable: string; // "GPU-Orc does not answer: {error}"
  notRequested: string; // title of "grant" while greyed out
  noHold: string; // title of "end" while greyed out
}

export interface LocalHoldRow {
  label: string; // this machine's name
  color: Color;
  pending: boolean; // a request waits for a person's decision: its dot pulses (still once granted or active)
  card: boolean; // somebody asks for or holds the cards: the row is a card, else a plain line
  title: string; // always visible: the state in words, also the reason the buttons are greyed out
  detail: string | null; // under the title: the reason, the model that waits, since when
  grant: Action; // enabled only while a hold is requested and nobody granted it
  end: Action; // enabled only while the hold is requested, granted or active
}

// Hour and minute of an ISO time in the page's language.
export function clockTime(iso: string, lang: string): string {
  return new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
}

// The row for the hold of THIS machine's cards (AIfred's own, its local GPU-Orc). Both buttons are always there; the
// one that may not act is greyed out (``blocked``) and the state in the title is the visible reason (a phone has no hover).
const HELD_STATES: ReadonlySet<LocalHoldStatus["state"]> = new Set(["requested", "granted", "active"]);

export function localHoldRow(status: LocalHoldStatus, labels: LocalHoldLabels): LocalHoldRow {
  const owner = status.owner ?? "";
  const fill = (text: string) => text.replace("{owner}", owner).replace("{error}", status.error);
  const detailParts = [
    status.reason ? labels.reason.replace("{reason}", status.reason) : null,
    status.model ? labels.model.replace("{model}", status.model) : null,
    status.since ? labels.since.replace("{time}", clockTime(status.since, labels.lang)) : null,
  ].filter((part) => part !== null);
  const detail = detailParts.join(" · ");
  const mib = (n: number) => n.toLocaleString(labels.lang);
  const usedText = status.used_mib !== null && status.total_mib !== null
    ? labels.hostUsed.replace("{used}", mib(status.used_mib)).replace("{total}", mib(status.total_mib))
    : null;
  const states: Record<LocalHoldStatus["state"], [Color, string, string | null]> = {
    off: ["grey", labels.off, null],
    unreachable: ["red", fill(labels.unreachable), null],
    // No hold: the title says how full the cards are (no hold is not empty cards), the detail that nobody asks.
    free: ["green", usedText ?? labels.free, usedText === null ? null : labels.free],
    requested: ["amber", fill(labels.requested), detail],
    granted: ["amber", fill(labels.granted), null],
    active: ["amber", fill(labels.active), detail],
  };
  const [color, title, shownDetail] = states[status.state];
  return {
    label: status.machine,
    color,
    pending: status.state === "requested",
    card: HELD_STATES.has(status.state),
    title,
    detail: shownDetail,
    grant: { verb: "grant", service: "host", item: owner, ...(status.state === "requested" ? {} : { blocked: labels.notRequested }) },
    // Ending works on every hold, also a requested one: the way out of a request whose asker has crashed.
    end: { verb: "end", service: "host", item: owner, ...(HELD_STATES.has(status.state) ? {} : { blocked: labels.noHold }) },
  };
}

// The color of a service in the closed bar: the worst of its rows.
export function summaryColor(rows: Row[]): Color {
  for (const color of ["red", "yellow", "green"] as const) {
    if (rows.some((row) => row.color === color)) return color;
  }
  return "grey";
}

// Days only when there are days, hours only when there are hours: 2 T 03:15:20, 3:15:20, 15:20.
export function formatRemaining(seconds: number, daysShort: string): string {
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const two = (n: number) => String(n).padStart(2, "0");
  if (days > 0) return `${days} ${daysShort} ${two(hours)}:${two(minutes)}:${two(total % 60)}`;
  return hours > 0 ? `${hours}:${two(minutes)}:${two(total % 60)}` : `${minutes}:${two(total % 60)}`;
}
