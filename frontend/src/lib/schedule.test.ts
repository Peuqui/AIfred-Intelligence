import { expect, test } from "vitest";
import { type ScheduleFields, composeSchedule, decomposeSchedule } from "./schedule";

const BASE: ScheduleFields = {
  type: "cron", minute: "0", hour: "8", dom: "*", month: "*", dow: "*",
  intervalValue: 1, intervalUnit: "hours", date: "2026-11-08", time: "08:00",
};

test("each type composes the expression the scheduler stores", () => {
  expect(composeSchedule({ ...BASE, dow: "1-5" })).toBe("0 8 * * 1-5");
  expect(composeSchedule({ ...BASE, type: "interval", intervalValue: 90, intervalUnit: "minutes" })).toBe("5400");
  expect(composeSchedule({ ...BASE, type: "once", date: "2026-12-24", time: "18:30" })).toBe("2026-12-24T18:30:00");
});

test("a stored expression comes back into its fields, the interval in its largest even unit", () => {
  expect(decomposeSchedule(BASE, "cron", "30 7 1 * 0")).toMatchObject({ minute: "30", hour: "7", dom: "1", month: "*", dow: "0" });
  expect(decomposeSchedule(BASE, "interval", "172800")).toMatchObject({ intervalValue: 2, intervalUnit: "days" });
  expect(decomposeSchedule(BASE, "interval", "5400")).toMatchObject({ intervalValue: 90, intervalUnit: "minutes" });
  expect(decomposeSchedule(BASE, "once", "2026-12-24T18:30:00")).toMatchObject({ date: "2026-12-24", time: "18:30" });
});

test("compose and decompose are each other's inverse", () => {
  for (const [type, expr] of [["cron", "15 */2 * 1-6 1-5"], ["interval", "3600"], ["interval", "90"], ["once", "2027-01-01T00:05:00"]] as const) {
    expect(composeSchedule(decomposeSchedule(BASE, type, expr))).toBe(expr);
  }
});
