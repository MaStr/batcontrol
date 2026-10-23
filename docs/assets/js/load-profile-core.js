/*
 * Load profile generator - calculation core.
 *
 * Turns a Fronius Solar.web "detailed" XLSX export into a batcontrol load
 * profile (month, weekday, hour, energy in Wh). Pure functions, no DOM, so the
 * same file runs in the browser and under node (tests).
 *
 * Mirrors the idea of PR #124 (SolarWebExportProcessor), with two fixes:
 * hours without data are treated as gaps (not as 0 Wh) and are filled from
 * the remaining data.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.LoadProfileCore = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var HOURS_PER_YEAR = 8760;
  var PROFILE_ROWS = 12 * 7 * 24;
  var MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /* ------------------------------------------------------------------ */
  /* ZIP / XLSX reading (no external library)                           */
  /* ------------------------------------------------------------------ */

  function readZipEntries(buf) {
    var bytes = new Uint8Array(buf);
    var view = new DataView(buf);
    var eocd = -1;
    for (var i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
      if (view.getUint32(i, true) === 0x06054b50) {
        eocd = i;
        break;
      }
    }
    if (eocd < 0) {
      throw new Error('Not a valid XLSX file (ZIP directory not found).');
    }
    var count = view.getUint16(eocd + 10, true);
    var pos = view.getUint32(eocd + 16, true);
    var entries = {};
    var decoder = new TextDecoder('utf-8');
    for (var n = 0; n < count; n++) {
      if (view.getUint32(pos, true) !== 0x02014b50) {
        throw new Error('Corrupt ZIP directory.');
      }
      var method = view.getUint16(pos + 10, true);
      var compSize = view.getUint32(pos + 20, true);
      var nameLen = view.getUint16(pos + 28, true);
      var extraLen = view.getUint16(pos + 30, true);
      var commentLen = view.getUint16(pos + 32, true);
      var localOffset = view.getUint32(pos + 42, true);
      var name = decoder.decode(bytes.subarray(pos + 46, pos + 46 + nameLen));
      var localNameLen = view.getUint16(localOffset + 26, true);
      var localExtraLen = view.getUint16(localOffset + 28, true);
      var dataStart = localOffset + 30 + localNameLen + localExtraLen;
      entries[name] = {
        method: method,
        data: bytes.subarray(dataStart, dataStart + compSize)
      };
      pos += 46 + nameLen + extraLen + commentLen;
    }
    return entries;
  }

  async function entryText(entry) {
    var raw = entry.data;
    if (entry.method === 8) {
      var stream = new Blob([raw]).stream().pipeThrough(
        new DecompressionStream('deflate-raw'));
      raw = new Uint8Array(await new Response(stream).arrayBuffer());
    } else if (entry.method !== 0) {
      throw new Error('Unsupported ZIP compression method ' + entry.method + '.');
    }
    return new TextDecoder('utf-8').decode(raw);
  }

  function xmlUnescape(s) {
    return s.replace(/&(#x[0-9a-fA-F]+|#\d+|amp|lt|gt|quot|apos);/g,
      function (m, g) {
        switch (g) {
          case 'amp': return '&';
          case 'lt': return '<';
          case 'gt': return '>';
          case 'quot': return '"';
          case 'apos': return "'";
          default:
            return String.fromCodePoint(g[1] === 'x'
              ? parseInt(g.slice(2), 16) : parseInt(g.slice(1), 10));
        }
      });
  }

  function textOf(xml) {
    var out = '';
    var re = /<t(?:\s[^>]*)?>([\s\S]*?)<\/t>/g;
    var m;
    while ((m = re.exec(xml)) !== null) {
      out += xmlUnescape(m[1]);
    }
    return out;
  }

  function parseSharedStrings(xml) {
    var list = [];
    var re = /<si(?:\s[^>]*)?>([\s\S]*?)<\/si>/g;
    var m;
    while ((m = re.exec(xml)) !== null) {
      list.push(textOf(m[1]));
    }
    return list;
  }

  function columnIndex(ref) {
    var letters = ref.replace(/[0-9]/g, '');
    var idx = 0;
    for (var i = 0; i < letters.length; i++) {
      idx = idx * 26 + (letters.charCodeAt(i) - 64);
    }
    return idx - 1;
  }

  function parseSheet(xml, shared) {
    var rows = [];
    var rowRe = /<row(?:\s[^>]*)?>([\s\S]*?)<\/row>/g;
    var cellRe = /<c\s([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g;
    var rm;
    while ((rm = rowRe.exec(xml)) !== null) {
      var row = [];
      var cm;
      cellRe.lastIndex = 0;
      var body = rm[1];
      while ((cm = cellRe.exec(body)) !== null) {
        var attrs = cm[1];
        var inner = cm[2] || '';
        var ref = /\br="([A-Z]+)\d+"/.exec(attrs);
        var type = /\bt="([^"]*)"/.exec(attrs);
        if (!ref) {
          continue;
        }
        var value = null;
        var v = /<v>([\s\S]*?)<\/v>/.exec(inner);
        if (type && type[1] === 's' && v) {
          value = shared[parseInt(v[1], 10)];
        } else if (type && type[1] === 'inlineStr') {
          value = textOf(inner);
        } else if (type && (type[1] === 'str' || type[1] === 'e') && v) {
          value = xmlUnescape(v[1]);
        } else if (v) {
          value = parseFloat(v[1]);
        }
        row[columnIndex(ref[1])] = value;
      }
      rows.push(row);
    }
    return rows;
  }

  async function readXlsxRows(buf) {
    var entries = readZipEntries(buf);
    var sheetPath = 'xl/worksheets/sheet1.xml';
    if (entries['xl/workbook.xml'] && entries['xl/_rels/workbook.xml.rels']) {
      var wb = await entryText(entries['xl/workbook.xml']);
      var rels = await entryText(entries['xl/_rels/workbook.xml.rels']);
      var sheet = /<sheet\s[^>]*?r:id="([^"]+)"/.exec(wb);
      if (sheet) {
        var relRe = /<Relationship\s[^>]*>/g;
        var rel;
        while ((rel = relRe.exec(rels)) !== null) {
          var id = /\bId="([^"]+)"/.exec(rel[0]);
          var target = /\bTarget="([^"]+)"/.exec(rel[0]);
          if (id && target && id[1] === sheet[1]) {
            sheetPath = target[1].replace(/^\//, '');
            if (sheetPath.indexOf('xl/') !== 0) {
              sheetPath = 'xl/' + sheetPath;
            }
          }
        }
      }
    }
    if (!entries[sheetPath]) {
      throw new Error('No worksheet found in the XLSX file.');
    }
    var shared = entries['xl/sharedStrings.xml']
      ? parseSharedStrings(await entryText(entries['xl/sharedStrings.xml'])) : [];
    return parseSheet(await entryText(entries[sheetPath]), shared);
  }

  /* ------------------------------------------------------------------ */
  /* Table extraction                                                   */
  /* ------------------------------------------------------------------ */

  // Returns naive local time in ms (treated as UTC), or null.
  function parseTimestamp(v) {
    if (typeof v === 'number') {
      if (v < 20000 || v > 80000) {
        return null;
      }
      return Math.round((v - 25569) * 1440) * 60000;
    }
    if (typeof v !== 'string') {
      return null;
    }
    var s = v.trim();
    var m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(s);
    if (m) {
      return Date.UTC(+m[3], +m[2] - 1, +m[1], +m[4], +m[5], 0);
    }
    m = /^(\d{4})-(\d{2})-(\d{2})[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(s);
    if (m) {
      return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0);
    }
    return null;
  }

  function cleanUnit(u) {
    return String(u == null ? '' : u).replace(/[\[\]\s]/g, '');
  }

  function extractTable(rows) {
    var start = -1;
    for (var i = 0; i < rows.length; i++) {
      if (parseTimestamp(rows[i][0]) !== null) {
        start = i;
        break;
      }
    }
    if (start < 0) {
      throw new Error('No timestamps found in the first column. Is this a ' +
        'Solar.web export with date and time in column A?');
    }
    var nameRow = start >= 2 ? rows[start - 2] : (rows[start - 1] || []);
    var unitRow = start >= 2 ? rows[start - 1] : [];
    var width = 0;
    for (var r = 0; r < rows.length; r++) {
      width = Math.max(width, rows[r].length);
    }
    var timestamps = [];
    var columns = [];
    for (var c = 1; c < width; c++) {
      columns.push({
        name: String(nameRow[c] == null ? '' : nameRow[c]).trim(),
        unit: cleanUnit(unitRow[c]),
        values: []
      });
    }
    for (var k = start; k < rows.length; k++) {
      var ts = parseTimestamp(rows[k][0]);
      if (ts === null) {
        continue;
      }
      timestamps.push(ts);
      for (var j = 0; j < columns.length; j++) {
        var cell = rows[k][j + 1];
        columns[j].values.push(typeof cell === 'number' && isFinite(cell) ? cell : NaN);
      }
    }
    return { timestamps: timestamps, columns: columns };
  }

  function minutesBetween(timestamps) {
    var best = Infinity;
    for (var i = 1; i < timestamps.length; i++) {
      var d = (timestamps[i] - timestamps[i - 1]) / 60000;
      if (d > 0 && d < best) {
        best = d;
      }
    }
    return best;
  }

  function unitFactor(unit, intervalMin) {
    switch (unit) {
      case 'Wh': case '': return 1;
      case 'kWh': return 1000;
      case 'W': return intervalMin / 60;
      case 'kW': return 1000 * intervalMin / 60;
      default: return null;
    }
  }

  // Finds the consumption column and the wallbox candidates.
  function detectColumns(table) {
    var consumption = -1;
    var wattpilot = [];
    var other = [];
    table.columns.forEach(function (col, idx) {
      var isEnergy = unitFactor(col.unit, 60) !== null;
      if (!isEnergy || col.name === '') {
        return;
      }
      if (/^Verbrauch$/i.test(col.name) && consumption < 0) {
        consumption = idx;
      } else if (/Wattpilot/i.test(col.name) && /Energie/i.test(col.name)) {
        wattpilot.push(idx);
      } else {
        other.push(idx);
      }
    });
    if (consumption < 0) {
      table.columns.forEach(function (col, idx) {
        if (consumption < 0 && /^Verbrauch/i.test(col.name) &&
            unitFactor(col.unit, 60) !== null) {
          consumption = idx;
        }
      });
    }
    return { consumption: consumption, wattpilot: wattpilot, other: other };
  }

  /* ------------------------------------------------------------------ */
  /* Profile calculation                                                */
  /* ------------------------------------------------------------------ */

  function mean(arr) {
    if (!arr.length) {
      return NaN;
    }
    var s = 0;
    for (var i = 0; i < arr.length; i++) {
      s += arr[i];
    }
    return s / arr.length;
  }

  function meanRange(arr, from, to) {
    var s = 0;
    var n = 0;
    for (var i = Math.max(0, from); i <= Math.min(arr.length - 1, to); i++) {
      if (isFinite(arr[i])) {
        s += arr[i];
        n++;
      }
    }
    return n ? s / n : NaN;
  }

  /**
   * opts: {
   *   consumptionColumn: index, wallboxColumns: [index] (may be empty),
   *   smooth: bool, smoothThresholdW: 1200, smoothWindow: 2
   * }
   */
  function buildProfile(table, opts) {
    var n = table.timestamps.length;
    var interval = minutesBetween(table.timestamps);
    if (!isFinite(interval) || interval > 60) {
      throw new Error('The data resolution is coarser than 1 hour (smallest ' +
        'step: ' + interval + ' min). Export the detailed view from Solar.web.');
    }
    var cons = table.columns[opts.consumptionColumn];
    var consFactor = unitFactor(cons.unit, interval);
    var wbCols = (opts.wallboxColumns || []).map(function (i) {
      return { col: table.columns[i], factor: unitFactor(table.columns[i].unit, interval) };
    });

    var total = new Array(n);
    var wallbox = new Array(n);
    var base = new Array(n);
    var negatives = 0;
    var wallboxWh = 0;
    for (var i = 0; i < n; i++) {
      var v = cons.values[i] * consFactor;
      var wb = 0;
      for (var w = 0; w < wbCols.length; w++) {
        var x = wbCols[w].col.values[i];
        if (isFinite(x)) {
          wb += x * wbCols[w].factor;
        }
      }
      total[i] = v;
      wallbox[i] = wb;
      if (isFinite(v)) {
        var b = v - wb;
        if (b < 0) {
          negatives++;
          b = 0;
        }
        base[i] = b;
        wallboxWh += Math.min(wb, v);
      } else {
        base[i] = NaN;
      }
    }

    var smoothed = base.slice();
    var smoothedPoints = 0;
    if (opts.smooth && wbCols.length) {
      var thr = (opts.smoothThresholdW || 1200) * interval / 60;
      var win = Math.max(1, opts.smoothWindow || 2);
      for (var s = 1; s < n; s++) {
        if (Math.abs(wallbox[s] - wallbox[s - 1]) > thr) {
          var before = meanRange(base, s - 1 - win, s - 2);
          var after = meanRange(base, s + 1, s + win);
          if (isFinite(before) && s - 1 >= 0) {
            smoothed[s - 1] = before;
            smoothedPoints++;
          }
          if (isFinite(after)) {
            smoothed[s] = after;
            smoothedPoints++;
          }
        }
      }
    }

    // Hourly sums. Hours with too few samples are gaps, not zeros.
    var expected = Math.max(1, Math.round(60 / interval));
    var minCount = Math.ceil(expected * 0.75);
    var hours = {};
    var totalWhAll = 0;
    var validRows = 0;
    for (var r = 0; r < n; r++) {
      if (!isFinite(smoothed[r])) {
        continue;
      }
      validRows++;
      totalWhAll += total[r];
      var key = Math.floor(table.timestamps[r] / 3600000);
      var h = hours[key] || (hours[key] = { sum: 0, count: 0 });
      h.sum += smoothed[r];
      h.count++;
    }
    var cells = [];
    for (var ci = 0; ci < PROFILE_ROWS; ci++) {
      cells.push([]);
    }
    var hourKeys = Object.keys(hours);
    var usedHours = 0;
    var usedWh = 0;
    var monthsSeen = {};
    var spikes = 0;
    var maxHourWh = 0;
    hourKeys.forEach(function (k) {
      var h = hours[k];
      if (h.count < minCount) {
        return;
      }
      var wh = h.sum * expected / h.count;
      var d = new Date(+k * 3600000);
      var month = d.getUTCMonth();
      var weekday = (d.getUTCDay() + 6) % 7;
      cells[(month * 7 + weekday) * 24 + d.getUTCHours()].push(wh);
      monthsSeen[month] = true;
      usedHours++;
      usedWh += wh;
      if (wh > maxHourWh) {
        maxHourWh = wh;
      }
      if (wh > 30000) {
        spikes++;
      }
    });
    if (!usedHours) {
      throw new Error('No usable consumption data found in the file.');
    }

    // Average per cell, then fill gaps in three tiers.
    var profile = new Array(PROFILE_ROWS);
    var present = 0;
    for (var p = 0; p < PROFILE_ROWS; p++) {
      profile[p] = cells[p].length ? mean(cells[p]) : NaN;
      if (cells[p].length) {
        present++;
      }
    }
    var filledTier = [0, 0, 0];
    function wdHourAvg(wd, hr) {
      var vals = [];
      for (var m = 0; m < 12; m++) {
        var val = profile[(m * 7 + wd) * 24 + hr];
        if (isFinite(val)) {
          vals.push(val);
        }
      }
      return mean(vals);
    }
    function hourAvg(hr) {
      var vals = [];
      for (var wd = 0; wd < 7; wd++) {
        var val = wdHourAvg(wd, hr);
        if (isFinite(val)) {
          vals.push(val);
        }
      }
      return mean(vals);
    }
    var allAvg = mean(profile.filter(isFinite));
    var filled = profile.slice();
    for (var q = 0; q < PROFILE_ROWS; q++) {
      if (isFinite(filled[q])) {
        continue;
      }
      var hr = q % 24;
      var wd2 = Math.floor(q / 24) % 7;
      var val1 = wdHourAvg(wd2, hr);
      if (isFinite(val1)) {
        filled[q] = val1;
        filledTier[0]++;
        continue;
      }
      var val2 = hourAvg(hr);
      if (isFinite(val2)) {
        filled[q] = val2;
        filledTier[1]++;
      } else {
        filled[q] = allAvg;
        filledTier[2]++;
      }
    }

    var sumProfile = 0;
    for (var t = 0; t < PROFILE_ROWS; t++) {
      sumProfile += filled[t];
    }
    var annualKwh = sumProfile * HOURS_PER_YEAR / PROFILE_ROWS / 1000;

    var first = table.timestamps[0];
    var last = table.timestamps[n - 1];
    var spanHours = Math.max(1, Math.round((last - first + interval * 60000) / 3600000));
    var months = [];
    for (var mm = 0; mm < 12; mm++) {
      if (monthsSeen[mm]) {
        months.push(mm + 1);
      }
    }
    var missingMonths = [];
    for (var mi = 1; mi <= 12; mi++) {
      if (months.indexOf(mi) < 0) {
        missingMonths.push(mi);
      }
    }
    var stats = {
      rows: n,
      validRows: validRows,
      intervalMin: interval,
      firstTs: first,
      lastTs: last,
      days: (last - first) / 86400000,
      spanHours: spanHours,
      usedHours: usedHours,
      gapHours: Math.max(0, spanHours - usedHours),
      gapShare: Math.max(0, 1 - usedHours / spanHours),
      months: months,
      missingMonths: missingMonths,
      cellsFromData: present,
      filledTier: filledTier,
      negativeRows: negatives,
      smoothedPoints: smoothedPoints,
      spikeHours: spikes,
      maxHourWh: maxHourWh,
      totalConsumptionKwh: totalWhAll / 1000,
      wallboxKwh: wallboxWh / 1000,
      wallboxShare: totalWhAll > 0 ? wallboxWh / totalWhAll : 0,
      wallboxRemoved: wbCols.length > 0,
      annualKwh: annualKwh,
      rawExtrapolationKwh: usedHours ? usedWh / usedHours * HOURS_PER_YEAR / 1000 : 0
    };
    stats.suggestedAnnualKwh = suggestAnnual(annualKwh);
    stats.checks = plausibilityChecks(stats);
    return { profile: filled, stats: stats };
  }

  // Round up generously: +5 % reserve, then to the next 100 kWh.
  function suggestAnnual(kwh) {
    return Math.ceil(kwh * 1.05 / 100) * 100;
  }

  function plausibilityChecks(s) {
    var checks = [];
    function add(level, text) {
      checks.push({ level: level, text: text });
    }
    if (s.days >= 360) {
      add('ok', 'Coverage: ' + Math.round(s.days) + ' days, a full year.');
    } else if (s.days >= 180) {
      add('warn', 'Coverage: only ' + Math.round(s.days) + ' days. Seasonal ' +
        'effects (heating, lighting) are missing for the other months.');
    } else {
      add('bad', 'Coverage: only ' + Math.round(s.days) + ' days. The yearly ' +
        'value will be unreliable; use at least 12 months if possible.');
    }
    if (s.missingMonths.length) {
      add('warn', 'No data for ' + s.missingMonths.map(function (m) {
        return MONTH_NAMES[m - 1];
      }).join(', ') + '. These months are filled with the average of the months ' +
        'you have.');
    }
    var gapPct = s.gapShare * 100;
    if (gapPct < 1) {
      add('ok', 'Data gaps: ' + gapPct.toFixed(1) + ' % of the hours.');
    } else if (gapPct < 10) {
      add('warn', 'Data gaps: ' + gapPct.toFixed(1) + ' % of the hours are ' +
        'missing and are filled from similar hours.');
    } else {
      add('bad', 'Data gaps: ' + gapPct.toFixed(1) + ' % of the hours are ' +
        'missing. Check the export range.');
    }
    if (s.filledTier[0] + s.filledTier[1] + s.filledTier[2] > 0) {
      add('info', (s.filledTier[0] + s.filledTier[1] + s.filledTier[2]) +
        ' of 2016 profile cells had no data and were filled with averages.');
    }
    if (s.negativeRows > 0) {
      add('warn', s.negativeRows + ' interval(s) had a wallbox value above the ' +
        'total consumption and were set to 0.');
    }
    if (s.spikeHours > 0) {
      add('warn', s.spikeHours + ' hour(s) above 30 kWh, check for measurement ' +
        'errors.');
    }
    if (s.wallboxRemoved) {
      add('info', 'Wallbox removed: ' + s.wallboxKwh.toFixed(0) + ' kWh (' +
        (s.wallboxShare * 100).toFixed(0) + ' % of the measured consumption).');
      if (s.wallboxShare > 0.5) {
        add('warn', 'More than half of the consumption is wallbox. Please check ' +
          'the selected columns.');
      }
    }
    if (s.annualKwh < 1000 || s.annualKwh > 20000) {
      add('warn', 'A yearly consumption of ' + Math.round(s.annualKwh) +
        ' kWh is unusual for a household. Check units and columns.');
    } else {
      add('ok', 'A yearly consumption of ' + Math.round(s.annualKwh) +
        ' kWh is within the usual range of a household.');
    }
    var dev = s.rawExtrapolationKwh > 0
      ? Math.abs(s.annualKwh - s.rawExtrapolationKwh) / s.rawExtrapolationKwh : 0;
    if (dev > 0.1) {
      add('warn', 'The profile (' + Math.round(s.annualKwh) + ' kWh) deviates ' +
        (dev * 100).toFixed(0) + ' % from the simple extrapolation of the raw ' +
        'data (' + Math.round(s.rawExtrapolationKwh) + ' kWh), typically caused ' +
        'by uneven coverage of the months.');
    }
    return checks;
  }

  function toCsv(profile) {
    var lines = ['month,weekday,hour,energy'];
    for (var m = 0; m < 12; m++) {
      for (var d = 0; d < 7; d++) {
        for (var h = 0; h < 24; h++) {
          lines.push((m + 1) + ',' + d + ',' + h + ',' +
            profile[(m * 7 + d) * 24 + h].toFixed(2));
        }
      }
    }
    return lines.join('\n') + '\n';
  }

  async function processXlsx(buf) {
    var table = extractTable(await readXlsxRows(buf));
    var detected = detectColumns(table);
    return { table: table, detected: detected };
  }

  return {
    PROFILE_ROWS: PROFILE_ROWS,
    MONTH_NAMES: MONTH_NAMES,
    readXlsxRows: readXlsxRows,
    extractTable: extractTable,
    detectColumns: detectColumns,
    buildProfile: buildProfile,
    suggestAnnual: suggestAnnual,
    toCsv: toCsv,
    loadXlsx: processXlsx
  };
}));
