/* 周报里的“仿真验收展示”：按执行者那次运行实际走的数控刀路重放加工过程。
   材料去除用高度图近似（从上、从翻面后的另一面各一张），只为看清每一步切了哪里；
   验收结论不是这里算的，来自仿真引擎和逐面比对，原样显示在旁边。
   依赖：three.min.js、OrbitControls.js（同目录 vendor/），数据在 window.HD_EXHIBITS。 */
(function () {
  'use strict';
  var V = {};
  var el = function (id) { return document.getElementById(id); };
  var fmt = function (x, n) { return x == null ? '—' : Number(x).toFixed(n == null ? 5 : n); };
  var STATUS_CLASS = { passed: 'ok', unsupported: 'warn', dependency_failed: 'idle', failed: 'bad', blocked: 'bad', error: 'bad' };

  function Grid(stock) {
    var L = Math.max(stock.max[0] - stock.min[0], stock.max[1] - stock.min[1]);
    this.cell = Math.max(0.8, L / 380);
    this.x0 = stock.min[0]; this.y0 = stock.min[1];
    this.nx = Math.max(2, Math.round((stock.max[0] - stock.min[0]) / this.cell) + 1);
    this.ny = Math.max(2, Math.round((stock.max[1] - stock.min[1]) / this.cell) + 1);
    this.zTop = stock.max[2]; this.zBot = stock.min[2];
    this.top = new Float32Array(this.nx * this.ny).fill(this.zTop);
    this.bot = new Float32Array(this.nx * this.ny).fill(this.zBot);
  }
  Grid.prototype.copyFrom = function (g) { this.top.set(g.top); this.bot.set(g.bot); };
  Grid.prototype.clone = function () {
    var g = Object.create(Grid.prototype);
    for (var k in this) if (Object.prototype.hasOwnProperty.call(this, k)) g[k] = this[k];
    g.top = new Float32Array(this.top); g.bot = new Float32Array(this.bot); return g;
  };
  /* 刀具在 (x,y) 处、刀尖高度 z 时去掉材料。axis：top 从上往下切，bottom 翻面后从另一面切。 */
  Grid.prototype.stamp = function (x, y, z, r, axis, slope) {
    var c = this.cell, nx = this.nx, ny = this.ny;
    var i0 = Math.max(0, Math.ceil((x - r - this.x0) / c)), i1 = Math.min(nx - 1, Math.floor((x + r - this.x0) / c));
    var j0 = Math.max(0, Math.ceil((y - r - this.y0) / c)), j1 = Math.min(ny - 1, Math.floor((y + r - this.y0) / c));
    var r2 = r * r, hit = false;
    for (var j = j0; j <= j1; j++) {
      var dy = this.y0 + j * c - y, row = j * nx;
      for (var i = i0; i <= i1; i++) {
        var dx = this.x0 + i * c - x, d2 = dx * dx + dy * dy;
        if (d2 > r2) continue;
        var k = row + i, h;
        if (axis === 'top') {
          h = slope ? z + Math.sqrt(d2) * slope : z;
          if (h < this.top[k]) { this.top[k] = h; hit = true; }
        } else {
          h = slope ? z - Math.sqrt(d2) * slope : z;
          if (h > this.bot[k]) { this.bot[k] = h; hit = true; }
        }
      }
    }
    return hit;
  };

  function toolOf(prog, id) {
    var t = prog.tools[String(id)] || prog.tools[Object.keys(prog.tools)[0]] || { d: 6, len: 10, shape: 'cylindrical' };
    var r = (t.d || 6) / 2;
    return { r: r, len: t.len || 10, slope: t.shape === 'conical' && r > 0 ? (t.len || 0) / r : 0, shape: t.shape || 'cylindrical' };
  }
  /* 把一段程序从路程 s0 切到 s1（毫米，快移和切削都算路程）。返回是否动了材料。 */
  function carve(grid, prog, s0, s1) {
    if (prog.axis === 'side') return false;
    var m = prog.moves, acc = prog._acc, hit = false, sp = grid.cell * 0.7;
    var k = lowerBound(acc, s0);
    for (; k < prog.count && acc[k] < s1; k++) {
      var o = k * 9;
      if (!m[o]) continue;                                   // 快移不切
      var segStart = acc[k], segEnd = acc[k + 1], len = segEnd - segStart;
      if (len <= 0) {
        var t0 = toolOf(prog, m[o + 1]);
        hit = grid.stamp(m[o + 5], m[o + 6], m[o + 7], t0.r, prog.axis, t0.slope) || hit; continue;
      }
      var a = Math.max(s0, segStart), b = Math.min(s1, segEnd), t = toolOf(prog, m[o + 1]);
      var n = Math.max(1, Math.ceil((b - a) / sp));
      for (var q = 0; q <= n; q++) {
        var f = (a + (b - a) * q / n - segStart) / len;
        hit = grid.stamp(m[o + 2] + (m[o + 5] - m[o + 2]) * f, m[o + 3] + (m[o + 6] - m[o + 3]) * f,
                         m[o + 4] + (m[o + 7] - m[o + 4]) * f, t.r, prog.axis, t.slope) || hit;
      }
    }
    return hit;
  }
  function lowerBound(acc, s) {                              // 最后一个 acc[k] <= s 的 k
    var lo = 0, hi = acc.length - 2;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (acc[mid] <= s) lo = mid; else hi = mid - 1; }
    return lo;
  }
  function prepare(part) {
    if (part._ready) return;
    part.steps.forEach(function (st) {
      st.programs.forEach(function (p) {
        var acc = new Float64Array(p.count + 1), m = p.moves;
        for (var k = 0; k < p.count; k++) {
          var o = k * 9, dx = m[o + 5] - m[o + 2], dy = m[o + 6] - m[o + 3], dz = m[o + 7] - m[o + 4];
          var d = Math.sqrt(dx * dx + dy * dy + dz * dz);
          acc[k + 1] = acc[k] + (m[o] ? d : d * 0.25);       // 快移按四分之一路程算：放得快一些
        }
        p._acc = acc; p._len = acc[p.count];
      });
      st._len = st.programs.reduce(function (s, p) { return s + p._len; }, 0);
    });
    part._ready = true;
  }

  /* ---------------- 三维场景 ---------------- */
  function Scene(canvas) {
    var T = THREE;
    this.renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.scene = new T.Scene();
    this.camera = new T.PerspectiveCamera(35, 1, 1, 20000);
    this.camera.up.set(0, 0, 1);
    this.controls = new T.OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.scene.add(new T.HemisphereLight(0xffffff, 0x8a8f98, 0.95));
    var d = new T.DirectionalLight(0xffffff, 0.75); d.position.set(-0.6, -0.9, 1.4); this.scene.add(d);
    this.group = new T.Group(); this.scene.add(this.group);
    var self = this;
    var loop = function () { self.controls.update(); self.resize(); self.renderer.render(self.scene, self.camera); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
  Scene.prototype.resize = function () {
    var c = this.renderer.domElement, w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.floor(w * this.renderer.getPixelRatio()) || c.height !== Math.floor(h * this.renderer.getPixelRatio())) {
      this.renderer.setSize(w, h, false); this.camera.aspect = w / Math.max(1, h); this.camera.updateProjectionMatrix();
    }
  };
  Scene.prototype.setGrid = function (grid, stock) {
    var T = THREE, g = this.group;
    while (g.children.length) { var ch = g.children.pop(); if (ch.geometry) ch.geometry.dispose(); }
    this.grid = grid;
    var mk = function (color) {
      var geo = new T.BufferGeometry();
      var pos = new Float32Array(grid.nx * grid.ny * 3), col = new Float32Array(grid.nx * grid.ny * 3);
      for (var j = 0; j < grid.ny; j++) for (var i = 0; i < grid.nx; i++) {
        var k = (j * grid.nx + i) * 3; pos[k] = grid.x0 + i * grid.cell; pos[k + 1] = grid.y0 + j * grid.cell;
      }
      geo.setAttribute('position', new T.BufferAttribute(pos, 3));
      geo.setAttribute('color', new T.BufferAttribute(col, 3));
      var mesh = new T.Mesh(geo, new T.MeshStandardMaterial({ vertexColors: true, flatShading: true, metalness: 0.25, roughness: 0.62, side: T.DoubleSide }));
      mesh.userData.base = color; g.add(mesh); return mesh;
    };
    this.topMesh = mk(); this.botMesh = mk();
    var box = new T.Box3(new T.Vector3(stock.min[0], stock.min[1], stock.min[2]), new T.Vector3(stock.max[0], stock.max[1], stock.max[2]));
    this.outline = new T.Box3Helper(box, 0x7d8794); this.outline.material.transparent = true; this.outline.material.opacity = 0.45; g.add(this.outline);
    this.pathAll = new T.Line(new T.BufferGeometry(), new T.LineBasicMaterial({ color: 0x9aa4b2, transparent: true, opacity: 0.16 }));
    this.pathDone = new T.Line(new T.BufferGeometry(), new T.LineBasicMaterial({ color: 0xe8590c }));
    g.add(this.pathAll); g.add(this.pathDone);
    this.tool = new T.Mesh(new T.CylinderGeometry(1, 1, 1, 28), new T.MeshStandardMaterial({ color: 0xf2c037, metalness: 0.5, roughness: 0.35 }));
    this.tool.geometry.rotateX(Math.PI / 2); this.tool.visible = false; g.add(this.tool);
    var c = box.getCenter(new T.Vector3()), s = box.getSize(new T.Vector3()), R = Math.max(s.x, s.y) * 1.25 + 60;
    this.controls.target.copy(c);
    this.camera.position.set(c.x - R * 0.55, c.y - R * 0.95, c.z + R * 0.75);
    this.camera.near = R / 400; this.camera.far = R * 30; this.camera.updateProjectionMatrix();
    this.refresh(true);
  };
  /* 把高度图画出来：切过的地方亮一些；切穿的格子不画。 */
  Scene.prototype.refresh = function (reindex) {
    var grid = this.grid, nx = grid.nx, ny = grid.ny, n = nx * ny;
    var tp = this.topMesh.geometry.attributes.position.array, bp = this.botMesh.geometry.attributes.position.array;
    var tc = this.topMesh.geometry.attributes.color.array, bc = this.botMesh.geometry.attributes.color.array;
    for (var k = 0; k < n; k++) {
      var zt = grid.top[k], zb = grid.bot[k], o = k * 3;
      tp[o + 2] = Math.max(zt, zb); bp[o + 2] = Math.min(zb, zt);
      var cutT = zt < grid.zTop - 1e-4, cutB = zb > grid.zBot + 1e-4;
      tc[o] = cutT ? 0.78 : 0.52; tc[o + 1] = cutT ? 0.83 : 0.55; tc[o + 2] = cutT ? 0.90 : 0.60;
      bc[o] = cutB ? 0.74 : 0.46; bc[o + 1] = cutB ? 0.80 : 0.49; bc[o + 2] = cutB ? 0.88 : 0.54;
    }
    var through = 0;
    for (k = 0; k < n; k++) if (grid.top[k] <= grid.bot[k] + 1e-3) through++;
    if (reindex || through !== this._through) {
      var idx = new Uint32Array((nx - 1) * (ny - 1) * 6), w = 0;
      for (var j = 0; j < ny - 1; j++) for (var i = 0; i < nx - 1; i++) {
        var a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
        var gone = function (q) { return grid.top[q] <= grid.bot[q] + 1e-3; };
        if (gone(a) && gone(b) && gone(c) && gone(d)) continue;
        idx[w++] = a; idx[w++] = b; idx[w++] = d; idx[w++] = a; idx[w++] = d; idx[w++] = c;
      }
      var ib = new THREE.BufferAttribute(idx.subarray(0, w), 1);
      this.topMesh.geometry.setIndex(ib); this.botMesh.geometry.setIndex(ib.clone());
      this._through = through;
    }
    ['topMesh', 'botMesh'].forEach(function (m) {
      this[m].geometry.attributes.position.needsUpdate = true; this[m].geometry.attributes.color.needsUpdate = true;
      this[m].geometry.computeBoundingSphere();
    }, this);
  };
  Scene.prototype.setPath = function (prog) {
    var pts = new Float32Array((prog.count + 1) * 3), m = prog.moves;
    if (prog.count) { pts[0] = m[2]; pts[1] = m[3]; pts[2] = m[4]; }
    for (var k = 0; k < prog.count; k++) { var o = k * 9; pts[(k + 1) * 3] = m[o + 5]; pts[(k + 1) * 3 + 1] = m[o + 6]; pts[(k + 1) * 3 + 2] = m[o + 7]; }
    [this.pathAll, this.pathDone].forEach(function (ln) {
      ln.geometry.dispose(); ln.geometry = new THREE.BufferGeometry();
      ln.geometry.setAttribute('position', new THREE.BufferAttribute(pts, 3));
    });
    this.pathDone.geometry.setDrawRange(0, 0);
  };
  Scene.prototype.setTool = function (prog, s) {
    if (!prog.count) { this.tool.visible = false; return; }
    var k = Math.min(prog.count - 1, lowerBound(prog._acc, s)), o = k * 9, m = prog.moves;
    var len = prog._acc[k + 1] - prog._acc[k], f = len > 0 ? Math.min(1, (s - prog._acc[k]) / len) : 1;
    var x = m[o + 2] + (m[o + 5] - m[o + 2]) * f, y = m[o + 3] + (m[o + 6] - m[o + 3]) * f, z = m[o + 4] + (m[o + 7] - m[o + 4]) * f;
    var t = toolOf(prog, m[o + 1]), h = Math.max(30, t.len * 2.2), dir = prog.axis === 'bottom' ? -1 : 1;
    this.tool.visible = true; this.tool.scale.set(t.r, t.r, h);
    this.tool.position.set(x, y, z + dir * h / 2);
    var from = Math.max(0, k - 400);                        // 只把刚走过的一小段标亮，不然层数多的程序会糊成一片
    this.pathDone.geometry.setDrawRange(from, k + 2 - from);
  };

  /* ---------------- 播放和界面 ---------------- */
  V.init = function (opts) {
    V.index = opts.index; V.sc = new Scene(el('hdv-canvas')); V.speed = 1; V.playing = false;
    var tabs = el('hdv-parts');
    V.index.forEach(function (p) {
      var b = document.createElement('button'); b.className = 'hdv-tab'; b.textContent = p.name; b.dataset.part = p.id;
      b.onclick = function () { V.selectPart(p.id); }; tabs.appendChild(b);
    });
    el('hdv-play').onclick = function () { V.toggle(); };
    el('hdv-replay').onclick = function () { V.seek(0); V.play(); };
    el('hdv-speed').onchange = function () { V.speed = Number(this.value); };
    el('hdv-scrub').oninput = function () { V.pause(); V.seek(Number(this.value) / 1000 * V.step._len); };
    var want = (location.hash.match(/part=([^&]+)/) || [])[1], ws = (location.hash.match(/step=([^&]+)/) || [])[1];
    var first = V.index.filter(function (p) { return p.id === want; })[0] || V.index.filter(function (p) { return p.steps.some(function (s) { return s.moves > 0; }); })[0] || V.index[0];
    if (first) V.selectPart(first.id, ws);
    var at = (location.hash.match(/at=([0-9.]+)/) || [])[1];
    if (at && V.step && V.step._len) V.seek(Number(at) * V.step._len);   // 链接里带 at=0.6：直接停在这一步的 60% 处
    var last = performance.now();
    var tick = function (now) {
      var dt = Math.min(0.1, (now - last) / 1000); last = now;
      if (V.playing && V.step) V.advance(dt);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  V.selectPart = function (id, stepId) {
    var part = window.HD_EXHIBITS[id]; if (!part) return;
    prepare(part); V.part = part;
    Array.prototype.forEach.call(document.querySelectorAll('.hdv-tab'), function (b) { b.classList.toggle('on', b.dataset.part === id); });
    el('hdv-part-title').textContent = part.name + (part.drawing_no ? '　图号 ' + part.drawing_no : '');
    el('hdv-part-meta').textContent = [part.material && ('材料 ' + part.material), part.stock_mm && ('毛坯 ' + part.stock_mm.join('×') + ' mm'),
      part.card_id && ('工艺卡片 ' + part.card_id), part.scope && ('本次范围：' + part.scope)].filter(Boolean).join('　·　');
    var list = el('hdv-steps'); list.innerHTML = '';
    part.steps.forEach(function (st, i) {
      var d = document.createElement('button'); d.className = 'hdv-step'; d.dataset.i = i;
      var c = st.card || {};
      d.innerHTML = '<span class="hdv-badge ' + (STATUS_CLASS[st.status] || 'idle') + '">' + esc(st.status_zh) + '</span>' +
        '<span class="hdv-step-no">工序 ' + esc(c.card_step || '—') + '　' + esc(st.id) + '</span>' +
        '<span class="hdv-step-how">' + esc([c.method, c.machine, c.fixture].filter(Boolean).join(' · ')) + '</span>' +
        '<span class="hdv-step-text">' + esc(c.card_text || '（工艺计划里没有这一步的卡片原文）') + '</span>';
      d.onclick = function () { V.selectStep(i, true); }; list.appendChild(d);
    });
    V.base = part.stock ? new Grid(part.stock) : null;
    var idx = 0, found = false;
    part.steps.forEach(function (st, i) { if (!found && (stepId ? st.id === stepId : st._len > 0)) { idx = i; found = true; } });
    V.selectStep(idx, false);
  };
  V.selectStep = function (i, autoplay) {
    var part = V.part, st = part.steps[i]; V.step = st; V.stepIndex = i; V.pause();
    Array.prototype.forEach.call(document.querySelectorAll('.hdv-step'), function (b) { b.classList.toggle('on', Number(b.dataset.i) === i); });
    var c = st.card || {};
    var facts = [['结论', st.status_zh], ['重合度（IoU）', st.iou == null ? '—' : fmt(st.iou, 5)], ['真实仿真', st.simulated ? '是' : '否'],
      ['从毛坯连续做下来', st.continuous ? '是' : '否'],
      ['逐面比对', st.faces_checked ? (st.faces_checked + ' 个面' + (st.faces_mismatched ? '，' + st.faces_mismatched + ' 个不符' : '') + (st.faces_unavailable ? '，' + st.faces_unavailable + ' 个判不出' : (st.faces_mismatched ? '' : '，全部符合'))) : '—'],
      ['数控程序', st.programs.length ? st.programs.map(function (p) { return p.id + '（' + p.count + ' 段' + (p.cut_mm ? '，切削 ' + (p.cut_mm / 1000).toFixed(1) + ' m' : '') + (p.axis === 'bottom' ? '，翻面' : p.axis === 'side' ? '，侧向' : '') + '）'; }).join('、') : '这一步没有生成或没有执行'],
      ['证据', '交回单 ' + part.source.report]];
    el('hdv-facts').innerHTML = facts.map(function (f) { return '<div><dt>' + esc(f[0]) + '</dt><dd>' + esc(f[1]) + '</dd></div>'; }).join('');
    el('hdv-card').innerHTML = '<b>工艺卡片 工序 ' + esc(c.card_step || '—') + '</b>' + (c.card_page ? '（第 ' + esc(c.card_page) + ' 页）' : '') +
      '<p>' + esc(c.card_text || '') + '</p>' + (c.requirements && c.requirements.length ? '<p class="hdv-req">要求：' + esc(c.requirements.join('；')) + '</p>' : '');
    el('hdv-note').textContent = st._len > 0 ? '' : (st.status === 'dependency_failed' ? '这一步没有执行：前一步没有通过，系统按规矩停下，不拿目标形状接着往下做。' : '这一步没有可重放的刀路。' + (st.reason ? '原因：' + st.reason : ''));
    if (!V.base) { el('hdv-note').textContent = '这个零件没有留下毛坯范围和刀路，只能看结论。'; return; }
    /* 这一步开始时的材料：把前面各步的程序都切一遍 */
    var g = V.base.clone();
    for (var k = 0; k < i; k++) part.steps[k].programs.forEach(function (p) { carve(g, p, 0, p._len + 1); });
    V.start = g; V.grid = g.clone(); V.sc.setGrid(V.grid, part.stock);
    V.pos = 0; V.progIndex = -1; V.seek(0);
    if (autoplay && st._len > 0) V.play();
  };
  V.locate = function (s) {                                  // 路程 s 落在第几段程序、程序内多少
    var acc = 0, ps = V.step.programs;
    for (var i = 0; i < ps.length; i++) { if (s <= acc + ps[i]._len || i === ps.length - 1) return { i: i, s: Math.min(ps[i]._len, s - acc) }; acc += ps[i]._len; }
    return { i: 0, s: 0 };
  };
  V.seek = function (s) {
    var st = V.step; if (!st || !st.programs.length) { V.sc.tool && (V.sc.tool.visible = false); return; }
    s = Math.max(0, Math.min(st._len, s));
    if (s < V.pos) { V.grid.copyFrom(V.start); V.pos = 0; }
    var a = V.locate(V.pos), b = V.locate(s), dirty = false;
    for (var i = a.i; i <= b.i; i++) {
      var p = st.programs[i];
      dirty = carve(V.grid, p, i === a.i ? a.s : 0, i === b.i ? b.s : p._len + 1) || dirty;
    }
    if (b.i !== V.progIndex) { V.progIndex = b.i; V.sc.setPath(st.programs[b.i]); }
    V.pos = s; V.sc.setTool(st.programs[b.i], b.s);
    if (dirty || s === 0) V.sc.refresh(s === 0);
    el('hdv-scrub').value = st._len ? Math.round(s / st._len * 1000) : 0;
    var p = st.programs[b.i];
    el('hdv-now').textContent = st._len ? ('程序 ' + p.id + (p.axis === 'bottom' ? '（翻面，从另一面切）' : p.axis === 'side' ? '（侧向装夹，只画刀路）' : '') + '　' + Math.round(s / st._len * 100) + '%') : '';
  };
  V.advance = function (dt) {
    var st = V.step; if (!st._len) { V.pause(); return; }
    var s = V.pos + st._len / 22 * V.speed * dt;             // 一步默认约 22 秒放完
    if (s >= st._len) { V.seek(st._len); V.pause(); return; }
    V.seek(s);
  };
  V.play = function () { if (V.step && V.step._len > 0) { if (V.pos >= V.step._len) V.seek(0); V.playing = true; el('hdv-play').textContent = '暂停'; } };
  V.pause = function () { V.playing = false; el('hdv-play').textContent = '播放'; };
  V.toggle = function () { V.playing ? V.pause() : V.play(); };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  window.HDViewer = V;
})();
