export const replayMarkup = `
  <main>
    <header class="masthead">
      <div>
        <p class="mode" data-copy="mode">Synthetic archive</p>
        <h1 data-copy="map_title">Ride replay</h1>
        <p class="subtitle" data-copy="intro">Explore saved points and compare two rides across months.</p>
      </div>
      <button id="menu" type="button" hidden>☰</button>
      <a id="language" class="language" href="?lang=zh-Hans" lang="zh-Hans">简体中文</a>
    </header>
    <p id="startup" role="status" data-copy="loading">Reading…</p>
    <div id="archive-controls"></div>
    <div class="workspace" id="workspace" hidden>
      <section class="panel library" aria-labelledby="library-heading">
        <div class="panel-heading">
          <h2 id="library-heading" data-copy="rides">Rides</h2>
          <button id="compare" type="button" aria-pressed="false" data-copy="compareBoth">Compare both</button>
        </div>
        <div id="rides" class="rides"></div>
        <nav id="pages" aria-label="Pages" hidden>
          <button id="previous" type="button"></button>
          <span id="page"></span>
          <button id="next" type="button"></button>
        </nav>
        <p class="note" id="archive-id"></p>
      </section>
      <section class="panel stage" aria-labelledby="map-heading">
        <div class="panel-heading stage-heading">
          <div>
            <h2 id="map-heading" data-copy="map">Saved routes</h2>
            <p id="active" class="active-label" aria-live="polite"></p>
          </div>
          <div class="stage-actions">
            <button id="more-points" type="button" hidden></button>
            <button id="fit" type="button" data-copy="fit">Fit routes</button>
          </div>
        </div>
        <div class="viewport" id="viewport">
          <p id="map-message" data-copy="loading">Reading…</p>
        </div>
        <div class="playback">
          <div id="transport" class="transport" hidden>
            <button id="play" type="button"></button>
            <div class="scrubber">
              <div class="scrubber-label">
                <span id="timeline-label" data-copy="timeline">Saved points</span>
                <span id="position"></span>
              </div>
              <input id="timeline" type="range" min="0" max="0" value="0" step="1" aria-labelledby="timeline-label" aria-describedby="active">
            </div>
          </div>
          <p id="sample" class="sample" hidden></p>
          <p id="quality" class="note"></p>
        </div>
      </section>
      <section class="panel statistics" aria-labelledby="statistics-heading">
        <div class="panel-heading">
          <h2 id="statistics-heading" data-copy="totals">Whole-ride statistics</h2>
        </div>
        <table aria-labelledby="statistics-heading">
          <thead>
            <tr>
              <th scope="col" data-copy="ride_metric">Metric</th>
              <th scope="col" data-copy="active_route">Current ride</th>
              <th scope="col" class="selected" id="selected-heading" data-copy="selected" hidden>Selected rides</th>
            </tr>
          </thead>
          <tbody id="metrics"></tbody>
        </table>
      </section>
    </div>
    <dialog id="picker" aria-labelledby="picker-title">
      <h2 id="picker-title" data-copy="choose_route">Choose a ride at this spot</h2>
      <div id="choices"></div>
      <button id="cancel" type="button" data-copy="cancel">Cancel</button>
    </dialog>
    <template id="ride-template">
      <button class="ride" type="button" aria-pressed="false">
        <span class="marker" aria-hidden="true"></span>
        <span class="ride-body">
          <strong></strong>
          <span class="ride-time"></span>
        </span>
        <span class="ride-status" aria-hidden="true"></span>
      </button>
    </template>
  </main>
`;
