/**
 * Modern Ants Replay Visualizer - Canvas2D renderer
 * Single-file module, no external dependencies.
 */
'use strict';

const AntsVisualizer = (() => {
  const PLAYER_COLORS = [
    '#33bb33', '#3388ee', '#eed622', '#e23333',
    '#ee8833', '#cc44dd', '#33ddcc', '#ee5599',
    '#99dd33', '#7755ee'
  ];
  const FOOD_COLOR = 'hsl(50,20%,50%)';
  const LAND_COLOR = 'hsl(30,35%,35%)';
  const WATER_COLOR = 'hsl(200,15%,12%)';
  const HILL_STROKE = '#fff';
  const DEAD_COLOR = '#ffffff';

  const DIR = { n: [0, -1], s: [0, 1], e: [1, 0], w: [-1, 0] };

  class Visualizer {
    constructor(container) {
      this.container = container;
      this.canvas = document.createElement('canvas');
      this.ctx = this.canvas.getContext('2d');
      this.container.appendChild(this.canvas);

      this.replay = null;
      this.turn = 0;
      this.playing = false;
      this.speed = 1;
      this.cellSize = 0;
      this.offsetX = 0;
      this.offsetY = 0;
      this.shiftX = 0;
      this.shiftY = 0;
      this.zoom = 1;
      this._dragging = false;

      this._buildUI();
      this._bindEvents();
      this._resize();
    }

    load(data) {
      this.replay = typeof data === 'string' ? JSON.parse(data) : data;
      const rd = this.replay.replaydata || this.replay;
      this.map = this._parseMap(rd.map);
      this.rows = rd.map.rows;
      this.cols = rd.map.cols;
      this.maxTurn = this.replay.game_length || (rd.scores ? rd.scores[0].length - 1 : rd.turns || 1000);
      this.ants = this._parseAnts(rd.ants);
      this.food = rd.food || [];
      this.hills = rd.hills || [];
      this.scores = rd.scores || [];
      this.finalScores = this.replay.score || [];
      this.players = rd.players || this.scores.length;
      this.playerNames = this.replay.playernames || [];
      this.fogPlayer = -1; // -1 = no fog, 0+ = show that player's vision
      this.viewRadius2 = (this.replay.replaydata || this.replay).viewradius2 || 77;
      // Precompute ant counts per player per turn
      const numP = this.scores.length || (this.replay.replaydata || this.replay).players || 2;
      this.antCounts = Array.from({length: numP}, () => new Int16Array(this.maxTurn + 1));
      for (const ant of this.ants) {
        for (let tt = ant.spawn; tt < ant.death && tt <= this.maxTurn; tt++) {
          this.antCounts[ant.player][tt]++;
        }
      }
      // Precompute hill stats per player
      this.hillsOwned = Array.from({length: numP}, () => new Int16Array(this.maxTurn + 1));
      this.hillsRazed = Array.from({length: numP}, () => new Int16Array(this.maxTurn + 1));
      this.startingHills = new Array(numP).fill(0);
      for (const h of this.hills) {
        const [, , owner, razeTurn] = h;
        this.startingHills[owner]++;
        for (let tt = 0; tt <= this.maxTurn; tt++) {
          if (tt < razeTurn) this.hillsOwned[owner][tt]++;
        }
      }
      // Count hills razed BY each player (from replay data - razer gets +2 score per raze)
      // We detect razes by score jumps of 2
      for (let p = 0; p < numP; p++) {
        let razed = 0;
        for (let tt = 1; tt <= this.maxTurn && tt < (this.scores[p] || []).length; tt++) {
          if (this.scores[p][tt] - this.scores[p][tt - 1] >= 2) {
            razed += Math.floor((this.scores[p][tt] - this.scores[p][tt - 1]) / 2);
          }
          this.hillsRazed[p][tt] = razed;
        }
        // Fill remaining
        for (let tt = (this.scores[p] || []).length; tt <= this.maxTurn; tt++) {
          this.hillsRazed[p][tt] = razed;
        }
      }
      this.loop = false;
      // Read initial turn from URL
      const urlT = parseInt(new URL(window.location).searchParams.get('t'));
      this.turn = (urlT >= 0 && urlT <= this.maxTurn) ? urlT : 0;
      this._updateTurnLabel();
      this._resize();
      this._render();
      this._togglePlay();
    }

    _parseMap(mapData) {
      const grid = [];
      for (const row of mapData.data) {
        const r = [];
        for (const ch of row) {
          r.push(ch === '%');
        }
        grid.push(r);
      }
      return grid; // true = water
    }

    _parseAnts(antsData) {
      return antsData.map(a => {
        const ant = { spawn: a[2], death: a[3], player: a[4], orders: a[5] };
        // Precompute positions for each turn
        const len = a[3] - a[2] + 1;
        const posX = new Int16Array(len);
        const posY = new Int16Array(len);
        let col = a[1], row = a[0];
        posX[0] = col; posY[0] = row;
        for (let i = 1; i < len && i - 1 < ant.orders.length; i++) {
          const ch = ant.orders[i - 1].toLowerCase();
          const d = DIR[ch];
          if (d) {
            col = (col + d[0] + this.cols) % this.cols;
            row = (row + d[1] + this.rows) % this.rows;
          }
          posX[i] = col; posY[i] = row;
        }
        // Fill remaining if orders shorter than life
        for (let i = ant.orders.length + 1; i < len; i++) {
          posX[i] = col; posY[i] = row;
        }
        ant.posX = posX;
        ant.posY = posY;
        return ant;
      });
    }

    _buildMapCache() {
      if (!this.replay) return;
      // Only build once at 1px per cell - drawImage scales it
      if (this._mapCanvas && this._mapCanvas.width === this.cols) return;
      if (!this._mapCanvas) this._mapCanvas = document.createElement('canvas');
      this._mapCanvas.width = this.cols;
      this._mapCanvas.height = this.rows;
      const mctx = this._mapCanvas.getContext('2d');
      const imgData = mctx.createImageData(this.cols, this.rows);
      const d = imgData.data;
      for (let r = 0; r < this.rows; r++) {
        for (let c = 0; c < this.cols; c++) {
          const i = (r * this.cols + c) * 4;
          if (this.map[r][c]) {
            // Water
            const v = ((r * 7 + c * 13) % 17) / 17;
            const l = 18 + v * 5;
            // Approximate hsl(220,40%,l%) to rgb
            const s = 0.4, h = 220;
            const C = (1 - Math.abs(2 * l / 100 - 1)) * s;
            const X = C * (1 - Math.abs((h / 60) % 2 - 1));
            const m = l / 100 - C / 2;
            d[i] = Math.round((0) * 255 + m * 255);
            d[i+1] = Math.round((X) * 255 + m * 255);
            d[i+2] = Math.round((C) * 255 + m * 255);
          } else {
            // Land
            const v = ((r * 11 + c * 7) % 13) / 13;
            const l = 33 + v * 5;
            const s = 0.35, h = 30;
            const C = (1 - Math.abs(2 * l / 100 - 1)) * s;
            const X = C * (1 - Math.abs((h / 60) % 2 - 1));
            const m = l / 100 - C / 2;
            d[i] = Math.round((C) * 255 + m * 255);
            d[i+1] = Math.round((X) * 255 + m * 255);
            d[i+2] = Math.round((0) * 255 + m * 255);
          }
          d[i+3] = 255;
        }
      }

      // Darken land tiles around hills
      const hills = (this.replay.replaydata || this.replay).hills || [];
      const embossR = 5;
      for (const h of hills) {
        const [hr, hc] = h;
        for (let dr = -embossR; dr <= embossR; dr++) {
          for (let dc = -embossR; dc <= embossR; dc++) {
            const dist = Math.sqrt(dr * dr + dc * dc);
            if (dist > embossR || dist === 0) continue;
            const r2 = ((hr + dr) % this.rows + this.rows) % this.rows;
            const c2 = ((hc + dc) % this.cols + this.cols) % this.cols;
            if (this.map[r2][c2]) continue;
            const i = (r2 * this.cols + c2) * 4;
            const darken = (1 - dist / embossR) * 20;
            d[i] = Math.max(0, d[i] - darken);
            d[i+1] = Math.max(0, d[i+1] - darken);
            d[i+2] = Math.max(0, d[i+2] - darken);
          }
        }
      }
      mctx.putImageData(imgData, 0, 0);
    }

    _buildHillSprites() {
      const img = this._hillImage;
      if (!img) return;
      this._hillSprites = [];
      for (let p = 0; p < PLAYER_COLORS.length; p++) {
        const c = document.createElement('canvas');
        c.width = 60; c.height = 120;
        const cx = c.getContext('2d');
        cx.drawImage(img, 0, 0);
        cx.globalCompositeOperation = 'source-atop';
        cx.fillStyle = PLAYER_COLORS[p];
        cx.fillRect(0, 0, 60, 120);
        cx.globalCompositeOperation = 'source-over';
        this._hillSprites.push(c);
      }
    }

    _buildUI() {
      this.controls = document.createElement('div');
      this.controls.className = 'av-controls';
      this.controls.innerHTML = `
        <canvas class="av-graph-bar"></canvas>
        <div class="av-toolbar">
          <div class="av-toolbar-left">
            <button class="av-btn av-fog-btn" data-action="fog" title="Toggle fog of war">👁 All</button>
          </div>
          <div class="av-toolbar-center">
            <button class="av-btn" data-action="start">⏮︎</button>
            <button class="av-btn" data-action="back">⏪︎</button>
            <button class="av-btn av-play" data-action="play">▶︎</button>
            <button class="av-btn" data-action="fwd">⏩︎</button>
            <button class="av-btn" data-action="end">⏭︎</button>
            <span class="av-turn">0 / 0</span>
          </div>
          <div class="av-toolbar-right">
            <button class="av-btn av-loop-btn" data-action="loop" title="Toggle loop (L)">🔁</button>
            <button class="av-btn av-speed" data-action="slower">−</button>
            <span class="av-turn av-speed-label">1×</span>
            <button class="av-btn av-speed" data-action="faster">+</button>
          </div>
        </div>
      `;
      this.container.appendChild(this.controls);
      this.graphBar = this.controls.querySelector('.av-graph-bar');
      this.turnLabel = this.controls.querySelector('.av-turn');
      this.playBtn = this.controls.querySelector('[data-action="play"]');
      this.speedLabel = this.controls.querySelector('.av-speed-label');
    }

    _bindEvents() {
      window.addEventListener('resize', () => this._resize());

      this.controls.addEventListener('click', e => {
        const action = e.target.dataset.action;
        if (!action || !this.replay) return;
        switch (action) {
          case 'play': this._togglePlay(); break;
          case 'start': this._setTurn(0); break;
          case 'end': this._setTurn(this.maxTurn); break;
          case 'back': this._setTurn(Math.max(0, this.turn - 1), true); break;
          case 'fwd': this._setTurn(Math.min(this.maxTurn, this.turn + 1), true); break;
          case 'slower': this._changeSpeed(-1); break;
          case 'faster': this._changeSpeed(1); break;
          case 'fog': this._toggleFog(); break;
          case 'loop': this._toggleLoop(); break;
        }
      });

      this.graphBar.addEventListener('mousedown', e => {
        this._graphSeeking = true;
        this._seekFromGraph(e);
      });
      window.addEventListener('mousemove', e => {
        if (this._graphSeeking) this._seekFromGraph(e);
      });
      window.addEventListener('mouseup', () => { this._graphSeeking = false; });

      this.container.addEventListener('keydown', e => {
        if (e.key === ' ') { e.preventDefault(); this._togglePlay(); }
        if (e.key === 'ArrowRight') this._setTurn(Math.min(this.maxTurn, this.turn + 1), true);
        if (e.key === 'ArrowLeft') this._setTurn(Math.max(0, this.turn - 1), true);
        if (e.key === '?') this._toggleHelp();
        if (e.key === 'Escape' && this._helpOverlay) this._toggleHelp();
        if (e.key === 'l' || e.key === 'L') this._toggleLoop();
      });

      this.container.setAttribute('tabindex', '0');

      // Click on canvas to detect scoreboard clicks and hover for tooltip
      this.canvas.addEventListener('click', e => {
        if (this._dragged) return;
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        // Check if click is in scoreboard area
        const pad = 10, rowH = 24, panelW = 280;
        const tableTop = pad + 6;
        const numP = this.scores ? this.scores.length : 0;
        if (x >= pad && x <= pad + panelW && y >= tableTop + rowH && y <= tableTop + rowH * (numP + 1)) {
          const clickedRank = Math.floor((y - tableTop - rowH) / rowH);
          // Get sorted order to map visual row to player index
          const t = this.turn;
          const order = Array.from({length: numP}, (_, i) => i);
          const rd = this.replay.replaydata || this.replay;
          order.sort((a, b) => {
            const sa = (this.scores[a] ? this.scores[a][Math.min(t, this.scores[a].length - 1)] || 0 : 0) + (t >= this.maxTurn && rd.bonus ? rd.bonus[a] || 0 : 0);
            const sb = (this.scores[b] ? this.scores[b][Math.min(t, this.scores[b].length - 1)] || 0 : 0) + (t >= this.maxTurn && rd.bonus ? rd.bonus[b] || 0 : 0);
            return sb - sa;
          });
          const p = order[clickedRank];
          if (p !== undefined) {
            this.fogPlayer = this.fogPlayer === p ? -1 : p;
            const btn = this.controls.querySelector('.av-fog-btn');
            if (this.fogPlayer < 0) {
              btn.textContent = '👁 All';
              btn.style.borderColor = '#444';
            } else {
              const name = this.playerNames[this.fogPlayer] || `P${this.fogPlayer + 1}`;
              btn.textContent = '👁 ' + name;
              btn.style.borderColor = PLAYER_COLORS[this.fogPlayer % PLAYER_COLORS.length];
            }
            if (!this.playing) this._render();
          }
        }
      });

      this.canvas.addEventListener('mousemove', e => {
        if (this._dragging) return;
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        this._updateTooltip(mx, my);
      });
      this.canvas.addEventListener('mouseleave', () => { this._hideTooltip(); });

      // Double-click to reset view
      this.canvas.addEventListener('dblclick', e => {
        e.preventDefault();
        this.zoom = 1;
        this.shiftX = 0;
        this.shiftY = 0;
        this.cellSize = Math.min(this.viewW / this.cols, this.viewH / this.rows);
        this.offsetX = (this.viewW - this.cellSize * this.cols) / 2;
        this.offsetY = (this.viewH - this.cellSize * this.rows) / 2;
        if (!this.playing) this._render();
      });

      this.canvas.addEventListener('mousedown', e => {
        this._dragged = false;
        this._dragging = true;
        this._dragX = e.clientX;
        this._dragY = e.clientY;
        this.canvas.style.cursor = 'grabbing';
      });
      window.addEventListener('mousemove', e => {
        if (!this._dragging || !this.replay) return;
        this._dragged = true;
        const dx = e.clientX - this._dragX;
        const dy = e.clientY - this._dragY;
        this._dragX = e.clientX;
        this._dragY = e.clientY;
        this.shiftX -= dx / this.cellSize;
        this.shiftY -= dy / this.cellSize;
        if (!this.playing) this._render();
      });
      window.addEventListener('mouseup', () => {
        this._dragging = false;
        this.canvas.style.cursor = 'grab';
      });
      this.canvas.style.cursor = 'grab';

      this.canvas.addEventListener('wheel', e => {
        e.preventDefault();
        if (!this.replay) return;
        const delta = -e.deltaY * (e.deltaMode === 1 ? 20 : 1);
        this.zoom *= 1 + delta * 0.002;
        this.zoom = Math.max(0.5, Math.min(10, this.zoom));
        this.cellSize = Math.min(this.viewW / this.cols, this.viewH / this.rows) * this.zoom;
        this.offsetX = (this.viewW - this.cellSize * this.cols) / 2;
        this.offsetY = (this.viewH - this.cellSize * this.rows) / 2;
        if (!this.playing) this._render();
      }, { passive: false });
    }

    _resize() {
      const rect = this.container.getBoundingClientRect();
      const controlsH = this.controls.offsetHeight || 48;
      const w = rect.width;
      const h = rect.height - controlsH;
      this.canvas.width = w * devicePixelRatio;
      this.canvas.height = h * devicePixelRatio;
      this.canvas.style.width = w + 'px';
      this.canvas.style.height = h + 'px';
      this.ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      this.viewW = w;
      this.viewH = h;
      if (this.replay) {
        this.cellSize = Math.min(w / this.cols, h / this.rows) * this.zoom;
        this.offsetX = (w - this.cellSize * this.cols) / 2;
        this.offsetY = (h - this.cellSize * this.rows) / 2;
        this._buildMapCache();
        this._render();
      }
    }

    _togglePlay() {
      this.playing = !this.playing;
      this.playBtn.textContent = this.playing ? '⏸︎' : '▶︎';
      if (this.playing) {
        this._animate();
      } else {
        // Animate remainder of current turn
        if (this.turnFrac > 0 && this.turn < this.maxTurn) {
          const startFrac = this.turnFrac;
          const duration = 150 * (1 - startFrac);
          const start = performance.now();
          const finish = () => {
            this.turn++;
            this.turnFrac = 0;
            this._updateTurnLabel();
            this._render();
          };
          if (duration < 10) { finish(); return; }
          const step = (now) => {
            const p = Math.min((now - start) / duration, 1);
            this.turnFrac = startFrac + (1 - startFrac) * p;
            this._render();
            if (p < 1) requestAnimationFrame(step); else finish();
          };
          requestAnimationFrame(step);
        } else {
          this.turnFrac = 0;
          this._render();
        }
      }
    }

    _changeSpeed(dir) {
      const speeds = [0.25, 0.5, 1, 2, 4, 8, 16];
      const i = speeds.indexOf(this.speed);
      const next = Math.max(0, Math.min(speeds.length - 1, i + dir));
      this.speed = speeds[next];
      this.speedLabel.textContent = this.speed + '×';
    }

    _toggleFog() {
      const numP = this.scores.length || 2;
      this.fogPlayer = (this.fogPlayer + 2) % (numP + 1) - 1; // cycles -1, 0, 1, 2, ...
      const btn = this.controls.querySelector('.av-fog-btn');
      if (this.fogPlayer < 0) {
        btn.textContent = '👁 All';
        btn.style.borderColor = '#444';
      } else {
        const name = this.playerNames[this.fogPlayer] || `P${this.fogPlayer + 1}`;
        btn.textContent = '👁 ' + name;
        btn.style.borderColor = PLAYER_COLORS[this.fogPlayer % PLAYER_COLORS.length];
      }
      if (!this.playing) this._render();
    }

    _seekFromGraph(e) {
      if (!this.replay) return;
      const rect = this.graphBar.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      this._setTurn(Math.round(x * this.maxTurn));
    }

    _updateTooltip(mx, my) {
      if (!this.replay) return;
      const cs = this.cellSize;
      const ox = this.offsetX;
      const oy = this.offsetY;
      const sx = ((this.shiftX % this.cols) + this.cols) % this.cols;
      const sy = ((this.shiftY % this.rows) + this.rows) % this.rows;
      const mapCol = Math.floor((mx - ox) / cs + sx) % this.cols;
      const mapRow = Math.floor((my - oy) / cs + sy) % this.rows;
      const col = (mapCol + this.cols) % this.cols;
      const row = (mapRow + this.rows) % this.rows;

      // Find ant at this cell
      for (const ant of this.ants) {
        if (this.turn < ant.spawn || this.turn >= ant.death) continue;
        const idx = this.turn - ant.spawn;
        if (ant.posX[idx] === col && ant.posY[idx] === row) {
          const name = this.playerNames[ant.player] || `Player ${ant.player + 1}`;
          const age = this.turn - ant.spawn;
          this._showTooltip(mx, my, `${name} | Age: ${age}`);
          return;
        }
      }
      this._hideTooltip();
    }

    _showTooltip(x, y, text) {
      if (!this._tooltip) {
        this._tooltip = document.createElement('div');
        this._tooltip.style.cssText = 'position:absolute;background:rgba(0,0,0,0.85);color:#eee;font-size:11px;padding:4px 8px;border-radius:4px;pointer-events:none;white-space:nowrap;z-index:20;';
        this.container.appendChild(this._tooltip);
      }
      this._tooltip.textContent = text;
      this._tooltip.style.left = (x + 12) + 'px';
      this._tooltip.style.top = (y - 8) + 'px';
      this._tooltip.style.display = 'block';
    }

    _hideTooltip() {
      if (this._tooltip) this._tooltip.style.display = 'none';
    }

    _toggleHelp() {
      if (this._helpOverlay) {
        this._helpOverlay.remove();
        this._helpOverlay = null;
        return;
      }
      this._helpOverlay = document.createElement('div');
      this._helpOverlay.style.cssText = 'position:absolute;inset:0;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:50;';
      this._helpOverlay.innerHTML = `<div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:1.5rem;max-width:320px;color:#ccc;font-size:13px;line-height:1.8;">
        <strong style="color:#fff;font-size:14px;">Keyboard Shortcuts</strong><br>
        <code>Space</code> — Play / Pause<br>
        <code>← →</code> — Step back / forward<br>
        <code>+ −</code> — Speed up / down<br>
        <code>L</code> — Toggle loop<br>
        <code>?</code> — This help<br><br>
        <strong style="color:#fff;">Mouse</strong><br>
        Drag — Pan map<br>
        Scroll — Zoom<br>
        Double-click — Reset view<br>
        Click scoreboard — Toggle fog<br>
        Click graph — Seek<br><br>
        <span style="color:#666;font-size:11px;">Press ? to close</span>
      </div>`;
      this._helpOverlay.addEventListener('click', () => this._toggleHelp());
      this.container.appendChild(this._helpOverlay);
    }

    _toggleLoop() {
      this.loop = !this.loop;
      const btn = this.controls.querySelector('.av-loop-btn');
      btn.style.opacity = this.loop ? '1' : '0.4';
      if (!this.playing) this._updateTurnLabel();
    }

    _showEndSummary() {
      if (this._endOverlay) return;
      const rd = this.replay.replaydata || this.replay;
      const numP = this.scores.length;
      const cutoff = rd.cutoff || '';
      const statuses = this.replay.status || [];
      const playerturns = this.replay.playerturns || [];

      // Find winner
      let maxS = -1, winner = -1;
      for (let p = 0; p < numP; p++) {
        const s = (this.scores[p] ? this.scores[p][this.scores[p].length - 1] || 0 : 0) + ((rd.bonus && rd.bonus[p]) || 0);
        if (s > maxS) { maxS = s; winner = p; }
      }

      // Build table rows
      let headerRow = `<tr style="color:#666;font-size:10px;text-transform:uppercase;letter-spacing:0.05em;">
        <th style="text-align:left;padding:6px 8px;">Player</th>
        <th style="padding:6px 8px;">Hills</th>
        <th style="padding:6px 8px;">Razed</th>
        <th style="padding:6px 8px;">Peak Ants</th>
        <th style="padding:6px 8px;">Final Ants</th>
        <th style="padding:6px 8px;">Turns</th>
        <th style="padding:6px 8px;">Status</th>
        <th style="padding:6px 8px;">Score</th>
        <th style="padding:6px 8px;">Bonus</th>
        <th style="padding:6px 12px;font-size:11px;color:#aaa;">Final</th>
      </tr>`;

      // Sort by final score
      const order = Array.from({length: numP}, (_, i) => i);
      order.sort((a, b) => {
        const sa = (this.scores[a] ? this.scores[a][this.scores[a].length - 1] || 0 : 0) + ((rd.bonus && rd.bonus[a]) || 0);
        const sb = (this.scores[b] ? this.scores[b][this.scores[b].length - 1] || 0 : 0) + ((rd.bonus && rd.bonus[b]) || 0);
        return sb - sa;
      });

      let rows = '';
      for (const p of order) {
        const name = this.playerNames[p] || `Player ${p + 1}`;
        const baseScore = this.scores[p] ? this.scores[p][this.scores[p].length - 1] || 0 : 0;
        const bonus = (rd.bonus && rd.bonus[p]) || 0;
        const finalScore = baseScore + bonus;
        const color = PLAYER_COLORS[p % PLAYER_COLORS.length];
        const crown = p === winner ? ' 👑' : '';
        const hillsStart = this.startingHills ? this.startingHills[p] || 0 : 0;
        const hillsEnd = this.hillsOwned ? this.hillsOwned[p][this.maxTurn] || 0 : 0;
        const razed = this.hillsRazed ? this.hillsRazed[p][this.maxTurn] || 0 : 0;
        const finalAnts = this.antCounts ? this.antCounts[p][this.maxTurn] || 0 : 0;
        let peakAnts = 0;
        if (this.antCounts) {
          for (let i = 0; i <= this.maxTurn; i++) {
            if (this.antCounts[p][i] > peakAnts) peakAnts = this.antCounts[p][i];
          }
        }
        const turns = playerturns[p] || this.maxTurn;
        const status = statuses[p] || 'survived';
        const statusColor = status === 'survived' ? '#81c784' : '#e57373';

        rows += `<tr style="border-top:1px solid #2a2a2a;">
          <td style="padding:6px 8px;text-align:left;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;"></span><strong style="color:#eee;">${name}</strong>${crown}</td>
          <td style="padding:6px 8px;color:#ccc;">${hillsEnd}/${hillsStart}</td>
          <td style="padding:6px 8px;color:${razed > 0 ? '#81c784' : '#666'};">${razed}</td>
          <td style="padding:6px 8px;color:#ccc;">${peakAnts}</td>
          <td style="padding:6px 8px;color:${finalAnts > 0 ? '#ccc' : '#e57373'};">${finalAnts}</td>
          <td style="padding:6px 8px;color:#ccc;">${turns}</td>
          <td style="padding:6px 8px;color:${statusColor};">${status}</td>
          <td style="padding:6px 8px;color:#ccc;">${baseScore}</td>
          <td style="padding:6px 8px;color:${bonus > 0 ? '#81c784' : bonus < 0 ? '#e57373' : '#666'};">${bonus ? (bonus > 0 ? '+' : '') + bonus : '—'}</td>
          <td style="padding:6px 12px;color:${color};font-weight:bold;font-size:14px;">${finalScore}</td>
        </tr>`;
      }

      this._endOverlay = document.createElement('div');
      this._endOverlay.style.cssText = 'position:absolute;inset:0;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:40;';
      this._endOverlay.innerHTML = `<div style="background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:2rem;text-align:center;max-width:90%;overflow-x:auto;">
        <div style="font-size:18px;font-weight:700;color:#fff;margin-bottom:0.4rem;">Game Over</div>
        <div style="font-size:12px;color:#888;margin-bottom:1.2rem;">${cutoff} • ${this.maxTurn} turns</div>
        <table style="border-collapse:collapse;margin:0 auto;font-size:12px;text-align:center;">
          ${headerRow}${rows}
        </table>
        <div style="margin-top:1.2rem;font-size:11px;color:#555;">Click anywhere to dismiss</div>
      </div>`;
      this._endOverlay.addEventListener('click', () => {
        this._endOverlay.remove();
        this._endOverlay = null;
      });
      this.container.appendChild(this._endOverlay);
    }

    _setTurn(t, animate) {
      if (animate && t !== this.turn && !this.playing) {
        const from = this.turn;
        const dir = t > from ? 1 : -1;
        // Show ant moving from previous position to new position
        this.turn = dir > 0 ? from : t;
        this.turnFrac = dir > 0 ? 0 : 1;
        const duration = 150;
        const start = performance.now();
        const step = (now) => {
          const p = Math.min((now - start) / duration, 1);
          this.turnFrac = dir > 0 ? p : 1 - p;
          this._render();
          if (p < 1) {
            requestAnimationFrame(step);
          } else {
            this.turn = t;
            this.turnFrac = 0;
            this._updateTurnLabel();
            this._render();
          }
        };
        requestAnimationFrame(step);
      } else {
        this.turn = t;
        this.turnFrac = 0;
        this._updateTurnLabel();
        this._render();
      }
    }

    _updateTurnLabel() {
      let text = `${this.turn} / ${this.maxTurn}`;
      if (this.turn >= this.maxTurn && this.replay) {
        const cutoff = (this.replay.replaydata || this.replay).cutoff;
        if (cutoff) text += ` — ${cutoff}`;
      }
      if (this.loop) text += ' 🔁';
      this.turnLabel.textContent = text;
      // Update URL with current turn
      if (this.replay) {
        const url = new URL(window.location);
        url.searchParams.set('t', this.turn);
        history.replaceState(null, '', url);
      }
    }

    _animate() {
      if (!this.playing) return;
      let last = performance.now();
      const BASE_TPS = 6; // turns per second at 1×
      const step = (now) => {
        if (!this.playing) return;
        const elapsed = now - last;
        const interval = 1000 / (BASE_TPS * this.speed);
        this.turnFrac = Math.min(elapsed / interval, 1);
        this._render();
        if (elapsed >= interval) {
          last = now - (elapsed % interval);
          this.turn++;
          if (this.turn >= this.maxTurn) {
            if (this.loop) {
              this.turn = 0;
            } else {
              this.turn = this.maxTurn;
              this.turnFrac = 0;
              this.playing = false;
              this.playBtn.textContent = '▶︎';
              this._updateTurnLabel();
              this._render();
              this._showEndSummary();
              return;
            }
          }
          this._updateTurnLabel();
        }
        if (this.playing) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    _render() {
      const ctx = this.ctx;
      const cs = this.cellSize;
      const ox = this.offsetX;
      const oy = this.offsetY;
      const t = this.turn;

      // Clear
      ctx.fillStyle = '#0d0d0d';
      ctx.fillRect(0, 0, this.viewW, this.viewH);

      if (!this.replay) return;

      // Draw map (blit cached tile with toroidal wrap)
      const sx = ((this.shiftX % this.cols) + this.cols) % this.cols;
      const sy = ((this.shiftY % this.rows) + this.rows) % this.rows;
      const mapW = this.cols * cs;
      const mapH = this.rows * cs;
      if (this._mapCanvas) {
        ctx.imageSmoothingEnabled = false;
        const baseX = ox - sx * cs;
        const baseY = oy - sy * cs;
        for (let dy = -1; dy <= Math.ceil(this.viewH / mapH); dy++) {
          const py = baseY + dy * mapH;
          if (py > this.viewH || py + mapH < 0) continue;
          for (let dx = -1; dx <= Math.ceil(this.viewW / mapW); dx++) {
            const px = baseX + dx * mapW;
            if (px > this.viewW || px + mapW < 0) continue;
            ctx.drawImage(this._mapCanvas, px, py, mapW, mapH);
          }
        }
        ctx.imageSmoothingEnabled = true;
      }

      // Darken wrapped area (fixed viewport-centered rectangle)
      const primX = (this.viewW - mapW) / 2;
      const primY = (this.viewH - mapH) / 2;
      ctx.fillStyle = 'rgba(0,0,0,0.45)';
      if (primY > 0) ctx.fillRect(0, 0, this.viewW, primY);
      if (primY + mapH < this.viewH) ctx.fillRect(0, primY + mapH, this.viewW, this.viewH - primY - mapH);
      if (primX > 0) ctx.fillRect(0, primY, primX, mapH);
      if (primX + mapW < this.viewW) ctx.fillRect(primX + mapW, primY, this.viewW - primX - mapW, mapH);

      // Draw food
      const foodRadius = cs * 0.3;
      for (const f of this.food) {
        const [row, col, spawn, death] = f;
        if (t >= spawn && t < death) {
          const dc = ((col - this.shiftX) % this.cols + this.cols) % this.cols;
          const dr = ((row - this.shiftY) % this.rows + this.rows) % this.rows;
          const bx = ox + dc * cs + cs / 2;
          const by = oy + dr * cs + cs / 2;
          ctx.fillStyle = FOOD_COLOR;
          for (let wy = by - mapH; wy <= this.viewH + mapH; wy += mapH) {
            if (wy < -cs || wy > this.viewH + cs) continue;
            for (let wx = bx - mapW; wx <= this.viewW + mapW; wx += mapW) {
              if (wx < -cs || wx > this.viewW + cs) continue;
              ctx.beginPath();
              ctx.arc(wx, wy, foodRadius, 0, Math.PI * 2);
              ctx.fill();
            }
          }
        }
      }
      ctx.globalAlpha = 1;

      // Draw hills
      for (const h of this.hills) {
        const [row, col, player, razeTurn] = h;
        const dc = ((col - this.shiftX) % this.cols + this.cols) % this.cols;
        const dr = ((row - this.shiftY) % this.rows + this.rows) % this.rows;
        const bx = ox + dc * cs + cs / 2;
        const by = oy + dr * cs + cs / 2;
        const alive = t < razeTurn;
        const color = PLAYER_COLORS[player % PLAYER_COLORS.length];
        const r = cs * 0.48;
        for (let wy = by - mapH; wy <= this.viewH + mapH; wy += mapH) {
          if (wy < -cs || wy > this.viewH + cs) continue;
          for (let wx = bx - mapW; wx <= this.viewW + mapW; wx += mapW) {
            if (wx < -cs || wx > this.viewW + cs) continue;
            ctx.save();
            ctx.translate(wx, wy);

            // Mound with radial gradient: black center to team color edge
            const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
            grad.addColorStop(0, '#000');
            grad.addColorStop(alive ? 0.5 : 0.7, alive ? '#111' : '#333');
            grad.addColorStop(1, alive ? color : color);
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.fillStyle = grad;
            ctx.globalAlpha = alive ? 1 : 0.2;
            ctx.fill();
            ctx.globalAlpha = 1;
            ctx.globalAlpha = 1;

            // Dead hill: very faded
            if (!alive) {
            }

            ctx.restore();
          }
        }
      }

      // Proximity rings for hills under threat
      if (!this._hillRingR) this._hillRingR = {};
      const threatRadius = 10; // cells
      for (const h of this.hills) {
        const [hRow, hCol, owner, razeTurn] = h;
        if (t >= razeTurn) continue; // skip razed hills
        // Find closest enemy ant
        let minDist = Infinity;
        for (const ant of this.ants) {
          if (ant.player === owner) continue;
          if (t < ant.spawn || t >= ant.death) continue;
          const idx = t - ant.spawn;
          let dr = ant.posY[idx] - hRow;
          let dc = ant.posX[idx] - hCol;
          // Toroidal distance
          if (dr > this.rows / 2) dr -= this.rows;
          if (dr < -this.rows / 2) dr += this.rows;
          if (dc > this.cols / 2) dc -= this.cols;
          if (dc < -this.cols / 2) dc += this.cols;
          const dist = Math.sqrt(dr * dr + dc * dc);
          if (dist < minDist) minDist = dist;
        }
        if (minDist <= threatRadius) {
          const targetR = Math.max(cs * 1.5, (minDist - 1) * cs);
          const key = `${hRow},${hCol}`;
          const prev = this._hillRingR[key] || targetR;
          const ringR = prev + (targetR - prev) * 0.15;
          this._hillRingR[key] = ringR;
          const dc = ((hCol - this.shiftX) % this.cols + this.cols) % this.cols;
          const dr2 = ((hRow - this.shiftY) % this.rows + this.rows) % this.rows;
          const hx = ox + dc * cs + cs / 2;
          const hy = oy + dr2 * cs + cs / 2;
          const alpha = Math.max(0, 1 - minDist / threatRadius);
          const color = PLAYER_COLORS[owner % PLAYER_COLORS.length];
          for (let wy = hy - mapH; wy <= this.viewH + mapH; wy += mapH) {
            if (wy < -ringR || wy > this.viewH + ringR) continue;
            for (let wx = hx - mapW; wx <= this.viewW + mapW; wx += mapW) {
              if (wx < -ringR || wx > this.viewW + ringR) continue;
              ctx.strokeStyle = color;
              ctx.lineWidth = Math.max(1.5, cs * 0.15);
              ctx.globalAlpha = alpha * 0.7;
              ctx.beginPath();
              ctx.arc(wx, wy, ringR, 0, Math.PI * 2);
              ctx.stroke();
              // Inner ring slightly smaller
              ctx.globalAlpha = alpha * 0.3;
              ctx.beginPath();
              ctx.arc(wx, wy, ringR - cs * 0.5, 0, Math.PI * 2);
              ctx.stroke();
            }
          }
          ctx.globalAlpha = 1;
        } else {
          delete this._hillRingR[`${hRow},${hCol}`];
        }
      }

      // Draw ants
      const antRadius = cs * 0.4;
      const frac = Math.min((this.turnFrac || 0) * 2, 1);
      for (const ant of this.ants) {
        if (t < ant.spawn || t >= ant.death) continue;

        const idx = t - ant.spawn;
        const col = ant.posX[idx];
        const row = ant.posY[idx];

        // Interpolate to next position
        let nextCol = col, nextRow = row;
        if (idx + 1 < ant.posX.length) {
          nextCol = ant.posX[idx + 1];
          nextRow = ant.posY[idx + 1];
        }

        let dx = nextCol - col;
        let dy = nextRow - row;
        if (dx > this.cols / 2) dx -= this.cols;
        if (dx < -this.cols / 2) dx += this.cols;
        if (dy > this.rows / 2) dy -= this.rows;
        if (dy < -this.rows / 2) dy += this.rows;

        const drawCol = ((col + dx * frac - this.shiftX) % this.cols + this.cols) % this.cols;
        const drawRow = ((row + dy * frac - this.shiftY) % this.rows + this.rows) % this.rows;

        const bx = ox + drawCol * cs + cs / 2;
        const by = oy + drawRow * cs + cs / 2;

        const pColor = PLAYER_COLORS[ant.player % PLAYER_COLORS.length];

        ctx.fillStyle = pColor;
        ctx.globalAlpha = 1;
        for (let wy = by - mapH; wy <= this.viewH + mapH; wy += mapH) {
          if (wy < -cs || wy > this.viewH + cs) continue;
          for (let wx = bx - mapW; wx <= this.viewW + mapW; wx += mapW) {
            if (wx < -cs || wx > this.viewW + cs) continue;
            ctx.beginPath();
            ctx.arc(wx, wy, antRadius, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      // Fog of war
      if (this.fogPlayer >= 0) {
        if (!this._fogCanvas) this._fogCanvas = document.createElement('canvas');
        if (this._fogTurn !== t || this._fogP !== this.fogPlayer || this._fogW !== this.cols || this._fogH !== this.rows) {
          this._fogTurn = t; this._fogP = this.fogPlayer;
          this._fogW = this.cols; this._fogH = this.rows;
          this._fogCanvas.width = this.cols;
          this._fogCanvas.height = this.rows;
          const fctx = this._fogCanvas.getContext('2d');
          fctx.fillStyle = '#000';
          fctx.fillRect(0, 0, this.cols, this.rows);
          fctx.clearRect(0, 0, 0, 0); // reset
          // Mark visible as transparent
          const vr2 = this.viewRadius2;
          const vr = Math.ceil(Math.sqrt(vr2));
          const imgData = fctx.createImageData(this.cols, this.rows);
          const d = imgData.data;
          d.fill(153); // rgba(0,0,0,0.6) = alpha 153
          for (let i = 0; i < d.length; i += 4) { d[i] = 0; d[i+1] = 0; d[i+2] = 0; }
          for (const ant of this.ants) {
            if (ant.player !== this.fogPlayer) continue;
            if (t < ant.spawn || t >= ant.death) continue;
            const idx = t - ant.spawn;
            const ar = ant.posY[idx], ac = ant.posX[idx];
            for (let dr = -vr; dr <= vr; dr++) {
              for (let dc = -vr; dc <= vr; dc++) {
                if (dr * dr + dc * dc <= vr2) {
                  const r2 = ((ar + dr) % this.rows + this.rows) % this.rows;
                  const c2 = ((ac + dc) % this.cols + this.cols) % this.cols;
                  d[(r2 * this.cols + c2) * 4 + 3] = 0;
                }
              }
            }
          }
          fctx.putImageData(imgData, 0, 0);
        }
        // Blit fog (one pixel per cell, scaled by drawImage)
        const sx = ((this.shiftX % this.cols) + this.cols) % this.cols;
        const sy = ((this.shiftY % this.rows) + this.rows) % this.rows;
        const baseX = ox - sx * cs;
        const baseY = oy - sy * cs;
        ctx.imageSmoothingEnabled = false;
        for (let dy = -1; dy <= Math.ceil(this.viewH / mapH) + 1; dy++) {
          const py = baseY + dy * mapH;
          if (py > this.viewH || py + mapH < 0) continue;
          for (let dx = -1; dx <= Math.ceil(this.viewW / mapW) + 1; dx++) {
            const px = baseX + dx * mapW;
            if (px > this.viewW || px + mapW < 0) continue;
            ctx.drawImage(this._fogCanvas, px, py, mapW, mapH);
          }
        }
        ctx.imageSmoothingEnabled = true;
      }

      // Draw combat lines (dying ants to nearby enemies)
      const ar2 = (this.replay.replaydata || this.replay).attackradius2 || 5;
      const combatAnts = [];
      for (const ant of this.ants) {
        if (t < ant.spawn || t >= ant.death) continue;
        const idx = t - ant.spawn;
        const ni = Math.min(idx + 1, ant.posX.length - 1);
        combatAnts.push({ col: ant.posX[ni], row: ant.posY[ni], player: ant.player, dies: ant.death === t + 1 });
      }
      ctx.lineWidth = Math.max(1, Math.pow(cs, 0.3));
      ctx.globalAlpha = 0.8;
      for (let i = 0; i < combatAnts.length; i++) {
        if (!combatAnts[i].dies) continue;
        for (let k = 0; k < combatAnts.length; k++) {
          if (k === i) continue;
          if (combatAnts[i].player === combatAnts[k].player) continue;
          let dx = combatAnts[k].col - combatAnts[i].col;
          let dy = combatAnts[k].row - combatAnts[i].row;
          if (dx > this.cols / 2) dx -= this.cols;
          if (dx < -this.cols / 2) dx += this.cols;
          if (dy > this.rows / 2) dy -= this.rows;
          if (dy < -this.rows / 2) dy += this.rows;
          if (dx * dx + dy * dy <= ar2) {
            const cx = ((combatAnts[i].col - this.shiftX) % this.cols + this.cols) % this.cols;
            const cy = ((combatAnts[i].row - this.shiftY) % this.rows + this.rows) % this.rows;
            const x1 = ox + cx * cs + cs / 2;
            const y1 = oy + cy * cs + cs / 2;
            const x2 = x1 + dx * cs * 0.5;
            const y2 = y1 + dy * cs * 0.5;
            ctx.strokeStyle = PLAYER_COLORS[combatAnts[i].player % PLAYER_COLORS.length];
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
          }
        }
      }
      ctx.globalAlpha = 1;

      // Draw scoreboard
      this._drawScoreboard(ctx);
      this._drawGraphBar();
    }

    _drawScoreboard(ctx) {
      if (!this.scores.length) return;
      const numP = this.scores.length;
      const pad = 10;
      const rowH = 24;
      const panelW = 280;
      const totalRows = numP + 1; // header + players
      const tableH = rowH * totalRows;
      const panelH = 6 + tableH + 6;

      // Background
      ctx.beginPath();
      ctx.fillStyle = 'rgba(10,10,10,0.8)';
      ctx.roundRect ? ctx.roundRect(pad, pad, panelW, panelH, 8) : ctx.rect(pad, pad, panelW, panelH);
      ctx.fill();

      // Column positions (right-aligned values)
      const colName = pad + 14;
      const colHills = pad + panelW - 145;
      const colRazed = pad + panelW - 105;
      const colAnts = pad + panelW - 65;
      const colScore = pad + panelW - 20;

      // Header row
      const tableTop = pad + 6;
      const hy = tableTop + rowH / 2 + 4;
      ctx.font = '9px -apple-system, sans-serif';
      ctx.fillStyle = '#666';
      ctx.textAlign = 'right';
      ctx.fillText('Hills', colHills, hy);
      ctx.fillText('Razed', colRazed, hy);
      ctx.fillText('Ants', colAnts, hy);
      ctx.fillText('Score', colScore, hy);
      ctx.textAlign = 'left';

      const t = this.turn;
      const rd = this.replay.replaydata || this.replay;

      // Sort players by current score descending
      const order = Array.from({length: numP}, (_, i) => i);
      order.sort((a, b) => {
        const rd = this.replay.replaydata || this.replay;
        const sa = (this.scores[a] ? this.scores[a][Math.min(t, this.scores[a].length - 1)] || 0 : 0) + (t >= this.maxTurn && rd.bonus ? rd.bonus[a] || 0 : 0);
        const sb = (this.scores[b] ? this.scores[b][Math.min(t, this.scores[b].length - 1)] || 0 : 0) + (t >= this.maxTurn && rd.bonus ? rd.bonus[b] || 0 : 0);
        if (sb !== sa) return sb - sa;
        const aa = this.antCounts ? this.antCounts[a][Math.min(t, this.maxTurn)] || 0 : 0;
        const ab = this.antCounts ? this.antCounts[b][Math.min(t, this.maxTurn)] || 0 : 0;
        return ab - aa;
      });

      for (let rank = 0; rank < numP; rank++) {
        const p = order[rank];
        const targetY = tableTop + rowH * (rank + 1);
        // Animate row position
        if (!this._scoreboardY) this._scoreboardY = {};
        if (this._scoreboardY[p] === undefined) this._scoreboardY[p] = targetY;
        this._scoreboardY[p] += (targetY - this._scoreboardY[p]) * 0.12;
        const y = this._scoreboardY[p];
        const cy = y + rowH / 2 + 4;
        const color = PLAYER_COLORS[p % PLAYER_COLORS.length];
        const name = this.playerNames[p] || `Player ${p + 1}`;
        const score = this.scores[p] ? this.scores[p][Math.min(t, this.scores[p].length - 1)] || 0 : 0;
        const bonus = (t >= this.maxTurn && rd.bonus && rd.bonus[p]) || 0;
        const ants = (this.antCounts && this.antCounts[p]) ? this.antCounts[p][Math.min(t, this.maxTurn)] || 0 : 0;
        const hillsOwned = this.hillsOwned ? this.hillsOwned[p][Math.min(t, this.maxTurn)] || 0 : 0;
        const hillsStart = this.startingHills ? this.startingHills[p] || 0 : 0;
        const hillsRazed = this.hillsRazed ? this.hillsRazed[p][Math.min(t, this.maxTurn)] || 0 : 0;

        // Color bar left edge
        ctx.fillStyle = color;
        ctx.fillRect(pad + 4, y + 4, 3, rowH - 8);

        // Fog indicator
        if (this.fogPlayer === p) {
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.roundRect ? ctx.roundRect(pad + 4, y + 2, panelW - 12, rowH - 4, 4) : ctx.rect(pad + 4, y + 2, panelW - 12, rowH - 4);
          ctx.stroke();
        }

        // Name
        ctx.font = 'bold 11px -apple-system, sans-serif';
        ctx.fillStyle = '#eee';
        ctx.textAlign = 'left';
        ctx.fillText(name, colName, cy);

        // Stats
        ctx.font = '11px -apple-system, sans-serif';
        ctx.textAlign = 'right';

        // Hills owned/total
        ctx.fillStyle = hillsOwned > 0 ? '#ccc' : '#e57373';
        ctx.fillText(`${hillsOwned}/${hillsStart}`, colHills, cy);

        // Hills razed
        ctx.fillStyle = hillsRazed > 0 ? '#81c784' : '#666';
        ctx.fillText(hillsRazed, colRazed, cy);

        // Ant count
        ctx.fillStyle = ants > 0 ? '#ccc' : '#e57373';
        ctx.fillText(ants, colAnts, cy);

        // Score + bonus
        ctx.fillStyle = color;
        ctx.font = 'bold 11px -apple-system, sans-serif';
        const scoreStr = bonus ? `${score + bonus}` : `${score}`;
        ctx.fillText(scoreStr, colScore, cy);

        ctx.textAlign = 'left';
      }

    }

    _drawGraphBar() {
      const bar = this.graphBar;
      if (!bar || !this.antCounts || !this.antCounts.length) return;
      const rect = bar.getBoundingClientRect();
      const w = rect.width, h = rect.height;
      if (w <= 0 || h <= 0) return;
      bar.width = w * devicePixelRatio;
      bar.height = h * devicePixelRatio;
      const ctx = bar.getContext('2d');
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);

      const t = this.turn;
      const numP = this.antCounts.length;

      // Background
      ctx.fillStyle = '#1a1a1a';
      ctx.fillRect(0, 0, w, h);

      // Find max
      let maxAnts = 1;
      for (let p = 0; p < numP; p++) {
        for (let i = 0; i <= this.maxTurn; i++) {
          if (this.antCounts[p][i] > maxAnts) maxAnts = this.antCounts[p][i];
        }
      }

      const pad = 4;
      const topMargin = 18;  // space for event icons
      const botMargin = 14;  // space for turn numbers
      const innerW = w - pad * 2;
      const innerH = h - topMargin - botMargin;

      // Graph background area
      ctx.fillStyle = '#222';
      ctx.fillRect(pad, topMargin, innerW, innerH);

      // Lines per player
      for (let p = 0; p < numP; p++) {
        ctx.strokeStyle = PLAYER_COLORS[p % PLAYER_COLORS.length];
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        for (let i = 0; i <= this.maxTurn; i++) {
          const x = pad + (i / this.maxTurn) * innerW;
          const y = topMargin + innerH - (this.antCounts[p][i] / maxAnts) * innerH;
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      // Hill raze markers
      for (const hl of this.hills) {
        const [, , player, razeTurn] = hl;
        if (razeTurn <= this.maxTurn) {
          const ex = pad + (razeTurn / this.maxTurn) * innerW;
          const color = PLAYER_COLORS[player % PLAYER_COLORS.length];
          // Vertical line in graph area
          ctx.strokeStyle = color;
          ctx.globalAlpha = 0.4;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(ex, topMargin);
          ctx.lineTo(ex, topMargin + innerH);
          ctx.stroke();
          ctx.globalAlpha = 1;
          // Hill icon above graph
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(ex, 2);
          ctx.lineTo(ex - 5, 14);
          ctx.lineTo(ex + 5, 14);
          ctx.closePath();
          ctx.stroke();
        }
      }

      // Elimination/crash/timeout markers
      const statuses = this.replay.status || [];
      const playerturns = this.replay.playerturns || [];
      for (let p = 0; p < numP && p < statuses.length; p++) {
        const color = PLAYER_COLORS[p % PLAYER_COLORS.length];
        const markers = [];

        // Timeout/crash: playerturns ended early
        if (playerturns[p] && playerturns[p] < this.maxTurn) {
          const s = statuses[p];
          markers.push({ turn: playerturns[p], label: s === 'crashed' ? 'crash' : 'timeout' });
        }

        // Eliminated: ant count reaches 0
        for (let i = 1; i <= this.maxTurn; i++) {
          if (this.antCounts[p][i] === 0 && this.antCounts[p][i - 1] > 0) {
            markers.push({ turn: i, label: 'eliminated' });
            break;
          }
        }

        for (const m of markers) {
          const ex = pad + (m.turn / this.maxTurn) * innerW;
          const ey = 8; // icon in top margin
          // Vertical line in graph area
          ctx.strokeStyle = color;
          ctx.globalAlpha = 0.4;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(ex, topMargin);
          ctx.lineTo(ex, topMargin + innerH);
          ctx.stroke();
          ctx.globalAlpha = 1;
          // Icon above graph
          ctx.strokeStyle = color;
          ctx.fillStyle = color;
          ctx.lineWidth = 2;
          if (m.label === 'timeout') {
            ctx.beginPath();
            ctx.arc(ex, ey, 6, 0, Math.PI * 2);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(ex, ey);
            ctx.lineTo(ex, ey - 4);
            ctx.moveTo(ex, ey);
            ctx.lineTo(ex + 3, ey);
            ctx.stroke();
          } else if (m.label === 'crash') {
            ctx.beginPath();
            ctx.arc(ex, ey, 6, 0, Math.PI * 2);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(ex - 5, ey + 5);
            ctx.lineTo(ex + 5, ey - 5);
            ctx.stroke();
          } else {
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.moveTo(ex - 5, ey - 5);
            ctx.lineTo(ex + 5, ey + 5);
            ctx.moveTo(ex + 5, ey - 5);
            ctx.lineTo(ex - 5, ey + 5);
            ctx.stroke();
          }
        }
      }

      // Playhead
      const tx = pad + (t / this.maxTurn) * innerW;
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(tx, topMargin);
      ctx.lineTo(tx, topMargin + innerH);
      ctx.stroke();

      // Turn markers in bottom margin
      ctx.fillStyle = '#fff';
      ctx.font = '9px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      const step = Math.ceil(this.maxTurn / 10 / 50) * 50;
      for (let i = step; i < this.maxTurn; i += step) {
        const x = pad + (i / this.maxTurn) * innerW;
        ctx.fillStyle = '#fff';
        ctx.fillRect(x, topMargin + innerH - 5, 1, 5);
        ctx.fillText(i, x, h - 2);
      }

      // Current turn label at playhead
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px -apple-system, sans-serif';
      ctx.textAlign = tx > w / 2 ? 'right' : 'left';
      ctx.fillText(`T${t}`, tx + (tx > w / 2 ? -4 : 4), h - 2);
    }
  }

  return { Visualizer };
})();
