const el = id => document.getElementById(id);
let lastChat = '', lastData = 0, toastTimer;

function render(d) {
  lastData = Date.now();
  const fresh = Date.now() / 1000 - d.updated < 30;
  el('state').textContent = fresh ? (d.online ? '● Online' : '○ Offline') : '○ Sem atualização';
  el('state').dataset.state = fresh && d.online ? 'online' : 'unavailable';
  el('count').textContent = d.players.length + ' / ' + d.maxPlayers;
  el('players').replaceChildren();
  for (const name of d.players) {
    const li = document.createElement('li');
    const dot = document.createElement('span');
    dot.className = 'dot';
    li.append(dot, document.createTextNode(name));
    el('players').append(li);
  }
  if (!d.players.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = fresh && d.online ? 'Ninguém online no momento.' : 'Nenhum jogador disponível.';
    el('players').append(li);
  }
  const key = JSON.stringify(d.chat);
  if (key !== lastChat) {
    const nearBottom = el('chat').scrollHeight - el('chat').scrollTop - el('chat').clientHeight < 70;
    const initial = !lastChat;
    lastChat = key;
    el('chat').replaceChildren();
    for (const m of d.chat) {
      const row = document.createElement('div');
      row.className = 'message';
      const head = document.createElement('div');
      head.className = 'message-head';
      const author = document.createElement('b');
      author.textContent = m.player;
      head.append(author);
      if (m.source === 'web') {
        const tag = document.createElement('span');
        tag.className = 'web-tag';
        tag.textContent = 'WEB';
        head.append(tag);
      }
      const time = document.createElement('time');
      const parts = m.time.split(':');
      time.textContent = String((+parts[0] + 21) % 24).padStart(2, '0') + ':' + parts[1];
      time.title = 'Horário de Brasília';
      head.append(time);
      const text = document.createElement('p');
      text.textContent = m.text;
      row.append(head, text);
      el('chat').append(row);
    }
    if (!d.chat.length) {
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = 'Ainda não há mensagens. Converse com quem está no jogo.';
      el('chat').append(p);
    }
    if (nearBottom || initial) el('chat').scrollTop = el('chat').scrollHeight;
  }
  el('notice').hidden = !d.warnings.length;
  el('notice').textContent = d.warnings.join(' · ');
}

function reconnecting() {
  el('live').textContent = 'Reconectando…';
  el('live').dataset.state = 'reconnecting';
}
const events = new EventSource('/api/events');
events.onmessage = e => {
  try {
    const data = JSON.parse(e.data);
    render(data);
    if (Date.now() / 1000 - data.updated < 30) {
      el('live').textContent = 'Ao vivo';
      el('live').dataset.state = 'live';
    } else {
      reconnecting();
    }
  } catch { reconnecting(); }
};
events.onerror = reconnecting;
setInterval(() => {
  if (!lastData || Date.now() - lastData > 30000) {
    el('state').textContent = '○ Sem atualização';
    el('state').dataset.state = 'unavailable';
    reconnecting();
  }
}, 10000);

el('copy').onclick = async () => {
  clearTimeout(toastTimer);
  try {
    await navigator.clipboard.writeText('oak.fabiomigueldp.me');
    el('toast').textContent = 'Endereço copiado. Cole no Minecraft para entrar.';
  } catch {
    el('toast').textContent = 'Copie o endereço: oak.fabiomigueldp.me';
  }
  toastTimer = setTimeout(() => el('toast').textContent = '', 6000);
};
try { el('name').value = localStorage.getItem('oak-web-name') || ''; } catch {}
el('message').onkeydown = e => {
  // Do not submit while an input method is composing text.
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    el('chat-form').requestSubmit();
  }
};
el('chat-form').onsubmit = async e => {
  e.preventDefault();
  if (el('send').disabled) return;
  const name = el('name').value.trim(), message = el('message').value.trim();
  if (!message) return;
  el('send').disabled = true;
  el('form-status').dataset.state = 'sending';
  el('form-status').textContent = 'Enviando…';
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, message }),
      signal: AbortSignal.timeout(10000)
    });
    const d = await r.json();
    if (!r.ok) throw Error(d.error || 'Não foi possível enviar. Tente novamente.');
    // Preserve a new draft typed while the previous message was being sent.
    if (el('message').value.trim() === message) el('message').value = '';
    el('form-status').dataset.state = 'success';
    el('form-status').textContent = 'Enviado';
    try { localStorage.setItem('oak-web-name', name); } catch {}
    el('message').focus();
  } catch (err) {
    el('form-status').dataset.state = 'error';
    el('form-status').textContent = err.name === 'TimeoutError'
      ? 'Confira o jogo antes de reenviar.'
      : err instanceof TypeError ? 'Sem conexão. Confira o jogo antes de reenviar.' : err.message;
  } finally { el('send').disabled = false; }
};
const mapHelp = document.querySelector('.map-help');
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && mapHelp.open) {
    mapHelp.open = false;
    mapHelp.querySelector('summary').focus();
  }
});
document.addEventListener('click', e => {
  if (!mapHelp.contains(e.target)) mapHelp.open = false;
});
