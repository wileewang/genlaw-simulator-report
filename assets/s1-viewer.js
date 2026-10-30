import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { fetchMeshBuffer } from './mesh-fetch.js';

const root = document.querySelector('[data-s1-viewer]');
if (root) start().catch(fail);

function fail(error) {
  console.error('S1 viewer:', error);
  root.querySelector('.viewer-loading').hidden = true;
  root.querySelector('.canvas-wrap').hidden = true;
  root.querySelector('.fallback').hidden = false;
  root.querySelector('.replay-controls').hidden = true;
  root.querySelectorAll('button,input').forEach(e => e.disabled = true);
  document.querySelectorAll('[data-select-case],#s1-case-select').forEach(e => e.disabled = true);
  window.__S1_VIEWER__ = { ready: false, error: String(error) };
}

async function start() {
  const configURL = new URL(root.dataset.config, location.href);
  const response = await fetch(configURL);
  if (!response.ok) throw Error('Case configuration unavailable');
  const config = await response.json(), siteBase = new URL('../', configURL);
  const wrap = root.querySelector('.canvas-wrap');
  const loading = root.querySelector('.viewer-loading');
  const status = root.querySelector('.viewer-status');
  const caption = root.querySelector('.frame-caption');
  const play = root.querySelector('[data-play]');
  const slider = root.querySelector('input[type=range]');
  const select = document.querySelector('#s1-case-select');
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
  renderer.setClearColor(0xeff3f8, 0); renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setScissorTest(true); wrap.prepend(renderer.domElement);
  renderer.domElement.tabIndex = 0;
  renderer.domElement.setAttribute('role', 'img');
  renderer.domElement.setAttribute('aria-label', '真实仿真加工状态；可拖动旋转，方向键旋转');
  const camera = new THREE.PerspectiveCamera(34, 1, .1, 120); camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enablePan = false; controls.enableDamping = false;
  controls.minDistance = 7; controls.maxDistance = 42; controls.maxPolarAngle = Math.PI * .95;
  const material = new THREE.MeshStandardMaterial({ color: 0xc69b70, roughness: .72, metalness: .12, flatShading: true });
  const targetMaterial = new THREE.MeshStandardMaterial({ color: 0x5e88b7, roughness: .72, metalness: .12, flatShading: true });
  const grids = [];
  function scene() {
    const s = new THREE.Scene(); s.add(new THREE.HemisphereLight(0xffffff, 0x637487, 2.5));
    const key = new THREE.DirectionalLight(0xffffff, 2.8); key.position.set(1, -6, 16); s.add(key);
    const fill = new THREE.DirectionalLight(0xaacbff, 1.5); fill.position.set(-8, 10, 7); s.add(fill);
    const grid = new THREE.GridHelper(16, 16, 0xbac7d6, 0xdce3ec); grid.rotation.x = Math.PI / 2;
    grid.material.transparent = true; grid.material.opacity = .45; s.add(grid); grids.push(grid); return s;
  }
  const targetScene = scene(), actualScene = scene(), replayScene = scene();
  const empty = new THREE.BufferGeometry();
  const targetMesh = new THREE.Mesh(empty, targetMaterial), actualMesh = new THREE.Mesh(empty, material), replayMesh = new THREE.Mesh(empty, material);
  targetScene.add(targetMesh); actualScene.add(actualMesh); replayScene.add(replayMesh);
  const center = new THREE.Vector3();
  let mode = 'replay', frame = 0, timer = null, queued = false, serial = Promise.resolve();
  let active = null, frames = [], owned = [], captions = [], cachedFrame = null, playGeneration = 0;
  const qa = window.__S1_VIEWER__ = { ready: false, caseId: null, frameIndex: 0, mode, playing: false, renderCount: 0, rendererCount: 1, networkRetries: 0 };

  const parts = value => Array.isArray(value) ? value : [value];
  const exclusive = () => active?.state_loading === 'exclusive';
  function attach(mesh, value) {
    // One shape may be delivered as several files. All parts are rendered;
    // splitting never selects a subset of the actual surface.
    mesh.clear();
    mesh.geometry = Array.isArray(value) ? empty : value;
    if (Array.isArray(value)) for (const g of value) mesh.add(new THREE.Mesh(g,mesh.material));
  }
  function release(value) {
    for (const g of parts(value)) {
      const index = owned.indexOf(g);
      if (index < 0) throw Error('Geometry ownership is inconsistent');
      owned.splice(index,1); g.dispose();
    }
    renderer.renderLists.dispose();
  }
  async function display(source, remainingTriangles = Infinity) {
    if (!Array.isArray(source)) return geometry(source,remainingTriangles);
    if (!source.length || source.length > 16 || new Set(source).size !== source.length ||
        source.some(path=>typeof path !== 'string')) throw Error('Invalid multipart display');
    const result = [];
    for (const path of source) {
      const g = await geometry(path,remainingTriangles);
      remainingTriangles -= g.index.count/3; result.push(g);
    }
    return result;
  }

  async function geometry(path, remainingTriangles = Infinity) {
    const delivery = config.geometry_transport?.[path];
    const compressed = delivery?.encoding === 'gzip' && typeof DecompressionStream === 'function';
    const url = new URL(compressed ? delivery.path : path, siteBase);
    const retryURL = compressed && delivery.retry_path ? new URL(delivery.retry_path, siteBase) : url;
    const buffer = await fetchMeshBuffer(url, () => { qa.networkRetries++; }, { retryURL });
    if (delivery) {
      if (buffer.byteLength !== delivery.decoded_bytes) throw Error('Incomplete geometry: ' + path);
      if (globalThis.crypto?.subtle) {
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),
          byte => byte.toString(16).padStart(2,'0')).join('');
        if (digest !== delivery.decoded_sha256) throw Error('Geometry integrity mismatch: ' + path);
      }
    }
    const h = new DataView(buffer);
    if (buffer.byteLength < 16 || new TextDecoder().decode(new Uint8Array(buffer,0,8)) !== 'GLWMESH1') throw Error('Invalid geometry');
    const nv = h.getUint32(8,true), nf = h.getUint32(12,true);
    const limit = active.mesh_triangle_limit ?? 500000;
    if (!Number.isInteger(limit) || limit < 1 || limit > 1000000 || !nv || !nf || nf > limit || nf > remainingTriangles ||
        buffer.byteLength > 64*1024*1024 || buffer.byteLength !== 16 + nv*12 + nf*12) throw Error('Geometry exceeds supported budget');
    const g = new THREE.BufferGeometry();
    g.setAttribute('position',new THREE.BufferAttribute(new Float32Array(buffer,16,nv*3),3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(buffer,16+nv*12,nf*3),1));
    g.computeVertexNormals(); g.computeBoundingSphere(); owned.push(g); return g;
  }
  function draw(s, x, y, w, h) {
    renderer.setViewport(x,y,w,h); renderer.setScissor(x,y,w,h);
    camera.aspect = w/h; camera.updateProjectionMatrix(); renderer.render(s,camera);
  }
  function render() {
    queued = false; const w = wrap.clientWidth, h = wrap.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w,h,false);
    if (mode === 'compare') {
      if (matchMedia('(max-width:720px)').matches) { draw(targetScene,0,h/2,w,h/2); draw(actualScene,0,0,w,h/2); }
      else { draw(targetScene,0,0,w/2,h); draw(actualScene,w/2,0,w/2,h); }
    } else draw(replayScene,0,0,w,h);
    qa.renderCount++;
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(render); } }
  function pause() { clearTimeout(timer); timer = null; playGeneration++; play.textContent = '播放'; qa.playing = false; }
  function showFrame(i) {
    if (!Number.isInteger(i) || !frames[i]) throw Error('Requested frame is not loaded');
    frame = i; qa.frameIndex = i; slider.value = String(i); attach(replayMesh,frames[i]);
    caption.textContent = captions[i]; status.textContent = `${active.name} · 状态 ${i+1} / ${frames.length}`; schedule();
  }
  function setMode(value) {
    pause(); mode = value; qa.mode = value; root.dataset.mode = value;
    root.querySelectorAll('[data-s1-mode]').forEach(e => e.setAttribute('aria-pressed',String(e.dataset.s1Mode === value)));
    root.querySelector('.replay-controls').hidden = value !== 'replay';
    root.querySelector('.view-label.actual b').textContent = value === 'replay' ? '仿真去料状态' : '仿真加工后 · 实际网格';
    if (active) {
      if (value === 'replay') showFrame(frame);
      else status.textContent = `${active.name} · 同坐标对比 · 单位 mm`;
    }
    fitCamera();
    schedule();
  }
  async function changeMode(value) {
    if (exclusive()) {
      const selected = frame;
      await loadFrame(active.id,value === 'compare' ? frames.length-1 : frame);
      if (value === 'compare') { frame = selected; qa.frameIndex = selected; slider.value = String(selected); }
    }
    setMode(value);
  }
  function requestMode(value) {
    pause();
    serial = serial.then(()=>changeMode(value)).catch(error=>{pause();fail(error);});
    return serial;
  }
  function fitCamera() {
    if (active?.frame_loading !== 'on_demand') return;
    let width = wrap.clientWidth, height = wrap.clientHeight;
    if (mode === 'compare') {
      if (matchMedia('(max-width:720px)').matches) height /= 2; else width /= 2;
    }
    if (!width || !height) return;
    camera.aspect = width/height; camera.updateProjectionMatrix(); camera.updateMatrixWorld();
    const tangent = Math.tan(THREE.MathUtils.degToRad(camera.fov/2));
    const corners = [];
    for (const x of [active.stock.min[0],active.stock.max[0]])
      for (const y of [active.stock.min[1],active.stock.max[1]])
        for (const z of [active.stock.min[2],active.stock.max[2]]) corners.push(new THREE.Vector3(x,y,z));
    let extra = 0;
    for (const corner of corners) {
      const p = corner.clone().applyMatrix4(camera.matrixWorldInverse);
      extra = Math.max(extra,Math.abs(p.x)/(tangent*camera.aspect*.88)+p.z,Math.abs(p.y)/(tangent*.88)+p.z);
    }
    if (extra > 0) {
      camera.position.addScaledVector(camera.position.clone().sub(center).normalize(),extra);
      controls.update(); camera.updateMatrixWorld();
    }
    qa.projectedStockExtent = Math.max(...corners.flatMap(p=>{p.project(camera);return [Math.abs(p.x),Math.abs(p.y)];}));
  }
  function reset() {
    const position = active?.camera_position || [15,-12,12];
    if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) throw Error('Invalid camera position');
    camera.position.set(...position); controls.target.copy(center); controls.update();
    qa.cameraResetPosition = position.slice(); fitCamera(); schedule();
  }
  function enabled(value) {
    select.disabled = !value;
    root.querySelectorAll('button,input').forEach(e => e.disabled = !value);
    document.querySelectorAll('[data-select-case]').forEach(e => e.disabled = !value);
  }
  function triangleBudget() {
    const limit = active.total_triangle_limit ?? 2000000;
    if (!Number.isInteger(limit) || limit < 1 || limit > 2000000) throw Error('Invalid case display budget');
    return limit;
  }
  function updateResidentGeometry() {
    // Include the stock box in the resident budget for on-demand cases.
    qa.loadedTriangles = owned.reduce((sum,g)=>sum+(g.index?.count || 0)/3,0);
    if (qa.loadedTriangles > triangleBudget()) throw Error('Case exceeds supported display budget');
    qa.peakLoadedTriangles = Math.max(qa.peakLoadedTriangles || 0,qa.loadedTriangles);
    qa.ownedGeometryCount = owned.length;
    qa.residentIntermediateFrames = cachedFrame !== null && cachedFrame !== frames.length-1 ? 1 : 0;
    qa.residentFinalStates = frames.length && frames[frames.length-1] ? 1 : 0;
  }
  async function loadFrame(caseId, i) {
    if (active.id !== caseId) return;
    if (!Number.isInteger(i) || i < 0 || i >= frames.length) throw Error('Invalid frame index');
    if (frames[i]) { showFrame(i); return; }
    if (active.frame_loading !== 'on_demand') throw Error('Missing preloaded frame');
    enabled(false); qa.loadingFrame = true; loading.hidden = false;
    loading.textContent = `正在载入加工状态… ${i+1} / ${frames.length}`;
    // Free the previous intermediate state before fetching its replacement.
    attach(replayMesh,empty);
    if (cachedFrame !== null) {
      const prior = frames[cachedFrame]; frames[cachedFrame] = null; cachedFrame = null;
      if (exclusive()) attach(actualMesh,empty);
      release(prior);
    }
    updateResidentGeometry(); schedule();
    const finalFrame = i === frames.length-1;
    frames[i] = await display(finalFrame ? active.actual_mesh : active.frames[i-1].mesh,triangleBudget()-qa.loadedTriangles);
    if (finalFrame) attach(actualMesh,frames[i]);
    cachedFrame = i; updateResidentGeometry(); showFrame(i);
    qa.loadingFrame = false; loading.hidden = true; enabled(true);
  }
  function requestFrame(i) {
    const caseId = active.id;
    // Case changes and frame changes share one queue: a late download cannot
    // attach its geometry to another part or run alongside another frame load.
    serial = serial.then(()=>loadFrame(caseId,i)).catch(error=>{ pause(); fail(error); });
    return serial;
  }
  async function loadCase(id, requestedMode) {
    const entry = config.cases.find(e => e.id === id); if (!entry) throw Error('Unknown case');
    if (qa.ready && active?.id === id) { await changeMode(entry.frames.length ? requestedMode : 'compare'); return; }
    pause(); qa.ready = false; enabled(false); loading.hidden = false;
    // Release prior geometry before loading the next case. Keep one renderer.
    for (const mesh of [targetMesh,actualMesh,replayMesh]) attach(mesh,empty);
    for (const g of owned) g.dispose(); owned = []; frames = []; cachedFrame = null; renderer.renderLists.dispose();
    active = entry; select.value = id;
    if (entry.frame_loading !== undefined && entry.frame_loading !== 'on_demand') throw Error('Invalid frame loading mode');
    if (entry.state_loading !== undefined && (entry.state_loading !== 'exclusive' || entry.frame_loading !== 'on_demand')) throw Error('Invalid state loading mode');
    qa.frameLoading = entry.frame_loading || 'preload'; qa.loadingFrame = false; qa.peakLoadedTriangles = 0;
    qa.stateLoading = entry.state_loading || 'retain_final';
    qa.residentIntermediateFrames = 0;
    const scale = entry.view_scale ?? 1;
    if (!Number.isFinite(scale) || scale < 1 || scale > 20) throw Error('Invalid view scale');
    camera.far = 120*scale; controls.minDistance = 7*scale; controls.maxDistance = 42*scale;
    const low = entry.stock.min, high = entry.stock.max;
    center.set(...low.map((v,i)=>(v+high[i])/2));
    grids.forEach(g => { g.position.set(center.x,center.y,low[2]-.2); g.scale.setScalar(scale); });
    const stock = new THREE.BoxGeometry(...low.map((v,i)=>high[i]-v)); stock.translate(...center.toArray()); owned.push(stock);
    frames.push(stock);
    const demand = entry.frame_loading === 'on_demand';
    const paths = [entry.target_mesh,...(exclusive() ? [] : [entry.actual_mesh]),...(demand ? [] : entry.frames.map(f=>f.mesh))], loaded = [];
    // At most two downloads at once, and only the selected case is retained.
    for (let i=0;i<paths.length;i+=2) {
      loading.textContent = `正在载入加工状态… ${i} / ${paths.length}`;
      loaded.push(...await Promise.all(paths.slice(i,i+2).map(path=>display(path))));
    }
    attach(targetMesh,loaded[0]);
    if (!exclusive()) attach(actualMesh,loaded[1]);
    qa.loadedTriangles = loaded.flatMap(parts).reduce((sum,g)=>sum+g.index.count/3,0);
    if (entry.total_triangle_limit !== undefined && (!Number.isInteger(entry.total_triangle_limit) ||
        entry.total_triangle_limit > 2000000 || qa.loadedTriangles > entry.total_triangle_limit)) throw Error('Case exceeds supported display budget');
    frames.push(...(demand ? entry.frames.map(()=>null) : loaded.slice(2)));
    frames.push(exclusive() ? null : loaded[1]);
    if (demand) updateResidentGeometry();
    captions = ['毛坯 · 未加工', ...entry.frames.map((f,i)=>`${f.label || ['钻入','继续钻削','接近贯穿'][i]}${Number.isFinite(f.time_seconds) ? ` · ${f.time_seconds.toFixed(2)} s` : ''}`), '完成 · 最终结果'];
    frame = 0; qa.frameIndex = 0; slider.value = '0'; slider.max = String(frames.length-1);
    qa.caseId = id; qa.stockThicknessMm = high[2]-low[2]; qa.frameCount = frames.length;
    qa.iouPercent = entry.iou_percent; qa.ownedGeometryCount = owned.length;
    if (entry.thumbnail) root.querySelector('.fallback img').src = new URL(entry.thumbnail,siteBase);
    const resolutionNote = root.querySelector('[data-resolution-note]');
    if (resolutionNote) resolutionNote.textContent = entry.frames.length && entry.frames.every(f=>f.fine_checkpoint_acceptance) ?
      `实际工步状态：边界与最终结果均为 ${entry.resolution_mm} mm，已用于 IoU 与几何检查。各步从原始毛坯执行累计 NC；播放间隔不代表加工耗时。` : entry.frames.length && entry.frames.every(f=>f.display_only) ?
      `实际工步状态回放：中间状态 ${entry.frames[0].resolution_mm} mm，仅用于展示；最终结果 ${entry.resolution_mm} mm，用于 IoU 与几何检查。播放间隔不代表加工耗时。` : entry.frames.some(f=>f.kind==='cumulative_native_prefix') ?
      `真实去料状态：时间帧 0.05 mm，工步边界与最终结果 ${entry.resolution_mm} mm。工步边界复用同一原生 NC 的累计前缀结果；播放速度仅作展示。` : entry.frames.length && entry.frames.every(f=>f.kind==='native_time_cutoff' && f.resolution_mm===entry.resolution_mm) ?
      `同一 NC 的真实去料状态：中间与最终网格均为 ${entry.resolution_mm} mm。时间帧用于展示，不代表独立工步验收；播放间隔不代表加工耗时。` : entry.frames.length ?
      `同一 NC 的真实去料状态。中间帧 0.05 mm，最终结果 ${entry.resolution_mm} mm；播放速度仅作展示。` :
      `${entry.case_kind==='normal' ? '此例展示最终去料结果；加工回放见基准例' : '此反例保留最终去料结果'}，分辨率 ${entry.resolution_mm} mm。`;
    reset(); await changeMode(entry.frames.length ? requestedMode : 'compare'); enabled(true);
    root.querySelector('[data-s1-mode=replay]').disabled = !entry.frames.length;
    loading.hidden = true; qa.ready = true;
  }
  function requestCase(id,value=mode) {
    serial = serial.then(()=>loadCase(id,value)).catch(error=>{ pause(); fail(error); });
    return serial;
  }
  root.querySelectorAll('[data-s1-mode]').forEach(e=>e.addEventListener('click',()=>requestMode(e.dataset.s1Mode)));
  select.addEventListener('change',()=>requestCase(select.value));
  document.querySelectorAll('[data-select-case]').forEach(e=>e.addEventListener('click',()=>{
    requestCase(e.dataset.selectCase,e.dataset.selectMode);
    document.querySelector('#machining-process').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth',block:'start'});
  }));
  play.addEventListener('click',async()=>{
    if (qa.playing) { pause(); return; }
    const generation = ++playGeneration;
    play.textContent = '暂停'; qa.playing = true;
    if (frame === frames.length-1) await requestFrame(0);
    function tick() {
      if (!qa.playing || generation !== playGeneration) return;
      timer = setTimeout(async()=>{
        await requestFrame(frame+1);
        if (!qa.playing || generation !== playGeneration) return;
        if (frame === frames.length-1) pause(); else tick();
      },1100);
    }
    tick();
  });
  slider.addEventListener('input',()=>{pause();requestFrame(Number(slider.value));});
  root.querySelector('[data-reset]').addEventListener('click',reset);
  controls.addEventListener('change',schedule);
  new ResizeObserver(()=>{if(qa.ready)fitCamera();schedule();}).observe(wrap);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
  new IntersectionObserver(entries=>{if(!entries[0].isIntersecting)pause();}).observe(root);
  renderer.domElement.addEventListener('keydown',event=>{
    const direction={ArrowLeft:-1,ArrowRight:1}[event.key];if(!direction)return;
    const offset=camera.position.clone().sub(controls.target).applyAxisAngle(new THREE.Vector3(0,0,1),direction*.15);
    camera.position.copy(controls.target).add(offset);controls.update();schedule();event.preventDefault();
  });
  renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();pause();fail('WebGL context lost');});
  await requestCase(config.cases[0].id,'replay');
}
