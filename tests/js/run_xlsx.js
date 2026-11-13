// CLI wrapper around docs/assets/js/load-profile-core.js for the pytest suite.
// usage: node run_xlsx.js <file.xlsx> '<json options>'
'use strict';
const fs = require('fs');
const path = require('path');
const core = require(path.join(__dirname, '..', '..', 'docs', 'assets', 'js',
  'load-profile-core.js'));

(async () => {
  const buf = fs.readFileSync(process.argv[2]);
  const opts = JSON.parse(process.argv[3] || '{}');
  const { table, detected } = await core.loadXlsx(
    buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const out = {
    columns: table.columns.map((c) => ({ name: c.name, unit: c.unit })),
    detected,
    rows: table.timestamps.length,
  };
  if (detected.consumption >= 0) {
    const wallbox = opts.removeWallbox === false ? [] : detected.wattpilot;
    const res = core.buildProfile(table, {
      consumptionColumn: detected.consumption,
      wallboxColumns: wallbox,
      smooth: Boolean(opts.smooth),
      smoothThresholdW: 1200,
      smoothWindow: 2,
    });
    out.stats = res.stats;
    out.profile = res.profile;
    out.csv = core.toCsv(res.profile);
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => {
  process.stdout.write(JSON.stringify({ error: e.message }));
});
