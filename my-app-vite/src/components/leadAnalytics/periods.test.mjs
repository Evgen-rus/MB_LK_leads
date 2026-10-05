import assert from "node:assert/strict";
import test from "node:test";
import { MAX_ANALYSIS_PERIODS, periodDayCount, splitAnalysisPeriod } from "./periods.ts";

test("splits date ranges into clipped calendar weeks and months", () => {
  const range = { period_start: "2026-07-15", period_end: "2026-10-02" };
  const weeks = splitAnalysisPeriod(range, "week");
  assert.deepEqual(weeks[1], { period_start: "2026-07-15", period_end: "2026-07-19" });
  assert.deepEqual(weeks[2], { period_start: "2026-07-20", period_end: "2026-07-26" });
  assert.deepEqual(weeks.at(-1), { period_start: "2026-09-28", period_end: "2026-10-02" });
  assert.equal(periodDayCount(weeks[1]), 5);
  assert.deepEqual(splitAnalysisPeriod({ period_start: "2026-12-27", period_end: "2027-01-05" }, "week").slice(1), [
    { period_start: "2026-12-27", period_end: "2026-12-27" },
    { period_start: "2026-12-28", period_end: "2027-01-03" },
    { period_start: "2027-01-04", period_end: "2027-01-05" }
  ]);

  const months = splitAnalysisPeriod(range, "month");
  assert.deepEqual(months.slice(1), [
    { period_start: "2026-07-15", period_end: "2026-07-31" },
    { period_start: "2026-08-01", period_end: "2026-08-31" },
    { period_start: "2026-09-01", period_end: "2026-09-30" },
    { period_start: "2026-10-01", period_end: "2026-10-02" }
  ]);
});

test("keeps leap-day boundaries, avoids duplicate whole periods, and enforces the API limit", () => {
  const leapMonth = splitAnalysisPeriod({ period_start: "2024-02-15", period_end: "2024-03-02" }, "month");
  assert.deepEqual(leapMonth.slice(1), [
    { period_start: "2024-02-15", period_end: "2024-02-29" },
    { period_start: "2024-03-01", period_end: "2024-03-02" }
  ]);

  const oneWeek = { period_start: "2026-07-20", period_end: "2026-07-26" };
  assert.deepEqual(splitAnalysisPeriod(oneWeek, "week"), [oneWeek]);
  const allowedMonths = splitAnalysisPeriod({ period_start: "2020-01-01", period_end: "2025-03-31" }, "month");
  assert.equal(allowedMonths.length, MAX_ANALYSIS_PERIODS);
  assert.throws(() => splitAnalysisPeriod({ period_start: "2020-01-01", period_end: "2025-04-30" }, "month"), RangeError);
});
