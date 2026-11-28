/** Shared synthetic ride facts for the standalone replay and the browser card fixture. */
const september = { ride_id: 'synthetic-ride', detail_id: 'synthetic-detail', query_month: '202609',
  start_time: '2026-09-03T08:00:00Z', end_time: '2026-09-03T08:10:00Z', distance_m: 1200,
  duration_s: 600, duration_h: 1/6, max_speed_m_s: 10, average_speed_m_s: 2, energy_wh: 90,
  precision: { mileages: 1 }, source: 'trip_detail', energy_intensity_wh_per_km: 75 };

export const syntheticVehicles = [
  { device_id: 'synthetic-a', name: 'Synthetic A' },
  { device_id: 'synthetic-b', name: 'Synthetic B' },
];

export function syntheticRide(device, { august, sameId, overlap, incomplete, zero } = {}) {
  const vehicle = syntheticVehicles.find(item => item.device_id === device);
  if (!vehicle) throw new Error('Unknown synthetic vehicle');
  const second = vehicle === syntheticVehicles[1];
  const ride = { ...september, device_id: device, vehicle_label: vehicle.name };
  if (august) Object.assign(ride, {
    ride_id: sameId ? september.ride_id : 'synthetic-august', query_month: '202608',
    start_time: '2026-08-03T08:00:00Z', end_time: '2026-08-03T08:10:00Z',
  });
  if (second && overlap) Object.assign(ride, {
    distance_m: 2400, duration_s: 900, duration_h: .25, end_time: ride.end_time.replace('08:10:', '08:15:'),
    average_speed_m_s: 8/3, energy_wh: 120, energy_intensity_wh_per_km: 50,
  });
  if (second && incomplete) Object.assign(ride, {
    end_time: ride.end_time.replace(/08:(10|15):/, '08:20:'), warnings: ['duration_time_difference'],
    average_speed_m_s: null, energy_wh: null, energy_intensity_wh_per_km: null,
  });
  if (second && zero) Object.assign(ride, {
    distance_m: 0, duration_s: 0, duration_h: 0, end_time: ride.start_time,
    max_speed_m_s: 0, average_speed_m_s: null, energy_wh: 0, energy_intensity_wh_per_km: null,
  });
  return ride;
}

export function syntheticDetail(ride, { include_track, point_offset, max_points }, { endpointsOnly, overlap } = {}) {
  const second = ride.device_id === syntheticVehicles[1].device_id;
  const pointCount = endpointsOnly ? 2 : 60;
  const points = Array.from({ length: pointCount }, (_, sequence) => {
    const shift = second && !(overlap && sequence >= 20 && sequence <= 40) ? 0.002 : 0;
    return { sequence, latitude: 30 + sequence * .0001 + shift, longitude: 120 + Math.sin(sequence / 8) * .001 + shift,
      speed_raw: 25 + Math.sin(sequence / 4) * 12, distance_delta_raw: 40 + Math.cos(sequence / 5) * 9 };
  }).filter(point => ![15, 16].includes(point.sequence));
  return { source_mode: 'ride_archive', query_month: ride.query_month, received_at: ride.end_time,
    detail_context: { source_mode: 'ride_archive', archive_revision: 1, scalar_revision: 1, geometry_received_at: ride.end_time },
    ride: { ...ride, detail_saved: true, track: include_track ? points.slice(point_offset, point_offset + max_points) : [],
      track_summary: { total_known: pointCount, offset: point_offset, limit: max_points,
        server_window_days: endpointsOnly ? 180 : null, next_offset: null,
        geometry_kind: endpointsOnly ? 'server_simplified' : 'sampled_unverified' } } };
}
