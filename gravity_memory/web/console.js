'use strict';
let session={team:false,authenticated:false,owner:false,namespaces:[],namespace_activity:[]};
let rangeDays=30, agentFilter='', runDay='', runUntil='', focusRun='', graphKey='', lessonFilter='', setupClient='Claude Code';
const consoleViews={runs:runsPage,graph:graphPage,namespaces:namespacesPage,connectors:connectorsPage,setup:setupPage,keys:keysPage};
const nf=new Intl.NumberFormat();
const quantity=n=>nf.format(n??0);
const bytes=n=>n<1024?quantity(n)+' B':n<1048576?(n/1024).toFixed(1)+' KB':(n/1048576).toFixed(1)+' MB';
const localTime=value=>value?new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'—';
const currentSpace=()=>session.namespaces.find(n=>n.id===scope);
const canManage=()=>!session.team||session.owner||['owner','admin'].includes(currentSpace()?.role);
const canWrite=()=>canManage()||currentSpace()?.role==='member';
const options=(values,selected)=>values.map(value=>`<option value="${safe(value)}" ${value===selected?'selected':''}>${safe(value)}</option>`).join('');
function formAction(form,action){
  form.onsubmit=async event=>{
    event.preventDefault();
    const button=event.submitter,error=form.querySelector('[role=alert]');
    button.disabled=true;if(error)error.textContent='';
    try{await action(new FormData(form))}
    catch(exc){if(error)error.textContent=exc.message;else toast(exc.message)}
    finally{button.disabled=false}
  };
}
async function jsonRequest(path,args){
  const result=await fetch(path,args===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});
  const data=await result.json();if(!result.ok)throw Error(data.error||'Request failed');return data;
}
async function refreshSession(){
  session=await jsonRequest('/session');
  const select=el('workspace');
  select.innerHTML=session.namespaces.map(n=>`<option value="${safe(n.id)}">${safe(n.name)}</option>`).join('');
  select.value=scope;
}
function switchSpace(id){
  if(!session.namespaces.some(n=>n.id===id))return;
  scope=id;sessionStorage.setItem('gravity-workspace',scope);
  overview={};focusRun='';runDay='';runUntil='';agentFilter='';graphKey='';lessonFilter='';playArgs={query:'',budget:4096,filter:''};playResult=null;resetWorkspaceViews();
  clearTimeout(toastTimer);el('message').className='';el('message').textContent='';
  dialog.close();gotoPage('overview');
}
function accountHeader(){
  el('engine-status').textContent=session.team?'Team server':'Local engine';
  el('account-info').innerHTML=session.team?`${safe(session.user)}<small>${session.owner?'Server owner':'Team member'}</small>`:'Local workspace<small>On this device</small>';
  el('account-mark').textContent=session.team?String(session.user||'G').slice(0,1).toUpperCase():'G';
  el('account-menu').hidden=!session.team;
  el('account-password').onclick=()=>{
    el('account-menu').open=false;
    openAdminForm('Change password','<label>Current password<input required type="password" name="current_password" autocomplete="current-password"></label><label>New password<input required name="new_password" type="password" minlength="12" maxlength="512" autocomplete="new-password"></label><label>Confirm new password<input required name="confirm" type="password" autocomplete="new-password"></label><p class="hint">Other browser sessions will be signed out.</p>',async f=>{
      if(f.get('new_password')!==f.get('confirm'))throw Error('New passwords must match.');
      await jsonRequest('/auth/password',{current_password:f.get('current_password'),new_password:f.get('new_password')});
      dialog.close();toast('Password changed');
    },'Save new password');
  };
  el('logout').onclick=async()=>{
    try{
      await jsonRequest('/auth/logout',{});
      sessionStorage.removeItem('gravity-workspace');
      history.replaceState(null,'',location.pathname);
      location.reload();
    }catch(error){toast(error.message)}
  };
}
async function initializeConsole(){
  await refreshSession();
  if(session.team&&!session.authenticated){showLogin();return}
  accountHeader();
  if(!session.namespaces.length){
    document.body.classList.add('no-namespace');el('navigation').replaceChildren();
    el('title').textContent='Your namespaces';el('subtitle').textContent='';
    content.innerHTML='<section class="panel empty">Your account has no namespace access yet. Ask a team administrator for access.</section>';content.setAttribute('aria-busy','false');return;
  }
  const saved=sessionStorage.getItem('gravity-workspace');
  scope=session.namespaces.some(x=>x.id===saved)?saved:session.namespaces[0].id;
  const initial=location.hash.slice(1);await gotoPage(Object.hasOwn(pages,initial)?initial:'overview',false);
}
function showLogin(){
  document.body.classList.add('sign-in');
  el('main').innerHTML=`<section class="login-panel"><div class="login-brand"><img src="/favicon.ico" alt=""><b>Gravity Memory</b></div><h1>Sign in to your team</h1>
    <form id="login-form"><label>Username<input name="user" autocomplete="username" required autofocus></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><p class="err" role="alert"></p><button class="primary">Sign in</button></form></section>`;
  formAction(el('login-form'),async f=>{await jsonRequest('/auth/login',Object.fromEntries(f));location.reload()});
}
function commandPalette(){
  const palette=el('palette'),input=el('palette-input'),items=el('palette-items');
  function show(){
    const q=input.value.trim().toLowerCase();
    const matches=Object.entries(pages).filter(([id,p])=>p[0].toLowerCase().includes(q)||id.includes(q));
    items.innerHTML=matches.map(([id,p])=>`<button type="button" data-jump="${id}">${icon(id)} ${safe(p[0])}</button>`).join('')+(q?`<button type="button" data-find>${icon('context')} Search memories for “${safe(input.value.trim())}”</button>`:'');
  }
  input.value='';show();palette.showModal();input.focus();input.oninput=show;
  items.onclick=async event=>{const b=event.target.closest('button');if(!b)return;const term=input.value.trim();palette.close();menu(false);
    if(b.dataset.jump){if(b.dataset.jump==='runs')focusRun='';await gotoPage(b.dataset.jump);el('main').focus();return}
    playgroundMode='context';await gotoPage('context');el('context-query').value=term;el('context-form').requestSubmit()};
  input.onkeydown=event=>{if(event.key==='ArrowDown'){event.preventDefault();items.querySelector('button')?.focus()}
    if(event.key==='Enter'){event.preventDefault();items.querySelector('button')?.click()}};
  items.onkeydown=event=>{const list=Array.from(items.querySelectorAll('button')),index=list.indexOf(document.activeElement);
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();list[(index+(event.key==='ArrowDown'?1:-1)+list.length)%list.length]?.focus()}};
}
function frame(title,action,body,cls=''){
  return `<section class="panel ${cls}"><div class="panelhead"><h2>${title}</h2>${action||''}</div>${body}</section>`;
}
function emptyState(symbol,title,text,action=''){
  return `<div class="blank"><span class="empty-symbol">${icon(symbol)}</span><h3>${title}</h3><p>${text}</p>${action}</div>`;
}
function metricCard(label,value,ico,foot,route){
  return `<button class="metric" data-page="${route}"><span>${icon(ico)} ${label}</span><strong>${quantity(value)}</strong><small>${foot}</small></button>`;
}
function activityChart(days){
  if(!days.length)return '<p class="empty">No activity in this window.</p>';
  const peak=Math.max(1,...days.map(d=>d.reads+d.writes)), width=700,height=126;
  const step=width/days.length,bar=Math.max(1,step*.72), base=118, scale=94/peak;
  const bars=days.map((d,i)=>{
    const x=i*step+(step-bar)/2,read=d.reads*scale,write=d.writes*scale;
    return `<g role="button" tabindex="0" aria-label="${safe(d.date)}: ${quantity(d.reads)} reads and ${quantity(d.writes)} writes. Show runs." data-day="${safe(d.date)}"><title>${safe(d.date)} · ${d.reads} reads · ${d.writes} writes</title><rect class="hit" x="${i*step}" width="${step}" height="${height}"/><rect class="bar-read" x="${x}" y="${base-read}" width="${bar}" height="${Math.max(0,read)}"/><rect class="bar-write" x="${x}" y="${base-read-write}" width="${bar}" height="${Math.max(0,write)}"/>${!read&&!write?`<rect class="bar-zero" x="${x}" y="${base-1}" width="${bar}" height="1"/>`:''}</g>`;
  }).join('');
  const label=d=>new Date(d+'T12:00:00Z').toLocaleDateString(undefined,{month:'short',day:'numeric',timeZone:'UTC'});
  return `<div class="chart-wrap"><div class="chart-labels"><span>${quantity(peak)}</span><span>0</span></div><div class="chart-data"><svg role="group" aria-label="Daily memory reads and writes, UTC" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${[24,71,118].map(y=>`<line class="grid-line" x1="0" x2="700" y1="${y}" y2="${y}"/>`).join('')}${bars}</svg><div class="chart-dates"><span>${safe(label(days[0].date))}</span><span>${safe(label(days.at(-1).date))} UTC</span></div></div></div>`;
}
function rangeControl(){return `<div class="segmented" aria-label="Activity interval">${[7,30,90].map(n=>`<button data-range="${n}" aria-pressed="${rangeDays===n}">${n}D</button>`).join('')}</div>`}
function runRow(r){return `<button class="console-row" data-run="${safe(r.run_id)}"><span class="tile-icon">${icon('runs')}</span><span class="row-core"><strong>${safe(r.title)}</strong><small>${safe(r.actor)} · ${safe(localTime(r.started_at))}</small></span>${badge(r.status)}<span class="arrow">›</span></button>`}
function latestRow(q){return `<button class="console-row" data-trace="${safe(q.trace_id)}"><span class="tile-icon">${icon('context')}</span><span class="row-core"><strong>${safe(q.query)}</strong><small>${safe(q.actor)} · ${safe(localTime(q.at))}</small></span><span class="arrow">›</span></button>`}
function namespaceList(){
  const counts=new Map((session.namespace_activity||[]).map(n=>[n.id,n.requests]));
  return session.namespaces.map(n=>({id:n.id,name:n.name,requests:counts.get(n.id)||0})).sort((a,b)=>b.requests-a.requests||a.name.localeCompare(b.name));
}
async function dashboard(d,rev){
  const [a,latest]=await Promise.all([api('activity',{days:rangeDays,agent:agentFilter||undefined}),api('runs',{limit:5,agent:agentFilter||undefined})]);
  await refreshSession();
  if(rev!==serial)return;
  const names=namespaceList(),peak=Math.max(1,...names.map(n=>n.requests));
  const agentOptions=[...new Set([...a.agents,...(agentFilter?[agentFilter]:[])])];
  if(!agentFilter)agentOptions.sort();
  content.innerHTML=`<div class="dashboard-controls"><span class="period-label">${icon('clock')} Last ${rangeDays} days</span><div><label class="agent-filter"><span class="visually-hidden">Filter activity by agent</span><select id="overview-agent"><option value="">All agents</option>${options(agentOptions,agentFilter)}</select></label>${rangeControl()}</div></div>
  <div class="metrics console-metrics">${metricCard('Agent runs',a.runs,'runs','In selected interval','runs')}${metricCard('Memory reads',a.reads,'context','In selected interval','runs')}${metricCard('Memory writes',a.writes,'changes','In selected interval','runs')}${metricCard('Memory keys',d.counts.keys,'library','In this namespace','library')}</div>
  <div class="activity-layout">${frame('Request activity','<div class="legend"><span><i class="read-swatch"></i>Reads</span><span><i class="write-swatch"></i>Writes</span></div>',activityChart(a.days),'activity-panel')}
  ${frame('Namespaces',buttonLink('namespaces','Manage'),`<div class="namespace-rank">${names.slice(0,5).map(n=>`<button data-space="${safe(n.id)}"><span class="ns-name">${icon('namespaces')}${safe(n.name)}</span><progress value="${Number(n.requests)}" max="${peak}" aria-label="Requests"></progress><span class="mono">${quantity(n.requests)}</span></button>`).join('')}</div><p class="panel-caption">Requests across accessible namespaces · last 30 days</p>`,'namespace-panel')}</div>
  <div class="split-panels">${frame('Recent agent runs',buttonLink('runs','All runs'),latest.items.length?latest.items.map(runRow).join(''):emptyState('runs','No runs recorded','Connect an agent or try a save-and-recall run.',buttonLink('setup','Connect an agent')))}
  ${frame('Latest searches',buttonLink('context','Playground'),a.latest_searches.length?a.latest_searches.slice(0,5).map(latestRow).join(''):emptyState('context','No searches in this interval','Recall requests appear here with the exact evidence returned.'))}</div>
  <div class="split-panels">${frame('Recent sources',buttonLink('sources','View archive'),d.recent.length?d.recent.slice(0,4).map(recent).join(''):emptyState('sources','No sources yet','Add a source to start your archive.'))}
  ${frame('Memory changes',buttonLink('library','Open library'),d.memories.length?memoryTable(d.memories.slice(0,4)):emptyState('library','No memory keys yet','Cited facts appear here as agents add sources.'))}</div>`;
  el('overview-agent').onchange=e=>{agentFilter=e.target.value;render()};
}
function codeBlock(label,data){
  return `<details class="code-details"><summary>${safe(label)}</summary><pre>${safe(JSON.stringify(data,null,2))}</pre></details>`;
}
async function runsPage(rev){
  if(focusRun){await inspector(focusRun,rev);return}
  const [result,a]=await Promise.all([api('runs',{limit:100,agent:agentFilter||undefined,since:runDay||undefined,until:runUntil||undefined}),api('activity',{days:90})]);
  if(rev!==serial)return;
  content.innerHTML=`<form class="filters run-filters" id="run-filters"><label>Agent<select name="agent"><option value="">All agents</option>${options([...new Set([...a.agents,...(agentFilter?[agentFilter]:[])])],agentFilter)}</select></label><label>From (UTC)<input type="date" name="since" value="${safe(runDay)}"></label><label>Through (UTC)<input type="date" name="until" value="${safe(runUntil)}"></label><button class="secondary">Filter runs</button><button type="button" class="minor" id="clear-runs">Clear</button><p class="err" role="alert"></p></form>
  ${frame('Run history',`<span class="count">${quantity(result.items.length)}</span>`,result.items.length?result.items.map(runRow).join(''):emptyState('runs','No runs found','Try a different filter or start a run from Agent setup.',buttonLink('setup','Agent setup')))}`;
  formAction(el('run-filters'),async f=>{if(f.get('since')&&f.get('until')&&f.get('since')>f.get('until'))throw Error('The end date must follow the start date.');agentFilter=f.get('agent');runDay=f.get('since');runUntil=f.get('until');await render()});
  el('clear-runs').onclick=()=>{agentFilter='';runDay='';runUntil='';render()};
}
async function inspector(id,rev){
  const {run,events}=await api('run_detail',{run_id:id});if(rev!==serial)return;
  el('title').textContent='Run inspector';el('crumb').textContent='Run inspector';el('subtitle').textContent='';
  const rows=events.map(event=>`<button class="console-row" data-trace="${safe(event.trace_id)}"><span class="tile-icon">${icon(event.op==='recall'?'context':'activity')}</span><span class="row-core"><strong>${safe(pretty(event.op))}</strong><small>${safe(localTime(event.at))} · ${safe(event.actor)}</small></span><span class="mono">${Number(event.latency_ms||0).toFixed(1)} ms</span><span class="arrow">›</span></button>`).join('');
  content.innerHTML=`<button class="minor back" data-all-runs>← All runs</button><section class="panel inspector-summary"><div class="title-line"><span class="agent-emblem">${icon('runs')}</span><div><h2>${safe(run.title)}</h2><p class="mono">${safe(run.run_id)}</p></div>${badge(run.status)}</div><dl class="meta-list"><div><dt>Agent</dt><dd>${safe(run.actor)}</dd></div><div><dt>Started</dt><dd>${safe(localTime(run.started_at))}</dd></div><div><dt>Requests</dt><dd>${quantity(events.length)}</dd></div></dl></section>
  ${run.parent_run_id?`<button class="link" data-run="${safe(run.parent_run_id)}">Inspect parent run →</button>`:''}${run.summary?frame('Handoff summary','','<p class="padded">'+safe(run.summary)+'</p>'):''}
  ${frame('Request timeline','<small>Open a request to inspect evidence</small>',rows||'<p class="empty">No requests recorded.</p>')}`;
}
function evidenceItems(items=[]){
  return items.map(item=>`<article class="evidence-item"><div class="title-line"><strong>${safe(item.key||pretty(item.kind||'passage'))}</strong>${item.kind?badge(item.kind):''}</div>${item.value!=null?`<p>${safe(item.value)}</p>`:''}<blockquote>${safe(item.quote||'')}</blockquote>${item.source_id?`<button class="link" data-proof="${safe(item.source_id)}" data-start="${Number(item.start||0)}" data-stop="${Number(item.stop||0)}">${icon('sources')} ${safe(item.source||item.source_id)} ↗</button>`:''}</article>`).join('');
}
function explainRetrieval(t){
  if(t.op!=='recall')return '';
  const d=t.diagnostics||{},out=t.output||{},decisions=[...(d.candidates||[]),...(d.excluded||[])];
  const rows=decisions.map(x=>`<li><span>${x.rank?'#'+Number(x.rank)+' · ':''}${safe(x.key||x.kind||'Source')}</span><small>${safe(pretty(x.reason||'Unknown'))}</small>${x.source_id?`<button class="link" data-source="${safe(x.source_id)}">Inspect source</button>`:''}</li>`).join('');
  return `<section class="retrieval-reason"><h3>Retrieval decisions</h3><p>${safe(d.reason||out.reason||'')}</p><dl class="meta-list"><div><dt>Context size</dt><dd>${bytes(d.response_bytes||out.bytes||0)} / ${bytes(out.budget_bytes||0)}</dd></div>${d.model?`<div><dt>Model</dt><dd>${safe(d.model)}</dd></div>`:''}${d.version?`<div><dt>Engine</dt><dd>${safe(d.version)}</dd></div>`:''}</dl>${rows?`<details><summary>Ranked and excluded candidates (${decisions.length})</summary><ol>${rows}</ol></details>`:''}</section>`;
}
async function traceView(id){
  const version=modal('Request trace','<p class="note">Loading request…</p>');
  try{
    const t=await api('trace',{trace_id:id});if(version!==dialogSerial)return;
    detail.innerHTML=`<div class="trace-title">${badge(t.op)}<span class="mono">${safe(localTime(t.at))}</span></div><h2 class="trace-query">${safe(t.input.query||t.input.title||pretty(t.op))}</h2><p class="mono wrap">${safe(t.trace_id)}</p><dl class="meta-list trace-meta"><div><dt>Actor</dt><dd>${safe(t.actor)}</dd></div><div><dt>Latency</dt><dd>${Number(t.latency_ms||0).toFixed(2)} ms</dd></div><div><dt>Route</dt><dd>${safe(t.output?.route||'—')}</dd></div></dl>
    ${t.op==='recall'?`<div class="between"><h3>Returned evidence</h3><span class="count">${t.output?.items?.length||0}</span></div>${evidenceItems(t.output?.items)||'<p class="note">No eligible evidence was returned.</p>'}<button id="compare-trace" class="secondary">Compare with current recall</button><div id="comparison" aria-live="polite"></div>`:''}
    ${explainRetrieval(t)}${codeBlock('Request input',t.input)}${codeBlock('Recorded response',t.output)}${codeBlock('Retrieval diagnostics',t.diagnostics)}<p class="err" role="alert"></p>`;
    if(el('compare-trace'))el('compare-trace').onclick=async event=>{const button=event.currentTarget;button.disabled=true;
      try{const c=await api('compare_trace',{trace_id:id});if(version!==dialogSerial)return;
        el('comparison').innerHTML=`<div class="compare-grid">${[['Recorded',c.recorded],['Current',c.current]].map(([label,value])=>`<section><h3>${label}</h3>${evidenceItems(value?.items)||'<p class="note">No evidence.</p>'}${codeBlock('Full bundle',value)}</section>`).join('')}</div>`;
      }catch(error){if(version===dialogSerial)err(error)}finally{button.disabled=false}};
  }catch(error){if(version===dialogSerial)detail.innerHTML='<p class="err" role="alert">'+safe(error.message)+'</p>'}
}
async function graphPage(rev){
  const library=await api('library',{limit:100});
  if(rev!==serial)return;
  const key=graphKey||library.items[0]?.key||'';
  const graph=key?await api('connections',{key,limit:24}):null;
  if(rev!==serial)return;
  content.innerHTML=`<form class="filters" id="graph-form"><label>Memory key<input name="key" list="graph-keys" required value="${safe(key)}" placeholder="e.g. contact.channel"></label><datalist id="graph-keys">${library.items.map(k=>`<option value="${safe(k.key)}">`).join('')}</datalist><button class="secondary">Explore connections</button><p class="err" role="alert"></p></form>
  ${graph?frame('Evidence connections',`<span class="count">${graph.edges.length} links</span>`,`<div class="graph-root"><span class="agent-emblem">${icon('graph')}</span><div><small>Selected memory</small><h3>${safe(key)}</h3></div></div><div class="graph-columns">${[['source','Supporting sources'],['memory','Related memories']].map(([kind,title])=>`<div><h3>${title}</h3>${graph.nodes.filter(n=>n.kind===kind&&n.key!==key).map(n=>`<button class="graph-node" ${kind==='source'?`data-source="${safe(n.request_id)}"`:`data-graph="${safe(n.key)}"`}>${icon(kind==='source'?'sources':'library')}${safe(n.label||n.key)} <span>↗</span></button>`).join('')||'<p class="note">No connections.</p>'}</div>`).join('')}</div>`):frame('Evidence connections','',emptyState('graph','No connected memories','Once a memory has supporting sources, you can explore them here.'))}`;
  formAction(el('graph-form'),async f=>{graphKey=f.get('key').trim();await render()});
}
function admin(op,args){return jsonRequest('/admin/'+op,args)}
function openAdminForm(title,fields,action,submitLabel){
  modal(title,`<form id="admin-form">${fields}<p class="err" role="alert"></p><div class="actionbar"><button class="primary">${submitLabel}</button></div></form>`);
  formAction(el('admin-form'),action);
}
function namespacePolicy(p){
  const description=p.retention_days==null?'Until deleted':p.retention_days+' days';
  return frame('Trace retention & learning',null,`<div class="padded"><dl class="meta-list"><div><dt>Full traces</dt><dd>${quantity(p.trace_count)}</dd></div><div><dt>Trace storage</dt><dd>${bytes(p.trace_bytes||0)}</dd></div><div><dt>Retention</dt><dd>${safe(description)}</dd></div></dl><form class="policy-form" id="policy-form"><label>Retention in days<input type="number" name="days" min="1" placeholder="Until deleted" value="${safe(p.retention_days??'')}"></label><label>Automatic lesson types<input name="types" value="${safe((p.auto_types||[]).join(', '))}" placeholder="e.g. preference, procedure"></label><small class="hint">Comma-separated types. Leave empty to review every lesson.</small><p class="err" role="alert"></p><button class="secondary">Save policy</button></form></div>`);
}
async function namespacesPage(rev){
  await refreshSession();if(rev!==serial)return;
  const manage=canManage();
  const [members,p]=await Promise.all([session.team&&manage?admin('list_members',{namespace:scope}):null,manage?api('get_policy'):null]);if(rev!==serial)return;
  content.innerHTML=frame('Accessible namespaces',session.team&&session.owner?'<button class="secondary" id="new-namespace">Create namespace</button>':'',
    namespaceList().map(n=>`<button class="console-row" data-space="${safe(n.id)}"><span class="tile-icon">${icon('namespaces')}</span><span class="row-core"><strong>${safe(n.name)}</strong><small>${safe(n.id)}</small></span>${n.id===scope?'<span class="route">Selected</span>':''}<span class="arrow">›</span></button>`).join(''))+
    (members?frame('Namespace access',`<div class="inline">${session.owner?'<button class="secondary" id="new-user">Create account</button>':''}<button class="secondary" id="grant">Change access</button></div>`,members.items.map(m=>`<div class="plain-row"><span>${safe(m.user)}</span>${badge(m.role)}</div>`).join('')||'<p class="empty">No explicit grants.</p>'):'')+(p?namespacePolicy(p):'');
  if(el('new-namespace'))el('new-namespace').onclick=()=>openAdminForm('Create namespace','<label>Name<input required name="name" maxlength="80" placeholder="Engineering"></label><label>Namespace ID<input required name="id" placeholder="engineering"></label>',async f=>{const n=await admin('create_namespace',Object.fromEntries(f));await refreshSession();switchSpace(n.id);toast('Namespace created')},'Create namespace');
  if(el('new-user'))el('new-user').onclick=()=>openAdminForm('Create account','<label>Username<input required name="user" autocomplete="off"></label><label>Initial password<input required name="password" type="password" minlength="12" autocomplete="new-password"></label>',async f=>{await admin('create_user',Object.fromEntries(f));dialog.close();toast('Account created. Assign namespace access to invite this member.')},'Create account');
  if(el('grant'))el('grant').onclick=()=>openAdminForm('Change namespace access',`<p class="hint">Applies to ${safe(currentSpace()?.name)}.</p><label>Account username<input name="user" required></label><label>Role<select name="role"><option value="viewer">Viewer</option><option value="member">Member</option><option value="admin">Namespace administrator</option><option value="none">Remove access</option></select></label>`,async f=>{await admin('grant',{...Object.fromEntries(f),namespace:scope});dialog.close();await render();toast('Namespace access updated')},'Save access');
  if(el('policy-form'))formAction(el('policy-form'),async f=>{await api('policy',{retention_days:f.get('days')?Number(f.get('days')):null,auto_types:f.get('types').split(',').map(x=>x.trim()).filter(Boolean)});await render();toast('Namespace policy saved')});
}
async function keysPage(rev){
  if(!session.team){content.innerHTML=frame('Agent credentials','',emptyState('keys','Local workspace','Local agents can connect through stdio. The shared team server issues revocable namespace keys.',buttonLink('setup','Open agent setup')));return}
  if(!canManage()){content.innerHTML=frame('Namespace keys','',emptyState('keys','Administrator access required','Ask a namespace administrator for a scoped agent key.'));return}
  const keys=await admin('list_keys',{namespace:scope});if(rev!==serial)return;
  content.innerHTML=frame('Namespace keys','<button class="primary" id="issue-key">Create API key</button>',keys.items.length?keys.items.map(k=>`<div class="plain-row"><div><strong>${safe(k.name)}</strong><small class="key">${safe((k.permissions||[]).join(' · '))}</small></div>${k.revoked?badge('revoked'):`<button class="minor" data-revoke="${safe(k.id)}">Revoke</button>`}</div>`).join(''):emptyState('keys','No agent keys','Create a key for an application or coding agent.'));
  el('issue-key').onclick=()=>openAdminForm('Create API key',`<p class="hint">Only valid in ${safe(currentSpace()?.name)}.</p><label>Name<input required name="name" placeholder="Support agent"></label><fieldset class="permission-checks"><legend>Permissions</legend>${['read','write','feedback'].map(p=>`<label><input type="checkbox" name="perm" value="${p}" ${p==='read'?'checked':''}>${pretty(p)}</label>`).join('')}<label><input type="checkbox" name="trusted">Trusted outcome reporter</label></fieldset>`,async f=>{
    const permissions=f.getAll('perm');if(!permissions.length)throw Error('Choose at least one permission.');
    const result=await admin('issue_key',{namespace:scope,name:f.get('name'),permissions,trusted:f.has('trusted')});
    modal('Your new API key',`<p>Copy this key now. It is shown once.</p><label>Agent key<input id="issued-key" readonly value="${safe(result.key)}" spellcheck="false"></label><button class="secondary" id="copy-issued">Copy key</button>`);el('issued-key').select();el('copy-issued').onclick=()=>copyValue(result.key,el('issued-key'));await render()
  },'Create key');
  content.querySelectorAll('[data-revoke]').forEach(b=>b.onclick=()=>openAdminForm('Revoke API key','<p>Agents using this credential will lose access immediately.</p>',async()=>{await admin('revoke_key',{id:b.dataset.revoke});dialog.close();await render();toast('Key revoked')},'Revoke key'));
}
const connectorLabels={github:'GitHub',notion:'Notion',drive:'Google Drive'};
const connectorHints={github:'owner/repository or owner/repository/path',notion:'Notion page URL or ID',drive:'Google Drive file or folder URL or ID'};
async function connectorsPage(rev){
  if(!canManage()){content.innerHTML=frame('Connected sources','',emptyState('connectors','Administrator access required','A namespace administrator manages published connectors.'));return}
  const result=await api('connector_list');if(rev!==serial)return;
  content.innerHTML=`<div class="connector-grid">${Object.entries(connectorLabels).map(([id,name])=>`<article class="connector-card"><span class="provider-mark ${id}" aria-hidden="true">${id==='github'?'GH':id==='drive'?'△':'N'}</span><div><h2>${name}</h2><p>${{github:'Repository files and folders',notion:'Pages and child blocks',drive:'Documents and folders'}[id]}</p></div><button class="secondary" data-provider="${id}" aria-label="Connect ${name}">Connect</button></article>`).join('')}</div>
  ${frame('Published connections','',result.items.length?result.items.map(c=>`<div class="connector-item"><div class="between"><h3>${safe(c.name)}</h3>${badge(c.status)}</div><p class="mono wrap">${safe(connectorLabels[c.provider])} · ${safe(c.target)}</p><small>${quantity(c.documents)} documents · ${c.last_synced_at?'Last sync '+safe(localTime(c.last_synced_at)):'Awaiting initial sync'}</small>${c.last_error?`<p class="err">${safe(c.last_error)}</p>`:''}<div class="actionbar"><button class="minor" data-unpublish="${safe(c.id)}">Remove</button><button class="secondary" data-resync="${safe(c.id)}">Sync now</button></div></div>`).join(''):emptyState('connectors','No published connections','Select a provider to publish its content to this namespace.'))}`;
  content.querySelectorAll('[data-provider]').forEach(b=>b.onclick=()=>{const provider=b.dataset.provider;
    openAdminForm('Connect '+connectorLabels[provider],`<div class="publish-note">${icon('namespaces')}<div>Publish to <strong>${safe(currentSpace()?.name||scope)}</strong><small>Members and agents with read access to this namespace can retrieve these sources.</small></div></div><label>Connection name<input name="name" required maxlength="100"></label><label>Selected source<input name="target" required placeholder="${safe(connectorHints[provider])}"></label><label>Server credential alias<input name="credential" required placeholder="engineering" autocomplete="off"></label><p class="help">Use an alias configured by your server administrator.</p><label>Sync interval (minutes)<input name="interval" required type="number" min="1" value="60"></label>`,async f=>{await api('connector_add',{provider,name:f.get('name'),target:f.get('target'),credential:f.get('credential'),interval_minutes:Number(f.get('interval'))});dialog.close();await render();toast('Connection saved')},'Publish connection');
  });
  content.querySelectorAll('[data-resync]').forEach(b=>b.onclick=async()=>{b.disabled=true;b.textContent='Syncing…';try{const r=await api('connector_sync',{id:b.dataset.resync});await render();toast(`${r.added} added · ${r.updated} updated · ${r.removed} removed`)}catch(error){toast(error.message);await render()}});
  content.querySelectorAll('[data-unpublish]').forEach(b=>b.onclick=()=>openAdminForm('Remove connection','<p>This removes the published source material and its derived records from this namespace.</p>',async()=>{await api('connector_remove',{id:b.dataset.unpublish});dialog.close();await render();toast('Connection removed')},'Remove connection'));
}
async function renderLessons(rev){
  const r=await api('lessons',lessonFilter?{status:lessonFilter}:{});if(rev!==serial)return;
  const list=r.items.map(l=>`<div class="lesson-row"><div class="between"><strong>${safe(l.key)}</strong>${badge(l.status)}</div><p>${safe(l.value)}</p><small>${safe(l.type)}</small>${l.source_id?` · <button class="link" data-source="${safe(l.source_id)}">View source</button>`:''}${canManage()?`<div class="inline">${(['pending','proposed','review'].includes(l.status)?['approve','reject']:['approved','promoted','active'].includes(l.status)?['retract']:[]).map(d=>`<button class="secondary" data-lesson="${safe(l.lesson_id)}" data-decision="${d}">${pretty(d)}</button>`).join('')}</div>`:''}</div>`).join('');
  content.insertAdjacentHTML('afterbegin',frame('Agent lessons',`<label class="compact"><span class="visually-hidden">Lesson status</span><select id="lesson-filter"><option value="">All decisions</option>${options(['pending','approved','rejected','retracted'],lessonFilter)}</select></label>`,list||'<p class="empty">No agent lessons match this view.</p>')+'<div class="sectionhead"><h2>Source review</h2></div>');
  el('lesson-filter').onchange=e=>{lessonFilter=e.target.value;render()};
  content.querySelectorAll('[data-lesson]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('review_lesson',{lesson_id:b.dataset.lesson,decision:b.dataset.decision});await render();toast('Lesson decision recorded')}catch(error){toast(error.message);b.disabled=false}});
}
async function copyValue(value,element){
  try{await navigator.clipboard.writeText(value);toast('Copied to clipboard')}
  catch{if(element?.select)element.select();else if(element){const r=document.createRange();r.selectNodeContents(element);const s=getSelection();s.removeAllRanges();s.addRange(r)}toast('Selected. Use your copy shortcut.')}
}
const clientLabels={'Claude Code':'claude','Codex':'codex','Cursor':'cursor','OpenCode':'opencode','Python':'python','TypeScript':'typescript'};
function setupSnippet(client){
  if(session.team){
    if(client==='Python')return "from gravity_memory.client import GravityClient\n\nmemory = GravityClient()\nprint(memory.call('activity', {'days': 7}))";
    if(client==='TypeScript')return "import { GravityClient } from '@gravity-memory/client';\n\nconst memory = new GravityClient();\nconsole.log(await memory.call('activity', {days: 7}));";
    return 'gravity-memory agent config '+clientLabels[client]+'\ngravity-memory agent smoke';
  }
  const path='<absolute-path-to-workspace.sqlite>';
  if(client==='Python')return `from gravity_memory import Ledger\n\nmemory = Ledger(r"${path}")\nprint(memory.context("current project"))`;
  if(client==='TypeScript')return 'gravity-memory team --help\n# Use a shared team URL and a scoped key with the TypeScript client.';
  const spec={command:'gravity-memory',args:['--db',path,'mcp']};
  if(client==='Claude Code')return `claude mcp add --transport stdio gravity -- gravity-memory --db "${path}" mcp`;
  if(client==='Codex')return `codex mcp add gravity -- gravity-memory --db "${path}" mcp`;
  if(client==='OpenCode')return JSON.stringify({mcp:{servers:{gravity:{type:'local',command:[spec.command,...spec.args]}}}},null,2);
  return JSON.stringify({mcpServers:{gravity:spec}},null,2);
}
async function setupPage(){
  const team=session.team;
  content.innerHTML=`<div class="setup-layout"><section class="panel padded"><div class="sectionhead flush"><span class="tile-icon">${icon('setup')}</span><h2>Connect a client</h2></div>
  <div class="import-tabs client-tabs">${Object.keys(clientLabels).map(client=>`<button data-client="${client}" aria-pressed="${client===setupClient}" class="${client===setupClient?'selected':''}">${client}</button>`).join('')}</div>
  ${team?`<p>Set these environment variables in the agent process, then run the setup commands.</p><dl class="env-list"><div><dt>GRAVITY_URL</dt><dd>${safe(location.origin)}</dd></div><div><dt>GRAVITY_NAMESPACE</dt><dd>${safe(scope)}</dd></div><div><dt>GRAVITY_KEY</dt><dd>${buttonLink('keys','Create a scoped key')}</dd></div></dl>`:'<p>Use the absolute path to your personal SQLite workspace. The Windows launcher stores it in <code>%LOCALAPPDATA%\\GravityMemory\\workspace.sqlite</code>.</p>'}
  <div class="snippet-head"><span>${safe(setupClient)}</span><button class="minor" id="copy-setup">Copy</button></div><pre class="shell-snippet" id="setup-snippet">${safe(setupSnippet(setupClient))}</pre></section>
  <section class="panel padded setup-check"><span class="agent-emblem">${icon('activity')}</span><h2>Verify the memory loop</h2><p>Start a real run, write a unique verification fact, recall its citation, and record the outcome in this namespace.</p>${canWrite()?'<button class="primary" id="run-smoke">Run verification</button>':'<p class="note">Write access is required.</p>'}<p id="smoke-status" role="status" class="note"></p></section></div>`;
  content.querySelectorAll('[data-client]').forEach(b=>b.onclick=()=>{setupClient=b.dataset.client;setupPage()});
  el('copy-setup').onclick=()=>copyValue(setupSnippet(setupClient),el('setup-snippet'));
  if(el('run-smoke'))el('run-smoke').onclick=async event=>{
    const revision=serial,space=scope,b=event.currentTarget;b.disabled=true;const msg=el('smoke-status');msg.textContent='Running verification…';
    const id=crypto.randomUUID(),marker='V'+id.replaceAll('-','').slice(0,12),body='Gravity verification code is '+marker+'.',key='setup.verification_'+marker.toLowerCase();
    try{
      const run=await api('start_run',{request_id:id+'-run',title:'Console verification'},space);
      const sourceId=id+'-source';
      await api('remember',{request_id:sourceId,source:'Agent setup verification',text:body,facts:[{key,value:marker,quote:body}]},space);
      const result=await api('recall',{request_id:id+'-recall',run_id:run.run_id,query:marker,budget_bytes:4096},space);
      const success=result.items.some(item=>item.source_id===sourceId&&item.value===marker);
      await api('outcome',{request_id:id+'-outcome',run_id:run.run_id,success,detail:success?'Recalled the verification fact with its original citation.':'Verification citation did not match.'},space);
      await api('handoff',{request_id:id+'-close',run_id:run.run_id,summary:success?'Save and cited recall verified.':'Recall verification failed.'},space);
      if(revision===serial){msg.innerHTML=(success?'Verified.':'Verification failed.')+` <button class="link" data-run="${safe(run.run_id)}">Inspect run →</button>`}
    }catch(error){if(revision===serial){msg.className='err';msg.textContent=error.message}}finally{b.disabled=false}
  };
}
let playgroundMode='agent',playArgs={query:'',budget:4096,filter:''},playResult=null;
function playTabs(){
  return `<div class="tabstrip play-tabs" aria-label="Playground mode">${[['agent','Agent recall'],['context','Context explorer']].map(([id,label])=>`<button class="${id===playgroundMode?'selected':''}" data-playmode="${id}" aria-pressed="${id===playgroundMode}">${label}</button>`).join('')}</div>`;
}
function playgroundPage(){
  if(playgroundMode==='context'){contextPage();content.insertAdjacentHTML('afterbegin',playTabs());return}
  const capable=canWrite();content.innerHTML=playTabs()+`<form class="panel padded" id="agent-query"><label>Recall query<input name="query" id="agent-query-text" required maxlength="2048" placeholder="What should the next agent know?" value="${safe(playArgs.query)}"></label>
    <div class="query-settings"><label>Response budget (bytes)<input name="budget" type="number" min="256" max="262144" required value="${playArgs.budget}"></label></div>
    <details class="context-options"${playArgs.filter?' open':''}><summary>Metadata filter</summary><label>Filter expression (JSON)<textarea name="filter" placeholder='{"field":"metadata.team","operator":"eq","value":"eng"}'>${safe(playArgs.filter)}</textarea></label></details>
    <p class="err" role="alert"></p><div class="actionbar">${capable?'<button class="primary">Run recall →</button>':'<p class="note">Open Context explorer for read-only searches.</p>'}</div></form><div id="agent-results" aria-live="polite"></div>`;
  if(playResult)showPlayResult(playResult);
  if(capable)formAction(el('agent-query'),async f=>{
    const rev=serial,space=scope;
    const q=f.get('query').trim(),budget=Number(f.get('budget')),filter=f.get('filter').trim();let filterValue;
    if(filter){try{filterValue=JSON.parse(filter)}catch{throw Error('Enter a valid JSON filter.')}}
    playArgs={query:q,budget,filter};const nonce=crypto.randomUUID(),r=await api('start_run',{request_id:nonce+'-run',title:'Playground recall'},space);
    const result=await api('recall',{request_id:nonce+'-recall',run_id:r.run_id,query:q,budget_bytes:budget,filter:filterValue},space);
    await api('handoff',{request_id:nonce+'-close',run_id:r.run_id,summary:'Playground recall completed.'},space);
    if(rev===serial){playResult=result;showPlayResult(result)}
  });
}
function showPlayResult(result){
  el('agent-results').innerHTML=frame('Recall result',badge(result.status),`<div class="padded"><p class="hint">${safe(result.route)} · ${quantity(result.items.length)} evidence items</p>${evidenceItems(result.items)||'<p class="note">No eligible evidence was returned inside this budget.</p>'}<button class="secondary" data-trace="${safe(result.trace_id)}">Inspect request</button>${codeBlock('Response bundle',result)}</div>`);
}
document.addEventListener('click',event=>{
  const target=event.target.closest('[data-range],[data-day],[data-space],[data-run],[data-all-runs],[data-trace],[data-graph],[data-playmode]');
  if(!target)return;
  const d=target.dataset;
  if(d.range){rangeDays=Number(d.range);render()}
  if(d.day){runDay=d.day;runUntil=d.day;focusRun='';gotoPage('runs')}
  if(d.space)switchSpace(d.space);
  if(d.run){focusRun=d.run;dialog.close();gotoPage('runs')}
  if('allRuns' in d){focusRun='';gotoPage('runs')}
  if(d.trace)traceView(d.trace);
  if(d.graph){graphKey=d.graph;render()}
  if(d.playmode){playgroundMode=d.playmode;playgroundPage()}
});
document.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.hasAttribute('data-day')){event.preventDefault();runDay=event.target.dataset.day;runUntil=runDay;focusRun='';gotoPage('runs')}});
function eraseSource(id){
  openAdminForm('Erase source','<p>This permanently removes this source, its derived records and affected trace content from the namespace.</p><label>Type ERASE to confirm<input name="confirm" required pattern="ERASE" autocomplete="off"></label>',async f=>{
    if(f.get('confirm')!=='ERASE')throw Error('Type ERASE to confirm.');
    await api('erase_source',{request_id:id});dialog.close();playResult=null;contextState.result=null;await render();toast('Source erased')
  },'Erase source');
}
