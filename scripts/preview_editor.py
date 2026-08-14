#!/usr/bin/env python3
"""
Local HTTP server for subtitle preview + editing.
Serves HTML page (left=video, right=subtitle list), backed by Flask.
Save writes transcript.json and signals exit.
"""

import json
import os
import sys
import threading
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_file,
)

# ----------------------------------------------------------------------------- #
# Config
# ----------------------------------------------------------------------------- #
PORT = int(os.environ.get("PREVIEW_EDITOR_PORT", "8765"))
TRANSCRIPT_PATH = None
VIDEO_PATH = None
MANIFEST = None       # 多语言模式:解析后的 manifest dict
WORKSPACE = None      # 多语言模式:manifest 所在目录
RESULT_DONE = threading.Event()

# ----------------------------------------------------------------------------- #
# Flask app
# ----------------------------------------------------------------------------- #
app = Flask(__name__, static_folder=None)

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>字幕编辑器</title>
<style>
  :root {
    --bg: #f4f3fb;
    --surface: #ffffff;
    --surface-2: #faf9ff;
    --border: #eceaf7;
    --text: #1b1b2a;
    --muted: #8b89a6;
    --accent: #6336e7;
    --accent-light: #6f69f7;
    --accent2: #e5484d;
    --soft: #efebfd;
    --hover: #f4f1fd;
    --grad: linear-gradient(135deg, #6f69f7 0%, #6336e7 100%);
    --shadow: 0 8px 30px rgba(99,54,231,0.10);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* ---- panel header ---- */
  .panel-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 10px 12px 8px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .panel-title { font-size: 12px; font-weight: 600; color: var(--muted); letter-spacing: 0.04em; text-transform: uppercase; flex: 1; }
  .btn {
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    font-size: 12px;
    cursor: pointer;
    transition: background 0.12s;
    white-space: nowrap;
  }
  .btn:hover { background: var(--hover); }
  .btn.danger { color: var(--accent2); border-color: transparent; }
  .btn.danger:hover { background: #fef2f2; border-color: var(--accent2); }
  .btn.save {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
    padding: 6px 16px;
    font-size: 13px;
    width: 100%;
    border-radius: 8px;
  }
  .btn.save:hover { background: #1d4ed8; }

  /* ---- main ---- */
  .main { display: flex; flex: 1; overflow: hidden; position: relative; }

  /* ---- video pane ---- */
  .video-pane {
    flex: 1;
    display: flex;
    flex-direction: column;
    margin-right: 380px;
  }
  .video-wrap {
    flex: 1;
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    position: relative;
  }
  .video-wrap video { width: 100%; height: 100%; object-fit: contain; }
  .current-subtitle {
    position: absolute;
    bottom: 4.5%;
    left: 50%;
    transform: translateX(-50%);
    max-width: 90%;
    text-align: center;
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: clamp(13px, 2.2vw, 22px);
    line-height: 1.5;
    color: #ffffff;
    background: rgba(26, 26, 28, 0.76);
    padding: 4px 10px;
    border-radius: 4px;
    pointer-events: none;
    white-space: pre-wrap;
    word-break: break-word;
    text-shadow: 0 1px 3px rgba(0,0,0,0.8);
    display: none;
  }
  .current-subtitle.visible { display: block; }
  .current-subtitle-zh { font-size: 1em; font-weight: 600; line-height: 1.34; }
  .current-subtitle-en {
    margin-top: 1px;
    color: rgba(255,255,255,.90);
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    font-size: .78em;
    font-weight: 500;
    line-height: 1.28;
  }
  .bilingual-mode .current-subtitle { text-shadow: none; }
  .sub-text-zh { color: #16161f; font-size: 14px; line-height: 1.55; }
  .sub-text-en {
    margin-top: 3px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }
  .content-progress {
    position: absolute;
    z-index: 5;
    right: 0;
    bottom: 0;
    left: 0;
    height: 22px;
    display: none;
    overflow: hidden;
    border-top: 1px solid rgba(255,255,255,.16);
    background: rgba(54,54,58,.62);
    pointer-events: none;
  }
  .content-progress.visible { display: block; }
  .content-progress-fill {
    position: absolute;
    z-index: 1;
    inset: 0 auto 0 0;
    width: 0;
    background: rgba(198,198,204,.50);
  }
  .content-progress-markers { position: absolute; z-index: 2; inset: 0; }
  .content-progress-marker {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 1px;
    background: rgba(255,255,255,.40);
  }
  .content-progress-labels {
    position: absolute;
    z-index: 3;
    inset: 0;
  }
  .content-progress-label {
    position: absolute;
    top: 0;
    bottom: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    padding: 0 2px;
    color: rgba(255,255,255,.94);
    font-size: 11px;
    font-weight: 600;
    line-height: 1;
    letter-spacing: .02em;
    text-align: center;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* ---- list pane: always-visible right panel ---- */
  .list-pane {
    position: absolute;
    top: 0; right: 0; bottom: 0;
    width: 380px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: var(--surface);
    border-left: 1px solid var(--border);
    z-index: 10;
  }

  /* ---- find bar: hidden by default, shown with Ctrl+F ---- */
  .find-bar {
    display: none;
    padding: 8px 12px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }
  .find-bar.show { display: flex; }
  .list-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .list-header span { font-size: 11px; color: var(--muted); }

  /* ---- panel footer ---- */
  .panel-footer {
    padding: 10px 12px;
    border-top: 1px solid var(--border);
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .panel-status { font-size: 11px; color: var(--muted); text-align: center; min-height: 14px; }
  .subtitle-list { flex: 1; overflow-y: auto; padding: 6px; }
  .subtitle-list::-webkit-scrollbar { width: 6px; }
  .subtitle-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  /* ---- subtitle item ---- */
  .sub-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px 10px;
    border-radius: 8px;
    margin-bottom: 3px;
    cursor: pointer;
    transition: background 0.1s;
    position: relative;
    user-select: none;
    -webkit-user-select: none;
  }
  .sub-item:hover { background: var(--hover); }
  .sub-item.deleted { opacity: 0.35; text-decoration: line-through; }
  .sub-item mark {
    background: #fbbf24;
    color: #1a1a18;
    border-radius: 3px;
    padding: 0 1px;
  }
  .sub-check { flex-shrink: 0; margin-top: 4px; width: 16px; height: 16px; cursor: pointer; }
  .sub-times {
    flex-shrink: 0;
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    min-width: 110px;
    margin-top: 3px;
    line-height: 1.6;
  }
  .sub-text-wrap { flex: 1; min-width: 0; }
  .sub-text {
    font-size: 14px;
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    border-radius: 4px;
    padding: 1px 3px;
    margin: -1px -3px;
    outline: none;
    user-select: text;
    -webkit-user-select: text;
  }
  .sub-text:focus {
    background: rgba(37,99,235,0.06);
    box-shadow: 0 0 0 2px rgba(37,99,235,0.2);
  }
  .sub-text[contenteditable="true"] { cursor: text; }
  .sub-actions {
    flex-shrink: 0;
    display: flex;
    gap: 3px;
    margin-top: 1px;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .sub-item:hover .sub-actions { opacity: 1; }
  .icon-btn {
    width: 26px; height: 26px;
    border-radius: 5px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    cursor: pointer;
    font-size: 13px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
  }
  .icon-btn:hover { background: var(--hover); color: var(--text); }
  .icon-btn.del:hover { background: #fef2f2; color: var(--accent2); border-color: var(--accent2); }

  /* ---- find bar ---- */
  .find-bar {
    display: none;
    padding: 8px 12px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }
  .find-bar.show { display: flex; }
  .find-bar input {
    background: #f9f9f7;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    padding: 4px 10px;
    font-size: 13px;
    min-width: 160px;
  }
  .find-bar input:focus { outline: none; border-color: var(--accent); }
  .find-bar label { font-size: 12px; color: var(--muted); }

  /* ---- status bar ---- */
  .status {
    padding: 6px 16px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    font-size: 11px;
    color: var(--muted);
    flex-shrink: 0;
    display: flex;
    gap: 16px;
  }

  /* ============ Qwen 主题 · 清爽现代 ============ */
  body {
    background:
      radial-gradient(1100px 560px at 100% -12%, rgba(111,105,247,.07), transparent 60%),
      var(--bg);
  }
  /* 复选框统一紫色 */
  input[type="checkbox"] { accent-color: var(--accent); }

  /* 品牌标识 */
  .panel-header { padding: 13px 14px 11px; gap: 8px; background: var(--surface); }
  .brand { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
  .qwen-mark { flex: none; filter: drop-shadow(0 2px 5px rgba(99,54,231,.28)); }
  .brand-name { font-size: 14px; font-weight: 700; letter-spacing: .01em; color: var(--text); white-space: nowrap; }

  .list-header { background: var(--surface-2); }

  /* 按钮 */
  .btn { border-radius: 8px; transition: background .14s, border-color .14s, color .14s; }
  .btn:hover { background: var(--hover); border-color: #ddd8f3; }
  .btn.danger:hover { background: #fdecec; border-color: var(--accent2); }

  /* 保存:渐变紫主按钮 */
  .btn.save {
    background: var(--grad);
    border: none;
    color: #fff;
    border-radius: 10px;
    padding: 11px 16px;
    font-size: 13.5px;
    letter-spacing: .03em;
    box-shadow: 0 6px 18px rgba(99,54,231,.30);
    transition: transform .12s ease, box-shadow .12s ease, filter .12s ease;
  }
  .btn.save:hover { background: var(--grad); filter: brightness(1.05); box-shadow: 0 9px 24px rgba(99,54,231,.40); transform: translateY(-1px); }
  .btn.save:active { transform: translateY(0); box-shadow: 0 4px 12px rgba(99,54,231,.34); }

  /* 字幕项 */
  .sub-item { border-radius: 10px; }
  .sub-item:hover { background: var(--hover); }
  /* 正在播放的当前句:紫色高亮 + 左侧色条 */
  .sub-item.current { background: var(--soft); box-shadow: inset 3px 0 0 var(--accent); }
  .sub-item.current .sub-times { color: var(--accent); }

  /* 查找高亮 */
  .sub-item mark { background: #e7defc; color: var(--accent); }

  /* 编辑聚焦:紫色环 */
  .sub-text:focus { background: rgba(99,54,231,.06); box-shadow: 0 0 0 2px rgba(99,54,231,.22); }

  /* 图标按钮 */
  .icon-btn { border-radius: 7px; }
  .icon-btn:hover { background: var(--soft); color: var(--accent); border-color: #ddd6f6; }
  .icon-btn.del:hover { background: #fdecec; color: var(--accent2); border-color: var(--accent2); }

  /* 查找框 */
  .find-bar { background: var(--surface-2); }
  .find-bar input { background: #fff; border-radius: 7px; }
  .find-bar input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(99,54,231,.16); }

  /* 滚动条 */
  .subtitle-list::-webkit-scrollbar-thumb { background: #d9d3f2; }
  .subtitle-list::-webkit-scrollbar-thumb:hover { background: #c4bbef; }

  /* 右面板柔化 */
  .list-pane { border-left: 1px solid var(--border); box-shadow: -12px 0 44px rgba(99,54,231,.045); }
  .panel-footer { background: var(--surface); gap: 9px; }


  /* ====== 字幕条目重排:时间移到文字下方 · 更清爽(gemini-designer 建议) ====== */
  .sub-item {
    display: grid;
    grid-template-columns: 16px 1fr auto;
    grid-template-rows: auto auto;
    column-gap: 10px;
    row-gap: 3px;
    padding: 9px 12px;
    align-items: start;
  }
  /* 复选框:默认隐藏,hover 或选中才显示,降噪 */
  .sub-check { grid-column: 1; grid-row: 1 / span 2; margin-top: 3px; opacity: 0; transition: opacity .15s ease; }
  .sub-item:hover .sub-check, .sub-check:checked { opacity: 1; }
  .sub-text-wrap { grid-column: 2; grid-row: 1; min-width: 0; }
  .sub-text { line-height: 1.62; color: #16161f; }
  /* 时间戳:移到文字下方,退居次级 */
  .sub-times { grid-column: 2; grid-row: 2; min-width: 0; margin-top: 0; font-size: 11px; line-height: 1; letter-spacing: .02em; }
  .sub-actions { grid-column: 3; grid-row: 1; margin-top: 0; }
  /* 减图标:去掉冗余的编辑图标(双击文字即可编辑),只留删除 */
  .sub-actions .icon-btn:not(.del) { display: none; }
  /* 正在播放:去掉左竖条,改用底色 + 文字提亮表达 */
  .sub-item.current { background: var(--soft); box-shadow: none; }
  .sub-item.current .sub-text { color: var(--accent); font-weight: 500; }
  .sub-item.current .sub-times { color: var(--accent); }

  /* 顶部栏(品牌+全选/删除)默认收起,hover 右侧面板时才浮现 → 更清爽 */
  .panel-header { max-height: 0; padding-top: 0; padding-bottom: 0; opacity: 0; overflow: hidden;
    transition: max-height .22s ease, padding .22s ease, opacity .2s ease; }
  .list-pane:hover .panel-header { max-height: 64px; padding-top: 13px; padding-bottom: 11px; opacity: 1; }

  /* 语言 tab(仅多语言时显示;不换行,横向滚动,隐藏滚动条) */
  .lang-tabs { display: none; gap: 6px; padding: 9px 12px 0; background: var(--surface); border-bottom: 1px solid var(--border);
    overflow-x: auto; flex-wrap: nowrap; scrollbar-width: none; -ms-overflow-style: none; }
  .lang-tabs::-webkit-scrollbar { display: none; }
  .lang-tabs.show { display: flex; }
  .lang-tab { flex: 0 0 auto; white-space: nowrap; padding: 6px 14px; border-radius: 9px 9px 0 0;
    border: 1px solid var(--border); border-bottom: none;
    background: var(--surface-2); color: var(--muted); font-size: 12.5px; font-weight: 600; cursor: pointer; transition: all .15s;
    display: flex; align-items: center; gap: 5px; }
  .lang-tab:hover { color: var(--text); }
  .lang-tab.active { background: var(--grad); color: #fff; border-color: transparent; box-shadow: 0 4px 12px rgba(99,54,231,.28); }
  .lang-tab .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-light); }
  .lang-tab.active .dot { background: #fff; }

  /* ---- mobile layout: video top, subtitles bottom ---- */
  @media (max-width: 768px) {
    body { height: 100dvh; overflow: hidden; }
    .main { flex-direction: column; overflow: hidden; }
    .video-pane { margin-right: 0; flex: 0 0 auto; height: 40dvh; }
    .video-wrap video { width: 100%; height: 100%; object-fit: contain; }
    .list-pane {
      position: relative;
      top: auto; right: auto; bottom: auto;
      width: 100%;
      flex: 1;
      border-left: none;
      border-top: 1px solid var(--border);
    }
    .sub-times { min-width: 80px; font-size: 10px; }
    .current-subtitle { font-size: clamp(11px, 3.5vw, 16px); }
  }
</style>
<script src="oil://bridge/html-v1.js"></script>
</head>
<body>

<div class="main">
  <div class="video-pane" id="videoPaneEl">
    <div class="video-wrap">
      <video id="vid" controls src="/video"></video>
      <div class="current-subtitle" id="curSub"></div>
      <div class="content-progress" id="contentProgress">
        <div class="content-progress-fill" id="contentProgressFill"></div>
        <div class="content-progress-markers" id="contentProgressMarkers"></div>
        <div class="content-progress-labels" id="contentProgressLabels"></div>
      </div>
    </div>
  </div>

  <!-- 配音音轨(多语言时按 tab 切换,与视频同步;视频静音) -->
  <audio id="dubAudio" preload="auto"></audio>

  <div class="list-pane" id="listPane">
    <!-- 语言 tab(仅多语言时显示) -->
    <div class="lang-tabs" id="langTabs"></div>

    <!-- Panel header: title + bulk actions -->
    <div class="panel-header">
      <span class="brand">
        <svg class="qwen-mark" viewBox="0 0 24 24" width="22" height="22" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M12.604 1.34c.393.69.784 1.382 1.174 2.075a.18.18 0 00.157.091h5.552c.174 0 .322.11.446.327l1.454 2.57c.19.337.24.478.024.837-.26.43-.513.864-.76 1.3l-.367.658c-.106.196-.223.28-.04.512l2.652 4.637c.172.301.111.494-.043.77-.437.785-.882 1.564-1.335 2.34-.159.272-.352.375-.68.37-.777-.016-1.552-.01-2.327.016a.099.099 0 00-.081.05 575.097 575.097 0 01-2.705 4.74c-.169.293-.38.363-.725.364-.997.003-2.002.004-3.017.002a.537.537 0 01-.465-.271l-1.335-2.323a.09.09 0 00-.083-.049H4.982c-.285.03-.553-.001-.805-.092l-1.603-2.77a.543.543 0 01-.002-.54l1.207-2.12a.198.198 0 000-.197 550.951 550.951 0 01-1.875-3.272l-.79-1.395c-.16-.31-.173-.496.095-.965.465-.813.927-1.625 1.387-2.436.132-.234.304-.334.584-.335a338.3 338.3 0 012.589-.001.124.124 0 00.107-.063l2.806-4.895a.488.488 0 01.422-.246c.524-.001 1.053 0 1.583-.006L11.704 1c.341-.003.724.032.9.34zm-3.432.403a.06.06 0 00-.052.03L6.254 6.788a.157.157 0 01-.135.078H3.253c-.056 0-.07.025-.041.074l5.81 10.156c.025.042.013.062-.034.063l-2.795.015a.218.218 0 00-.2.116l-1.32 2.31c-.044.078-.021.118.068.118l5.716.008c.046 0 .08.02.104.061l1.403 2.454c.046.081.092.082.139 0l5.006-8.76.783-1.382a.055.055 0 01.096 0l1.424 2.53a.122.122 0 00.107.062l2.763-.02a.04.04 0 00.035-.02.041.041 0 000-.04l-2.9-5.086a.108.108 0 010-.113l.293-.507 1.12-1.977c.024-.041.012-.062-.035-.062H9.2c-.059 0-.073-.026-.043-.077l1.434-2.505a.107.107 0 000-.114L9.225 1.774a.06.06 0 00-.053-.031zm6.29 8.02c.046 0 .058.02.034.06l-.832 1.465-2.613 4.585a.056.056 0 01-.05.029.058.058 0 01-.05-.029L8.498 9.841c-.02-.034-.01-.052.028-.054l.216-.012 6.722-.012z" fill="url(#qwenGrad)" fill-rule="nonzero"></path><defs><linearGradient id="qwenGrad" x1="0%" x2="100%" y1="0%" y2="100%"><stop offset="0%" stop-color="#6F69F7"></stop><stop offset="100%" stop-color="#6336E7"></stop></linearGradient></defs></svg>
        <span class="brand-name">字幕校对</span>
      </span>
      <button class="btn" id="btnSelectAll">全选</button>
      <button class="btn danger" id="btnDeleteSelected">删除</button>
    </div>

    <!-- find bar: hidden by default, Ctrl+F to show -->
    <div class="find-bar" id="findBar">
      <label>查找</label><input id="findInput" placeholder="大小写不敏感…">
      <label>替换</label><input id="replaceInput" placeholder="新文本…">
      <button class="btn" id="btnReplaceOne">替换下一个</button>
      <button class="btn" id="btnReplaceAll">全部替换</button>
    </div>

    <!-- subtitle count row -->
    <div class="list-header">
      <input type="checkbox" id="selectAllCheck" title="全选">
      <span id="listInfo"></span>
    </div>

    <div class="subtitle-list" id="list"></div>

    <!-- Panel footer: status + save -->
    <div class="panel-footer">
      <div class="panel-status" id="statusText"></div>
      <button class="btn save" id="btnSave">保存并关闭</button>
    </div>
  </div>
</div>

<script>
const LS_KEY = __CACHE_KEY__;

// ── state ────────────────────────────────────────────────────────────────────
let segments = [];
let deletedIds = new Set();
let editMode = null;
let findIndex = -1;
let currentNeedle = '';
let manifest = null;       // 多语言 manifest
let currentLang = null;    // 当前语言 code
let bilingualMode = false;
let chapters = [];

// ── DOM ─────────────────────────────────────────────────────────────────────
const vid        = document.getElementById('vid');
const curSub     = document.getElementById('curSub');
const listEl     = document.getElementById('list');
const findInput  = document.getElementById('findInput');
const repInput   = document.getElementById('replaceInput');
const selAll     = document.getElementById('selectAllCheck');
const listInfo   = document.getElementById('listInfo');
const statusTxt  = document.getElementById('statusText');
const listPane   = document.getElementById('listPane');
const videoPaneEl = document.getElementById('videoPaneEl');
const dubAudio   = document.getElementById('dubAudio');
const langTabs   = document.getElementById('langTabs');
const contentProgress = document.getElementById('contentProgress');
const contentProgressFill = document.getElementById('contentProgressFill');
const contentProgressMarkers = document.getElementById('contentProgressMarkers');
const contentProgressLabels = document.getElementById('contentProgressLabels');

// ── find bar toggle (Ctrl+F) ──────────────────────────────────────────────────
const findBarEl = document.getElementById('findBar');

function openFindBar() {
  findBarEl.classList.add('show');
  findInput.focus();
  findInput.select();
}
function closeFindBar() {
  findBarEl.classList.remove('show');
  currentNeedle = '';
  render();
}

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    e.preventDefault();
    findBarEl.classList.contains('show') ? closeFindBar() : openFindBar();
  }
  if (e.key === 'Escape' && findBarEl.classList.contains('show')) {
    closeFindBar();
  }
});

document.getElementById('btnClosePanel')?.addEventListener('click', closeFindBar);

// ── helpers ─────────────────────────────────────────────────────────────────
function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function insertMarks(text, needle) {
  if (!needle) return escapeHtml(text);
  const re = new RegExp(escapeRe(needle), 'gi');
  return escapeHtml(text).replace(re, m => `<mark>${m}</mark>`);
}

function fmtSeg(seg) {
  const fmt = s => {
    const m = Math.floor(s / 60);
    const sec = (s % 60).toFixed(2).padStart(5, '0');
    return `${m}:${sec}`;
  };
  return `${fmt(seg.start)} → ${fmt(seg.end)}`;
}

function getVisibleSegments() {
  return segments.filter(s => !deletedIds.has(s._id));
}

function segmentFields() {
  return bilingualMode ? ['zh', 'en'] : ['text'];
}

function segmentSearchText(seg) {
  return segmentFields().map(field => seg[field] || '').join('\n');
}

function renderCurrentSubtitle(seg) {
  curSub.replaceChildren();
  if (!seg) {
    curSub.classList.remove('visible');
    return;
  }
  if (bilingualMode) {
    const zh = document.createElement('div');
    zh.className = 'current-subtitle-zh';
    zh.textContent = (seg.zh || '').trim();
    const en = document.createElement('div');
    en.className = 'current-subtitle-en';
    en.textContent = (seg.en || '').trim();
    curSub.append(zh, en);
  } else {
    curSub.textContent = (seg.text || '').trim();
  }
  curSub.classList.add('visible');
}

function setupContentProgress() {
  const duration = Number(manifest.duration || 0);
  const minDuration = Number(manifest.min_progress_duration ?? 180);
  const enabled = duration > minDuration && chapters.length > 1;
  contentProgress.classList.toggle('visible', enabled);
  contentProgressMarkers.replaceChildren();
  contentProgressLabels.replaceChildren();
  if (!enabled) return;
  chapters.forEach((chapter, index) => {
    const startPercent = Math.max(0, Math.min(100, Number(chapter.start) / duration * 100));
    const endPercent = Math.max(startPercent, Math.min(100, Number(chapter.end) / duration * 100));
    if (index > 0) {
      const marker = document.createElement('span');
      marker.className = 'content-progress-marker';
      marker.style.left = `${startPercent}%`;
      contentProgressMarkers.appendChild(marker);
    }
    const label = document.createElement('span');
    label.className = 'content-progress-label';
    label.style.left = `${startPercent}%`;
    label.style.width = `${endPercent - startPercent}%`;
    label.textContent = chapter.title || `第 ${index + 1} 节`;
    contentProgressLabels.appendChild(label);
  });
  updateContentProgress(vid.currentTime || 0);
}

function updateContentProgress(time) {
  const duration = Number(manifest?.duration || vid.duration || 0);
  if (!contentProgress.classList.contains('visible') || !duration) return;
  const bounded = Math.max(0, Math.min(duration, time || 0));
  contentProgressFill.style.width = `${bounded / duration * 100}%`;
}

// ── boot: 读 manifest → 建 tab → 加载默认语言 ───────────────────────────────
async function init() {
  manifest = await (await fetch('/manifest')).json();
  bilingualMode = Boolean(manifest.bilingual);
  chapters = Array.isArray(manifest.chapters) ? manifest.chapters : [];
  document.body.classList.toggle('bilingual-mode', bilingualMode);
  const langs = manifest.languages || [];
  buildTabs(langs);
  const src = langs.find(l => l.source) || langs[0];
  await switchLang(src.code);
  setupContentProgress();
}

function buildTabs(langs) {
  if (langs.length <= 1) return;            // 单语言不显示 tab
  langTabs.classList.add('show');
  langTabs.innerHTML = '';
  langs.forEach(L => {
    const t = document.createElement('div');
    t.className = 'lang-tab';
    t.dataset.code = L.code;
    t.innerHTML = '<span class="dot"></span>' + L.name + (L.audio ? '(配音)' : '');
    t.addEventListener('click', () => switchLang(L.code));
    langTabs.appendChild(t);
  });
}

async function switchLang(code) {
  currentLang = code;
  const L = (manifest.languages || []).find(l => l.code === code) || {};
  const data = await (await fetch('/api/transcript?lang=' + encodeURIComponent(code), {cache: 'no-store'})).json();
  segments = (data.segments || []).map(s => Object.assign({}, s, { _id: Math.random().toString(36).slice(2) }));
  deletedIds = new Set();
  editMode = null;
  document.querySelectorAll('.lang-tab').forEach(t => t.classList.toggle('active', t.dataset.code === code));
  setAudio(L);
  render();
  updateInfo();
  syncCurSub();   // 立即按当前时间刷新字幕浮层+高亮,不必等播放
}

// ── 音轨切换:源语言用视频原声;其它语言静音视频 + 播放该语言配音轨(与视频同步) ──
function setAudio(L) {
  if (L && L.audio) {
    vid.muted = true;
    if (dubAudio.dataset.code !== L.code) {
      dubAudio.src = '/track/' + L.code;
      dubAudio.dataset.code = L.code;
    }
    dubAudio.currentTime = vid.currentTime;
    if (!vid.paused) dubAudio.play().catch(() => {});
  } else {
    vid.muted = false;
    dubAudio.pause();
  }
}

vid.addEventListener('play', () => {
  if (vid.muted && dubAudio.src) { dubAudio.currentTime = vid.currentTime; dubAudio.play().catch(() => {}); }
});
vid.addEventListener('pause', () => dubAudio.pause());
vid.addEventListener('ended', () => dubAudio.pause());
vid.addEventListener('seeking', () => { if (vid.muted && dubAudio.src) dubAudio.currentTime = vid.currentTime; });
vid.addEventListener('ratechange', () => { dubAudio.playbackRate = vid.playbackRate; });

init();

// ── video sync ─────────────────────────────────────────────────────────────
// 按当前视频时间刷新底部字幕浮层 + 列表高亮(切 tab / 跳转时也能立即生效,不必等播放)
function syncCurSub(doScroll) {
  const t = vid.currentTime;
  let active = null;
  for (const s of segments) {
    if (!deletedIds.has(s._id) && t >= s.start && t <= s.end) {
      active = s; break;
    }
  }
  if (active) {
    renderCurrentSubtitle(active);
  } else {
    renderCurrentSubtitle(null);
  }
  document.querySelectorAll('.sub-item').forEach(el => {
    const isActive = active && el.dataset.id == active._id;
    el.classList.toggle('current', !!isActive);
    if (isActive && doScroll !== false) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
}

vid.addEventListener('timeupdate', () => {
  // 配音轨漂移校正:与视频时间对不上就拉回
  if (vid.muted && dubAudio.src && !dubAudio.paused && Math.abs(dubAudio.currentTime - vid.currentTime) > 0.3) {
    dubAudio.currentTime = vid.currentTime;
  }
  syncCurSub();
  updateContentProgress(vid.currentTime);
});
vid.addEventListener('loadedmetadata', setupContentProgress);

// ── render ──────────────────────────────────────────────────────────────────
function render() {
  listEl.innerHTML = '';
  const vis = getVisibleSegments();
  selAll.checked = vis.length > 0 && vis.every(s => !deletedIds.has(s._id));

  segments.forEach(seg => {
    const id = seg._id || (seg._id = Math.random().toString(36).slice(2));
    const isDel = deletedIds.has(id);
    const isEdit = editMode === id;

    const item = document.createElement('div');
    item.className = 'sub-item' + (isDel ? ' deleted' : '') + (isEdit ? ' current' : '');
    item.dataset.id = id;

    const times = document.createElement('div');
    times.className = 'sub-times';
    times.textContent = fmtSeg(seg);

    // Checkbox — stop all propagation so clicks don't bubble to row seek
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'sub-check';
    check.checked = isDel;
    check.addEventListener('click', e => { e.stopPropagation(); toggleDelete(id); });
    check.addEventListener('mousedown', e => e.stopPropagation());
    check.addEventListener('touchstart', e => e.stopPropagation());

    const textWrap = document.createElement('div');
    textWrap.className = 'sub-text-wrap';

    const textElements = segmentFields().map(field => {
      const textEl = document.createElement('div');
      textEl.className = `sub-text sub-text-${field}`;
      textEl.dataset.field = field;
      textEl.contentEditable = 'false';
      const fieldText = seg[field] || '';
      if (currentNeedle) textEl.innerHTML = insertMarks(fieldText, currentNeedle);
      else textEl.textContent = fieldText;
      textEl.addEventListener('mousedown', e => {
        if (textEl.contentEditable === 'true') e.stopPropagation();
      });
      textEl.addEventListener('dblclick', e => {
        e.stopPropagation();
        startEdit(id, textEl, field);
      });
      textEl.addEventListener('blur', () => {
        if (textEl.contentEditable === 'true') {
          finishEdit(id, field, textEl.innerText.trim());
          textEl.contentEditable = 'false';
          if (currentNeedle) textEl.innerHTML = insertMarks(seg[field] || '', currentNeedle);
          else textEl.textContent = seg[field] || '';
        }
      });
      textEl.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
          textEl.contentEditable = 'false';
          textEl.textContent = seg[field] || '';
          editMode = null;
        }
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          textEl.blur();
        }
      });
      textWrap.appendChild(textEl);
      return textEl;
    });

    // Action buttons
    const actions = document.createElement('div');
    actions.className = 'sub-actions';
    const editBtn = document.createElement('button');
    editBtn.className = 'icon-btn';
    editBtn.textContent = '✎';
    editBtn.title = '编辑';
    editBtn.addEventListener('click', e => { e.stopPropagation(); startEdit(id, textElements[0], segmentFields()[0]); });
    const delBtn = document.createElement('button');
    delBtn.className = 'icon-btn del';
    delBtn.textContent = '✕';
    delBtn.title = '删除';
    delBtn.addEventListener('click', e => { e.stopPropagation(); toggleDelete(id); });
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);

    // Click row → seek video (only when text not in edit mode)
    item.addEventListener('click', e => {
      const editing = [...listEl.querySelectorAll(`[data-id="${id}"] .sub-text`)]
        .some(el => el.contentEditable === 'true');
      if (editing) return;
      vid.currentTime = seg.start;
      // Immediately show this subtitle without waiting for timeupdate
      renderCurrentSubtitle(seg);
      updateContentProgress(seg.start);
    });

    item.appendChild(check);
    item.appendChild(times);
    item.appendChild(textWrap);
    item.appendChild(actions);
    listEl.appendChild(item);
  });
}

function startEdit(id, textEl, field = 'text') {
  editMode = id;
  textEl.contentEditable = 'true';
  textEl.innerHTML = '';
  const seg = segments.find(s => s._id === id);
  if (seg) textEl.textContent = seg[field] || '';
  textEl.focus();
  // Move cursor to end
  const range = document.createRange();
  range.selectNodeContents(textEl);
  range.collapse(false);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

function finishEdit(id, field, value) {
  const seg = segments.find(s => s._id === id);
  if (seg) seg[field] = value;
  editMode = null;
  saveLS();
}

function toggleDelete(id) {
  deletedIds.has(id) ? deletedIds.delete(id) : deletedIds.add(id);
  saveLS();
  render();
  updateInfo();
}

function updateInfo() {
  const total = segments.length;
  const vis   = getVisibleSegments().length;
  listInfo.textContent = `共 ${total} 条 | 显示 ${vis} | 已删除 ${total - vis}`;
}

// ── localStorage ────────────────────────────────────────────────────────────
function saveLS() {
  localStorage.setItem(LS_KEY, JSON.stringify({
    segments,
    deletedIds: [...deletedIds]
  }));
}

// ── find / replace ──────────────────────────────────────────────────────────
// Update highlights on every keystroke in find input
findInput.addEventListener('input', () => {
  currentNeedle = findInput.value;
  render();
});

function doFind(replaceVal, replaceOne) {
  const needle = findInput.value;
  if (!needle) return;
  const re = new RegExp(escapeRe(needle), 'i');
  const visible = getVisibleSegments();
  let startIdx = findIndex < 0 ? 0 : findIndex + (replaceOne ? 1 : 0);

  for (let i = 0; i < visible.length; i++) {
    const idx = (startIdx + i) % visible.length;
    if (re.test(segmentSearchText(visible[idx]))) {
      findIndex = idx;
      const el = listEl.querySelector(`[data-id="${visible[idx]._id}"]`);
      if (el) {
        el.scrollIntoView({ block: 'nearest' });
        el.style.outline = '2px solid var(--accent)';
        setTimeout(() => { if (el) el.style.outline = ''; }, 1500);
      }
      if (replaceOne) {
        const seg = segments.find(s => s._id === visible[idx]._id);
        if (seg) {
          const field = segmentFields().find(name => re.test(seg[name] || ''));
          if (field) seg[field] = (seg[field] || '').replace(new RegExp(escapeRe(needle), 'gi'), replaceVal);
          saveLS();
          render();
        }
      } else if (replaceVal) {
        let count = 0;
        segments.forEach(s => {
          if (deletedIds.has(s._id)) return;
          segmentFields().forEach(field => {
            const before = s[field] || '';
            const next = before.replace(new RegExp(escapeRe(needle), 'gi'), replaceVal);
            if (next !== before) { s[field] = next; count++; }
          });
        });
        statusTxt.textContent = `已替换 ${count} 处`;
        saveLS();
        render();
        return;
      }
      return;
    }
  }
  statusTxt.textContent = '未找到: ' + needle;
}

document.getElementById('btnReplaceOne').addEventListener('click', () => doFind(repInput.value, true));
document.getElementById('btnReplaceAll').addEventListener('click', () => doFind(repInput.value, false));
findInput.addEventListener('keydown', e => { if (e.key === 'Enter') doFind(repInput.value, true); });

// ── bulk select / delete ────────────────────────────────────────────────────
document.getElementById('btnSelectAll').addEventListener('click', () => {
  const allDel = segments.every(s => deletedIds.has(s._id));
  if (allDel) { deletedIds.clear(); statusTxt.textContent = '已取消全部删除'; }
  else { segments.forEach(s => deletedIds.add(s._id)); statusTxt.textContent = '已选中全部'; }
  saveLS();
  render();
  updateInfo();
});

document.getElementById('btnDeleteSelected').addEventListener('click', () => {
  const sel = segments.filter(s => deletedIds.has(s._id));
  if (!sel.length) { statusTxt.textContent = '请先勾选要删除的条目'; return; }
  sel.forEach(s => deletedIds.add(s._id));
  saveLS();
  render();
  updateInfo();
  statusTxt.textContent = `已删除 ${sel.length} 条`;
});

selAll.addEventListener('change', () => {
  if (selAll.checked) segments.forEach(s => deletedIds.add(s._id));
  else deletedIds.clear();
  saveLS();
  render();
  updateInfo();
});

// ── save & close ─────────────────────────────────────────────────────────────
document.getElementById('btnSave').addEventListener('click', async () => {
  // Flush any in-progress contenteditable edit before saving
  const active = document.activeElement;
  if (active && active.classList.contains('sub-text')) active.blur();

  const toSave = segments
    .filter(s => !deletedIds.has(s._id))
    .map(({ _id, ...rest }) => rest);

  const res = await fetch('/api/transcript', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segments: toSave, lang: currentLang })
  });

  if (!res.ok) {
    statusTxt.textContent = '保存失败，请重试';
    return;
  }

  localStorage.removeItem(LS_KEY);

  // Notify the Agent via the Oil interactive-page bridge (if available).
  // Falls back to postMessage when running inside an iframe wrapper.
  // Failure here is non-fatal — the transcript is already saved to disk.
  let notified = false;
  try {
    if (window.Oil && typeof window.Oil.sendMessage === 'function') {
      await window.Oil.sendMessage({ text: '检查完成，开始烧录', closePreview: true });
      notified = true;
    } else if (window.parent !== window) {
      window.parent.postMessage({
        type: 'SAVE_DONE',
        text: '检查完成，开始烧录',
        closePreview: true
      }, '*');
      notified = true;
    }
  } catch (err) {
    console.warn('通知 Agent 失败:', err);
  }

  statusTxt.textContent = notified
    ? `✅ 已保存 ${toSave.length} 条字幕，已通知 Agent 继续烧录`
    : `✅ 已保存 ${toSave.length} 条字幕，可关闭此页面`;
  window.close(); // may be blocked by browser; user can close manually
});
</script>
</body>
</html>"""


# ----------------------------------------------------------------------------- #
# Routes
# ----------------------------------------------------------------------------- #
def _lang_transcript_path(lang):
    """按语言 code 找 transcript 文件;多语言模式从 manifest 解析,否则回退单语言。"""
    if MANIFEST and lang and lang != "src":
        for L in MANIFEST.get("languages", []):
            if L["code"] == lang:
                return os.path.join(WORKSPACE, L["transcript"])
    return TRANSCRIPT_PATH


@app.route("/")
def index():
    # Include transcript mtime/size so a reused browser tab can never retain
    # an older subtitle payload for the same exported video.
    try:
        st = os.stat(TRANSCRIPT_PATH)
        cache_key = f"subtitle_editor_v3:{VIDEO_PATH}:{TRANSCRIPT_PATH}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        cache_key = f"subtitle_editor_v3:{VIDEO_PATH}:{TRANSCRIPT_PATH}"
    html = HTML_TEMPLATE.replace("__CACHE_KEY__", json.dumps(cache_key))
    return Response(html, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.route("/manifest")
def manifest():
    if MANIFEST:
        response = jsonify(MANIFEST)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response
    # 单语言:合成一个只有一种语言的 manifest,前端逻辑统一
    return jsonify({"video": "/video", "languages": [
        {"code": "src", "name": "字幕", "transcript": "src", "source": True}]})


@app.route("/video")
def video():
    if not os.path.exists(VIDEO_PATH):
        return "Video not found", 404
    return send_file(VIDEO_PATH, mimetype="video/mp4")


@app.route("/track/<code>")
def track(code):
    """某语言的配音音轨。"""
    if MANIFEST:
        for L in MANIFEST.get("languages", []):
            if L["code"] == code and L.get("audio"):
                p = os.path.join(WORKSPACE, L["audio"])
                if os.path.exists(p):
                    return send_file(p, mimetype="audio/mp4")
    return "no track", 404


@app.route("/api/transcript", methods=["GET"])
def get_transcript():
    path = _lang_transcript_path(request.args.get("lang"))
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    import re as _re
    raw = _re.sub(r'\bNaN\b', 'null', raw)
    raw = _re.sub(r'\b-?Infinity\b', 'null', raw)
    data = json.loads(raw)
    segs = data.get("segments", data) if isinstance(data, dict) else data
    response = jsonify({"segments": segs})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/api/transcript", methods=["POST"])
def post_transcript():
    body = request.get_json()
    path = _lang_transcript_path(body.get("lang") or request.args.get("lang"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"segments": body["segments"]}, f, ensure_ascii=False, indent=2)
    RESULT_DONE.set()
    return jsonify({"ok": True})


# ----------------------------------------------------------------------------- #
# Main
# ----------------------------------------------------------------------------- #
def main():
    global VIDEO_PATH, TRANSCRIPT_PATH, MANIFEST, WORKSPACE

    if len(sys.argv) < 2:
        print("Usage: preview_editor.py <video.mp4> <transcript.json>")
        print("   or: preview_editor.py <manifest.json>   # 多语言模式")
        sys.exit(1)

    a1 = os.path.abspath(sys.argv[1])
    if a1.endswith("manifest.json") and len(sys.argv) == 2:
        # 多语言:manifest 模式
        WORKSPACE = os.path.dirname(a1)
        MANIFEST = json.load(open(a1, encoding="utf-8"))
        chapters_file = MANIFEST.get("chapters_file")
        if chapters_file:
            chapters_path = os.path.join(WORKSPACE, chapters_file)
            chapters_payload = json.load(open(chapters_path, encoding="utf-8"))
            MANIFEST["chapters"] = chapters_payload.get("chapters", [])
            MANIFEST["duration"] = chapters_payload.get(
                "duration", MANIFEST.get("duration", 0)
            )
            MANIFEST["min_progress_duration"] = chapters_payload.get(
                "min_progress_duration", MANIFEST.get("min_progress_duration", 180)
            )
        VIDEO_PATH = os.path.join(WORKSPACE, MANIFEST["video"])
        src = next((L for L in MANIFEST["languages"] if L.get("source")), MANIFEST["languages"][0])
        TRANSCRIPT_PATH = os.path.join(WORKSPACE, src["transcript"])
    else:
        if len(sys.argv) < 3:
            print("Usage: preview_editor.py <video.mp4> <transcript.json>")
            sys.exit(1)
        VIDEO_PATH = a1
        TRANSCRIPT_PATH = os.path.abspath(sys.argv[2])

    if not Path(VIDEO_PATH).exists():
        print(f"❌ File not found: {VIDEO_PATH}")
        sys.exit(1)

    import shutil
    shutil.copy2(TRANSCRIPT_PATH, TRANSCRIPT_PATH + ".orig.json")
    print(f"[preview] Video:     {VIDEO_PATH}")
    print(f"[preview] {'Manifest: ' + a1 if MANIFEST else 'Transcript: ' + TRANSCRIPT_PATH}")
    print(f"[preview] Flask running on http://localhost:{PORT}")

    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)
    print("[preview] Exiting.")


if __name__ == "__main__":
    main()
