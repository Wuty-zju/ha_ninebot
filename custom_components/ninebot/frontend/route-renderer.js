/** Lifecycle adapter for the pinned Leaflet 1.9.4 Canvas implementation. */
export function createRouteRenderer(L) {
  const Canvas = L.Canvas.extend({
    _redraw() {
      // Leaflet also calls _redraw synchronously from _updatePaths. Cancel the
      // queued frame before upstream clears its handle, or removal cannot find it.
      L.Util.cancelAnimFrame(this._redrawRequest);
      return L.Canvas.prototype._redraw.call(this);
    },
  });
  return new Canvas();
}
