// SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0 (see LICENSE)
// Run a Pine v6 file with PineTS on an OHLC CSV and write the named plots as CSV (one column per plot).
//   node run_pine.mjs <file.pine> <bars.csv> <out.csv> [--input 'Title=value' ...] [plot name ...]
// Input values: true/false, numbers, or text (keyed by the input's title).
// bars.csv columns: time (unix ms), open, high, low, close[, volume]. Without plot names, every plot is written.
import { readFileSync, writeFileSync } from 'node:fs';
import { PineTS, Indicator } from 'pinets';

const [, , pineFile, barsFile, outFile, ...rest] = process.argv;
const inputs = {};
const names = [];
for (let i = 0; i < rest.length; i++) {
  if (rest[i] === '--input') {
    const [k, ...v] = rest[++i].split('=');
    const t = v.join('=');
    inputs[k] = t === 'true' ? true : t === 'false' ? false : t !== '' && !Number.isNaN(Number(t)) ? Number(t) : t;
  } else names.push(rest[i]);
}
const lines = readFileSync(barsFile, 'utf8').trim().split('\n');
const head = lines[0].split(',');
const col = (n) => head.indexOf(n);
const candles = lines.slice(1).map((ln) => {
  const v = ln.split(',');
  return {
    openTime: Number(v[col('time')]), open: Number(v[col('open')]), high: Number(v[col('high')]),
    low: Number(v[col('low')]), close: Number(v[col('close')]), volume: col('volume') >= 0 ? Number(v[col('volume')]) : 0,
  };
});
const pineTS = new PineTS(candles);
const { plots } = await pineTS.run(new Indicator(readFileSync(pineFile, 'utf8'), inputs));
const keys = names.length ? names : Object.keys(plots);
for (const k of keys) if (!plots[k]) { console.error(`no plot "${k}"; plots: ${Object.keys(plots).join(' | ')}`); process.exit(2); }
const rows = ['bar,' + keys.map((k) => `"${k}"`).join(',')];
for (let i = 0; i < candles.length; i++) {
  rows.push(i + ',' + keys.map((k) => { const p = plots[k].data[i]; const v = p && typeof p === 'object' ? p.value : p; return v === null || v === undefined || Number.isNaN(v) ? '' : v; }).join(','));
}
writeFileSync(outFile, rows.join('\n') + '\n');
console.log(`${candles.length} bars, plots: ${keys.join(' | ')}`);
