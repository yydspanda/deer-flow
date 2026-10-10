import type { SocMemoryCandidate, SocMemoryRecord } from "./types";

export const MEMORY_REVIEW_AFTER_DAYS = 180;
export const MEMORY_ACTIVATION_DAYS = 365;
const DAY_MS = 86_400_000;

// These are proposals for an explicit review/activation, never changes to saved records.
export function defaultMemoryActivationWindow(
  validUntilLimit?: string | null,
  now = Date.now(),
): { validUntil: string; reviewDays: number } | null {
  const validUntil = Math.min(
    now + MEMORY_ACTIVATION_DAYS * DAY_MS,
    validUntilLimit ? Date.parse(validUntilLimit) : Infinity,
  );
  // Leave room for datetime-local minute precision and the subsequent HTTP request.
  const reviewDays = Math.min(
    MEMORY_REVIEW_AFTER_DAYS,
    Math.floor((validUntil - now - 60_000) / DAY_MS),
  );
  if (!Number.isFinite(validUntil) || reviewDays < 1) return null;
  return { validUntil: new Date(validUntil).toISOString(), reviewDays };
}

export function memoryConfirmationWindow(
  candidate: SocMemoryCandidate,
  now = Date.now(),
) {
  return defaultMemoryActivationWindow(
    candidate.source.source_type === "repeated_pattern"
      ? null
      : candidate.validity.valid_until,
    now,
  );
}

export function memoryRecordActivationWindow(
  record: SocMemoryRecord,
  now = Date.now(),
) {
  const expiry = Date.parse(record.validity.valid_until ?? "");
  if (
    record.source.source_type === "repeated_pattern" &&
    expiry <= now &&
    expiry <= Date.parse(record.created_at)
  ) {
    // The existing governed repair grants 90 days only to records born expired.
    // Keep its old 60/30-day proposal; the server owns the repair and audit.
    return {
      validUntil: new Date(now + 60 * DAY_MS).toISOString(),
      reviewDays: 30,
    };
  }
  return defaultMemoryActivationWindow(record.validity.valid_until, now);
}

export function memoryActivationLocalDate(validUntil: string) {
  const value = new Date(validUntil);
  return new Date(value.getTime() - value.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}
