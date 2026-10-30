const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago", then the date. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < MINUTE) return "just now"; // also a clock a little behind the server's
  const format = new Intl.RelativeTimeFormat("en", { numeric: "always" });
  if (seconds < HOUR) return format.format(-Math.floor(seconds / MINUTE), "minute");
  if (seconds < DAY) return format.format(-Math.floor(seconds / HOUR), "hour");
  if (seconds < 7 * DAY) return format.format(-Math.floor(seconds / DAY), "day");
  return then.toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" });
}
