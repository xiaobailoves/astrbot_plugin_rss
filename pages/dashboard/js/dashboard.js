/* RSS Dashboard */

function _B() { return window.AstrBotPluginPage; }
const $ = {
  get: (p, q) => _B().apiGet(p, q || {}).then(r => (r.ok !== undefined ? (r.ok ? r : Promise.reject(r.error)) : r)),
  post: (p, d) => _B().apiPost(p, d || {}).then(r => (r.ok !== undefined ? (r.ok ? r : Promise.reject(r.error)) : r)),
};

/* ── Toast ── */
function toast(msg, err) {
  const el = document.createElement('div');
  el.className = 'toast' + (err ? ' err' : '');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

/* ── Modal ── */
function modal(title, body, actions) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  const box = document.createElement('div');
  box.className = 'modal-box';
  box.innerHTML = '<h3>' + esc(title) + '</h3><div class="modal-body">' + body + '</div><div class="modal-actions"></div>';
  const act = box.querySelector('.modal-actions');
  actions.forEach(a => {
    const btn = document.createElement('button');
    btn.className = 'btn ' + (a.primary ? 'btn-primary' : 'btn-outline');
    btn.textContent = a.label;
    btn.onclick = function () { overlay.remove(); if (a.cb) a.cb(); };
    act.appendChild(btn);
  });
  overlay.appendChild(box);
  overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
}

function confirm(msg, cb) {
  modal('确认', '<p>' + esc(msg) + '</p>', [
    { label: '取消' },
    { label: '确定', primary: true, cb: cb },
  ]);
}

/* ── State ── */
let tab = 'overview';
let st = { subs: [], eps: [], hist: [], cfg: {}, stats: null, logs: [] };
let charts = [];

async function load() {
  try { st.subs = await $.get('subscriptions/all'); } catch (e) { st.subs = []; }
  try { st.eps = await $.get('rsshub'); } catch (e) { st.eps = []; }
  try { st.hist = (await $.get('history', { count: 50 })).items || []; } catch (e) { st.hist = []; }
  try { st.cfg = await $.get('config'); } catch (e) { st.cfg = {}; }
  try { st.stats = await $.get('stats'); } catch (e) { st.stats = null; }
  try { st.logs = (await $.get('logs', { count: 200 })).items || []; } catch (e) { st.logs = []; }
  render();
}

function disposeCharts() { charts.forEach(c => { try { c.dispose(); } catch (e) { /* */ } }); charts = []; }

/* ── Tabs ── */
document.getElementById('tabs').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#tabs button').forEach(b => b.classList.remove('on'));
  e.target.classList.add('on');
  tab = e.target.dataset.t;
  render();
});

/* ── DOM helper ── */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === 'className') { if (v) el.className = v; }
      else if (k === 'style') { if (v) el.style.cssText = v; }
      else if (k.startsWith('on')) { el.addEventListener(k.slice(2), v); }
      else if (v !== undefined && v !== false) { el.setAttribute(k, v); }
    });
  }
  kids.forEach(c => { if (typeof c === 'string') el.appendChild(document.createTextNode(c)); else if (c) el.appendChild(c); });
  return el;
}

function render() {
  disposeCharts();
  const app = document.getElementById('app'); app.innerHTML = '';
  try {
    if (tab === 'overview') overview(app);
    else if (tab === 'subs') subscriptions(app);
    else if (tab === 'rsshub') endpoints(app);
    else if (tab === 'history') history(app);
    else if (tab === 'config') config(app);
    else if (tab === 'logs') logs(app);
  } catch (e) { app.innerHTML = '<div class="card empty">错误: ' + e.message + '</div>'; }
}

/* ═══════════════════════ 概览 ═══════════════════════ */

function overview(app) {
  const ss = st.stats || {};
  const subs = st.subs || [];
  const paused = subs.filter(x => x.paused).length;
  const active = subs.length - paused;

  const items = [
    { n: subs.length, l: '订阅', c: '#0969da' },
    { n: active, l: '活跃', c: '#1a7f37' },
    { n: paused, l: '暂停', c: '#bc4c00' },
    { n: ss.feeds || 0, l: 'Feed', c: '#656d76' },
    { n: ss.push_history || 0, l: '推送', c: '#8250df' },
    { n: ss.failed_pushes || 0, l: '失败', c: '#cf222e' },
  ];

  const cards = h('div', { className: 'ov-cards' });
  items.forEach(it => cards.appendChild(
    h('div', { className: 'ov-card' },
      h('div', { className: 'num', style: 'color:' + it.c }, String(it.n)),
      h('div', { className: 'lbl' }, it.l)
    )
  ));
  app.appendChild(cards);

  const cr = h('div', { className: 'ov-charts' });
  cr.appendChild(h('div', { className: 'card' }, h('h3', {}, '订阅概况'), h('div', { id: 'ch-bar', style: 'height:220px' })));
  cr.appendChild(h('div', { className: 'card' }, h('h3', {}, '活跃 / 暂停'), h('div', { id: 'ch-pie', style: 'height:220px' })));
  app.appendChild(cr);

  setTimeout(() => {
    const bd = document.getElementById('ch-bar'), pd = document.getElementById('ch-pie');
    if (!bd || !pd) return;
    const c1 = echarts.init(bd);
    c1.setOption({
      grid: { left: 36, right: 12, top: 12, bottom: 28 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: ['订阅', '活跃', '暂停', 'Feed'], axisLabel: { fontSize: 11, color: '#656d76' } },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { fontSize: 11, color: '#656d76' } },
      series: [{ type: 'bar', barWidth: '50%', data: [
        { value: subs.length, itemStyle: { color: '#0969da', borderRadius: [4,4,0,0] } },
        { value: active, itemStyle: { color: '#1a7f37', borderRadius: [4,4,0,0] } },
        { value: paused, itemStyle: { color: '#bc4c00', borderRadius: [4,4,0,0] } },
        { value: ss.feeds || 0, itemStyle: { color: '#656d76', borderRadius: [4,4,0,0] } },
      ] }]
    });
    charts.push(c1);
    const c2 = echarts.init(pd);
    c2.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, textStyle: { fontSize: 12 }, itemWidth: 8, itemHeight: 8 },
      series: [{ type: 'pie', radius: ['58%','78%'], center: ['50%','43%'], label: { show: false }, emphasis: { scaleSize: 4 },
        data: [
          { value: Math.max(active, 0), name: '活跃', itemStyle: { color: '#1a7f37' } },
          { value: paused, name: '暂停', itemStyle: { color: '#bc4c00' } },
        ] }]
    });
    charts.push(c2);
  }, 120);

  const hist = st.hist || [];
  if (hist.length) {
    const log = h('div', { className: 'ov-log' });
    hist.slice(0, 6).forEach(x => log.appendChild(
      h('div', { className: 'row' }, h('span', { className: 'time' }, esc(x.time)), h('span', { className: 'txt' }, esc(x.title)))
    ));
    app.appendChild(h('div', { className: 'card' }, h('h3', {}, '最近推送'), log));
  }
}

/* ═══════════════════════ 订阅 ═══════════════════════ */

function subscriptions(app) {
  var hdr = h('div', { style: 'display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;' });
  hdr.appendChild(h('h3', { style: 'margin:0;font-size:13px;font-weight:600;' }, '订阅列表 (' + st.subs.length + ')'));
  hdr.appendChild(h('button', { className: 'btn btn-primary btn-sm', onclick: showAddSub }, '+ 添加订阅'));
  var list = h('div', { className: 'card' }, hdr);
  if (!st.subs.length) {
    list.appendChild(h('div', { className: 'empty' }, '暂无订阅'));
  } else {
    var tbody = h('tbody');
    st.subs.forEach(function(s, i) {
      var tr = h('tr', { className: s.paused ? 'paused' : '' });
      tr.innerHTML = '<td style="overflow:hidden;text-overflow:ellipsis"><b>' + (s.paused ? '[停] ' : '') + esc(s.title) + '</b><br><span style="font-size:11px;color:var(--muted);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(s.url || '') + '</span></td>' +
        '<td style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--muted)">' + esc(s.user || '') + '</td>';
      var act = h('td', { className: 'act-cell' });
      act.appendChild(h('button', { className: 'btn btn-ghost', onclick: function() { fetchItems(i, s); } }, '拉取'));
      act.appendChild(h('button', { className: 'btn btn-ghost', onclick: function() { togglePause(i, s); } }, s.paused ? '恢复' : '暂停'));
      act.appendChild(h('button', { className: 'btn btn-ghost', onclick: function() { showDetail(i, s); } }, '详情'));
      tr.appendChild(act);
      tbody.appendChild(tr);
    });
    var tbl = h('table');
    tbl.style.tableLayout = 'fixed';
    tbl.innerHTML = '<thead><tr><th style="width:33.33%">频道</th><th style="width:33.33%">用户</th><th style="width:33.33%">操作</th></tr></thead>';
    tbl.appendChild(tbody);
    list.appendChild(tbl);
  }
  app.appendChild(list);
}

/* ═══════════════════════ 端点 ═══════════════════════ */

function endpoints(app) {
  app.appendChild(h('div', { className: 'card' },
    h('h3', {}, '添加端点'),
    h('div', { className: 'f-row' },
      h('div', { style: 'flex:4' }, h('input', { id: 'ep-url', placeholder: 'https://rsshub.app' })),
      h('div', { style: 'flex:0;min-width:70px' }, h('button', { className: 'btn btn-primary', onclick: addEp }, '添加'))
    )
  ));
  const list = h('div', { className: 'card' }, h('h3', {}, '端点列表 (' + st.eps.length + ')'));
  if (!st.eps.length) {
    list.appendChild(h('div', { className: 'empty' }, '暂无端点'));
  } else {
    st.eps.forEach((ep, i) => {
      list.appendChild(h('div', { style: 'display:flex;align-items:center;padding:10px 0;border-bottom:1px solid #f0f2f5;gap:12px;' },
        h('code', { style: 'flex:1;font-size:13px;' }, ep.url),
        h('button', { className: 'btn btn-ghost danger', onclick: () => delEp(i) }, '删除')
      ));
    });
  }
  app.appendChild(list);
}

/* ═══════════════════════ 历史 ═══════════════════════ */

function history(app) {
  app.appendChild(h('div', { className: 'card' },
    h('h3', {}, '推送历史 (' + st.hist.length + ')'),
    h('button', { className: 'btn btn-outline btn-sm', onclick: load }, '刷新')
  ));
  if (!st.hist.length) { app.appendChild(h('div', { className: 'card empty' }, '暂无记录')); return; }
  const tbody = h('tbody');
  st.hist.slice(0, 30).forEach(x => {
    tbody.appendChild(h('tr', {},
      h('td', { style: 'white-space:nowrap;font-size:12px;' }, esc(x.time)),
      h('td', { style: 'font-size:12px;' }, esc(x.chan_title || '').substring(0, 18)),
      h('td', {}, esc(x.title || '').substring(0, 60))
    ));
  });
  const tbl = h('table');
  tbl.innerHTML = '<thead><tr><th>时间</th><th>频道</th><th>标题</th></tr></thead>';
  tbl.appendChild(tbody);
  app.appendChild(h('div', { className: 'card', style: 'padding:0;overflow:hidden;' }, tbl));
}

/* ═══════════════════════ 配置 ═══════════════════════ */

function config(app) {
  const labels = {
    title_max_length:       ['标题最大长度', '超过会被截断，范围 1–200'],
    description_max_length: ['正文最大长度', '超过会被截断，范围 1–10000'],
    max_items_per_poll:     ['每次拉取条目数', '-1 表示不限制'],
    compose:                ['QQ 合并转发', '多条打包为一条转发消息'],
    t2i:                    ['文字转图片', '开启后图片内容会丢失'],
    is_hide_url:            ['隐藏链接', '推送中不显示原文链接'],
    verify_ssl:             ['验证 HTTPS 证书', '关闭可跳过证书错误'],
    max_consecutive_failures: ['连续失败自动暂停', '失败此次数后自动暂停订阅，默认 100'],
  };
  const card = h('div', { className: 'card' }, h('h3', {}, '插件配置'));
  Object.entries(labels).forEach(([k, info]) => {
    const v = st.cfg[k];
    if (v === undefined) return;
    const row = h('div', { style: 'display:flex;align-items:center;gap:16px;padding:10px 0;border-bottom:1px solid #f0f2f5;' });
    const left = h('div', { style: 'flex:1;' });
    left.appendChild(h('div', { style: 'font-size:13px;font-weight:500;' }, info[0]));
    left.appendChild(h('div', { style: 'font-size:11px;color:var(--muted);margin-top:2px;' }, info[1]));
    row.appendChild(left);
    const right = h('div', {});
    if (typeof v === 'boolean') {
      right.appendChild(h('select', { 'data-key': k, style: 'width:auto;min-width:80px;' }, h('option', { value: '1', selected: v === true }, '开启'), h('option', { value: '0', selected: v === false }, '关闭')));
    } else {
      right.appendChild(h('input', { 'data-key': k, value: v == null ? '' : String(v), style: 'width:100px;' }));
    }
    row.appendChild(right);
    card.appendChild(row);
  });
  card.appendChild(h('div', { style: 'margin-top:14px;display:flex;gap:8px;' },
    h('button', { className: 'btn btn-primary', onclick: saveCfg }, '保存'),
    h('button', { className: 'btn btn-outline', onclick: reload }, '重载调度')
  ));
  app.appendChild(card);
}

/* ═══════════════════════ 日志 ═══════════════════════ */

function logs(app) {
  app.appendChild(h('div', { className: 'card' },
    h('h3', {}, '插件日志 (' + st.logs.length + ')'),
    h('div', { style: 'display:flex;gap:8px;margin-bottom:10px;' },
      h('button', { className: 'btn btn-outline btn-sm', onclick: () => load() }, '刷新'),
      h('button', { className: 'btn btn-outline btn-sm', onclick: () => { $.get('logs', { level: 'ERROR', count: 100 }).then(r => { st.logs = r.items || []; render(); }); } }, 'ERROR'),
      h('button', { className: 'btn btn-outline btn-sm', onclick: () => { $.get('logs', { level: 'WARNING', count: 100 }).then(r => { st.logs = r.items || []; render(); }); } }, 'WARNING'),
      h('button', { className: 'btn btn-outline btn-sm', onclick: () => { $.get('logs', { count: 200 }).then(r => { st.logs = r.items || []; render(); }); } }, '全部'),
    )
  ));
  if (!st.logs.length) { app.appendChild(h('div', { className: 'card empty' }, '暂无日志')); return; }
  const tbody = h('tbody');
  st.logs.forEach(x => {
    const c = x.level === 'ERROR' ? '#cf222e' : x.level === 'WARNING' ? '#bc4c00' : '#656d76';
    tbody.appendChild(h('tr', { className: 'log-row' },
      h('td', { style: 'white-space:nowrap;width:1%' }, x.time),
      h('td', { className: 'log-lvl', style: 'color:' + c + ';width:1%' }, x.level),
      h('td', { style: 'word-break:break-all;' }, esc(x.msg))
    ));
  });
  const tbl = h('table');
  tbl.innerHTML = '<thead><tr><th>时间</th><th>级别</th><th>消息</th></tr></thead>';
  tbl.appendChild(tbody);
  app.appendChild(h('div', { className: 'card', style: 'padding:0;overflow:hidden;' }, tbl));
}

/* ═══════════════════════ Actions ═══════════════════════ */

function showAddSub() {
  var epsOpts = st.eps.map(function(e, i) { return '<option value="' + i + '">' + esc(e.url) + '</option>'; }).join('');
  var body =
    '<div style="margin-bottom:10px;"><label class="f-label">方式</label><select id="ad-type" style="width:100%"><option value="rsshub">RSSHub</option><option value="url">直连 URL</option></select></div>' +
    '<div id="ad-ep-wrap" style="margin-bottom:10px;"><label class="f-label">端点</label><select id="ad-ep" style="width:100%">' + epsOpts + '</select></div>' +
    '<div id="ad-route-wrap" style="margin-bottom:10px;"><label class="f-label">路由</label><input id="ad-route" placeholder="/twitter/user/xxx" /></div>' +
    '<div id="ad-url-wrap" style="margin-bottom:10px;display:none"><label class="f-label">URL</label><input id="ad-url" placeholder="https://..." /></div>' +
    '<div style="margin-bottom:10px;"><label class="f-label">用户 / 群聊</label><input id="ad-user" placeholder="aiocqhttp:群号:QQ号" /></div>' +
    '<div style="margin-bottom:10px;"><label class="f-label">Cron</label><input id="ad-cron" value="0 * * * *" /><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">' +
      ['0 * * * *','0 */2 * * *','0 */6 * * *','0 9 * * *','0 9 * * 1-5','*/30 * * * *'].map(function(v) {
        return '<button class="btn btn-outline btn-sm" onclick="document.getElementById(\'ad-cron\').value=\'' + v + '\'" type="button">' + v + '</button>';
      }).join('') +
    '</div></div>' +
    '<div style="margin-bottom:10px;"><label class="f-label">模型</label><select id="ad-model" style="width:100%"><option value="">默认</option><option value="twitter">twitter</option><option value="compose">compose</option></select></div>';
  modal('添加订阅', body, [
    { label: '取消' },
    { label: '添加', primary: true, cb: function () {
      var type = document.getElementById('ad-type').value;
      var user = document.getElementById('ad-user').value;
      var cron = document.getElementById('ad-cron').value;
      var model = document.getElementById('ad-model').value || undefined;
      if (!user) { toast('请输入用户/群聊', true); return; }
      var req;
      if (type === 'rsshub') {
        var ep = parseInt(document.getElementById('ad-ep').value);
        var route = document.getElementById('ad-route').value;
        if (!route) { toast('请输入路由', true); return; }
        req = $.post('subscriptions/rsshub', { user: user, endpoint_idx: ep, route: route, cron: cron });
      } else {
        var url = document.getElementById('ad-url').value;
        if (!url) { toast('请输入 URL', true); return; }
        req = $.post('subscriptions/url', { user: user, url: url, cron: cron, renderer: model });
      }
      req.then(function () { toast('添加成功'); load(); }).catch(function (e) { toast('添加失败: ' + e, true); });
    }}
  ]);
  // Toggle RSSHub/URL fields
  setTimeout(function () {
    var typeSel = document.getElementById('ad-type');
    var toggle = function () {
      var is = typeSel.value === 'rsshub';
      document.getElementById('ad-ep-wrap').style.display = is ? 'block' : 'none';
      document.getElementById('ad-route-wrap').style.display = is ? 'block' : 'none';
      document.getElementById('ad-url-wrap').style.display = is ? 'block' : 'none';
    };
    typeSel.onchange = toggle;
    toggle();
  }, 50);
}

async function addSub() {
  // Legacy — no longer used directly since form is now in modal
}

function addEp() {
  const url = document.getElementById('ep-url').value;
  if (!url) return toast('请输入 URL', true);
  $.post('rsshub', { url }).then(() => { toast('已添加'); load(); }).catch(e => toast('失败: ' + e, true));
}

function delSub(idx, s) {
  confirm('确定删除 ' + esc(s.title) + ' ？', function () {
    $.post('subscriptions/delete', { user: s.user || '', idx }).then(() => { toast('已删除'); load(); }).catch(e => toast('删除失败: ' + e, true));
  });
}

function togglePause(idx, s) {
  const ep = s.paused ? 'resume' : 'pause';
  $.post('subscriptions/' + ep, { user: s.user || '', idx }).then(() => { toast(s.paused ? '已恢复' : '已暂停'); load(); }).catch(e => toast('操作失败: ' + e, true));
}

function delEp(idx) {
  confirm('确定删除该端点？', function () {
    $.post('rsshub/delete', { idx }).then(() => { toast('已删除'); load(); }).catch(e => toast('删除失败: ' + e, true));
  });
}

function fetchItems(idx, s) {
  $.post('subscriptions/fetch', { user: s.user || '', idx: idx }).then(r => {
    const items = r.items || [];
    if (!items.length) return toast('暂无新内容');
    let html = '<div style="max-height:400px;overflow-y:auto;font-size:13px;">';
    items.slice(0, 8).forEach(item => {
      html += '<div style="padding:8px 0;border-bottom:1px solid #f0f2f5;"><b>' + esc(item.title) + '</b><div style="color:#8b949e;font-size:12px;margin-top:2px;">' + esc(item.description || '').substring(0, 150) + '</div></div>';
    });
    html += '</div>';
    modal('拉取结果 (' + r.count + '条)', html, [{ label: '关闭' }]);
  }).catch(e => toast('拉取失败: ' + e, true));
}

function showDetail(idx, s) {
  var pausedTag = s.paused ? '<span class="tag" style="background:#fff7ed;color:#9a3412">已暂停</span>' : '<span class="tag tag-green">运行中</span>';
  var body =
    '<div style="display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:13px;">' +
      '<span style="color:var(--muted)">频道</span><b>' + esc(s.title) + '</b>' +
      '<span style="color:var(--muted)">URL</span><span style="word-break:break-all;font-size:12px">' + esc(s.url || '') + '</span>' +
      '<span style="color:var(--muted)">用户</span><code style="font-size:12px">' + esc(s.user || '') + '</code>' +
      '<span style="color:var(--muted)">Cron</span><code style="font-size:12px">' + esc(s.cron) + '</code>' +
      '<span style="color:var(--muted)">模型</span><span>' + (s.renderer ? '<span class="tag tag-green">' + esc(s.renderer) + '</span>' : '<span class="tag tag-gray">默认</span>') + '</span>' +
      '<span style="color:var(--muted)">状态</span><span>' + pausedTag + '</span>' +
    '</div>';
  modal('订阅详情', body, [
    { label: '编辑', primary: false, cb: function() { editSub(idx, s); } },
    { label: '删除', primary: false, cb: function() { delSub(idx, s); } },
    { label: '关闭', primary: true },
  ]);
}

function editSub(idx, s) {
  const body = '<div style="margin-bottom:12px;"><label class="f-label">用户 / 群聊</label><input id="ed-user" value="' + esc(s.user || '') + '" /></div>' +
    '<div style="margin-bottom:12px;"><label class="f-label">Cron 表达式</label><input id="ed-cron" value="' + esc(s.cron) + '" /></div>' +
    '<div style="margin-bottom:12px;"><label class="f-label">模型</label><select id="ed-model" style="width:100%"><option value=""' + (s.renderer ? '' : ' selected') + '>默认</option><option value="twitter"' + (s.renderer === 'twitter' ? ' selected' : '') + '>twitter</option><option value="compose"' + (s.renderer === 'compose' ? ' selected' : '') + '>compose</option></select></div>';
  modal('编辑订阅', body, [
    { label: '取消' },
    { label: '保存', primary: true, cb: function () {
      const newUser = document.getElementById('ed-user').value;
      const cron = document.getElementById('ed-cron').value;
      const model = document.getElementById('ed-model').value;
      const p = { cron, user: s.user || '', idx };
      if (newUser && newUser !== s.user) p.new_user = newUser;
      if (model === ' ') p.renderer = ''; else if (model) p.renderer = model;
      $.post('subscriptions/update', p).then(() => { toast('已更新'); load(); }).catch(e => toast('更新失败: ' + e, true));
    }},
  ]);
}

function saveCfg() {
  const data = {};
  document.querySelectorAll('[data-key]').forEach(el => {
    let v = el.value;
    if (el.tagName === 'SELECT') v = v === '1';
    else if (v !== '' && !isNaN(Number(v))) v = Number(v);
    data[el.dataset.key] = v;
  });
  $.post('config', data).then(() => toast('配置已保存')).catch(e => toast('保存失败: ' + e, true));
}

function reload() {
  $.post('reload').then(r => {
    toast('已重载，任务: ' + r.jobs);
    document.getElementById('info').textContent = r.jobs + ' 个任务';
  }).catch(e => toast('重载失败: ' + e, true));
}

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

/* ── Init ── */
(async () => {
  // 轮询等待 bridge 注入（最多等 5 秒）
  for (let i = 0; i < 50; i++) {
    if (window.AstrBotPluginPage) break;
    await new Promise(r => setTimeout(r, 100));
  }
  const bridge = window.AstrBotPluginPage;
  if (!bridge) {
    document.body.innerHTML = '<div style="text-align:center;padding:80px 20px;color:#8b949e;font-size:15px;">请在 AstrBot 仪表盘中打开<br><small style="color:#ccc">若已从仪表盘打开，请升级 AstrBot 到最新版本</small></div>';
    return;
  }
  try { await bridge.ready(); } catch (e) { /* */ }
  await load();
  try { const r = await $.post('reload'); document.getElementById('info').textContent = r.jobs + ' 个任务'; } catch (e) { /* */ }
  window.addEventListener('resize', () => charts.forEach(c => { try { c.resize(); } catch (e) { /* */ } }));
})();
