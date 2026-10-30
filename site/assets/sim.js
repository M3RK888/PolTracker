// The forecast's simulation, rerun in the browser for the playground page.
// Same model as forecast_model.py (draw_errors, forecast_races): each simulated
// margin is the race's blended estimate plus a national miss (one per simulation),
// a regional miss (one per Census region per simulation) and the race's own miss,
// all Student-t. Picks are handled by conditioning: simulations where any pick
// doesn't come true are thrown away, and everything is counted from the rest.
(function () {
  // mulberry32: small, fast, seedable, so the same picks and seed give the same result
  const rng = (seed) => {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return (((t ^ (t >>> 14)) >>> 0) + 1) / 4294967297;   // strictly between 0 and 1
    };
  };

  // Student-t draws with standard deviation 1. t = z / sqrt(chi2 / df); chi2 with df
  // degrees of freedom is 2 x Gamma(df / 2), drawn with Marsaglia and Tsang's method
  // (fewer random numbers than summing df squared normals, same distribution).
  const tSampler = (rand, df) => {
    let spare = null;
    const normal = () => {
      if (spare !== null) { const s = spare; spare = null; return s; }
      const u = rand(), v = rand(), r = Math.sqrt(-2 * Math.log(u));   // Box-Muller
      spare = r * Math.sin(2 * Math.PI * v);
      return r * Math.cos(2 * Math.PI * v);
    };
    const d = df / 2 - 1 / 3, c = 1 / Math.sqrt(9 * d);
    const chi2 = () => {
      for (;;) {
        const x = normal(), v0 = 1 + c * x;
        if (v0 <= 0) continue;
        const v = v0 * v0 * v0, u = rand();
        if (Math.log(u) < 0.5 * x * x + d - d * v + d * Math.log(v)) return 2 * d * v;
      }
    };
    const unitSd = Math.sqrt((df - 2) / df);   // rescale so the standard deviation is 1
    if (df === 5) {
      // Faster for the model's own setting: read draws off a table of the distribution's
      // exact quantiles (one random number per draw instead of several). The House
      // needs 8.7 million draws per 20,000 simulations.
      const q = quantiles5();
      const N = q.length;
      return () => {
        const u = Math.min(N - 1, Math.max(0, rand() * N - 0.5)), i = Math.min(N - 2, u | 0);
        return (q[i] + (q[i + 1] - q[i]) * (u - i)) * unitSd;
      };
    }
    return () => (normal() / Math.sqrt(chi2() / df)) * unitSd;
  };

  // Student-t with 5 degrees of freedom: its quantiles at (k + 0.5) / N, k = 0..N-1.
  // The distribution function has a closed form in theta = atan(t / sqrt 5):
  //   F = 1/2 + (theta + sin(theta) cos(theta) (1 + 2/3 cos(theta)^2)) / pi
  // which is solved for theta by bisection.
  let q5 = null;
  const quantiles5 = () => {
    if (q5) return q5;
    const N = 1 << 16;
    q5 = new Float64Array(N);
    const g = (th) => { const c = Math.cos(th); return th + Math.sin(th) * c * (1 + (2 / 3) * c * c); };
    for (let k = 0; k < N; k++) {
      const target = Math.PI * ((k + 0.5) / N - 0.5);
      let lo = -Math.PI / 2, hi = Math.PI / 2;
      for (let it = 0; it < 48; it++) { const mid = (lo + hi) / 2; if (g(mid) < target) lo = mid; else hi = mid; }
      q5[k] = Math.sqrt(5) * Math.tan((lo + hi) / 2);
    }
    return q5;
  };

  // model: { blended: [..], region: [region index per race], nRegions, weight: [seats or EVs per race],
  //          sigma: {national, region, race}, df }
  // picks: { raceIndex: +1 (Democrat wins) or -1 (Republican wins) }
  // opts: { seed, batch (20,000), minMatch (2,000), maxTried (400,000), onProgress(info) }
  // Resolves to { tried, matched, wins (per race, count of matching sims the Democrat won),
  //               hist (count of matching sims by Democratic seats/EVs from these races) }
  const run = async (model, picks, opts = {}) => {
    const batch = opts.batch ?? 20000, minMatch = opts.minMatch ?? 2000, maxTried = opts.maxTried ?? 400000;
    const n = model.blended.length, nReg = model.nRegions;
    const blended = Float64Array.from(model.blended), region = Int32Array.from(model.region);
    const weight = Int32Array.from(model.weight);
    const sN = model.sigma.national, sR = model.sigma.region, sX = model.sigma.race;
    const pickIdx = Int32Array.from(Object.keys(picks).map(Number));
    const pickSide = Int8Array.from(pickIdx, (i) => picks[i]);
    const isPicked = new Uint8Array(n);
    pickIdx.forEach((i) => { isPicked[i] = 1; });
    const free = Int32Array.from([...Array(n).keys()].filter((i) => !isPicked[i]));
    let pickedDem = 0;
    pickIdx.forEach((i, k) => { if (pickSide[k] > 0) pickedDem += weight[i]; });
    const totalWeight = weight.reduce((a, b) => a + b, 0);

    const t = tSampler(rng(opts.seed ?? 1), model.df);
    const reg = new Float64Array(nReg);
    const wins = new Float64Array(n), hist = new Float64Array(totalWeight + 1);
    let tried = 0, matched = 0;
    const yieldNow = () => new Promise((r) => setTimeout(r, 0));

    const one = () => {
      const nat = t() * sN;
      for (let k = 0; k < nReg; k++) reg[k] = t() * sR;
      // Picks first: most simulations that fail a pick stop here, which keeps long shots fast
      for (let k = 0; k < pickIdx.length; k++) {
        const i = pickIdx[k];
        const m = blended[i] + nat + reg[region[i]] + t() * sX;
        if ((m > 0) !== (pickSide[k] > 0)) return;
      }
      matched++;
      let dem = pickedDem;
      for (let k = 0; k < pickIdx.length; k++) if (pickSide[k] > 0) wins[pickIdx[k]]++;
      for (let k = 0; k < free.length; k++) {
        const i = free[k];
        if (blended[i] + nat + reg[region[i]] + t() * sX > 0) { wins[i]++; dem += weight[i]; }
      }
      hist[dem]++;
    };

    // Whole batches until enough simulations match (or we've tried enough); yield to
    // the browser every ~30 ms so the page stays responsive and can show progress.
    let last = performance.now();
    while (tried < maxTried && (tried === 0 || matched < minMatch)) {
      for (let s = 0; s < batch; s++) {
        one();
        tried++;
        if ((s & 63) === 0 && performance.now() - last > 30) {
          opts.onProgress?.({ tried, matched, batchDone: (s + 1) / batch, batchNo: Math.ceil(tried / batch) });
          await yieldNow();
          last = performance.now();
        }
      }
    }
    return { tried, matched, wins, hist };
  };

  window.PMSim = { run, rng, tSampler };
})();
