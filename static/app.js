const state = {feed: [], saved: [], readFilter: 'all', stats: null, busy: false, view: 'discover', graph: null, graphRaf: null};
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const formatDate = iso => { try { return new Date(iso).toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'}); } catch { return ''; } };
const fetchJson = async (path, options) => {
  const response = await fetch(path, options);
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    const login = response.redirected || response.url.includes('cloudflareaccess.com');
    throw new Error(login ? 'Cloudflare sign-in expired. Reload the page and sign in again.' : `Unexpected response from ${path} (${response.status}). Reload the page and try again.`);
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
};

function renderStats() {
  const s = state.stats;
  if (!s) return;
  $('unseen-pill').textContent = s.unseen.toLocaleString();
  $('saved-pill').textContent = s.saved.toLocaleString();
  $('corpus-count').textContent = s.total.toLocaleString();
  $('seen-count').textContent = (s.saved + s.passed).toLocaleString();
  $('remaining-count').textContent = s.unseen.toLocaleString();
  $('progress-fill').style.width = `${s.total ? (100 * (s.saved + s.passed) / s.total) : 0}%`;
  $('skip-button').hidden = !s.skip_cooldown;
  if (s.skip_cooldown) $('skip-button').title = `Skip for now; may return after ${s.skip_cooldown} other choices`;
  $('update-label').textContent = s.refreshing ? 'Gathering fresh ideas…' : s.last_refresh ? `Updated ${formatDate(s.last_refresh * 1000)}` : 'Waiting for first refresh…';
  $('refresh-button').disabled = !!s.refreshing;
  const sources = Object.entries(s.sources || {}).sort((a,b) => a[0].localeCompare(b[0]));
  const working = sources.filter(([,result]) => !result.startsWith('error')).length;
  $('source-health-count').textContent = `${working}/${sources.length} working`;
  $('source-health-list').innerHTML = sources.map(([name,result]) => `<div class="source-health-row"><span>${esc(name)}</span><span class="${result.startsWith('error') ? 'source-error' : ''}">${esc(result)}</span></div>`).join('');
  const count = state.feed.length;
  $('feed-subtitle').textContent = count ? `A mix of ${new Set(state.feed.map(x => x.source)).size} sources, tuned by your swipes.` : 'Fresh items are being gathered for you.';
}

function renderCard() {
  const card = $('swipe-card');
  const item = state.feed[0];
  card.className = 'swipe-card';
  $('pass-button').disabled = $('skip-button').disabled = $('save-button').disabled = !item || state.busy;
  if (!item) {
    const cooling = state.stats?.cooling || 0;
    card.innerHTML = `<div class="empty-card"><span class="empty-star">✳</span><h3>${state.stats?.refreshing ? 'Gathering your stack…' : cooling ? 'Your skips are cooling down.' : 'You’re all caught up.'}</h3><p>${state.stats?.refreshing ? 'The first source refresh is underway. New papers and posts will appear shortly.' : cooling ? `${cooling} skipped ${cooling === 1 ? 'item can' : 'items can'} return after more choices. Refresh sources for new ideas in the meantime.` : 'Every item in this batch has had its turn. Refresh the sources to find more.'}</p><button id="empty-refresh">Refresh sources</button></div>`;
    $('empty-refresh').onclick = refreshSources;
    return;
  }
  const type = item.category === 'research_paper' ? 'RESEARCH PAPER' : item.category === 'twitter_article' ? 'TWITTER ARTICLE' : 'BLOG';
  const summary = item.summary || 'Open the original to read more.';
  card.innerHTML = `<div class="card-top"><div class="card-type"><i></i>${type}</div><div class="card-match"><strong>${item.match}/100</strong> fit · ${esc(item.why)}</div></div><h3 class="card-title">${esc(item.title)}</h3><p class="card-summary">${esc(summary)}</p><div class="card-bottom"><div class="card-meta"><span class="source-tag">${esc(item.source)}</span><span class="meta-text">${esc(item.author || '')}</span><span class="meta-sep">·</span><span class="meta-text">${formatDate(item.published)}</span></div><a class="open-link" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">Read original ↗</a></div><div class="swipe-stamp pass">LESS</div><div class="swipe-stamp save">MORE</div>`;
}

async function loadStats() { state.stats = await fetchJson('/api/stats'); renderStats(); }
async function loadFeed() { state.feed = (await fetchJson('/api/feed')).items; renderCard(); renderStats(); }
async function loadSaved() {
  state.saved = (await fetchJson('/api/liked')).items;
  renderSaved();
}

function renderSaved() {
  const visible = state.saved.filter(item => state.readFilter === 'all' || (state.readFilter === 'read') === item.read);
  const readCount = state.saved.filter(item => item.read).length;
  $('read-filter-count').textContent = `${visible.length} shown · ${readCount} read`;
  document.querySelectorAll('[data-read-filter]').forEach(button => {
    const active = button.dataset.readFilter === state.readFilter;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  if (!state.saved.length) {
    $('saved-grid').innerHTML = '<div class="empty-card" style="min-height:270px"><span class="empty-star">♡</span><h3>No positive swipes yet.</h3><p>Swipe right to ask for more like an idea. Reading it is up to you.</p></div>';
    return;
  }
  if (!visible.length) {
    $('saved-grid').innerHTML = '<div class="empty-card saved-empty"><span class="empty-star">✓</span><h3>Nothing here.</h3><p>Try another read filter.</p></div>';
    return;
  }
  $('saved-grid').innerHTML = visible.map(item => `<article class="saved-item${item.read ? ' is-read' : ''}"><div class="saved-top"><span>${esc(item.source)}</span><span class="saved-date">${item.read ? 'READ · ' : ''}${formatDate(item.published)}</span></div><h3>${esc(item.title)}</h3><p>${esc(item.summary)}</p><div class="saved-actions"><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">Read original ↗</a><button class="read-toggle" data-read-id="${esc(item.id)}" data-read-value="${item.read ? 'false' : 'true'}">${item.read ? 'Mark unread' : 'Mark as read'}</button></div></article>`).join('');
}

async function setReadState(id, read) {
  const item = state.saved.find(saved => saved.id === id);
  if (!item) return;
  await fetchJson('/api/read', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id, read})});
  item.read = read;
  renderSaved();
}

async function decide(direction) {
  if (state.busy || !state.feed.length) return;
  state.busy = true;
  const item = state.feed[0];
  const card = $('swipe-card');
  card.classList.add(direction === 1 ? 'exit-right' : direction === -1 ? 'exit-left' : 'exit-down');
  try {
    await fetchJson(direction === 0 ? '/api/skip' : '/api/vote', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(direction === 0 ? {id:item.id} : {id:item.id, vote:direction})});
  } catch (error) {
    card.classList.remove('exit-left','exit-right','exit-down');
    alert(`Could not save your choice: ${error.message}`);
    state.busy = false;
    renderCard();
    return;
  }
  await new Promise(resolve => setTimeout(resolve, 220));
  state.feed.shift();
  try {
    renderCard();
    await loadStats();
    if (direction !== 0 || !state.feed.length) await loadFeed();
  } catch (error) {
    alert(`Choice saved, but the feed could not refresh: ${error.message}`);
  } finally {
    state.busy = false;
    renderCard();
  }
}

function setupDrag() {
  const card = $('swipe-card');
  let start = null, dx = 0;
  card.addEventListener('pointerdown', e => {
    if (state.busy || !state.feed.length || e.target.closest('a')) return;
    start = {x:e.clientX, y:e.clientY}; dx = 0;
    card.setPointerCapture(e.pointerId); card.classList.add('dragging');
  });
  card.addEventListener('pointermove', e => {
    if (!start) return;
    dx = e.clientX - start.x;
    if (Math.abs(e.clientY - start.y) > Math.abs(dx) * 2 && Math.abs(dx) < 15) return;
    card.style.transform = `translateX(${dx}px) rotate(${dx / 25}deg)`;
    const amount = Math.min(1, Math.abs(dx) / 100);
    card.querySelector(dx >= 0 ? '.swipe-stamp.save' : '.swipe-stamp.pass')?.style.setProperty('opacity', amount);
    card.querySelector(dx >= 0 ? '.swipe-stamp.pass' : '.swipe-stamp.save')?.style.setProperty('opacity', 0);
  });
  const end = () => {
    if (!start) return;
    start = null; card.classList.remove('dragging');
    card.style.transform = '';
    if (Math.abs(dx) > 90) decide(dx > 0 ? 1 : -1);
    else card.querySelectorAll('.swipe-stamp').forEach(el => el.style.opacity = 0);
  };
  card.addEventListener('pointerup', end); card.addEventListener('pointercancel', end);
}

async function refreshSources() {
  await fetchJson('/api/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  if (state.stats) state.stats.refreshing = true;
  renderStats(); renderCard();
  pollRefresh();
}
let polling = false;
async function pollRefresh() {
  if (polling) return;
  polling = true;
  try {
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 3000));
      await loadStats();
      if (!state.stats.refreshing) { await loadFeed(); if (state.view === 'saved') await loadSaved(); if (state.view === 'network') await loadNetwork(); break; }
    }
  } finally { polling = false; }
}

async function setView(view) {
  state.view = view;
  document.querySelectorAll('[data-view]').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === `view-${view}`));
  if (view === 'saved') await loadSaved();
  if (view === 'network') await loadNetwork();
  else if (state.graphRaf) { cancelAnimationFrame(state.graphRaf); state.graphRaf = null; }
}

async function loadNetwork() {
  state.graph = await fetchJson('/api/network');
  drawNetwork();
}

function drawNetwork() {
  const canvas = $('network-canvas'), panel = canvas.parentElement, tip = $('network-tooltip');
  const data = state.graph;
  if (!data) return;
  const width = canvas.clientWidth, height = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr; canvas.height = height * dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
  const nodes = data.nodes.map((item, i) => ({...item, x: width/2 + (Math.random()-.5)*width*.7, y:height/2+(Math.random()-.5)*height*.7, vx:0, vy:0, r:item.saved?7:5}));
  const byId = Object.fromEntries(nodes.map(n => [n.id,n]));
  const edges = data.edges.map(e => ({...e,a:byId[e.source],b:byId[e.target]})).filter(e => e.a && e.b);
  let drag = null, dragStart = null, hover = null, frame = 0;
  function step() {
    frame++;
    if (frame < 700) {
      for (const e of edges) {
        const dx=e.b.x-e.a.x,dy=e.b.y-e.a.y,dist=Math.hypot(dx,dy)||1,desired=55+70*(1-e.weight),force=(dist-desired)*.0008;
        e.a.vx+=dx*force;e.a.vy+=dy*force;e.b.vx-=dx*force;e.b.vy-=dy*force;
      }
      for (let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++) {
        const a=nodes[i],b=nodes[j],dx=b.x-a.x,dy=b.y-a.y,d2=dx*dx+dy*dy+1,force=Math.min(.22,350/d2);
        a.vx-=dx*force/d2*30;a.vy-=dy*force/d2*30;b.vx+=dx*force/d2*30;b.vy+=dy*force/d2*30;
      }
      for(const n of nodes){
        n.vx+=(width/2-n.x)*.0003;n.vy+=(height/2-n.y)*.0003;
        if(n!==drag){n.x+=n.vx;n.y+=n.vy; n.x=Math.max(16,Math.min(width-16,n.x));n.y=Math.max(16,Math.min(height-16,n.y));}
        n.vx*=.86;n.vy*=.86;
      }
    }
    ctx.clearRect(0,0,width,height);
    for(const e of edges){ctx.strokeStyle=`rgba(129,162,188,${.08+e.weight*.45})`;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(e.a.x,e.a.y);ctx.lineTo(e.b.x,e.b.y);ctx.stroke();}
    for(const n of nodes){
      if(n.saved){ctx.fillStyle='rgba(206,245,117,.15)';ctx.beginPath();ctx.arc(n.x,n.y,n.r+6,0,Math.PI*2);ctx.fill();}
      ctx.fillStyle=n.saved?'#cef575':n===hover?'#d9ecff':'#8ab7df';ctx.beginPath();ctx.arc(n.x,n.y,n===hover?n.r+2:n.r,0,Math.PI*2);ctx.fill();
    }
    state.graphRaf=requestAnimationFrame(step);
  }
  const locate=e=>{const r=canvas.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};};
  const nearest=p=>nodes.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<15);
  canvas.onpointermove=e=>{const p=locate(e);if(drag){drag.x=p.x;drag.y=p.y;return;}hover=nearest(p);canvas.style.cursor=hover?'pointer':'grab';tip.style.display=hover?'block':'none';if(hover){tip.textContent=hover.title;tip.style.left=`${Math.min(p.x+15,width-240)}px`;tip.style.top=`${Math.max(55,p.y+55)}px`;}};
  canvas.onpointerdown=e=>{dragStart=locate(e);drag=nearest(dragStart);if(drag)canvas.setPointerCapture(e.pointerId);};
  canvas.onpointerup=e=>{const clicked=drag, end=locate(e);drag=null;if(clicked && dragStart && Math.hypot(dragStart.x-end.x)<7 && Math.abs(dragStart.y-end.y)<7)window.open(clicked.url,'_blank','noopener,noreferrer');dragStart=null;};
  canvas.onpointerleave=()=>{if(!drag){hover=null;tip.style.display='none';}};
  if(state.graphRaf)cancelAnimationFrame(state.graphRaf);
  step();
}

async function boot() {
  $('today').textContent = new Date().toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'});
  $('pass-button').onclick = () => decide(-1); $('skip-button').onclick = () => decide(0); $('save-button').onclick = () => decide(1);
  $('refresh-button').onclick = refreshSources;
  document.querySelectorAll('[data-view]').forEach(el => el.onclick = () => setView(el.dataset.view));
  document.querySelectorAll('[data-read-filter]').forEach(el => el.onclick = () => { state.readFilter = el.dataset.readFilter; renderSaved(); });
  $('saved-grid').addEventListener('click', async event => {
    const button = event.target.closest('[data-read-id]');
    if (!button) return;
    button.disabled = true;
    try { await setReadState(button.dataset.readId, button.dataset.readValue === 'true'); }
    catch (error) { button.disabled = false; alert(`Could not update read status: ${error.message}`); }
  });
  document.addEventListener('keydown', e => {if(state.view !== 'discover' || e.target.matches('input,textarea'))return;if(e.key==='ArrowLeft')decide(-1);if(e.key==='ArrowDown' && state.stats?.skip_cooldown){e.preventDefault();decide(0);}if(e.key==='ArrowRight')decide(1);});
  setupDrag();
  window.addEventListener('resize',()=>{if(state.view==='network')drawNetwork();});
  try {await loadStats(); await loadFeed(); if(state.stats.refreshing)pollRefresh();}
  catch(error){$('swipe-card').innerHTML=`<div class="empty-card"><h3>Could not load the feed.</h3><p>${esc(error.message)}</p></div>`;}
}
boot();
