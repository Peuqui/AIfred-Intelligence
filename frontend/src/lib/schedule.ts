// A job's schedule as the jobs page edits it, and the expression lib/scheduler.py stores: cron "m h dom mon dow",
// an interval in seconds, or a point in time "YYYY-MM-DDTHH:MM:SS". The server checks the expression (and says
// when it fires next); nothing here guesses a value for one that does not parse.

export type ScheduleType = "cron" | "interval" | "once";
export type IntervalUnit = "minutes" | "hours" | "days";

export const UNIT_SECONDS: Record<IntervalUnit, number> = { minutes: 60, hours: 3600, days: 86400 };

export interface ScheduleFields {
  type: ScheduleType;
  minute: string;
  hour: string;
  dom: string;
  month: string;
  dow: string;
  intervalValue: number;
  intervalUnit: IntervalUnit;
  date: string; // YYYY-MM-DD
  time: string; // HH:MM
}

export function composeSchedule(fields: ScheduleFields): string {
  if (fields.type === "cron") return [fields.minute, fields.hour, fields.dom, fields.month, fields.dow].join(" ");
  if (fields.type === "interval") return String(fields.intervalValue * UNIT_SECONDS[fields.intervalUnit]);
  return `${fields.date}T${fields.time}:00`;
}

// The fields of a stored expression: an interval in the largest unit it fills evenly, a cron expression in its
// five parts (one with fewer parts keeps what it has, the server shows it never fires).
export function decomposeSchedule(base: ScheduleFields, type: ScheduleType, expr: string): ScheduleFields {
  if (type === "cron") {
    const [minute = "", hour = "", dom = "", month = "", dow = ""] = expr.trim().split(/\s+/);
    return { ...base, type, minute, hour, dom, month, dow };
  }
  if (type === "interval") {
    const seconds = Number(expr);
    const unit = (["days", "hours", "minutes"] as IntervalUnit[]).find((candidate) => seconds % UNIT_SECONDS[candidate] === 0) ?? "minutes";
    return { ...base, type, intervalValue: seconds / UNIT_SECONDS[unit], intervalUnit: unit };
  }
  const [date = "", time = ""] = expr.split("T");
  return { ...base, type, date, time: time.slice(0, 5) };
}
