/* Loaded synchronously before the external BlueMap module, on both map entry points. */
(() => {
  'use strict';
  const mobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) ||
    matchMedia('(pointer: coarse)').matches;
  const constrained = mobile || navigator.deviceMemory <= 4 ||
    navigator.hardwareConcurrency <= 4 || navigator.connection?.saveData === true;
  // The URL makes manual recovery work even when browser storage is unavailable.
  const recovery = new URL(location.href).searchParams.get('oak-quality') === 'low';
  const light = constrained || recovery;
  const profile = {
    name: recovery ? 'recovery' : light ? 'mobile' : 'desktop',
    hires: recovery ? 50 : light ? 100 : 250,
    hiresMax: recovery ? 100 : light ? 200 : 500,
    lowres: light ? 1000 : 2000,
    lowresMax: light ? 2000 : 4000,
    resolution: light || window.devicePixelRatio > 2 ? 0.5 : 1,
    contextLosses: 0
  };
  window.oakMapProfile = profile;

  function migratePreferences(settings) {
    if (!settings.useCookies) return;
    try {
      const values = {
        hiresViewDistance: [profile.hires, 0, recovery ? profile.hires : profile.hiresMax],
        lowresViewDistance: [profile.lowres, 500, recovery ? profile.lowres : profile.lowresMax],
        superSampling: [profile.resolution, 0.5, profile.resolution]
      };
      for (const [name, [fallback, min, max]] of Object.entries(values)) {
        const key = 'bluemap-' + name;
        let saved;
        try { saved = JSON.parse(localStorage.getItem(key)); } catch {}
        // Preserve cheaper choices, including zero high-detail distance.
        const valid = typeof saved === 'number' && Number.isFinite(saved) &&
          saved >= min && saved <= max && (name !== 'superSampling' || [0.5, 1, 2].includes(saved));
        localStorage.setItem(key, JSON.stringify(valid ? saved : fallback));
      }
      if (light) localStorage.setItem('bluemap-pauseTileLoading', 'true');
    } catch {
      // BlueMap must use the adapted defaults if preferences cannot be persisted.
      settings.useCookies = false;
    }
  }

  // BlueMap has no pre-load custom-script hook. Adapt only its configuration
  // response; tiles, live data, headers, cancellation, and other requests pass through.
  const fetchOriginal = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const response = await fetchOriginal(input, init);
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = init?.method || (input instanceof Request ? input.method : 'GET');
    if (url.origin !== location.origin || url.pathname !== '/map/settings.json' ||
        method.toUpperCase() !== 'GET' || !response.ok) return response;
    const settings = await response.clone().json();
    Object.assign(settings, {
      hiresSliderDefault: profile.hires, hiresSliderMin: 0, hiresSliderMax: profile.hiresMax,
      lowresSliderDefault: profile.lowres, lowresSliderMin: 500, lowresSliderMax: profile.lowresMax,
      resolutionDefault: profile.resolution
    });
    migratePreferences(settings);
    const headers = new Headers(response.headers);
    for (const name of ['content-length', 'content-encoding', 'etag', 'last-modified']) headers.delete(name);
    headers.set('content-type', 'application/json');
    return new Response(JSON.stringify(settings), {status: response.status, statusText: response.statusText, headers});
  };

  // Do not reload automatically: a GPU failure must not become a reload loop.
  let notice;
  const mainCanvas = () => window.bluemap?.mapViewer?.renderer?.domElement;
  document.addEventListener('webglcontextlost', (event) => {
    // BlueMap also creates disposable preview canvases. Their teardown is not
    // a failure of the live map renderer.
    if (!mainCanvas() || event.target !== mainCanvas() || !event.target.isConnected) return;
    profile.contextLosses++;
    if (notice) return;
    notice = document.createElement('div');
    notice.id = 'oak-map-recovery';
    notice.setAttribute('role', 'alert');
    const text = document.createElement('p');
    text.textContent = 'O navegador interrompeu a renderização 3D do mapa.';
    const retry = document.createElement('a');
    const url = new URL(location.href);
    url.searchParams.delete('oak-quality');
    retry.href = url.href;
    retry.textContent = 'Reabrir mapa';
    const lightRetry = document.createElement('a');
    url.searchParams.set('oak-quality', 'low');
    lightRetry.href = url.href;
    lightRetry.textContent = 'Usar modo leve';
    notice.append(text, retry, lightRetry);
    document.body.append(notice);
  }, true);
  document.addEventListener('webglcontextrestored', (event) => {
    if (event.target !== mainCanvas()) return;
    notice?.remove();
    notice = undefined;
  }, true);
})();
