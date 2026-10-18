/** Presentation only: keep archived seconds and numeric HA units unchanged. */
export function formatRideDuration(seconds, hours = null, locale = 'en') {
  const valid = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const total = valid(seconds) ? Math.round(seconds) : valid(hours) ? Math.round(hours * 3600) : null;
  if (total === null || !Number.isSafeInteger(total)) return '—';
  const parts = [['hour', Math.floor(total / 3600)], ['minute', Math.floor(total % 3600 / 60)], ['second', total % 60]];
  const selected = total === 0 ? [['second', 0]] : parts.filter(([, value]) => value > 0);
  return selected.map(([unit, value]) => new Intl.NumberFormat(locale, {
    style: 'unit', unit, unitDisplay: 'short', maximumFractionDigits: 0,
  }).format(value)).join(' ');
}

export function formatRideInterval(start, end, locale = 'en', timeZone = 'Asia/Shanghai') {
  const parsed = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? new Date(value) : null;
  const first = parsed(start), last = parsed(end);
  if (!first && !last || first && last && last < first) return '—';
  const full = new Intl.DateTimeFormat(locale, { timeZone, dateStyle: 'medium', timeStyle: 'medium' });
  const day = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' });
  const sameDay = first && last && day.format(first) === day.format(last);
  const finish = sameDay ? new Intl.DateTimeFormat(locale, { timeZone, timeStyle: 'medium' }) : full;
  return `${first ? full.format(first) : '—'} – ${last ? finish.format(last) : '—'}`;
}
