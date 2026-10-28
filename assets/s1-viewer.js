import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';

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
  let active = null, frames = [], owned = [], captions = [];
  const qa = window.__S1_VIEWER__ = { ready: false, caseId: null, frameIndex: 0, mode, playing: false, renderCount: 0, rendererCount: 1 };

  async function geometry(path) {
    const delivery = config.geometry_transport?.[path];
    const compressed = delivery?.encoding === 'gzip' && typeof DecompressionStream === 'function';
    const r = await fetch(new URL(compressed ? delivery.path : path, siteBase));
    if (!r.ok) throw Error('Geometry unavailable: ' + path);
    const buffer = compressed ?
      await new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer() : await r.arrayBuffer();
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
    if (!nv || nf > 500000 || buffer.byteLength !== 16 + nv*12 + nf*12) throw Error('Geometry exceeds supported budget');
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
  function pause() { clearInterval(timer); timer = null; play.textContent = '播放'; qa.playing = false; }
  function showFrame(i) {
    frame = i; qa.frameIndex = i; slider.value = String(i); replayMesh.geometry = frames[i];
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
    schedule();
  }
  function reset() {
    const position = active?.camera_position || [15,-12,12];
    if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) throw Error('Invalid camera position');
    camera.position.set(...position); controls.target.copy(center); controls.update();
    qa.cameraResetPosition = position.slice(); schedule();
  }
  function enabled(value) {
    select.disabled = !value;
    root.querySelectorAll('button,input').forEach(e => e.disabled = !value);
    document.querySelectorAll('[data-select-case]').forEach(e => e.disabled = !value);
  }
  async function loadCase(id, requestedMode) {
    const entry = config.cases.find(e => e.id === id); if (!entry) throw Error('Unknown case');
    if (qa.ready && active?.id === id) { setMode(entry.frames.length ? requestedMode : 'compare'); return; }
    pause(); qa.ready = false; enabled(false); loading.hidden = false;
    // Release prior geometry before loading the next case. Keep one renderer.
    targetMesh.geometry = actualMesh.geometry = replayMesh.geometry = empty;
    for (const g of owned) g.dispose(); owned = []; frames = []; renderer.renderLists.dispose();
    active = entry; select.value = id;
    const low = entry.stock.min, high = entry.stock.max;
    center.set(...low.map((v,i)=>(v+high[i])/2));
    grids.forEach(g => g.position.set(center.x,center.y,low[2]-.2));
    const stock = new THREE.BoxGeometry(...low.map((v,i)=>high[i]-v)); stock.translate(...center.toArray()); owned.push(stock);
    frames.push(stock);
    const paths = [entry.target_mesh,entry.actual_mesh,...entry.frames.map(f=>f.mesh)], loaded = [];
    // At most two downloads at once, and only the selected case is retained.
    for (let i=0;i<paths.length;i+=2) {
      loading.textContent = `正在载入加工状态… ${i} / ${paths.length}`;
      loaded.push(...await Promise.all(paths.slice(i,i+2).map(geometry)));
    }
    [targetMesh.geometry,actualMesh.geometry] = loaded;
    frames.push(...loaded.slice(2));
    frames.push(actualMesh.geometry);
    captions = ['毛坯 · 未加工', ...entry.frames.map((f,i)=>`${f.label || ['钻入','继续钻削','接近贯穿'][i]} · ${f.time_seconds.toFixed(2)} s`), '完成 · 最终结果'];
    frame = 0; qa.frameIndex = 0; slider.value = '0'; slider.max = String(frames.length-1);
    qa.caseId = id; qa.stockThicknessMm = high[2]-low[2]; qa.frameCount = frames.length;
    qa.iouPercent = entry.iou_percent; qa.ownedGeometryCount = owned.length;
    root.querySelector('.fallback img').src = new URL(entry.thumbnail,siteBase);
    const resolutionNote = root.querySelector('[data-resolution-note]');
    if (resolutionNote) resolutionNote.textContent = entry.frames.length ?
      `同一 NC 的真实去料状态。中间帧 0.05 mm，最终结果 ${entry.resolution_mm} mm；播放速度仅作展示。` :
      `此反例保留最终去料结果，分辨率 ${entry.resolution_mm} mm。`;
    reset(); setMode(entry.frames.length ? requestedMode : 'compare'); enabled(true);
    root.querySelector('[data-s1-mode=replay]').disabled = !entry.frames.length;
    loading.hidden = true; qa.ready = true;
  }
  function requestCase(id,value=mode) {
    serial = serial.then(()=>loadCase(id,value)).catch(error=>{ pause(); fail(error); });
    return serial;
  }
  root.querySelectorAll('[data-s1-mode]').forEach(e=>e.addEventListener('click',()=>setMode(e.dataset.s1Mode)));
  select.addEventListener('change',()=>requestCase(select.value));
  document.querySelectorAll('[data-select-case]').forEach(e=>e.addEventListener('click',()=>{
    requestCase(e.dataset.selectCase,e.dataset.selectMode);
    document.querySelector('#machining-process').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth',block:'start'});
  }));
  play.addEventListener('click',()=>{
    if (timer) { pause(); return; }
    if (frame === frames.length-1) showFrame(0);
    play.textContent = '暂停'; qa.playing = true;
    timer = setInterval(()=>{showFrame(frame+1);if(frame===frames.length-1)pause();},1100);
  });
  slider.addEventListener('input',()=>{pause();showFrame(Number(slider.value));});
  root.querySelector('[data-reset]').addEventListener('click',reset);
  controls.addEventListener('change',schedule); new ResizeObserver(schedule).observe(wrap);
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
