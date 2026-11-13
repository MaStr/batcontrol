'use strict';
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const core = require(path.join(__dirname, '..', '..', 'docs', 'assets', 'js',
  'load-profile-core.js'));

const START = Date.UTC(2023, 0, 2, 0, 0); // Monday
const STEP = 5 * 60000;

function makeTable(rows, consumption, wallbox) {
  const timestamps = [];
  for (let i = 0; i < rows; i++) {
    timestamps.push(START + i * STEP);
  }
  const columns = [{ name: 'Verbrauch', unit: 'Wh', values: consumption }];
  if (wallbox) {
    columns.push({ name: 'Energie Wattpilot', unit: 'Wh', values: wallbox });
  }
  return { timestamps, columns };
}

test('suggestAnnual rounds up with reserve to the next 100 kWh', () => {
  assert.strictEqual(core.suggestAnnual(4183), 4400);
  assert.strictEqual(core.suggestAnnual(4000), 4200);
  assert.strictEqual(core.suggestAnnual(1), 100);
});

test('hours without data are gaps, not zeros', () => {
  // two days, constant 100 Wh per 5 min = 1200 Wh per hour, hours 0-11 of day 2 missing
  const rows = 2 * 288;
  const cons = new Array(rows).fill(100);
  for (let i = 288; i < 288 + 12 * 12; i++) {
    cons[i] = NaN;
  }
  const table = makeTable(rows, cons);
  const res = core.buildProfile(table, { consumptionColumn: 0, wallboxColumns: [] });
  const jan = (m, wd, h) => res.profile[(m * 7 + wd) * 24 + h];
  assert.ok(Math.abs(jan(0, 1, 3) - 1200) < 1e-6, 'filled from the other data');
  assert.ok(Math.abs(jan(0, 0, 3) - 1200) < 1e-6);
  assert.strictEqual(res.stats.gapHours, 12);
});

test('hours with few missing samples are scaled up, many missing are dropped', () => {
  const rows = 288;
  const cons = new Array(rows).fill(100);
  cons[1] = NaN; // hour 0: 11 of 12 samples -> scaled to 1200
  for (let i = 12; i < 12 + 6; i++) {
    cons[i] = NaN; // hour 1: only 6 of 12 samples -> dropped
  }
  const res = core.buildProfile(makeTable(rows, cons),
    { consumptionColumn: 0, wallboxColumns: [] });
  assert.ok(Math.abs(res.profile[0] - 1200) < 1e-6);
  assert.strictEqual(res.stats.usedHours, 23);
});

test('wallbox is subtracted and clamped at zero', () => {
  const rows = 288;
  const cons = new Array(rows).fill(150);
  const wb = new Array(rows).fill(0);
  for (let i = 0; i < 12; i++) {
    wb[i] = 50; // hour 0: 50 of 150 Wh
  }
  wb[12] = 200; // above the consumption -> negative, clamped
  const res = core.buildProfile(makeTable(rows, cons, wb),
    { consumptionColumn: 0, wallboxColumns: [1] });
  assert.ok(Math.abs(res.profile[0] - 12 * 100) < 1e-6);
  assert.strictEqual(res.stats.negativeRows, 1);
  assert.ok(res.stats.wallboxRemoved);
});

test('smoothing reduces artefacts at wallbox start and stop', () => {
  // base load 100 Wh/5min; each day the wallbox charges 583 Wh/5min for 2 h and
  // the meter shows a dip of the total at the ramp edges
  const days = 30;
  const rows = days * 288;
  const cons = new Array(rows).fill(100);
  const wb = new Array(rows).fill(0);
  for (let d = 0; d < days; d++) {
    const s = d * 288 + 18 * 12;
    for (let i = 0; i < 24; i++) {
      wb[s + i] = 583;
      cons[s + i] = 100 + 583;
    }
    cons[s] = 100 + 583 - 400; // artefact: wallbox counted before the total
    cons[s + 24] = 100 + 300; // artefact: total lags behind after stop
  }
  const trueAnnual = 100 * 12 * 8760 / 1000;
  const run = (smooth) => core.buildProfile(makeTable(rows, cons, wb), {
    consumptionColumn: 0, wallboxColumns: [1], smooth,
    smoothThresholdW: 1200, smoothWindow: 2,
  }).stats;
  const off = run(false);
  const on = run(true);
  assert.ok(on.smoothedPoints > 0);
  const errOff = Math.abs(off.rawExtrapolationKwh - trueAnnual);
  const errOn = Math.abs(on.rawExtrapolationKwh - trueAnnual);
  assert.ok(errOn < errOff, `smoothing should reduce the error (${errOn} vs ${errOff})`);
  assert.ok(errOn / trueAnnual < 0.005);
});

test('resolution coarser than one hour is rejected', () => {
  const table = {
    timestamps: [START, START + 2 * 3600000, START + 4 * 3600000],
    columns: [{ name: 'Verbrauch', unit: 'Wh', values: [1, 2, 3] }],
  };
  assert.throws(() => core.buildProfile(table,
    { consumptionColumn: 0, wallboxColumns: [] }), /coarser than 1 hour/);
});

test('power units are converted to energy', () => {
  const rows = 288;
  const table = makeTable(rows, new Array(rows).fill(1.2)); // 1.2 kW
  table.columns[0].unit = 'kW';
  const res = core.buildProfile(table, { consumptionColumn: 0, wallboxColumns: [] });
  assert.ok(Math.abs(res.profile[0] - 1200) < 1e-6);
});

test('csv output has the batcontrol format', () => {
  const rows = 288;
  const res = core.buildProfile(makeTable(rows, new Array(rows).fill(100)),
    { consumptionColumn: 0, wallboxColumns: [] });
  const lines = core.toCsv(res.profile).trim().split('\n');
  assert.strictEqual(lines[0], 'month,weekday,hour,energy');
  assert.strictEqual(lines.length, 2017);
  assert.strictEqual(lines[1], '1,0,0,1200.00');
  assert.ok(lines[2016].startsWith('12,6,23,'));
});
