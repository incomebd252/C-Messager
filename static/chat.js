(() => {
  'use strict';

  const ME = document.body.dataset.me;
  const CSRF = document.body.dataset.csrf;
  const $ = (id) => document.getElementById(id);
  const PLACEHOLDER = 'Select chat from left, Or start new chats from ID.';

  const S = { chat: null, info: null, convKey: '', msgKey: '', headKey: '' };
  let pending = null;

  // ---------------------------------------------------------- helpers
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function api(url, opts = {}) {
    opts.headers = Object.assign({ 'X-CSRF-Token': CSRF }, opts.headers || {});
    try {
      const r = await fetch(url, opts);
      let data = null;
      try { data = await r.json(); } catch (e) { /* json noy */ }
      if (r.status === 401) { location.href = '/login'; }
      return { ok: r.ok, status: r.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: null };
    }
  }
  const post = (url, obj) => api(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj || {}),
  });

  let toastTimer;
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
  }

  const toDate = (s) => new Date(s);
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  const hm = (s) => toDate(s).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  function shortTime(s) {
    if (!s) return '';
    const d = toDate(s);
    return sameDay(d, new Date()) ? hm(s) : d.toLocaleDateString([], { day: 'numeric', month: 'short' });
  }
  function dayLabel(s) {
    const d = toDate(s), t = new Date(), y = new Date();
    y.setDate(t.getDate() - 1);
    if (sameDay(d, t)) return 'Ajke';
    if (sameDay(d, y)) return 'Gotokal';
    return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function ago(s) {
    if (!s) return 'Offline';
    const sec = (Date.now() - toDate(s).getTime()) / 1000;
    if (sec < 90) return 'Last seen ekhon-i';
    if (sec < 3600) return 'Last seen ' + Math.floor(sec / 60) + ' minute age';
    if (sec < 86400) return 'Last seen ' + Math.floor(sec / 3600) + ' ghonta age';
    return 'Last seen ' + toDate(s).toLocaleDateString([], { day: 'numeric', month: 'short' });
  }

  function avatar(code, av, online) {
    const wrap = el('span', 'avwrap');
    const img = el('img', 'av');
    img.src = '/avatar/' + encodeURIComponent(code) + '?v=' + encodeURIComponent(av || 0);
    img.alt = '';
    img.loading = 'lazy';
    wrap.appendChild(img);
    if (online) wrap.appendChild(el('span', 'dot'));
    return wrap;
  }

  // ---------------------------------------------- conversation list
  function renderConvs(list) {
    const key = JSON.stringify([list, S.chat]);
    if (key === S.convKey) return;
    S.convKey = key;
    const box = $('convs');
    box.textContent = '';
    if (!list.length) {
      box.appendChild(el('div', 'empty', 'No chat yet. Search by writing ID or name above, Or create a new group.'));
      return;
    }
    list.forEach((c) => {
      const b = el('button', 'conv' + (c.code === S.chat ? ' active' : ''));
      b.type = 'button';
      b.appendChild(avatar(c.code, c.av, c.online));
      const mid = el('span', 'conv-mid');
      const top = el('span', 'conv-top');
      top.append(el('span', 'conv-name', (c.type === 'group' ? '\u{1F465} ' : '') + c.name),
                 el('small', '', shortTime(c.time)));
      mid.append(top, el('span', 'last', c.last));
      b.appendChild(mid);
      const un = c.code === S.chat ? 0 : c.unread;
      if (un) b.appendChild(el('span', 'unread', un > 99 ? '99+' : String(un)));
      b.onclick = () => openChat(c.code);
      box.appendChild(b);
    });
  }

  async function loadConvs() {
    const r = await api('/api/conversations');
    if (!r.ok || !r.data) return;
    renderConvs(r.data);
    const total = r.data.reduce((n, c) => n + (c.code === S.chat ? 0 : c.unread), 0);
    document.title = total ? '(' + total + ') C-Messager' : 'C-Messager';
  }

  // ----------------------------------------------------- open / close
  async function openChat(code) {
    S.chat = code;
    S.msgKey = '';
    S.headKey = '';
    S.convKey = '';
    $('app').classList.add('chat-open');
    $('head').hidden = false;
    $('composer').hidden = false;
    $('msgs').textContent = '';
    closeResults();
    const ok = await loadMessages(true);
    if (ok) $('text').focus();
    loadConvs();
  }

  function closeChat() {
    S.chat = null;
    S.info = null;
    S.msgKey = '';
    S.headKey = '';
    S.convKey = '';
    $('app').classList.remove('chat-open');
    $('head').hidden = true;
    $('composer').hidden = true;
    const box = $('msgs');
    box.textContent = '';
    box.appendChild(el('div', 'placeholder', PLACEHOLDER));
    loadConvs();
  }

  // -------------------------------------------------------- messages
  async function loadMessages(force) {
    if (!S.chat) return false;
    const code = S.chat;
    const r = await api('/api/messages/' + encodeURIComponent(code));
    if (code !== S.chat) return false;
    if (!r.ok || !r.data || !r.data.info) {
      if (force || r.status === 403 || r.status === 404) {
        toast((r.data && r.data.error) || 'Can't open chat !!');
        closeChat();
      }
      return false;
    }
    const { info, messages } = r.data;
    S.info = info;
    renderHead(info);
    const key = JSON.stringify(messages.map((m) => [m.id, m.deleted, m.seen]));
    if (force || key !== S.msgKey) {
      S.msgKey = key;
      renderMessages(messages, info);
    }
    return true;
  }

  function renderHead(info) {
    const minute = info.type === 'user' && !info.online ? Math.floor(Date.now() / 60000) : 0;
    const key = JSON.stringify([info.type, info.code, info.name, info.av, info.bio, info.online,
      info.online ? '' : info.last_seen, minute, info.count]);
    if (key === S.headKey) return;
    S.headKey = key;
    const h = $('headInfo');
    h.textContent = '';
    h.appendChild(avatar(info.code, info.av, info.type === 'user' && info.online));
    const t = el('div', 'head-text');
    t.appendChild(el('div', 'head-name', info.name));
    if (info.type === 'group') {
      t.appendChild(el('small', 'head-sub', info.count + ' jon member'));
    } else {
      t.appendChild(el('small', 'head-sub', info.online ? 'Online' : ago(info.last_seen)));
      if (info.bio) t.appendChild(el('small', 'head-bio', info.bio));
    }
    h.appendChild(t);
    $('infoBtn').hidden = info.type !== 'group';
  }

  function renderMessages(msgs, info) {
    const box = $('msgs');
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    const first = box.querySelector('.bubble') === null;
    const stick = first || nearBottom;
    box.textContent = '';
    if (!msgs.length) {
      box.appendChild(el('div', 'placeholder', 'No message yet. Send first message'));
      return;
    }
    let day = '';
    msgs.forEach((m) => {
      const d = dayLabel(m.time);
      if (d !== day) {
        day = d;
        box.appendChild(el('div', 'divider', d));
      }
      const row = el('div', 'bubble' + (m.mine ? ' mine' : '') + (m.deleted ? ' gone' : ''));
      if (info.type === 'group' && !m.mine) row.appendChild(el('div', 'who', m.sender_name));
      if (m.deleted) {
        row.appendChild(el('span', '', 'Message has been delated'));
      } else {
        if (m.image) {
          const a = el('a', 'pic');
          a.href = m.image;
          a.target = '_blank';
          a.rel = 'noopener';
          const im = el('img');
          im.src = m.image;
          im.alt = 'Pathano chhobi';
          im.loading = 'lazy';
          im.onload = () => { if (stick) box.scrollTop = box.scrollHeight; };
          a.appendChild(im);
          row.appendChild(a);
        }
        if (m.body) row.appendChild(el('span', 'txt', m.body));
      }
      const meta = el('div', 'meta');
      meta.appendChild(el('time', '', hm(m.time)));
      if (m.mine && info.type === 'user' && !m.deleted) {
        meta.appendChild(el('span', 'tick' + (m.seen ? ' seen' : ''), m.seen ? '\u2713\u2713' : '\u2713'));
      }
      if (m.mine && !m.deleted) {
        const x = el('button', 'del', '\u{1F5D1}');
        x.type = 'button';
        x.title = 'Message muchun';
        x.setAttribute('aria-label', 'Message muchun');
        x.onclick = () => deleteMessage(m.id);
        meta.appendChild(x);
      }
      row.appendChild(meta);
      box.appendChild(row);
    });
    if (stick) box.scrollTop = box.scrollHeight;
  }

  async function deleteMessage(id) {
    if (!confirm('Ei message ti muche felben?')) return;
    const r = await post('/api/message/' + id + '/delete');
    if (!r.ok) toast((r.data && r.data.error) || 'Can't delete.');
    await loadMessages(true);
    loadConvs();
  }

  // --------------------------------------------------------- sending
  function clearPending() {
    pending = null;
    $('file').value = '';
    $('chip').hidden = true;
  }
  $('attach').onclick = () => $('file').click();
  $('file').onchange = () => {
    const f = $('file').files[0];
    if (!f) return;
    if (f.size > 8 * 1024 * 1024) {
      toast('Picture can't more than 8 MB!!');
      $('file').value = '';
      return;
    }
    pending = f;
    $('chipName').textContent = f.name;
    $('chip').hidden = false;
  };
  $('chipX').onclick = clearPending;

  $('composer').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = $('text').value.trim();
    if (!S.chat || (!text && !pending)) return;
    const fd = new FormData();
    fd.append('to', S.chat);
    fd.append('body', text);
    if (pending) fd.append('image', pending);
    $('sendBtn').disabled = true;
    const r = await api('/api/send', { method: 'POST', body: fd });
    $('sendBtn').disabled = false;
    if (!r.ok) {
      toast((r.data && r.data.error) || 'Can't send message.');
      return;
    }
    $('text').value = '';
    clearPending();
    $('text').focus();
    await loadMessages(true);
    loadConvs();
  });

  // ---------------------------------------------------------- search
  let searchTimer;
  function closeResults() {
    $('results').hidden = true;
    $('results').textContent = '';
    $('search').value = '';
  }
  async function doSearch() {
    const q = $('search').value.trim();
    const box = $('results');
    if (q.length < 2) { box.hidden = true; return; }
    const r = await api('/api/search?q=' + encodeURIComponent(q));
    if (q !== $('search').value.trim()) return;
    box.textContent = '';
    box.hidden = false;
    if (!r.ok || !r.data || !r.data.length) {
      box.appendChild(el('div', 'empty', 'Can't find anyone.'));
      return;
    }
    r.data.forEach((u) => {
      const b = el('button', 'res');
      b.type = 'button';
      const t = el('span', 'res-text');
      t.append(el('b', '', u.name), el('small', '', u.code));
      b.append(avatar(u.code, u.av, u.online), t);
      b.onclick = () => openChat(u.code);
      box.appendChild(b);
    });
  }
  $('search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(doSearch, 250);
  });
  $('search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = $('results').querySelector('.res');
      if (first) { e.preventDefault(); first.click(); }
    }
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-wrap')) $('results').hidden = true;
  });

  // ------------------------------------------------------- group UI
  $('newGroupBtn').onclick = () => {
    $('groupErr').textContent = '';
    $('groupDlg').showModal();
    $('gName').focus();
  };
  $('groupCancel').onclick = () => $('groupDlg').close();
  $('groupForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const members = $('gMembers').value.split(/[\s,]+/).filter(Boolean);
    const r = await post('/api/groups', { name: $('gName').value, members });
    if (!r.ok) {
      $('groupErr').textContent = (r.data && r.data.error) || 'Group not created';
      return;
    }
    $('groupDlg').close();
    $('groupForm').reset();
    openChat(r.data.code);
  });

  function renderInfo() {
    const info = S.info;
    if (!info || info.type !== 'group') return;
    $('infoTitle').textContent = info.name;
    $('infoErr').textContent = '';
    const list = $('memberList');
    list.textContent = '';
    info.members.forEach((m) => {
      const li = el('li', 'member');
      li.appendChild(avatar(m.code, m.av, m.online));
      const t = el('span', 'res-text');
      t.append(el('b', '', m.name + (m.code === ME ? ' (Apni)' : '')),
               el('small', '', m.code + (m.role === 'admin' ? ' \u00b7 Admin' : '')));
      li.appendChild(t);
      if (info.my_role === 'admin' && m.code !== ME) {
        const x = el('button', 'ghost danger', 'Bad din');
        x.type = 'button';
        x.onclick = async () => {
          if (!confirm(m.name + ' - Want to remove from group?')) return;
          const r = await post('/api/groups/' + encodeURIComponent(S.chat) + '/remove', { code: m.code });
          if (!r.ok) { $('infoErr').textContent = (r.data && r.data.error) || 'Can't remove.'; return; }
          await loadMessages(true);
          renderInfo();
        };
        li.appendChild(x);
      }
      list.appendChild(li);
    });
    $('addRow').hidden = info.my_role !== 'admin';
  }

  $('infoBtn').onclick = () => { renderInfo(); $('infoDlg').showModal(); };
  $('infoClose').onclick = () => $('infoDlg').close();
  $('addRow').addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = $('addCode').value.trim();
    if (!code) return;
    const r = await post('/api/groups/' + encodeURIComponent(S.chat) + '/add', { code });
    if (!r.ok) { $('infoErr').textContent = (r.data && r.data.error) || 'Can't add.'; return; }
    $('addCode').value = '';
    await loadMessages(true);
    renderInfo();
  });
  $('leaveBtn').onclick = async () => {
    if (!confirm('Apni ki sotti group ti chharte chan?')) return;
    const r = await post('/api/groups/' + encodeURIComponent(S.chat) + '/leave');
    if (!r.ok) { $('infoErr').textContent = (r.data && r.data.error) || 'Can't leave.'; return; }
    $('infoDlg').close();
    closeChat();
  };

  // ------------------------------------------------------ misc UI
  $('backBtn').onclick = closeChat;
  $('themeBtn').onclick = () => {
    const root = document.documentElement;
    const cur = root.getAttribute('data-theme') ||
      (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('cm-theme', next); } catch (e) { /* ignore */ }
  };

  // ---------------------------------------------------------- polling
  async function tick() {
    try { await Promise.all([loadConvs(), loadMessages(false)]); } catch (e) { /* ignore */ }
    setTimeout(tick, document.hidden ? 8000 : 2000);
  }
  closeChat();
  tick();
})();
