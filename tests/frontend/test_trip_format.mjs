import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatRideDuration, formatRideInterval } from '../../custom_components/ninebot/frontend/trip-format.js';

const unit = (value, name, locale = 'en') => new Intl.NumberFormat(locale, {
  style: 'unit', unit: name, unitDisplay: 'short', maximumFractionDigits: 0,
}).format(value);

test('duration preserves source seconds and omits zero leading units', () => {
  assert.equal(formatRideDuration(45), unit(45, 'second'));
  assert.equal(formatRideDuration(1906, 0.53), `${unit(31, 'minute')} ${unit(46, 'second')}`);
  assert.equal(formatRideDuration(3599), `${unit(59, 'minute')} ${unit(59, 'second')}`);
  assert.equal(formatRideDuration(3600), unit(1, 'hour'));
  assert.equal(formatRideDuration(3723), `${unit(1, 'hour')} ${unit(2, 'minute')} ${unit(3, 'second')}`);
  assert.equal(formatRideDuration(0), unit(0, 'second'));
});

test('legacy hours convert to seconds only for display; invalid values stay missing', () => {
  assert.equal(formatRideDuration(null, 600 / 3600), unit(10, 'minute'));
  for (const value of [null, undefined, true, -1, NaN, Infinity, '45']) {
    assert.equal(formatRideDuration(value), '—');
  }
  assert.equal(formatRideDuration(45.6), unit(46, 'second'));
  assert.equal(formatRideDuration(1906, null, 'zh-Hans'), `${unit(31, 'minute', 'zh-Hans')} ${unit(46, 'second', 'zh-Hans')}`);
});

test('one time range respects business timezone and midnight boundaries', () => {
  const same = formatRideInterval('2026-10-08T08:21:34Z', '2026-10-08T08:53:20Z');
  assert.match(same, /16:21:34|4:21:34/);
  assert.match(same, /16:53:20|4:53:20/);
  const crossing = formatRideInterval('2026-10-08T15:59:00Z', '2026-10-08T16:01:00Z');
  assert.match(crossing, /Oct 8/); assert.match(crossing, /Oct 9/);
  assert.equal(formatRideInterval(null, null), '—');
  assert.equal(formatRideInterval('bad', 'bad'), '—');
  assert.equal(formatRideInterval('2026-10-09T10:00:00Z', '2026-10-08T10:00:00Z'), '—');
});
