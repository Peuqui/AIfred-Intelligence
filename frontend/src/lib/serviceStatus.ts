// The status line's rules (lib/service_status.py sends the state with the seconds left as of the moment it was
// taken; the page counts down itself). One row per loaded thing: its color (green loaded, yellow loading or held,
// grey nothing loaded, red not reachable), its text, the seconds left (null = stays loaded) and what its button
// does (null = no button). STT and TTS load with their first request, the STT CPU model never unloads, a TTS engine
// on another machine is not this machine's to release. Ported from assets/custom.js (serviceStatus*, 7cbf8178).
import type { HostStatus, ServiceSnapshot } from "../api/types";

export type Service = "llm" | "describer" | "stt" | "tts";
export type Color = "green" | "yellow" | "grey" | "red";
export const SERVICES: readonly Service[] = ["llm", "describer", "stt", "tts"];

export interface Action {
  verb: "load" | "unload" | "hold" | "release";
  service: Service | "host";
  item: string;
}

export interface Row {
  color: Color;
  text: string;
  title?: string;
  detail?: string; // a second line under the text (a machine's loaded models)
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
  hostHeld: string;
  hostFreeCards: string;
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

// One line per machine with describers: red its GPU-Orc does not answer, yellow its card is held free for somebody,
// green open to describers; the button holds the card free or releases it.
export function hostRows(hosts: HostStatus[], labels: Labels): (Row & { label: string })[] {
  const mib = (n: number) => n.toLocaleString(labels.lang);
  return hosts.map((host) => {
    if (!host.reachable) {
      return { label: host.name, color: "red", text: labels.unreachable, title: host.error, remaining: null, upperBound: false, action: null };
    }
    const used = labels.hostUsed.replace("{used}", mib(host.used_mib)).replace("{total}", mib(host.total_mib));
    const text = `${host.held ? labels.hostHeld : labels.hostFreeCards} · ${used}`;
    return {
      label: host.name,
      color: host.held ? "yellow" : "green",
      text,
      detail: host.holders.join(", ") || undefined,
      title: host.held ? `${labels.hostHeld}: ${host.held}` : text,
      remaining: null,
      upperBound: false,
      action: { verb: host.held ? "release" : "hold", service: "host", item: host.name },
    };
  });
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
