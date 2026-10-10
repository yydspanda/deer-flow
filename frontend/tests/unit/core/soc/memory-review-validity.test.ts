import { expect, test } from "@rstest/core";

import {
  defaultMemoryActivationWindow,
  memoryConfirmationWindow,
  memoryRecordActivationWindow,
} from "@/core/soc/memory-review-validity";
import type { SocMemoryCandidate, SocMemoryRecord } from "@/core/soc/types";

const DAY_MS = 86_400_000;
const now = Date.parse("2026-10-10T08:00:00Z");

test("born-expired legacy pattern records can still request the existing bounded repair", () => {
  const record = {
    source: { source_type: "repeated_pattern" },
    created_at: new Date(now - 60 * DAY_MS).toISOString(),
    validity: { valid_until: new Date(now - 90 * DAY_MS).toISOString() },
  } as SocMemoryRecord;
  expect(memoryRecordActivationWindow(record, now)).toEqual({
    validUntil: new Date(now + 60 * DAY_MS).toISOString(),
    reviewDays: 30,
  });
  expect(
    memoryRecordActivationWindow(
      { ...record, created_at: new Date(now - 120 * DAY_MS).toISOString() },
      now,
    ),
  ).toBeNull();
  expect(
    memoryRecordActivationWindow(
      { ...record, source: { ...record.source, source_type: "manual_note" } },
      now,
    ),
  ).toBeNull();
});

function candidate(sourceType: string, validUntil: string | null) {
  return {
    source: { source_type: sourceType },
    validity: { valid_until: validUntil },
  } as SocMemoryCandidate;
}

test("new activation defaults to a 180-day review within its validity", () => {
  const window = defaultMemoryActivationWindow(null, now)!;
  expect(window.reviewDays).toBe(180);
  expect(Date.parse(window.validUntil)).toBe(now + 365 * DAY_MS);
});

test("restoring an old record preserves its expiry and shortens the proposed review interval", () => {
  const validUntil = new Date(now + 45.5 * DAY_MS).toISOString();
  expect(defaultMemoryActivationWindow(validUntil, now)).toEqual({
    validUntil,
    reviewDays: 45,
  });
});

test.each([
  new Date(now - DAY_MS).toISOString(),
  new Date(now + DAY_MS / 2).toISOString(),
  "invalid date",
])(
  "does not propose a renewed lifetime for unusable existing expiry %s",
  (limit) => {
    expect(defaultMemoryActivationWindow(limit, now)).toBeNull();
  },
);

test("new repeated-pattern confirmation proposes a new window while manual candidates keep their limit", () => {
  const limit = new Date(now + 45 * DAY_MS).toISOString();
  expect(
    memoryConfirmationWindow(candidate("repeated_pattern", limit), now),
  ).toEqual(defaultMemoryActivationWindow(null, now));
  const manual = memoryConfirmationWindow(
    candidate("analyst_feedback", limit),
    now,
  )!;
  expect(Date.parse(manual.validUntil)).toBeLessThanOrEqual(Date.parse(limit));
  expect(manual.reviewDays).toBeLessThanOrEqual(45);
  expect(manual.reviewDays).toBeGreaterThan(0);
});

test("an expired manual candidate cannot receive a fresh confirmation window", () => {
  expect(
    memoryConfirmationWindow(
      candidate("analyst_feedback", new Date(now - DAY_MS).toISOString()),
      now,
    ),
  ).toBeNull();
});
