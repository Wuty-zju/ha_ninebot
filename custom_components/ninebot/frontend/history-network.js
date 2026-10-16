/** Connection/user-scoped in-flight reads and reference-counted invalidations. */
const reads = new WeakMap(), subscriptions = new WeakMap();
const delays = [1000, 5000, 15000];
const safeStop = stop => { try { Promise.resolve(stop?.()).catch(() => {}); } catch {} };

export function invokeHistory(hass, service, data, current = () => true) {
  const request = () => hass.callWS({ type: 'call_service', domain: 'ninebot', service,
    service_data: data, return_response: true }).then(result => result.response);
  // Descending page cursors are consumed: never share their response tokens.
  if (service !== 'get_trip_detail' || data.local_only !== true || !hass.connection || !hass.user?.id) return request();
  let active = reads.get(hass.connection);
  if (!active) { active = new Map(); reads.set(hass.connection, active); }
  const key = JSON.stringify([hass.user.id, service, Object.entries(data).sort(([a], [b]) => a.localeCompare(b))]);
  if (active.has(key)) { const record = active.get(key); record.guards.add(current); return record.promise; }
  if (active.size >= 64) return Promise.reject({ code: 'busy' });
  const record = { guards: new Set([current]), promise: null };
  const promise = Promise.resolve().then(async () => {
    for (let attempt = 0; ; attempt++) {
      if (![...record.guards].some(check => check())) throw { code: 'cancelled' };
      try { return await request(); }
      catch (error) {
        const temporary = ['timeout', 'connection_lost', 'busy'].includes(error?.code) ||
          ['query_unavailable', 'calendar_unavailable'].includes(error?.translation_key);
        if (attempt || !temporary || hass.connected === false || hass.connection.connected === false) throw error;
        await new Promise(resolve => setTimeout(resolve, 250));
      }
    }
  });
  record.promise = promise; active.set(key, record);
  const clear = () => { record.guards.clear(); if (active.get(key) === record) active.delete(key); };
  promise.then(clear, clear);
  return promise;
}

export function watchHistory(hass, devices, listener) {
  const connection = hass.connection;
  if (!connection?.subscribeMessage || !hass.user?.id) return () => {};
  const ids = [...new Set(devices)].sort();
  if (!ids.length || ids.length > 5) throw new Error('invalid_scope');
  let scopes = subscriptions.get(connection);
  if (!scopes) { scopes = new Map(); subscriptions.set(connection, scopes); }
  const key = JSON.stringify([hass.user.id, ids]);
  let feed = scopes.get(key);
  if (!feed) {
    if (scopes.size >= 64) throw new Error('subscription_limit');
    feed = { listeners: new Set(), stopped: false, attempt: 0, serial: 0, stop: null, timer: null, last: null };
    scopes.set(key, feed);
    const notify = event => {
      feed.last = event;
      for (const callback of [...feed.listeners]) if (!feed.stopped) callback(event);
    };
    const retry = reason => {
      feed.serial++;
      const stop = feed.stop; feed.stop = null; safeStop(stop);
      if (feed.stopped || feed.timer) return;
      if (feed.attempt >= delays.length) { notify({ type: 'unavailable', reason }); return; }
      notify({ type: 'retrying', reason });
      if (!feed.stopped) feed.timer = setTimeout(() => { feed.timer = null; start(); }, delays[feed.attempt++]);
    };
    const start = () => {
      if (feed.stopped) return;
      const serial = ++feed.serial;
      Promise.resolve().then(() => connection.subscribeMessage(event => {
        if (feed.stopped || serial !== feed.serial) return;
        if (event?.type === 'closed') {
          if (event.reason === 'denied') notify({ type: 'denied' }); else retry(event.reason === 'unloaded' ? 'unloaded' : 'unavailable');
          return;
        }
        if (event?.schema_version !== 1 || !['baseline', 'changed'].includes(event.type) ||
            !Array.isArray(event.vehicles) || event.vehicles.length !== ids.length ||
            new Set(event.vehicles.map(row => row.device_id)).size !== ids.length ||
            event.vehicles.some(row => !ids.includes(row.device_id) ||
              typeof row.selection_version !== 'string' || !/^[a-f0-9]{32}:[0-9]{1,20}$/.test(row.selection_version))) {
          notify({ type: 'denied' }); return;
        }
        feed.attempt = 0;
        notify({ type: event.type, vehicles: event.vehicles.map(row =>
          ({ device_id: row.device_id, selection_version: row.selection_version })) });
      }, { type: 'ninebot/history/subscribe', device_ids: ids }, { resubscribe: false })).then(stop => {
        if (feed.stopped || serial !== feed.serial) safeStop(stop); else feed.stop = stop;
      }).catch(error => {
        if (feed.stopped || serial !== feed.serial) return;
        if (error?.code === 'unauthorized') notify({ type: 'denied' });
        else if (['unknown_command', 'not_supported', 'invalid_format'].includes(error?.code)) notify({ type: 'unsupported' });
        else retry(error?.code === 'unavailable' ? 'unavailable' : 'temporary');
      });
    };
    feed.start = start;
  }
  feed.listeners.add(listener);
  if (feed.last) listener(feed.last.type === 'changed' ? { ...feed.last, type: 'baseline' } : feed.last);
  if (!feed.started) { feed.started = true; feed.start(); }
  let released = false;
  return () => {
    if (released) return; released = true;
    feed.listeners.delete(listener);
    if (!feed.listeners.size) {
      feed.stopped = true; feed.serial++; clearTimeout(feed.timer); safeStop(feed.stop);
      feed.last = null; scopes.delete(key);
    }
  };
}
