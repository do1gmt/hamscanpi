// Selbsttest: die Zeitfunktionen aus web/app.js (tfmt, Zeitpläne umrechnen) ohne Browser ausführen.
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const a = src.indexOf('  // ---------- Zeitangaben: UTC oder Ortszeit'), b = src.indexOf('  function fmtRel(s)');
const z0 = src.indexOf('  function zoneAt(d, zone)'), z1 = src.indexOf('  let ZONE = zoneAt');
const code = src.slice(a, b) + src.slice(z0, z1) + '\nreturn { TZ, tfmt, tday, tzLabel, schedShown, utcHM, setSettings: s => { settings = s; } };';
const window = { I18N: { lang: 'de' } }, navigator = { language: 'de-DE' }, document = { querySelectorAll: () => [], getElementById: () => null };
let settings = null;
const api = new Function('window', 'I18N', 'navigator', 'document', 'settings_', `let settings = settings_; ${code}`)(window, window.I18N, navigator, document, null);
const d = new Date(Date.UTC(2026, 10, 14, 18, 5, 9));           // Sa 14.11.2026 18:05:09 UTC
const out = [];
out.push(api.tfmt(d, { date: false }), api.tfmt(d, { date: true, sec: true }), api.tfmt(d.getTime() / 1000, { date: true, year: true }));
api.TZ.mode = 'local'; api.TZ.zone = 'Europe/Berlin';
out.push(api.tfmt(d, { date: false }), api.tzLabel(new Date(Date.UTC(2026, 6, 1, 12))), api.tday(d));
api.setSettings({ pi_tzname: 'Europe/Berlin' });
api.TZ.mode = 'utc'; api.TZ.zone = 'UTC';
const sh = api.schedShown({ days: [4], from: '19:00', to: '19:30', tz: 'local' });          // Fr 19:00 MEZ → 18:00 UTC
const sh2 = api.schedShown({ days: [0], from: '00:30', to: '02:00', tz: 'local' });         // Mo 00:30 MEZ → So 23:30 UTC
out.push(JSON.stringify(sh), JSON.stringify(sh2), api.utcHM('1300'));
api.TZ.mode = 'local'; api.TZ.zone = 'Europe/Berlin';
out.push(JSON.stringify(api.schedShown({ days: [6], from: '23:30', to: '01:00', tz: 'utc' })), api.utcHM('1300'));
console.log(out.join('\n'));
