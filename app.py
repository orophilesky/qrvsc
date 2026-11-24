#!/usr/bin/env python3
"""Flask check-in service for EventPass."""

import csv
import io
import os
import shutil
import sqlite3
import ssl
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, jsonify, render_template_string, request

try:
    import psycopg
    from psycopg.rows import dict_row

    _PG_DRIVER = "psycopg"
except Exception:  # pragma: no cover - fallback for environments without psycopg
    psycopg = None
    dict_row = None
    try:
        import pg8000.dbapi as pg8000_dbapi

        _PG_DRIVER = "pg8000"
    except Exception:  # pragma: no cover
        pg8000_dbapi = None
        _PG_DRIVER = None


SCANNER_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <meta name="theme-color" content="#0F5C70">
  <title>EventPass Gate Scanner</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;800&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg-deep:#0F5C70;        /* main page background (deep teal) */
      --bg-wave:#2A7A8F;        /* lighter section background / strips */
      --card:#2F7387;           /* cards, panels, modals, inputs */
      --card-border:#3E8397;    /* borders, dividers */
      --text:#FFFFFF;           /* headings and main text */
      --text-muted:#CFE6EC;     /* secondary text, captions */
      --accent-pink:#E58CB5;    /* highlights, hover, badges */
      --accent-purple:#8E6BBF;  /* secondary accent */
      --accent-aqua:#4FB3D1;    /* links, primary buttons, focus ring */
      --bar-off:#4A8497;        /* inactive elements, progress track */
      --bar-on:#FFFFFF;         /* active elements, progress fill */
      /* compatibility aliases for existing selectors */
      --bg:var(--bg-deep); --fg:var(--text); --muted:var(--text-muted);
      --accent:var(--accent-aqua); --accent-light:var(--accent-aqua);
    }
    *{box-sizing:border-box}
    html{scroll-behavior:smooth}
    body{margin:0;min-height:100vh;background:var(--bg-deep);color:var(--text);font:500 16px "Montserrat",system-ui,sans-serif;line-height:1.5;position:relative;overflow-x:hidden}
    body::before{content:"";position:fixed;left:0;right:0;bottom:0;height:38vh;background:
      radial-gradient(120% 90% at 15% 120%, rgba(79,179,209,.28) 0 45%, transparent 46%),
      radial-gradient(120% 90% at 85% 125%, rgba(42,122,143,.55) 0 48%, transparent 49%),
      linear-gradient(180deg, rgba(42,122,143,0) 0%, rgba(42,122,143,.35) 100%);
      pointer-events:none;z-index:0}
    main{max-width:720px;margin:0 auto;padding:32px 20px 40px;position:relative;z-index:1}
    h1{margin:0 0 20px;font:800 clamp(1.9rem,5vw,2.6rem)/1.1 "Montserrat",system-ui,sans-serif;letter-spacing:-0.02em;color:var(--text);text-transform:lowercase}
    .card{background:var(--card);border:1px solid var(--card-border);border-radius:16px;padding:20px;box-shadow:none}
    .stack{display:grid;gap:16px}
    .field label{display:block;color:var(--text-muted);font-size:.95rem;font-weight:600;margin:0 0 8px 2px}
    input,button{width:100%;font:inherit;border:1px solid var(--card-border);border-radius:14px;transition:background-color .2s ease,border-color .2s ease,color .2s ease,transform .15s ease;min-height:48px;box-shadow:none}
    input{padding:14px 16px;background:var(--card);color:var(--text)}
    input::placeholder{color:var(--text-muted);opacity:.85}
    input:focus{outline:none;border-color:var(--accent-aqua);box-shadow:0 0 0 3px rgba(79,179,209,.35)}
    button{padding:14px 18px;cursor:pointer;font-weight:700;background:var(--card);color:var(--text)}
    button:hover{background:var(--bg-wave);border-color:var(--accent-aqua)}
    button:active{transform:translateY(1px)}
    button:focus{outline:none;border-color:var(--accent-aqua);box-shadow:0 0 0 3px rgba(79,179,209,.35)}
    button:disabled{background:var(--bar-off);color:var(--text-muted);cursor:not-allowed}
    a{color:var(--accent-aqua)}
    #submitCode{background:var(--accent-aqua);color:#fff;border-color:var(--accent-aqua)}
    #submitCode:hover{background:var(--accent-pink);border-color:var(--accent-pink);color:#fff}
    #submitCode:active{transform:translateY(1px)}
    #submitCode:focus{box-shadow:0 0 0 3px rgba(79,179,209,.4)}
    #counter{background:var(--card);color:var(--text);text-align:center;border:1px solid var(--card-border);border-radius:14px;padding:16px 18px;font-weight:700;box-shadow:none}
    #reader{width:100%;aspect-ratio:4/3;max-height:70vh;border:1px solid var(--card-border)!important;border-radius:16px;overflow:hidden;background:var(--bg-wave);position:relative;box-shadow:none;padding:10px}
    #reader video,#reader canvas.qr-canvas{position:absolute;inset:10px;width:calc(100% - 20px)!important;height:calc(100% - 20px)!important;object-fit:cover;display:block;border-radius:12px;background:#111827}
    #reader img{object-fit:cover;border-radius:12px}
    #reader>div{width:100%!important;max-width:100%!important;border-radius:12px;overflow:hidden}
    #manual{display:flex;gap:12px;align-items:stretch}
    #manual input{min-width:0;flex:1}
    #manual button{width:auto;min-width:132px;margin:0;flex:0 0 auto}
    #status{color:var(--text-muted);margin-top:4px;font-size:.95rem;padding:0 4px}
    #overlay{display:none;position:fixed;inset:0;z-index:5;align-items:center;justify-content:center;text-align:center;padding:24px;color:#fff;font:800 clamp(2rem,10vw,4rem)/1.05 "Montserrat",system-ui,sans-serif;letter-spacing:-0.02em}
    #overlay small{display:block;font:500 1.15rem "Montserrat",system-ui,sans-serif;margin-top:16px;overflow-wrap:anywhere;letter-spacing:normal}
    @media (max-width:640px){
      main{padding:20px 14px 28px}
      .card{padding:16px;border-radius:16px}
      #reader{border-radius:16px;padding:8px}
      #reader video,#reader canvas.qr-canvas{inset:8px;width:calc(100% - 16px)!important;height:calc(100% - 16px)!important;border-radius:12px}
      #manual{flex-direction:column}
      #manual button{width:100%}
    }
  </style>
</head>
<body><main>
  <h1>EventPass Gate Scanner</h1>
  <div class="card stack">
    <div class="field"><label for="gate">Gate name</label><input id="gate" autocomplete="off" placeholder="Gate A"></div>
    <div class="field"><label for="pin">Staff PIN</label><input id="pin" inputmode="numeric" type="password" autocomplete="off" placeholder="Staff PIN"></div>
    <div id="counter">Entered -- / -- | Remaining --</div>
    <div id="reader"></div>
    <div class="field"><label for="manualCode">Manual fallback</label>
      <div id="manual"><input id="manualCode" maxlength="12" autocomplete="off" placeholder="12-character code"><button id="submitCode" type="button">Scan code</button></div>
    </div>
    <div id="status">Allow camera access to scan.</div>
  </div>
</main><div id="overlay"></div>
<script>
(() => {
  const gate = document.getElementById('gate'), pin = document.getElementById('pin');
  const overlay = document.getElementById('overlay'), status = document.getElementById('status');
  const reader = document.getElementById('reader');
  const seen = new Map(); let showing = false;
  const color = {OK:'#1F9D6A', USED:'#C75D7A', INVALID:'#C75D7A', ERROR:'#B45309'};

  let activeMode = null; // 'native' | 'fallback'
  let native = {stream:null, video:null, canvas:null, ctx:null, detector:null, raf:0, detecting:false, lastDetect:0, running:false};
  let fallback = {scanner:null, starting:false, running:false, scriptPromise:null};

  function tone(ok) {
    try { const audio = new (window.AudioContext || window.webkitAudioContext)();
      const oscillator = audio.createOscillator(), gain = audio.createGain();
      oscillator.frequency.value = ok ? 880 : 220; gain.gain.value = .08;
      oscillator.connect(gain); gain.connect(audio.destination); oscillator.start();
      oscillator.stop(audio.currentTime + (ok ? .12 : .3)); oscillator.onended = () => audio.close();
    } catch (_) {}
    if (navigator.vibrate) navigator.vibrate(ok ? [100,50,100] : [300]);
  }
  async function stats() {
    try { const response = await fetch('/api/stats'); if (!response.ok) return;
      const data = await response.json();
      if (typeof data.entered === 'number') {
        document.getElementById('counter').textContent =
          `Entered ${data.entered} / ${data.total} | Remaining ${data.remaining}`;
      }
    } catch (_) {}
  }

  function stopNative() {
    native.running = false;
    if (native.raf) { cancelAnimationFrame(native.raf); native.raf = 0; }
    native.detecting = false;
    if (native.stream) {
      try { native.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
      native.stream = null;
    }
    if (native.video) {
      try { native.video.pause(); native.video.srcObject = null; native.video.remove(); } catch (_) {}
      native.video = null;
    }
  }
  async function stopFallback() {
    if (fallback.scanner && fallback.running) {
      try { await fallback.scanner.stop(); } catch (_) {}
      try { fallback.scanner.clear(); } catch (_) {}
    }
    fallback.running = false;
  }
  async function stopScanning() {
    stopNative();
    await stopFallback();
  }

  async function scan(raw) {
    const code = String(raw || '').trim().toUpperCase(), now = Date.now();
    if (showing || !code || (seen.has(code) && now - seen.get(code) < 3000)) return;
    seen.set(code, now); showing = true;
    if (seen.size > 200) for (const [key, stamp] of seen) if (now - stamp >= 3000) seen.delete(key);
    // immediate feedback: avoid black screen and show progress while API responds
    const mode = activeMode;
    try { if (navigator.vibrate) navigator.vibrate(40); } catch (_) {}
    overlay.textContent = 'CHECKING...';
    overlay.style.background = '#2A7A8F';
    overlay.style.display = 'flex';
    status.textContent = `Checking ${code}...`;
    // stop camera/scan loop immediately on detection to save CPU/battery
    await stopScanning();
    try {
      const response = await fetch('/api/scan', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({code, gate:gate.value.trim(), pin:pin.value})});
      let data = null;
      try { data = await response.json(); } catch (_) {}
      if (!response.ok) {
        const message = (data && data.error) ? data.error : (response.status === 401 ? 'Wrong staff PIN' : `Request failed (${response.status})`);
        throw new Error(message);
      }
      if (!data || !data.status) throw new Error('Invalid server response');
      let message, success = false;
      if (data.status === 'OK') {message = `ENTRY OK<small>Pass ${data.serial}</small>`;success = true;}
      else if (data.status === 'USED') message = `ALREADY USED<small>${data.used_at || ''} ? ${data.gate || 'Unknown gate'}</small>`;
      else message = 'INVALID PASS';
      overlay.innerHTML = message; overlay.style.background = color[data.status] || color.INVALID;
      status.textContent = data.status === 'OK' ? `Checked in: ${data.serial}` : data.status;
      tone(success); stats().catch(() => {});
    } catch (error) {
      const text = (error && error.name === 'TypeError') ? 'NETWORK ERROR, scan again' : (error.message || 'Scan failed');
      overlay.textContent = text;
      overlay.style.background = color.ERROR; tone(false);
      status.textContent = text;
    }
    overlay.style.display = 'flex';
    setTimeout(async () => {
      overlay.style.display='none'; showing=false;
      // resume camera for next scan
      if (mode === 'native') startNative().catch(() => startFallback());
      else if (mode === 'fallback') startFallback().catch(() => {});
      else startScanner().catch(() => {});
    }, 2000);
  }

  function loadHtml5Qrcode() {
    if (window.Html5Qrcode) return Promise.resolve();
    if (fallback.scriptPromise) return fallback.scriptPromise;
    fallback.scriptPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
      s.async = true;
      s.onload = () => window.Html5Qrcode ? resolve() : reject(new Error('Scanner library unavailable'));
      s.onerror = () => reject(new Error('Scanner library unavailable'));
      document.head.appendChild(s);
    });
    return fallback.scriptPromise;
  }

  async function startFallback() {
    if (fallback.running || fallback.starting || showing) return;
    fallback.starting = true;
    try {
      stopNative();
      status.textContent = 'Loading scanner...';
      await loadHtml5Qrcode();
      if (!fallback.scanner) fallback.scanner = new Html5Qrcode('reader');
      const config = { fps: 12, qrbox: {width: 250, height: 250}, aspectRatio: 1.0 };
      await fallback.scanner.start(
        { facingMode: { exact: 'environment' } },
        config,
        text => scan(text),
        () => {}
      ).catch(async () => {
        await fallback.scanner.start({ facingMode: 'environment' }, config, text => scan(text), () => {});
      });
      fallback.running = true; activeMode = 'fallback';
      status.textContent = 'Camera ready.';
    } catch (e) {
      try { await stopFallback(); } catch (_) {}
      status.textContent = 'Camera unavailable. Use manual fallback or HTTPS.';
    } finally {
      fallback.starting = false;
    }
  }

  async function startNative() {
    if (native.running || showing) return;
    if (!('BarcodeDetector' in window)) throw new Error('No BarcodeDetector');
    let formats = ['qr_code'];
    try {
      if (BarcodeDetector.getSupportedFormats) {
        const supported = await BarcodeDetector.getSupportedFormats();
        if (!supported.includes('qr_code')) throw new Error('QR not supported');
      }
    } catch (_) { /* continue; constructor may still work */ }
    try { native.detector = new BarcodeDetector({ formats }); } catch (e) { throw e; }

    stopNative();
    reader.innerHTML = '';
    const video = document.createElement('video');
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    video.playsInline = true;
    video.muted = true;
    video.autoplay = true;
    reader.appendChild(video);
    if (!native.canvas) {
      native.canvas = document.createElement('canvas');
      native.ctx = native.canvas.getContext('2d', { willReadFrequently: true });
    }
    native.video = video;

    const constraints = { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 24, max: 30 } }, audio: false };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    native.stream = stream;
    video.srcObject = stream;
    await video.play().catch(() => {});
    // best-effort continuous autofocus/exposure
    try {
      const track = stream.getVideoTracks()[0];
      const caps = track.getCapabilities ? track.getCapabilities() : {};
      const adv = {};
      if (caps.focusMode && caps.focusMode.includes('continuous')) adv.focusMode = 'continuous';
      if (caps.exposureMode && caps.exposureMode.includes('continuous')) adv.exposureMode = 'continuous';
      if (caps.whiteBalanceMode && caps.whiteBalanceMode.includes('continuous')) adv.whiteBalanceMode = 'continuous';
      if (Object.keys(adv).length) await track.applyConstraints({ advanced: [adv] });
    } catch (_) {}

    native.running = true; activeMode = 'native'; native.lastDetect = 0; native.detecting = false;
    status.textContent = 'Camera ready.';

    const tick = async (ts) => {
      if (!native.running || showing) return;
      native.raf = requestAnimationFrame(tick);
      if (native.detecting) return;
      if (ts - native.lastDetect < 85) return; // ~11-12 fps
      native.lastDetect = ts;
      const vw = video.videoWidth, vh = video.videoHeight;
      if (!vw || !vh) return;
      native.detecting = true;
      try {
        // decode a downscaled center ROI instead of full frame
        const roi = Math.min(vw, vh) * 0.6;
        const sx = Math.max(0, (vw - roi) / 2), sy = Math.max(0, (vh - roi) / 2);
        const scale = Math.min(1, 640 / roi);
        const dw = Math.max(1, Math.round(roi * scale)), dh = Math.max(1, Math.round(roi * scale));
        if (native.canvas.width !== dw || native.canvas.height !== dh) { native.canvas.width = dw; native.canvas.height = dh; }
        native.ctx.drawImage(video, sx, sy, roi, roi, 0, 0, dw, dh);
        const barcodes = await native.detector.detect(native.canvas);
        if (barcodes && barcodes.length && native.running && !showing) {
          const value = barcodes[0].rawValue || '';
          if (value) scan(value);
        }
      } catch (_) {
        // ignore per-frame decode errors
      } finally {
        native.detecting = false;
      }
    };
    native.raf = requestAnimationFrame(tick);
  }

  async function startScanner() {
    try {
      await startNative();
    } catch (_) {
      await startFallback();
    }
  }

  document.getElementById('submitCode').addEventListener('click', () => {
    const input = document.getElementById('manualCode'); scan(input.value); input.value = '';
  });
  document.getElementById('manualCode').addEventListener('keydown', e => {
    if (e.key === 'Enter') {e.preventDefault();document.getElementById('submitCode').click();}
  });
  stats(); setInterval(stats, 15000);
  startScanner();
})();
</script></body></html>"""

ADMIN_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0F5C70">
<title>EventPass Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;800&display=swap" rel="stylesheet">
<style>
:root{--bg-deep:#0F5C70;--bg-wave:#2A7A8F;--card:#2F7387;--card-border:#3E8397;--text:#FFFFFF;--text-muted:#CFE6EC;--accent-pink:#E58CB5;--accent-purple:#8E6BBF;--accent-aqua:#4FB3D1;--bar-off:#4A8497;--bar-on:#FFFFFF;--bg:var(--bg-deep);--fg:var(--text);--muted:var(--text-muted);--accent:var(--accent-aqua);--accent-light:var(--accent-aqua)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;background:var(--bg-deep);color:var(--text);font:500 15px "Montserrat",system-ui,sans-serif;line-height:1.5;position:relative;overflow-x:hidden}
body::before{content:"";position:fixed;left:0;right:0;bottom:0;height:38vh;background:radial-gradient(120% 90% at 15% 120%, rgba(79,179,209,.28) 0 45%, transparent 46%),radial-gradient(120% 90% at 85% 125%, rgba(42,122,143,.55) 0 48%, transparent 49%),linear-gradient(180deg, rgba(42,122,143,0) 0%, rgba(42,122,143,.35) 100%);pointer-events:none;z-index:0}
main{max-width:1100px;margin:0 auto;padding:32px 20px 40px;position:relative;z-index:1}
h1{margin:0 0 20px;font:800 clamp(2rem,4vw,2.8rem)/1.1 "Montserrat",system-ui,sans-serif;letter-spacing:-.02em;color:var(--text);text-transform:lowercase}
h2{margin:0 0 12px;font:800 1.2rem "Montserrat",system-ui,sans-serif;letter-spacing:-.01em;color:var(--text);text-transform:lowercase}
.card{background:var(--card);border:1px solid var(--card-border);border-radius:16px;padding:20px;box-shadow:none;margin:18px 0}
.controls,.search-row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
label{color:var(--text-muted);font-weight:600}
input,button{font:inherit;border:1px solid var(--card-border);border-radius:14px;min-height:48px;transition:background-color .2s ease,border-color .2s ease,color .2s ease;box-shadow:none}
input{padding:12px 14px;background:var(--card);color:var(--text)}
input::placeholder{color:var(--text-muted);opacity:.85}
input:focus{outline:none;border-color:var(--accent-aqua);box-shadow:0 0 0 3px rgba(79,179,209,.35)}
button{padding:12px 16px;cursor:pointer;font-weight:700;background:var(--card);color:var(--text)}
button:hover{background:var(--bg-wave);border-color:var(--accent-aqua)}
button:active{transform:translateY(1px)}
button:focus{outline:none;border-color:var(--accent-aqua);box-shadow:0 0 0 3px rgba(79,179,209,.35)}
button:disabled{background:var(--bar-off);color:var(--text-muted);cursor:not-allowed}
a{color:var(--accent-aqua)}
#refresh,#search{background:var(--accent-aqua);color:#fff;border-color:var(--accent-aqua)}
#refresh:hover,#search:hover{background:var(--accent-pink);border-color:var(--accent-pink);color:#fff}
#refresh:active,#search:active{transform:translateY(1px)}
#message{font-weight:700;margin:0;color:var(--text)}
#stats,#gates,#searchResult{color:var(--text)}
#searchResult{margin-top:12px}
#searchResult button{margin:8px 8px 0 0}
.scroll{overflow:auto;border:1px solid var(--card-border);border-radius:16px;padding:8px;box-shadow:none;background:var(--card)}
table{border-collapse:separate;border-spacing:0;width:100%;min-width:640px}
td,th{padding:12px 10px;text-align:left;border-bottom:1px solid var(--card-border)}
th{background:transparent;color:var(--text-muted);font-weight:700;position:sticky;top:0}
tr:last-child td{border-bottom:0}
@media (max-width:720px){main{padding:20px 14px 28px}.card{padding:16px;border-radius:16px}.controls,.search-row{flex-direction:column;align-items:stretch}.controls label{width:100%}.controls input,.search-row input,.controls button,.search-row button{width:100%}}
</style></head><body><main>
<h1>EventPass Admin</h1>
<div class="card controls">
  <label for="pin">Admin PIN</label>
  <input id="pin" type="password" autocomplete="off" placeholder="Admin PIN">
  <button id="refresh">Load live report</button>
  <button id="export">Export CSV</button>
</div>
<div class="card"><p id="message">Enter admin PIN to view reports.</p><div id="stats"></div></div>
<div class="card"><h2>Per-gate counts</h2><div id="gates"></div></div>
<div class="card"><h2>Search pass by code or serial</h2>
  <div class="search-row"><input id="query" placeholder="Code or serial"><button id="search">Search</button></div>
  <div id="searchResult"></div>
</div>
<div class="card"><h2>Recent scans (last 50)</h2><div class="scroll"><table><thead><tr>
<th>Time</th><th>Code</th><th>Result</th><th>Gate</th><th>Serial</th></tr></thead><tbody id="scans"></tbody></table></div></div>
</main>
<script>
const pin=()=>document.getElementById('pin').value;
async function post(url,body={}){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({...body,pin:pin()})});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
async function refresh(){try{const d=await post('/admin/api/data');document.getElementById('message').textContent='Updated.';
document.getElementById('stats').textContent=`Total ${d.total} | Entered ${d.entered} | Remaining ${d.remaining}`;
document.getElementById('gates').textContent=d.gates.map(x=>`${x.gate||'(no gate)'}: ${x.count}`).join(' ? ');
document.getElementById('scans').innerHTML=d.scans.map(x=>`<tr><td>${escapeHtml(x.ts)}</td><td>${escapeHtml(x.code)}</td>
<td>${escapeHtml(x.result)}</td><td>${escapeHtml(x.gate||'')}</td><td>${x.serial||''}</td></tr>`).join('');
}catch(e){document.getElementById('message').textContent=e.message;}}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
document.getElementById('refresh').onclick=refresh;
document.getElementById('search').onclick=async()=>{try{const d=await post('/admin/api/search',{query:document.getElementById('query').value});
const p=d.pass;if(!p){document.getElementById('searchResult').textContent='No matching pass.';return;}
const box=document.getElementById('searchResult');box.textContent=`Serial ${p.serial} ? ${p.code} ? ${p.used?'USED':'UNUSED'} ? ${p.gate||''} ? Revoked ${!!p.revoked} `;
if(p.used){const b=document.createElement('button');b.textContent='Undo entry';b.onclick=()=>action('undo',p.code);box.appendChild(b);}
if(!p.revoked){const b=document.createElement('button');b.textContent='Revoke pass';b.onclick=()=>action('revoke',p.code);box.appendChild(b);}
}catch(e){document.getElementById('message').textContent=e.message;}};
async function action(action,code){if(!confirm(`${action} pass ${code}?`))return;try{await post('/admin/api/action',{action,code});await refresh();
document.getElementById('search').click();}catch(e){document.getElementById('message').textContent=e.message;}}
document.getElementById('export').onclick=async()=>{try{const r=await fetch('/admin/export.csv',{headers:{'X-Admin-Pin':pin()}});
if(!r.ok)throw Error('Export denied');const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');
a.href=url;a.download='eventpass_checkins.csv';a.click();URL.revokeObjectURL(url);}catch(e){document.getElementById('message').textContent=e.message;}};
setInterval(()=>{if(pin())refresh();},15000);
</script></body></html>"""

class DbRow:
    def __init__(self, data):
        if isinstance(data, dict):
            self._data = dict(data)
            self._values = tuple(data.values())
        elif isinstance(data, DbRow):
            self._data = dict(data._data)
            self._values = tuple(data._values)
        else:
            seq = tuple(data)
            self._data = {}
            self._values = seq

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class CursorWrapper:
    def __init__(self, cursor, use_postgres=False):
        self._cursor = cursor
        self._use_postgres = use_postgres

    @property
    def rowcount(self):
        return getattr(self._cursor, "rowcount", -1)

    @property
    def description(self):
        return self._cursor.description

    def _wrap(self, row):
        if row is None:
            return None
        if isinstance(row, DbRow):
            return row
        if isinstance(row, dict):
            return DbRow(row)
        desc = self._cursor.description
        if desc:
            data = {d[0]: row[i] for i, d in enumerate(desc)}
            return DbRow(data)
        return DbRow(row)

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield self._wrap(row)

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class DBConnection:
    def __init__(self, conn, use_postgres=False):
        self._conn = conn
        self._use_postgres = use_postgres

    def _adapt_sql(self, sql):
        if self._use_postgres:
            if sql.strip().upper() == "BEGIN IMMEDIATE":
                return None
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql, params=None):
        sql = self._adapt_sql(sql)
        if sql is None:
            return CursorWrapper(self._conn.cursor(), self._use_postgres)
        cur = self._conn.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        return CursorWrapper(cur, self._use_postgres)

    def executescript(self, script):
        if self._use_postgres:
            cur = self._conn.cursor()
            try:
                cur.execute(script)
            finally:
                cur.close()
            return
        return self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self._conn.close()
        return False


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _database_url():
    return (
        os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_POSTGRES_URL")
    )


def _connect_postgres(url):
    if _PG_DRIVER == "psycopg":
        return psycopg.connect(url, row_factory=dict_row)
    if _PG_DRIVER == "pg8000":
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        sslmode = (query.get("sslmode", ["require"])[0] or "require").lower()
        ssl_context = None
        if sslmode not in ("disable", "allow"):
            ssl_context = ssl.create_default_context()
            if os.environ.get("SUPABASE_DB_SSL_INSECURE") == "1":
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        kwargs = dict(
            user=urllib.parse.unquote(parsed.username or ""),
            password=urllib.parse.unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=(parsed.path or "/postgres").lstrip("/") or "postgres",
            ssl_context=ssl_context,
        )
        try:
            return pg8000_dbapi.connect(**kwargs)
        except ssl.SSLCertVerificationError:
            if ssl_context is None or os.environ.get("SUPABASE_DB_SSL_INSECURE") == "1":
                raise
            insecure = ssl.create_default_context()
            insecure.check_hostname = False
            insecure.verify_mode = ssl.CERT_NONE
            return pg8000_dbapi.connect(**{**kwargs, "ssl_context": insecure})
    raise RuntimeError("No Postgres driver available. Install pg8000 or psycopg[binary].")


def get_sqlite_connection(db_path):
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


POSTGRES_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS passes (
        serial BIGSERIAL PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        used_at TEXT,
        gate TEXT,
        revoked INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_log (
        id BIGSERIAL PRIMARY KEY,
        ts TEXT NOT NULL,
        code TEXT NOT NULL,
        result TEXT NOT NULL,
        gate TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_audit (
        id BIGSERIAL PRIMARY KEY,
        ts TEXT NOT NULL,
        action TEXT NOT NULL,
        code TEXT NOT NULL,
        detail TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS scan_log_ts_idx ON scan_log(ts)",
    "CREATE INDEX IF NOT EXISTS scan_log_gate_idx ON scan_log(gate)",
]


def init_db(db_path=None, use_postgres=False, database_url=None):
    if use_postgres:
        conn = _connect_postgres(database_url or _database_url())
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='passes'
                ORDER BY ordinal_position
                """
            )
            rows = cur.fetchall()
            cols = [r.get("column_name") if isinstance(r, dict) else r[0] for r in rows] if rows else []
            expected = ["serial", "code", "used", "used_at", "gate", "revoked"]
            if cols and cols != expected:
                cur.execute("DROP TABLE IF EXISTS passes")
            for stmt in POSTGRES_SCHEMA_STATEMENTS:
                cur.execute(stmt)
            conn.commit()
        finally:
            try:
                cur.close()
            finally:
                conn.close()
        return
    with get_sqlite_connection(db_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS passes (
                serial INTEGER PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                used INTEGER DEFAULT 0,
                used_at TEXT,
                gate TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                code TEXT NOT NULL,
                result TEXT NOT NULL,
                gate TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                action TEXT NOT NULL,
                code TEXT NOT NULL,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS scan_log_ts_idx ON scan_log(ts);
            CREATE INDEX IF NOT EXISTS scan_log_gate_idx ON scan_log(gate);
            """
        )


class ScanRateLimiter:
    def __init__(self, limit=10, period=1.0):
        self.limit = limit
        self.period = period
        self.requests = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, ip, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            bucket = self.requests[ip]
            while bucket and now - bucket[0] >= self.period:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def _prepare_db_path(db_path):
    path = str(db_path)
    if not os.environ.get("VERCEL"):
        return path
    source = path
    if not os.path.isabs(source):
        source = os.path.abspath(source)
    target = os.path.join("/tmp", os.path.basename(source) or "passes.db")
    try:
        if os.path.exists(source) and not os.path.exists(target):
            shutil.copy2(source, target)
            for suffix in ("-wal", "-shm"):
                sidecar = source + suffix
                if os.path.exists(sidecar):
                    shutil.copy2(sidecar, target + suffix)
    except OSError:
        pass
    return target


def create_app(config=None):
    app = Flask(__name__)
    configured_db_path = os.environ.get("DB_PATH", "passes.db")
    database_url = _database_url()
    use_postgres = bool(database_url) or bool(config and config.get("USE_POSTGRES"))
    app.config.update(
        DB_PATH=_prepare_db_path(configured_db_path),
        DATABASE_URL=database_url,
        USE_POSTGRES=use_postgres,
        STAFF_PIN=os.environ.get("STAFF_PIN", ""),
        ADMIN_PIN=os.environ.get("ADMIN_PIN", ""),
    )
    if config:
        app.config.update(config)
        if "USE_POSTGRES" in config:
            use_postgres = bool(config["USE_POSTGRES"])
            app.config["USE_POSTGRES"] = use_postgres
        if config.get("DATABASE_URL"):
            app.config["DATABASE_URL"] = config["DATABASE_URL"]
    limiter = ScanRateLimiter(limit=30)

    try:
        init_db(app.config["DB_PATH"], use_postgres=app.config["USE_POSTGRES"], database_url=app.config.get("DATABASE_URL"))
    except Exception:
        if not app.config.get("TESTING"):
            app.logger.exception("Database initialization failed")

    def db():
        if app.config["USE_POSTGRES"]:
            return DBConnection(_connect_postgres(app.config.get("DATABASE_URL") or _database_url()), use_postgres=True)
        return DBConnection(get_sqlite_connection(app.config["DB_PATH"]), use_postgres=False)

    def json_db_error(exc):
        message = str(exc)
        low = message.lower()
        if "readonly" in low or "read-only" in low:
            return jsonify(error="Database is read-only on this deployment. Use a persistent host or an external database."), 500
        if "no such table" in low or "undefinedtable" in low or ("relation" in low and "does not exist" in low):
            return jsonify(error="Database is not initialized on this deployment."), 500
        return jsonify(error=f"Database error: {message}"), 500

    def authorized_admin():
        payload = request.get_json(silent=True) or {}
        provided = request.headers.get("X-Admin-Pin")
        if provided is None:
            provided = payload.get("pin")
        provided_pin = "" if provided is None else str(provided).strip()
        admin_pin = str(app.config["ADMIN_PIN"] or "").strip()
        return bool(admin_pin) and provided_pin == admin_pin

    def admin_required(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not authorized_admin():
                return jsonify(error="Unauthorized"), 401
            return function(*args, **kwargs)

        return wrapped

    @app.get("/")
    def scanner():
        return render_template_string(SCANNER_PAGE)

    @app.get("/api/stats")
    def stats():
        try:
            with db() as connection:
                total = connection.execute("SELECT COUNT(*) FROM passes").fetchone()[0]
                entered = connection.execute(
                    "SELECT COUNT(*) FROM passes WHERE used=1 AND revoked=0"
                ).fetchone()[0]
        except Exception as exc:
            return json_db_error(exc)
        return jsonify(total=int(total or 0), entered=int(entered or 0), remaining=int((total or 0) - (entered or 0)))

    @app.post("/api/scan")
    def scan():
        if not app.testing and not limiter.allow(request.remote_addr or "unknown"):
            return jsonify(error="Too many scans; retry shortly"), 429
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("code") or "").strip().upper()
        gate = str(payload.get("gate") or "").strip()[:100]
        pin = payload.get("pin")
        provided_pin = "" if pin is None else str(pin).strip()
        staff_pin = str(app.config["STAFF_PIN"] or "").strip()
        if not staff_pin or provided_pin != staff_pin:
            try:
                with db() as connection:
                    connection.execute(
                        "INSERT INTO scan_log(ts,code,result,gate) VALUES (?,?,?,?)",
                        (utc_now(), code, "UNAUTHORIZED", gate),
                    )
            except Exception:
                pass
            return jsonify(error="Wrong staff PIN"), 401
        now = utc_now()
        try:
            with db() as connection:
                if app.config["USE_POSTGRES"]:
                    claimed = connection.execute(
                        """UPDATE passes SET used=1, used_at=?, gate=?
                           WHERE code=? AND used=0 AND revoked=0
                           RETURNING serial""",
                        (now, gate, code),
                    ).fetchone()
                    if claimed is not None:
                        result = {"status": "OK", "serial": int(claimed["serial"])}
                        log_result = "OK"
                    else:
                        row = connection.execute(
                            "SELECT serial,used,used_at,gate,revoked FROM passes WHERE code=?",
                            (code,),
                        ).fetchone()
                        if row is None or row["revoked"]:
                            result = {"status": "INVALID"}
                            log_result = "INVALID"
                        else:
                            result = {
                                "status": "USED",
                                "serial": int(row["serial"]),
                                "used_at": row["used_at"],
                                "gate": row["gate"],
                            }
                            log_result = "USED"
                else:
                    connection.execute("BEGIN IMMEDIATE")
                    cursor = connection.execute(
                        """UPDATE passes SET used=1, used_at=?, gate=?
                           WHERE code=? AND used=0 AND revoked=0""",
                        (now, gate, code),
                    )
                    if cursor.rowcount == 1:
                        row = connection.execute(
                            "SELECT serial FROM passes WHERE code=?", (code,)
                        ).fetchone()
                        result = {"status": "OK", "serial": row["serial"]}
                        log_result = "OK"
                    else:
                        row = connection.execute(
                            "SELECT serial,used,used_at,gate,revoked FROM passes WHERE code=?",
                            (code,),
                        ).fetchone()
                        if row is None or row["revoked"]:
                            result = {"status": "INVALID"}
                            log_result = "INVALID"
                        else:
                            result = {
                                "status": "USED",
                                "serial": row["serial"],
                                "used_at": row["used_at"],
                                "gate": row["gate"],
                            }
                            log_result = "USED"
                connection.execute(
                    "INSERT INTO scan_log(ts,code,result,gate) VALUES (?,?,?,?)",
                    (now, code, log_result, gate),
                )
        except Exception as exc:
            return json_db_error(exc)
        return jsonify(result)

    @app.get("/admin")
    def admin_page():
        return render_template_string(ADMIN_PAGE)

    @app.post("/admin/api/data")
    @admin_required
    def admin_data():
        try:
            with db() as connection:
                total = connection.execute("SELECT COUNT(*) FROM passes").fetchone()[0]
                entered = connection.execute(
                    "SELECT COUNT(*) FROM passes WHERE used=1 AND revoked=0"
                ).fetchone()[0]
                gates = connection.execute(
                    """SELECT gate,COUNT(*) AS count FROM passes
                       WHERE used=1 AND revoked=0 GROUP BY gate ORDER BY gate"""
                ).fetchall()
                scans = connection.execute(
                    """SELECT s.ts,s.code,s.result,s.gate,p.serial FROM scan_log s
                       LEFT JOIN passes p ON p.code=s.code ORDER BY s.id DESC LIMIT 50"""
                ).fetchall()
        except Exception as exc:
            return json_db_error(exc)
        return jsonify(
            total=int(total or 0),
            entered=int(entered or 0),
            remaining=int((total or 0) - (entered or 0)),
            gates=[dict(row) for row in gates],
            scans=[dict(row) for row in scans],
        )

    @app.post("/admin/api/search")
    @admin_required
    def admin_search():
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query") or "").strip()
        try:
            with db() as connection:
                if query.isdigit():
                    row = connection.execute(
                        "SELECT serial,code,used,used_at,gate,revoked FROM passes WHERE serial=?",
                        (int(query),),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT serial,code,used,used_at,gate,revoked FROM passes WHERE code=?",
                        (query.upper(),),
                    ).fetchone()
        except Exception as exc:
            return json_db_error(exc)
        return jsonify({"pass": dict(row) if row else None})

    @app.post("/admin/api/action")
    @admin_required
    def admin_action():
        payload = request.get_json(silent=True) or {}
        action = payload.get("action")
        code = str(payload.get("code") or "").strip().upper()
        if action not in ("revoke", "undo") or not code:
            return jsonify(error="Expected action revoke/undo and a pass code"), 400
        try:
            with db() as connection:
                if app.config["USE_POSTGRES"]:
                    pass_row = connection.execute(
                        "SELECT serial,revoked FROM passes WHERE code=? FOR UPDATE",
                        (code,),
                    ).fetchone()
                else:
                    connection.execute("BEGIN IMMEDIATE")
                    pass_row = connection.execute(
                        "SELECT serial,revoked FROM passes WHERE code=?", (code,)
                    ).fetchone()
                if pass_row is None:
                    return jsonify(error="Pass not found"), 404
                if action == "revoke":
                    connection.execute("UPDATE passes SET revoked=1 WHERE code=?", (code,))
                    detail = "Pass revoked"
                else:
                    connection.execute(
                        "UPDATE passes SET used=0,used_at=NULL,gate=NULL WHERE code=?",
                        (code,),
                    )
                    detail = "Entry undone"
                connection.execute(
                    "INSERT INTO admin_audit(ts,action,code,detail) VALUES (?,?,?,?)",
                    (utc_now(), action.upper(), code, detail),
                )
        except Exception as exc:
            return json_db_error(exc)
        serial = pass_row["serial"]
        try:
            serial = int(serial)
        except Exception:
            pass
        return jsonify(status="OK", action=action, serial=serial)

    @app.get("/admin/export.csv")
    def admin_export():
        if not authorized_admin():
            return jsonify(error="Unauthorized"), 401
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("serial", "code", "used", "used_at", "gate"))
        try:
            with db() as connection:
                rows = connection.execute(
                    "SELECT serial,code,used,used_at,gate FROM passes ORDER BY serial"
                )
                writer.writerows(tuple(row) for row in rows)
        except Exception as exc:
            return json_db_error(exc)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=eventpass_checkins.csv"},
        )

    return app


app = create_app()


if __name__ == "__main__":
    if not str(app.config["STAFF_PIN"]).strip() or not str(app.config["ADMIN_PIN"]).strip():
        raise SystemExit("Set STAFF_PIN and ADMIN_PIN before starting EventPass.")
    init_db(app.config["DB_PATH"], use_postgres=app.config.get("USE_POSTGRES", False), database_url=app.config.get("DATABASE_URL"))
    port = int(os.environ.get("PORT", "5000"))
    try:
        from waitress import serve

        serve(app, host="0.0.0.0", port=port, threads=16)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)
