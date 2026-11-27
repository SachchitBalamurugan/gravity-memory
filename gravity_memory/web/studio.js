'use strict';
const el = id => document.getElementById(id);
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  profile:'M4 4h16v16H4z M7 16c0-4 8-4 8 0 M14 9a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M17 8h1 M17 11h1',
  context:'M15.5 15.5l5 5 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
  library:'M3 19V5h4v14z M10 19V5h4v14z M17 6l3-1 3 13-3 1z',
  sources:'M6 3h8l4 4v14H6z M14 3v5h5 M9 12h6 M9 16h6',
  review:'M3 15l3-10h12l3 10v5H3z M3 15h5l2 3h4l2-3h5',
  models:'M12 2l9 5v10l-9 5-9-5V7z M3 7l9 5 9-5 M12 12v10',
  import:'M4 14v6h16v-6 M12 16V3 M7 8l5-5 5 5',
  changes:'M3 7h15 M15 4l3 3-3 3 M21 17H6 M9 14l-3 3 3 3',
  arrow:'M5 12h14 M14 7l5 5-5 5', person:'M15 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M5 20v-2c0-6 14-6 14 0v2',
  chat:'M3 4h18v13H8l-5 4z M7 9h10 M7 12h6', link:'M9 15l6-6 M7 13l-2 2a3 3 0 0 0 4 4l3-3 M12 8l3-3a3 3 0 0 1 4 4l-3 3'
};
function icon(name) {
  return `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[name] || icons.sources}"></path></svg>`;
}
const pages = {
  overview:['Overview','Workspace overview','Recent evidence, current facts, and sources waiting for review.'],
  profile:['Memory profile','Memory profile','Current facts grouped by topic, with a citation for each value.'],
  context:['Context explorer','Context explorer','Search the facts and source passages available to your agent.'],
  library:['Memory library','Memory library','Inspect individual keys, corrections, and revision history.'],
  sources:['Source archive','Source archive','Browse original documents, conversation turns, and cited passages.'],
  review:['Review inbox','Review inbox','Confirm useful facts from unclassified sources.'],
  import:['Import data','Import data','Bring documents, conversations, and provider exports into this workspace.'],
  models:['Model library','Model library','Install and manage local models for review and retrieval.']
};
const navGroups = {'Workspace':['overview','profile','context'], 'Records':['library','sources','review'], 'Tools':['import','models']};
let page='overview', scope='personal', serial=0, dialogSerial=0, offset=0, query='', searchMode='archive', reviewRole='user', overview={}, toastTimer;
const content=el('content'), dialog=el('detail'), detail=el('dialog-content');
function toast(message){el('message').textContent=message;el('message').className='show';clearTimeout(toastTimer);toastTimer=setTimeout(()=>el('message').className='',4400)}
async function api(name,args={},space=scope){
 const response=await fetch('/studio/'+name,{method:'POST',headers:{'Content-Type':'application/json','X-Workspace':space},body:JSON.stringify(args)});
 const data=await response.json();if(!response.ok)throw Error(data.error||'Workspace unavailable');return data;
}
function nav(){
  el('navigation').innerHTML = Object.entries(navGroups).map(([label,ids]) => `<div class="group-title">${label}</div>${ids.map(id =>
    `<button data-page="${id}" ${page===id?'class="active" aria-current="page"':''}>${icon(id)}<span>${pages[id][0]}</span>${id==='review'&&overview.pending?`<span class="nav-count">${overview.pending}</span>`:''}</button>`).join('')}`).join('');
}
function badge(status){return `<span class="badge ${safe(status)}">${safe(status.replaceAll('_',' '))}</span>`}
function shownDate(date){return date?new Date(date).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}):''}
function buttonLink(pageId,label){return `<button class="link" data-page="${pageId}">${label} ${icon('arrow')}</button>`}
function metric(label,value,route,ico){return `<button class="metric" data-page="${route}"><span>${icon(ico)}${label}</span><strong>${value.toLocaleString()}</strong><i>↗</i></button>`}
function memoryTable(rows){
  return `<div class="tablehead"><span>Memory key</span><span>Current value</span><span>Status</span><span></span></div>${rows.length?rows.map(m=>
    `<button class="row" data-key="${safe(m.key)}"><span class="key">${safe(m.key)}</span><span>${safe(m.value??'—')}</span>${badge(m.status)}<span class="arrow">›</span></button>`).join(''):'<p class="empty">No matching memory keys.</p>'}`;
}
function recent(m){
  return `<button class="source-row" data-source="${safe(m.request_id)}"><i class="sourceicon">${icon(m.role==='document'?'sources':'chat')}</i><span><strong>${safe(m.source)}</strong><small>${safe(shownDate(m.effective_at))}</small></span><span class="arrow">›</span></button>`;
}
function overviewPage(d,profile){
  const n=d.counts;
  const items=profile.groups.flatMap(group=>group.items);
  const display=items.find(f=>f.key==='profile.name')?.value;
  const main=items.filter(f=>f.key!=='profile.name').sort((a,b)=>profilePriority(a.key)-profilePriority(b.key)).slice(0,3);
  const monogram=display?Array.from(String(display).trim())[0]:'G';
  content.innerHTML=`<div class="metrics">${metric('Memory keys',n.keys,'library','library')}${metric('Original sources',n.documents,'sources','sources')}${metric('Awaiting review',d.pending,'review','review')}</div>
    <div class="overviewgrid">
      <section class="profile-card">
        <div class="panelhead"><h2>Workspace profile</h2>${buttonLink('profile','Open profile')}</div>
        <div class="profile-identity"><span class="avatar" aria-hidden="true">${safe(monogram)}</span><div><h3>${safe(display || (profile.total?'Workspace facts':'No profile yet'))}</h3><span>${profile.total} current ${profile.total===1?'fact':'facts'}</span></div></div>
        ${main.length?`<dl class="profile-lines">${main.map(m=>`<div><dt>${safe(friendlyKey(m.key))}</dt><dd><button data-key="${safe(m.key)}">${safe(m.value)} <span>↗</span></button></dd></div>`).join('')}</dl>`:'<p class="profile-empty">Confirmed facts from this workspace appear here.</p>'}
      </section>
      <section class="panel recent"><div class="panelhead"><h2>Recent sources</h2>${buttonLink('sources','View all')}</div>
        ${d.recent.length?d.recent.slice(0,4).map(recent).join(''):'<p class="empty">Add a source to start your archive.</p>'}
      </section>
    </div>
    <section class="panel"><div class="panelhead"><h2>Memory library</h2>${buttonLink('library','Browse keys')}</div>${memoryTable(d.memories.slice(0,6))}</section>`;
}
function pager(more,n){return `<div class="pager"><small>${n?`${offset+1}–${offset+n}`:'0 results'}</small><button class="secondary" data-prev ${offset?'':'disabled'}>Previous</button><button class="secondary" data-next ${more?'':'disabled'}>Next</button></div>`}
function sourceItems(rows,review=false){
 return rows.map(m=>`<button class="listitem" ${Number.isInteger(m.start)?`data-proof="${safe(m.request_id)}" data-start="${m.start}" data-stop="${m.stop}"`:`data-source="${safe(m.request_id)}"`} ${review?'data-review="1"':''}><i class="sourceicon">${icon(review?'review':'sources')}</i><div><strong>${safe(m.source)}</strong><p>${safe(m.preview||m.quote)}</p><small>${safe(shownDate(m.effective_at))}</small></div><span class="arrow">›</span></button>`).join('');
}
async function render(){
  const rev=++serial;
  nav();
  el('title').textContent=pages[page][1]; el('crumb').textContent=pages[page][0];el('subtitle').textContent=pages[page][2];
  el('workspace').value=scope;el('import-shortcut').hidden=page==='import';content.setAttribute('aria-busy','true');
  if(!content.firstChild) content.innerHTML='<div class="loading" role="status">Loading workspace…</div>';
  try {
    const data=await api('overview');if(rev!==serial)return;overview=data;nav();
    if(page==='overview'||page==='profile'){
      const profile=await api('profile',page==='overview'?{limit:24}:profileArgs());
      if(rev===serial){if(page==='overview')overviewPage(data,profile);else profilePage(profile)}
    } else if(page==='context') contextPage();
    else if(page==='import') await importPage(rev);
    else if(page==='models'){
      const cat=await api('catalog');if(rev===serial)modelPage(cat);
    } else if(page==='review'){
      const rows=await api('pending',{role:reviewRole,limit:21,offset});if(rev!==serial)return;
      content.innerHTML=`<div class="toolbar"><div class="tabstrip">${['user','document'].map(role=>`<button data-role="${role}" aria-pressed="${reviewRole===role}" class="${reviewRole===role?'selected':''}">${role==='user'?'User turns':'Documents'}</button>`).join('')}</div></div><section class="panel">${rows.length?sourceItems(rows.slice(0,20),true):'<p class="empty">All caught up. No unclassified sources on this page.</p>'}${pager(rows.length>20,Math.min(rows.length,20))}</section>`;
    } else {
      const isLib=page==='library';
      const result=query?await api('search',{query,view:isLib?'facts':searchMode,limit:100}):await api(isLib?'library':'sources',{limit:20,offset});
      if(rev!==serial)return;
      const rows=query?result.results.slice(offset,offset+20):result.items;
      content.innerHTML=`<form class="toolbar" id="searchform"><div class="search-input">${icon('context')}<input id="query" aria-label="Search ${isLib?'memory keys':'source content'}" placeholder="${isLib?'Search current memories…':'Search original source text…'}" value="${safe(query)}"></div>
        ${isLib?'':`<select id="searchmode" aria-label="Retrieval mode"><option value="archive">Full text</option><option value="auto_archive" ${searchMode==='auto_archive'?'selected':''}>Automatic retrieval</option></select>`}
        <button class="secondary">Search</button>${query?'<button type="button" class="minor" data-clear>Clear</button>':''}</form>
        ${result.route?`<p class="note">${safe(result.route)} · ${safe(result.reason)}</p>`:''}
        <section class="panel">${isLib?memoryTable(rows):rows.length?sourceItems(rows):'<p class="empty">No sources matched.</p>'}${pager(query?offset+20<result.results.length:result.more,rows.length)}</section>`;
      el('searchform').onsubmit=e=>{e.preventDefault();query=el('query').value.trim();searchMode=el('searchmode')?.value||'archive';offset=0;render()};
    }
  }catch(error){if(rev===serial)content.innerHTML=`<div class="panel empty err" role="alert">${safe(error.message)}</div>`}
  finally{if(rev===serial)content.setAttribute('aria-busy','false')}
}
function menu(open) {document.body.classList.toggle('nav-open',open);el('mobile-menu').setAttribute('aria-expanded',String(open));el('menu-shade').hidden=!open}
function gotoPage(id,push=true){
  if(!Object.hasOwn(pages,id))return;
  page=id;query='';offset=0;content.replaceChildren();menu(false);
  if(push && location.hash!=='#'+id) history.pushState(null,'','#'+id);
  return render();
}
function modal(title,body){dialogSerial++;el('dialog-title').textContent=title;detail.innerHTML=body;if(!dialog.open)dialog.showModal();return dialogSerial}
function err(e){const target=detail.querySelector('.err');if(target)target.textContent=e.message;else toast(e.message)}
async function openMemory(key,at,known){
 try{
  const version=modal(key,'<p class="note">Reading evidence…</p>');
  const [m,history]=await Promise.all([api('lookup',{key,...(at?{as_of:at}:{}),...(known?{known_at:known}:{})}),api('history',{key,limit:100,...(at?{as_of:at}:{}),...(known?{known_at:known}:{})})]);if(version!==dialogSerial)return;
  detail.innerHTML=badge(m.status)+'<div class="big">'+safe(m.value??'No resolved value')+'</div>'+
    '<form id="at-form" class="inline"><label>Effective date <input type="date" id="asof" value="'+safe(at?.slice(0,10)||'')+'"></label><label>Known by <input type="date" id="known-asof" value="'+safe(known?.slice(0,10)||'')+'"></label><button class="secondary">Inspect</button></form>'+
    '<div class="sectionlabel">Revision history</div>'+history.map(v=>'<div class="timeline"><strong>'+safe(v.value??'Revoked')+'</strong> · <small>'+safe(shownDate(v.effective_at))+'</small><blockquote>'+safe(v.quote)+'</blockquote><button class="link" data-proof="'+safe(v.request_id)+'" data-start="'+v.start+'" data-stop="'+v.stop+'">'+safe(v.source)+' ↗</button></div>').join('')+
    '<p class="help">A new source with this key updates its timeline.</p><div class="actionbar">'+(m.status==='current'&&!at&&!known?'<button class="minor" id="retire-key">Retire memory</button>':'')+'<button class="secondary" id="correct">Record correction</button></div>';
  el('at-form').onsubmit=e=>{e.preventDefault();openMemory(key,endDate(el('asof').value),endDate(el('known-asof').value))};
  el('correct').onclick=()=>addForm(key);
  if(el('retire-key'))el('retire-key').onclick=()=>retireForm(key);
  showConnections(key,version,at,known);
 }catch(e){err(e)}
}
async function openSource(id,review=false,start=null,stop=null){
 const version=modal('Original source','<p class="note">Loading original…</p>');
 try{
  const m=await api('source',{request_id:id});if(version!==dialogSerial)return;if(!m)throw Error('Source no longer exists');
  const chars=Array.from(m.text);
  const marked=start==null?safe(m.text):safe(chars.slice(0,start).join(''))+'<mark>'+safe(chars.slice(start,stop).join(''))+'</mark>'+safe(chars.slice(stop).join(''));
  detail.innerHTML='<div class="sectionlabel">'+safe(m.role)+' · '+safe(shownDate(m.effective_at))+'</div><h2>'+safe(m.source)+'</h2><p class="note">'+safe(id)+'</p><div class="sourcetext" id="original">'+marked+'</div><div id="reviewbox"></div>';
  if(m.role!=='assistant'){
   if(review)reviewForm(m);
   else{el('reviewbox').innerHTML='<div class="actionbar"><button class="secondary" id="start-review">Review source</button></div>';el('start-review').onclick=()=>reviewForm(m)}
  }
 }catch(e){err(e)}
}
function addForm(key=''){
 const id=crypto.randomUUID();
 modal(key?'Record correction':'Add source','<form id="capture">'+(scope==='sample'?'<p class="note">Sample changes last for this session. Choose My workspace to keep a source.</p>':'')+'<label for="origin">Source name</label><input id="origin" required placeholder="e.g. Support call"><label for="role">Source type</label><select id="role"><option value="user">User statement</option><option value="document">Document</option><option value="assistant">Assistant output</option></select><label for="body">Original text</label><textarea id="body" required placeholder="Paste the original words…"></textarea>'+
 '<label for="effective">Effective date and time <small>(optional, local time)</small></label><input type="datetime-local" id="effective">'+
 (key?'<label for="fixed-key">Memory key</label><input id="fixed-key" readonly value="'+safe(key)+'"><label for="fixed-value">Correct value from the original text</label><input id="fixed-value" required>':'')+
 '<p class="err" role="alert"></p><div class="actionbar"><button type="submit" class="primary">Save source</button></div></form>');
 el('origin').focus();el('capture').onsubmit=async e=>{
  e.preventDefault();const button=e.submitter;button.disabled=true;
  try{const text=el('body').value,role=el('role').value;
   const result=await api('remember',{request_id:id,text,role,source:el('origin').value,...(el('effective').value?{effective_at:new Date(el('effective').value).toISOString()}:{}),...(key?{facts:[{key,value:el('fixed-value').value,quote:text}]}:{})});
   dialog.close();toast(result.keys.length?'Saved · '+result.keys.join(', '):'Source saved to archive');await render()
  }catch(error){err(error)}finally{button.disabled=false}
 };
}
async function waitJob(id,space){for(;;){await new Promise(r=>setTimeout(r,600));const j=await api('job',{id},space);if(j.status==='done')return j.result;if(j.status==='failed')throw Error(j.error)}}
function reviewForm(m){
 const box=el('reviewbox');const id=crypto.randomUUID();let selectedStart=null;
 box.innerHTML='<div class="sectionlabel">Confirm a fact</div><label for="fieldkey">Memory key</label><input id="fieldkey" placeholder="e.g. contact.channel"><label for="fielddef">Field meaning for local suggestions <small>(optional)</small></label><input id="fielddef" placeholder="e.g. Preferred contact channel of the account holder"><label for="fieldvalue">Value from the source</label><input id="fieldvalue" placeholder="Exact value"><label for="fieldquote">Supporting quote</label><textarea id="fieldquote"></textarea><div class="inline"><button class="minor" id="use-selection">Use selected passage</button><button class="minor" id="propose">Suggest with local model</button></div><div id="proposal"></div><p class="err" role="alert"></p><div class="actionbar"><button class="minor" id="dismiss-review">Dismiss source</button><button class="primary" id="confirm-review">Confirm fact</button></div>';
 el('fieldquote').value=m.text.length<=4096?m.text:'';
 el('use-selection').onmousedown=e=>e.preventDefault();
 el('fieldquote').oninput=()=>{selectedStart=null};
 el('use-selection').onclick=()=>{
  const sel=window.getSelection(),original=el('original');
  if(!sel?.rangeCount||!original.contains(sel.anchorNode)||!original.contains(sel.focusNode)||!sel.toString()){
   err(Error('Select words in the original source first.'));return;
  }
  const range=sel.getRangeAt(0),prefix=document.createRange();
  prefix.selectNodeContents(original);prefix.setEnd(range.startContainer,range.startOffset);
  selectedStart=Array.from(prefix.toString()).length;el('fieldquote').value=range.toString();
 };
 async function save(facts){await api('annotate',{request_id:id,original_id:m.request_id,facts});dialog.close();toast(facts.length?'Fact confirmed':'Source dismissed');await render()}
 el('dismiss-review').onclick=()=>save([]).catch(err);
 el('confirm-review').onclick=()=>save([{key:el('fieldkey').value.trim(),value:el('fieldvalue').value,quote:el('fieldquote').value,...(selectedStart===null?{}:{start:selectedStart})}]).catch(err);
 el('propose').onclick=async e=>{
  const button=e.currentTarget,version=dialogSerial;button.disabled=true;const space=scope;
  try{const key=el('fieldkey').value.trim();if(!key)throw Error('Enter the memory key you want the model to extract.');
   el('proposal').textContent='Running local review…';
   const {job}=await api('suggest',{original_id:m.request_id,fields:{[key]:el('fielddef').value.trim()||key.split('.').join(' ')}});
   const result=await waitJob(job,space);if(version!==dialogSerial)return;
   const first=result.facts[0];if(first){selectedStart=null;el('fieldvalue').value=first.value;el('fieldquote').value=first.quote}
   el('proposal').textContent=(first?'Proposal ready for confirmation. ':result.reason||'No supported fact found. ')+' '+result.attempts.map(a=>a.model+' · '+a.outcome+(a.reason?' ('+a.reason+')':'')).join(' → ');
  }catch(error){if(version===dialogSerial){el('proposal').textContent='';err(error)}}finally{button.disabled=false}
 };
}
function modelPage(cat){
 content.innerHTML='<div class="modelgrid">'+cat.models.map(m=>'<section class="modelcard"><div class="between"><i class="sourceicon">'+icon('models')+'</i>'+badge(m.installed?'installed':'available')+'</div><div><div class="sectionlabel">'+safe(m.task==='review'?'Source review':'Archive search')+'</div><h2>'+safe(m.name)+'</h2><small class="muted">'+safe(m.subtitle)+'</small></div><p>'+safe(m.description)+'</p><small class="key">'+Math.round(m.bytes/1e6)+' MB · '+safe(m.runtime)+' · '+safe(m.license)+'</small><div class="actionbar"><a target="_blank" rel="noreferrer" href="https://huggingface.co/'+safe(m.repo)+'">Model card ↗</a>'+(m.installed?'<a href="/packs/'+safe(m.id)+'" download>Export pack</a>':'')+(m.installed&&cat.windows_runtime_cached&&m.task==='review'?'<a href="/packs/runtime/'+safe(m.id)+'" download>Pack with Windows runtime</a>':'')+
 (m.installed&&m.task==='search'&&!m.runtime_ready?'<span class="help">Install the semantic Python extra to index sources.</span>':m.installed&&m.task==='search'?'<button class="secondary" data-index>Index sources</button>':m.installed&&m.runtime_ready?'<span class="help">Ready locally</span>':'<button class="secondary" data-install="'+safe(m.id)+'">'+(m.installed?'Set up runtime':'Install locally')+'</button>')+'</div></section>').join('')+
 '</div><section class="panel routing"><h2>Review routing</h2><p class="note">Choose which installed model reviews a source first. Every proposed value must match the original words.</p><form id="routeform" class="actionbar"><label>Preferred model <select id="preferred">'+cat.models.filter(m=>m.task==='review').map(m=>'<option value="'+m.id+'" '+(m.id===cat.routing.preferred?'selected':'')+'>'+safe(m.name)+'</option>').join('')+'</select></label><label><input type="checkbox" id="fallback" '+(cat.routing.fallback?'checked':'')+'> Try another installed model on failure</label><button class="secondary">Save routing</button></form></section><section class="panel routing"><h2>Transfer a local model</h2><p class="note">Import a catalog model from another computer. Each pack carries verified weights and can include the Windows review runtime. Workspace memories stay on their device.</p><button class="secondary" id="import-pack">Import model pack</button><input hidden type="file" id="packfile" accept=".gmpack,.zip"></section><div id="jobstatus" role="status" class="note"></div>';
 el('import-pack').onclick=()=>el('packfile').click();
 el('packfile').onchange=async e=>{
  const input=e.target, file=input.files[0];if(!file)return;
  input.disabled=true;el('jobstatus').textContent='Checking model pack…';
  try{const r=await fetch('/studio/import-pack',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});const data=await r.json();if(!r.ok)throw Error(data.error);toast('Imported '+data.installed);await render()}
  catch(error){toast(error.message);if(el('jobstatus'))el('jobstatus').textContent=error.message}
  finally{input.value='';input.disabled=false}
 };
 el('routeform').onsubmit=async e=>{e.preventDefault();try{await api('routing',{preferred:el('preferred').value,fallback:el('fallback').checked});toast('Local routing saved')}catch(error){toast(error.message)}};
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b)return;
 try{
  if(b.dataset.page)gotoPage(b.dataset.page);
  else if(b.hasAttribute('data-key'))await openMemory(b.dataset.key,b.dataset.at,b.dataset.known);
  else if(b.hasAttribute('data-proof'))await openSource(b.dataset.proof,false,+b.dataset.start,+b.dataset.stop);
  else if(b.hasAttribute('data-source'))await openSource(b.dataset.source,!!b.dataset.review);
  else if(b.dataset.role){reviewRole=b.dataset.role;offset=0;render()}
  else if(b.hasAttribute('data-clear')){query='';offset=0;render()}
  else if(b.hasAttribute('data-next')||b.hasAttribute('data-prev')){offset=Math.max(0,offset+(b.hasAttribute('data-next')?20:-20));render()}
  else if(b.dataset.install||b.hasAttribute('data-index')){
   const space=scope;b.disabled=true;el('jobstatus').textContent=b.dataset.install?'Downloading and verifying model files…':'Indexing original passages…';
   const {job}=await api(b.dataset.install?'install':'index',b.dataset.install?{model:b.dataset.install}:{});
   const result=await waitJob(job,space);toast(result.installed?'Model installed':'Indexed '+result.new_vectors+' new passages');render()
  }
 }catch(error){toast(error.message);b.disabled=false;if(el('jobstatus'))el('jobstatus').textContent=error.message}
});

el('close').onclick=()=>dialog.close();
dialog.addEventListener('close',()=>dialogSerial++);
el('mobile-menu').onclick=()=>menu(!document.body.classList.contains('nav-open'));
el('menu-shade').onclick=()=>menu(false);
el('add').onclick=()=>addForm();
el('quick-search').onclick=async()=>{await gotoPage('context');el('context-query')?.focus()};
document.addEventListener('keydown',event=>{
 if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!dialog.open){event.preventDefault();el('quick-search').click()}
 if(event.key==='Escape'&&document.body.classList.contains('nav-open')){menu(false);el('mobile-menu').focus()}
});
matchMedia('(max-width:760px)').addEventListener('change',()=>menu(false));
window.addEventListener('popstate',()=>gotoPage(location.hash.slice(1)||'overview',false));
el('workspace').onchange=()=>{
 scope=el('workspace').value;sessionStorage.setItem('gravity-workspace',scope);
 overview={};resetWorkspaceViews();gotoPage('overview');
};
(async()=>{
 try{const d=await api('overview');const saved=sessionStorage.getItem('gravity-workspace');scope=['personal','sample'].includes(saved)?saved:(d.has_personal?'personal':'sample')}catch{}
 const initial=location.hash.slice(1);await gotoPage(Object.hasOwn(pages,initial)?initial:'overview',false);
})();
