import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkedConfig } from '../../custom_components/ninebot/frontend/trip-card-editor.js';

test('visual editor validation preserves native-card options and explicit date scope', () => {
  const config = { type: 'custom:ninebot-trip-card', vehicles: [{ device_id: 'a' }, { device_id: 'b' }],
    title: 'Example', start_date: '2026-01-01', end_date: '2026-01-31',
    statistics_entities: ['ninebot:synthetic_day_distance_km'], limit: 1 };
  assert.deepEqual(checkedConfig(config), config);
  assert.notEqual(checkedConfig(config), config);
  assert.equal(checkedConfig({ device_id: 'a' }).start_date, undefined);
});

test('editor rejects conflicting devices, invalid dates and unbounded config', () => {
  for (const config of [{}, { entity: 'sensor.a' }, { device_id: 'a', limit: 101 },
    { device_id: 'a', start_date: 'bad' }, { vehicles: [{ device_id: 'a' }, { device_id: 'a' }] },
    { device_id: 'a', statistics_entities: ['javascript:bad'] }]) assert.throws(() => checkedConfig(config));
});
