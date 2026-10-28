import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { meshBuffer } from './mesh-data.js';

const root = document.querySelector('[data-case-viewer]');
if (root) start().catch(fail);

function fail(error) {
  console.error('GenLaw viewer:', error);
  root.querySelector('.viewer-loading').hidden = true;
  root.querySelector('.fallback').hidden = false;
  root.querySelector('.canvas-wrap').hidden = true;
  root.querySelector('.replay-controls').hidden = true;
  root.querySelectorAll('button').forEach(button => button.disabled = true);
  window.__GENLAW_VIEWER__ = { ready: false, error: String(error) };
}

async function start() {
  const base = root.dataset.assets;
  const wrap = root.querySelector('.canvas-wrap');
  const status = root.querySelector('.viewer-status');
  const loading = root.querySelector('.viewer-loading');
  const actualLabel = root.querySelector('.view-label.actual b');
  const caption = root.querySelector('.frame-caption');
  const slider = root.querySelector('input[type=range]');
  const play = root.querySelector('[data-play]');
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
  renderer.setClearColor(0xeff3f8, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setScissorTest(true);
  renderer.domElement.setAttribute('aria-label', '零件三维视图；拖动旋转、滚轮缩放，方向键旋转');
  renderer.domElement.setAttribute('role', 'img');
  renderer.domElement.tabIndex = 0;
  wrap.prepend(renderer.domElement);

  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 120);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false;
  controls.enablePan = false;
  controls.minDistance = 7;
  controls.maxDistance = 42;
  controls.maxPolarAngle = Math.PI * 0.95;
  const center = new THREE.Vector3(4, 3, -0.75);
  let mode = 'compare', frameIndex = 0, timer = null, pending = false, framesReady = false, framePromise = null;
  const qa = window.__GENLAW_VIEWER__ = { ready: false, mode, frameIndex, frameCount: 5, renderCount: 0, playing: false };

  function makeScene() {
    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xffffff, 0x637487, 2.5));
    const key = new THREE.DirectionalLight(0xffffff, 2.8);
    key.position.set(1, -6, 16); scene.add(key);
    const fill = new THREE.DirectionalLight(0xaacbff, 1.5);
    fill.position.set(-8, 10, 7); scene.add(fill);
    const grid = new THREE.GridHelper(16, 16, 0xbac7d6, 0xdce3ec);
    grid.rotation.x = Math.PI / 2; grid.position.set(4, 3, -1.7);
    grid.material.transparent = true; grid.material.opacity = 0.45;
    scene.add(grid);
    return scene;
  }
  async function geometry(name) {
    const response = await fetch(base + name);
    if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
    const buffer = await meshBuffer(response), header = new DataView(buffer);
    if (buffer.byteLength < 16) throw new Error('Incomplete mesh');
    if (new TextDecoder().decode(new Uint8Array(buffer, 0, 8)) !== 'GLWMESH1') throw new Error('Unknown mesh format');
    const vertices = header.getUint32(8, true), faces = header.getUint32(12, true);
    if (buffer.byteLength !== 16 + vertices * 12 + faces * 12) throw new Error('Incomplete mesh');
    const shape = new THREE.BufferGeometry();
    shape.setAttribute('position', new THREE.BufferAttribute(new Float32Array(buffer, 16, vertices * 3), 3));
    shape.setIndex(new THREE.BufferAttribute(new Uint32Array(buffer, 16 + vertices * 12, faces * 3), 1));
    shape.computeVertexNormals(); shape.computeBoundingSphere();
    return shape;
  }
  const [targetGeometry, actualGeometry] = await Promise.all([geometry('target.mesh'), geometry('actual.mesh')]);
  const targetMaterial = new THREE.MeshStandardMaterial({ color: 0x5e88b7, roughness: 0.72, metalness: 0.12, flatShading: true });
  const actualMaterial = new THREE.MeshStandardMaterial({ color: 0xc69b70, roughness: 0.72, metalness: 0.12, flatShading: true });
  const targetScene = makeScene(), actualScene = makeScene(), overlayScene = makeScene(), replayScene = makeScene();
  targetScene.add(new THREE.Mesh(targetGeometry, targetMaterial));
  actualScene.add(new THREE.Mesh(actualGeometry, actualMaterial));
  const overlayActual = new THREE.Mesh(actualGeometry, actualMaterial);
  const overlayTarget = new THREE.Mesh(targetGeometry, new THREE.MeshBasicMaterial({ color: 0x345e91, transparent: true, opacity: 0.28, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
  overlayTarget.renderOrder = 2;
  overlayScene.add(overlayActual, overlayTarget);
  const stock = new THREE.BoxGeometry(8, 6, 1.5).translate(4, 3, -0.75);
  const frameShapes = [stock, null, null, null, actualGeometry];
  const replayMesh = new THREE.Mesh(stock, actualMaterial); replayScene.add(replayMesh);
  const frameLabels = ['毛坯 · 未加工', '钻入 · 1.5 s', '继续钻削 · 2.3 s', '接近贯穿 · 3.0 s', '完成 · 最终结果'];
  function reset() { camera.position.set(15, -12, 12); controls.target.copy(center); controls.update(); schedule(); }
  function drawScene(scene, x, y, width, height) {
    renderer.setViewport(x, y, width, height); renderer.setScissor(x, y, width, height);
    camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.render(scene, camera);
  }
  function render() {
    pending = false;
    const width = wrap.clientWidth, height = wrap.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    if (mode === 'compare') {
      if (matchMedia('(max-width:720px)').matches) {
        drawScene(targetScene, 0, height / 2, width, height / 2);
        drawScene(actualScene, 0, 0, width, height / 2);
      } else {
        drawScene(targetScene, 0, 0, width / 2, height);
        drawScene(actualScene, width / 2, 0, width / 2, height);
      }
    } else drawScene(mode === 'overlay' ? overlayScene : replayScene, 0, 0, width, height);
    qa.renderCount += 1;
  }
  function schedule() { if (!pending) { pending = true; requestAnimationFrame(render); } }
  function pause() { clearInterval(timer); timer = null; play.textContent = '播放'; qa.playing = false; }
  async function loadFrames() {
    if (framesReady) return;
    if (!framePromise) framePromise = (async () => {
      play.disabled = true; status.textContent = '正在载入真实去料帧…';
      // Load sequentially to keep decoding and GPU uploads small on this machine.
      for (let index = 1; index <= 3; index++) frameShapes[index] = await geometry(`frame-${index}.mesh`);
      framesReady = true; qa.framesReady = true; play.disabled = false;
    })();
    return framePromise;
  }
  function showFrame(index) {
    frameIndex = index; qa.frameIndex = index; slider.value = String(index);
    replayMesh.geometry = frameShapes[index]; caption.textContent = frameLabels[index];
    status.textContent = `状态 ${index + 1} / 5 · ${index === 4 ? '最终网格 · 0.025 mm' : index === 0 ? '8 × 6 × 1.5 mm 毛坯' : 'CAMotics 中间帧 · 0.05 mm'}`;
    schedule();
  }
  function selectMode(value) {
    pause(); mode = value; qa.mode = value; root.dataset.mode = value;
    root.querySelectorAll('[data-view-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.viewMode === value)));
    root.querySelector('.replay-controls').hidden = value !== 'replay';
    actualLabel.textContent = value === 'replay' ? '仿真去料状态' : '仿真加工后 · 实际网格';
    if (value === 'replay') showFrame(frameIndex);
    else status.textContent = value === 'overlay' ? '同一坐标叠加 · 蓝色为目标 CAD；精确差异见体积指标' : '同步旋转 · 单位 mm · 同一坐标系';
    schedule();
  }
  root.querySelectorAll('[data-view-mode]').forEach(button => button.addEventListener('click', () => selectMode(button.dataset.viewMode)));
  root.querySelector('[data-reset]').addEventListener('click', reset);
  play.addEventListener('click', async () => {
    if (timer) { pause(); return; }
    try {
      await loadFrames();
      if (mode !== 'replay') return;
      if (frameIndex === 4) showFrame(0);
      play.textContent = '暂停'; qa.playing = true;
      timer = setInterval(() => { showFrame(frameIndex + 1); if (frameIndex === 4) pause(); }, 1200);
    } catch (error) { fail(error); }
  });
  slider.addEventListener('input', async () => {
    pause();
    const desired = Number(slider.value);
    try { await loadFrames(); if (mode === 'replay') showFrame(desired); } catch (error) { fail(error); }
  });
  renderer.domElement.addEventListener('keydown', event => {
    const direction = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (direction) {
      const offset = camera.position.clone().sub(controls.target);
      offset.applyAxisAngle(new THREE.Vector3(0, 0, 1), direction * 0.15);
      camera.position.copy(controls.target).add(offset); controls.update(); schedule(); event.preventDefault();
    }
  });
  controls.addEventListener('change', schedule);
  new ResizeObserver(schedule).observe(wrap);
  document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
  renderer.domElement.addEventListener('webglcontextlost', event => { event.preventDefault(); pause(); fail('WebGL context lost'); });
  reset(); selectMode('compare'); loading.hidden = true; qa.ready = true;
}
