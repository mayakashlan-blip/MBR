// Harness that executes Shannon's calc-bundle.js VERBATIM to compute
// supplies savings for one medspa/month. Runs identically in headless
// Chromium (production, via Playwright) and Node (local verification).
//
// The dispatch below is a faithful copy of calcVendor()/makePeriod() and
// getRebates() from her dashboard.html — thin, stable glue. All pricing
// math lives in the bundle, which is fetched live so her updates flow
// through without another Python port drifting out of date.
//
// input: {bundle, vendorConfig, pricingEras, rebates, nameMap, medspa,
//         transactions: {vendorId: rows}, month (1-12), year}
function computeSavings(input) {
  (0, eval)(input.bundle); // defines calcGalderma/Allergan/Evolus/Merz/Revance/Generic

  var SEL_YEAR = input.year;
  var SEL_MONTH = input.month - 1; // JS Dates are 0-indexed
  var m = input.medspa;
  var NAME_MAP = input.nameMap || {};
  var pe = input.pricingEras;
  var vendorConfig = input.vendorConfig || [];

  // bounds() — verbatim from dashboard.html
  var b = {
    end:   new Date(SEL_YEAR, SEL_MONTH + 1, 0),
    moSt:  new Date(SEL_YEAR, SEL_MONTH, 1),
    m3St:  new Date(SEL_YEAR, SEL_MONTH - 2, 1),
    ytdSt: new Date(SEL_YEAR, 0, 1),
  };
  function isoDate(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') +
           '-' + String(d.getDate()).padStart(2, '0');
  }
  function pm(v) { return parseFloat((v || '').toString().replace(/[$,\s]/g, '')) || 0; }

  // makePeriod() — verbatim port (minus the byMon chart series)
  function makePeriod(vendorId, rows, id, moxieId) {
    var fn = {galderma: calcGalderma, allergan: calcAllergan, evolus: calcEvolus,
              merz: calcMerz, revance: calcRevance}[vendorId];
    function call(filter) {
      if (fn) return moxieId !== undefined ? fn(rows, id, filter, pe, moxieId)
                                           : fn(rows, id, filter, pe);
      return calcGeneric(vendorId, rows, id, filter, pe, vendorConfig);
    }
    var allR = call({});
    if (allR.spend === 0 && allR.savings === 0) return null;
    var q = {};
    q.all = {sp: allR.spend, sv: allR.savings};
    var moR = call({startDate: isoDate(b.moSt), endDate: isoDate(b.end)});
    q.mo = {sp: moR.spend, sv: moR.savings};
    var m3R = call({startDate: isoDate(b.m3St), endDate: isoDate(b.end)});
    q.m3 = {sp: m3R.spend, sv: m3R.savings};
    var ytdR = call({startDate: isoDate(b.ytdSt), endDate: isoDate(b.end)});
    q.ytd = {sp: ytdR.spend, sv: ytdR.savings};
    return q;
  }

  // calcVendor() dispatch — verbatim
  function calcVendorFor(vc) {
    var rows = (input.transactions || {})[vc.id] || [];
    switch (vc.id) {
      case 'galderma':
        return makePeriod('galderma', rows, m.mk, String(m.id));
      case 'allergan':
        if (!m.al) return null;
        return makePeriod('allergan', rows, m.al, undefined);
      case 'evolus': {
        var emap = NAME_MAP.evolus || {}, evName = '';
        Object.keys(emap).forEach(function (n) { if (emap[n] === m.id) evName = n; });
        return makePeriod('evolus', rows, evName, String(m.id));
      }
      case 'merz': {
        var mmap = NAME_MAP.merz || {}, mzName = '';
        Object.keys(mmap).forEach(function (n) { if (mmap[n] === m.id) mzName = n; });
        return makePeriod('merz', rows, mzName, String(m.id));
      }
      case 'revance':
        return makePeriod('revance', rows, m.id, undefined);
      default:
        return makePeriod(vc.id, rows, String(m.id));
    }
  }

  var vendorCalcs = {};
  var labels = {};
  vendorConfig.filter(function (vc) { return vc.active !== false; }).forEach(function (vc) {
    vendorCalcs[vc.id] = calcVendorFor(vc);
    labels[vc.id] = vc.display_name || vc.id;
  });

  function agg(period) {
    var sp = 0, sv = 0;
    Object.keys(vendorCalcs).forEach(function (k) {
      var v = vendorCalcs[k];
      if (!v) return;
      sp += (v[period] || {}).sp || 0;
      sv += (v[period] || {}).sv || 0;
    });
    return {sp: sp, sv: sv};
  }

  // getRebates() — verbatim port
  function parseRebatePeriod(str) {
    var mm = (str || '').toString().trim().match(/Q([1-4])[-\s]+(\d{4})/i);
    if (!mm) return null;
    var q = parseInt(mm[1]), yr = parseInt(mm[2]);
    return {year: yr, startMonth: (q - 1) * 3 + 1, endMonth: q * 3};
  }
  function getRebates(moxieId) {
    var rebates = input.rebates;
    if (!Array.isArray(rebates) || !rebates.length) return null;
    var rows = rebates.filter(function (r) {
      return (r['Medspa ID'] || r.medspa_id || '').toString().trim() === moxieId.toString();
    });
    if (!rows.length) return null;
    var zero = function () { return {m3: 0, ytd: 0, all: 0, hasEver: false}; };
    var t = {galderma: zero(), allergan: zero(), evolus: zero(), merz: zero()};
    var vmap = {galderma: 'Galderma', allergan: 'Allergan', evolus: 'Evolus', merz: 'Merz'};
    var selM = SEL_MONTH + 1;
    var m3StartMonth = selM - 2, m3Year = SEL_YEAR;
    if (m3StartMonth < 1) { m3StartMonth += 12; m3Year -= 1; }
    rows.forEach(function (r) {
      var qp = parseRebatePeriod(r['Rebate For Period']);
      ['galderma', 'allergan', 'evolus', 'merz'].forEach(function (v) {
        var amt = pm(r[vmap[v]] || r[v]);
        if (!amt) return;
        t[v].hasEver = true;
        t[v].all += amt;
        if (!qp) return;
        if (qp.year === SEL_YEAR) t[v].ytd += amt;
        var qBeforeEnd = qp.year < SEL_YEAR || (qp.year === SEL_YEAR && qp.startMonth <= selM);
        var qAfterStart = qp.year > m3Year || (qp.year === m3Year && qp.endMonth >= m3StartMonth);
        if (qBeforeEnd && qAfterStart) t[v].m3 += amt;
      });
    });
    return t;
  }

  var rb = getRebates(m.id) || {};
  var rebateTotals = {m3: 0, ytd: 0, all: 0};
  var rbByVendorLabel = {};
  Object.keys(rb).forEach(function (v) {
    rebateTotals.m3 += rb[v].m3; rebateTotals.ytd += rb[v].ytd; rebateTotals.all += rb[v].all;
    rbByVendorLabel[labels[v] || v] = rb[v];
  });

  var vendors = [];
  Object.keys(vendorCalcs).forEach(function (k) {
    var v = vendorCalcs[k];
    var rbV = rb[k] || {m3: 0};
    if (!v && !(rbV.m3 > 0)) return;
    vendors.push({
      id: k,
      label: labels[k] || k,
      m3_spend: v ? v.m3.sp : 0,
      m3_savings: v ? v.m3.sv : 0,
      m3_rebates: rbV.m3 || 0,
    });
  });

  return {
    mo: agg('mo'), m3: agg('m3'), ytd: agg('ytd'), all: agg('all'),
    vendors: vendors,
    rebates: rebateTotals,
  };
}
