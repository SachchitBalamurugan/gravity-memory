'use strict';
const profileFilter = {date:'',known:'',subject:''};
let importTab='files', contextState={query:'',retrieval:'auto',date:'',known:'',strict:false,result:null};
let conversationRequest=null;
function resetWorkspaceViews(){
  Object.assign(profileFilter,{date:'',known:'',subject:''});
  contextState={query:'',retrieval:'auto',date:'',known:'',strict:false,result:null};
  importTab='files';conversationRequest=null;
}
function profilePriority(key){return ['work.employer','home.city','contact.channel','project.name'].includes(key)?['work.employer','home.city','contact.channel','project.name'].indexOf(key):10}
function friendlyKey(key){return ({'home.city':'Home city','work.employer':'Employer','contact.channel':'Contact channel','profile.timezone':'Timezone','profile.name':'Name','project.name':'Project'})[key]||key.replaceAll('.',' / ').replaceAll('_',' ')}
function pretty(value){return String(value).replace(/[._-]/g,' ').replace(/^./,c=>c.toUpperCase())}
function endDate(v){return v?v+'T23:59:59.999999Z':undefined}
function profileArgs(){return {limit:20,offset,as_of:endDate(profileFilter.date),known_at:endDate(profileFilter.known),subject:profileFilter.subject||undefined}}
function snapshotAttrs(at,known){return `${at?` data-at="${safe(at)}"`:''}${known?` data-known="${safe(known)}"`:''}`}
function citationButton(proof){
  return `<button class="link proof" data-proof="${safe(proof.request_id)}" data-start="${proof.start}" data-stop="${proof.stop}">${icon('sources')} ${safe(proof.source)} ↗</button>`;
}
function profilePage(profile){
  const at=endDate(profileFilter.date), known=endDate(profileFilter.known);
  content.innerHTML=`<form class="filters" id="profile-form">
    <label>Subject <input id="profile-subject" value="${safe(profileFilter.subject)}" placeholder="All subjects" list="subject-options"></label>
    <datalist id="subject-options"><option value="self"><option value="relation.partner"></datalist>
    <label>Effective on <input id="profile-at" type="date" value="${safe(profileFilter.date)}"></label>
    <label>Known by <input id="profile-known" type="date" value="${safe(profileFilter.known)}"></label>
    <button class="secondary">Apply filters</button>${at||known||profileFilter.subject?'<button class="minor" id="reset-profile" type="button">Reset</button>':''}
    <p id="profile-error" class="err" role="alert"></p></form><div class="sectionhead"><h2>Current facts</h2><span class="count">${profile.total}</span></div>
    <div class="profile-grid">${profile.groups.map(group=>`<section class="panel topic"><div class="panelhead"><h3>${safe(pretty(group.topic))}</h3><small>${group.items.length}</small></div>${group.items.map(item=>`<div class="factline"><div><span class="key">${safe(item.key)}</span><button class="fact-value" data-key="${safe(item.key)}"${snapshotAttrs(at,known)}>${safe(item.value)} <span>↗</span></button></div>${item.evidence[0]?citationButton(item.evidence[0]):''}</div>`).join('')}</section>`).join('')}</div>
    ${!profile.total?'<div class="panel empty">No current facts match this snapshot.</div>':''}${pager(profile.more,profile.groups.reduce((n,g)=>n+g.items.length,0))}`;
  el('profile-form').onsubmit=e=>{
    e.preventDefault();const subject=el('profile-subject').value.trim();
    if(subject&&subject!=='self'&&!/^relation\.[a-z][a-z0-9_]*$/.test(subject)){el('profile-error').textContent='Use self or a relation such as relation.partner, or leave the field empty.';el('profile-subject').focus();return}
    Object.assign(profileFilter,{date:el('profile-at').value,known:el('profile-known').value,subject});offset=0;render();
  };
  if(el('reset-profile'))el('reset-profile').onclick=()=>{Object.assign(profileFilter,{date:'',known:'',subject:''});offset=0;render()};
}
function contextResults(r){
  const at=endDate(contextState.date),known=endDate(contextState.known);
  const factCards=r.facts.map(f=>`<article class="context-fact"><button class="fact-value" data-key="${safe(f.key)}"${snapshotAttrs(at,known)}>${safe(f.value)} <span>↗</span></button><span class="key">${safe(f.key)}</span>${f.evidence[0]?`<blockquote>${safe(f.evidence[0].quote)}</blockquote>${citationButton(f.evidence[0])}`:''}</article>`).join('');
  const summarized=r.profile.groups.flatMap(g=>g.items);
  return `<div class="resultbar"><span><strong>${r.facts.length}</strong> current matches · <strong>${r.passages.length}</strong> source passages</span><span class="route" title="${safe(r.reason)}">${safe(pretty(r.route))}</span></div>
    <div class="context-grid"><section class="panel"><div class="panelhead"><h2>Current facts</h2><span class="count">${r.facts.length}</span></div>
    ${factCards||'<p class="empty">No matching current facts.</p>'}</section>
    <section class="panel"><div class="panelhead"><div><h2>Source passages</h2><small>Includes historical statements</small></div><span class="count">${r.passages.length}</span></div>
    ${r.passages.length?sourceItems(r.passages):`<p class="empty">${r.route==='facts_only'?'Source search is off for this request.':'No matching source passages.'}</p>`}</section></div>
    <p class="note">Retrieval used ${safe(pretty(r.route).toLowerCase())} · ${safe(r.reason)}</p>
    <details class="panel agent-profile"><summary>Included workspace profile <span class="count">${summarized.length}</span></summary>
      ${summarized.map(f=>`<button class="summary-fact" data-key="${safe(f.key)}"${snapshotAttrs(at,known)}><span class="key">${safe(f.key)}</span>${safe(f.value)}</button>`).join('')||'<p class="empty">No current profile facts.</p>'}</details>
    <details class="panel raw-response"><summary>API response</summary><button type="button" class="secondary" id="copy-context">Copy JSON</button><pre id="context-json"></pre></details>`;
}
function bindContextPayload(result){
  const pre=el('context-json'),button=el('copy-context');if(!pre)return;
  const json=JSON.stringify(result,null,2);pre.textContent=json;
  button.onclick=async()=>{try{await navigator.clipboard.writeText(json);toast('Context JSON copied')}catch{const range=document.createRange();range.selectNodeContents(pre);const selected=window.getSelection();selected.removeAllRanges();selected.addRange(range);toast('JSON selected. Use your copy shortcut.')}};
}
function contextPage(){
  content.innerHTML=`<form id="context-form" class="panel context-form">
    <label class="formlabel" for="context-query">Context query</label>
    <div class="queryline"><div class="search-input">${icon('context')}<input id="context-query" maxlength="2048" required placeholder="Find facts and supporting passages…" value="${safe(contextState.query)}"></div><button class="primary" id="run-context">Run search ${icon('arrow')}</button></div>
    <div class="query-settings"><label>Retrieval <select id="context-route"><option value="auto">Automatic</option><option value="full_text">Full text</option><option value="facts_only">Facts only</option></select></label>
    <label>Effective on <input id="context-date" type="date" value="${safe(contextState.date)}"></label></div>
    <details class="context-options"${contextState.known||contextState.strict?' open':''}><summary>Advanced filters</summary><div class="query-settings"><label>Known by <input id="context-known" type="date" value="${safe(contextState.known)}"></label><label><input id="context-strict" type="checkbox" ${contextState.strict?'checked':''}> Hold facts with newer unreviewed user turns</label></div></details></form>
    <div id="context-results" aria-live="polite">${contextState.result?contextResults(contextState.result):'<div class="context-empty"><span class="empty-symbol">'+icon('context')+'</span><h2>Search workspace context</h2><p>Enter a question, memory key, or phrase from a source.</p></div>'}</div>`;
  el('context-route').value=contextState.retrieval;
  if(contextState.result)bindContextPayload(contextState.result);
  el('context-form').onsubmit=async event=>{
    event.preventDefault();const revision=serial,state=contextState,button=el('run-context'),results=el('context-results');
    Object.assign(state,{query:el('context-query').value.trim(),retrieval:el('context-route').value,date:el('context-date').value,known:el('context-known').value,strict:el('context-strict').checked,result:null});
    if(!state.query)return;
    button.disabled=true;results.innerHTML='<p class="loading">Searching this workspace…</p>';
    try{
      const result=await api('context',{query:state.query,retrieval:state.retrieval,as_of:endDate(state.date),known_at:endDate(state.known),require_review:state.strict,limit:6,profile_limit:6});
      if(serial!==revision)return;
      state.result=result;results.innerHTML=contextResults(result);bindContextPayload(result);
    }catch(error){if(serial===revision)results.innerHTML=`<p class="err empty" role="alert">${safe(error.message)}</p>`}
    finally{button.disabled=false}
  };
}
async function showConnections(key,version,at,known){
  if(at||known)return;
  try{
    const graph=await api('connections',{key,limit:12});
    if(version!==dialogSerial||!dialog.open)return;
    const related=graph.nodes.filter(n=>n.kind==='memory'&&n.key!==key);
    const sources=graph.nodes.filter(n=>n.kind==='source');
    if(!related.length&&!sources.length)return;
    const linked=related.map(n=>`<button class="chip" data-key="${safe(n.key)}">${icon('link')} ${safe(n.key)}</button>`).join('');
    const refs=sources.map(n=>`<button class="chip" data-source="${safe(n.request_id)}">${icon('sources')} ${safe(n.label)}</button>`).join('');
    detail.insertAdjacentHTML('beforeend',`<section class="connections"><h3>Evidence connections</h3>${linked?`<p class="help">Keys sharing a source</p><div class="chips">${linked}</div>`:''}${refs?`<p class="help">Supporting sources</p><div class="chips">${refs}</div>`:''}${graph.truncated?'<p class="note">Showing the first connections.</p>':''}</section>`);
  }catch(error){if(version===dialogSerial&&dialog.open)detail.insertAdjacentHTML('beforeend',`<p class="note">Connections unavailable · ${safe(error.message)}</p>`)}
}
function retireForm(key){
  const id=crypto.randomUUID();
  modal('Retire memory','<p>This removes <strong>'+safe(key)+'</strong> from the current profile and records your reason in its revision history. Sources and previous values remain readable.</p><form id="retire-form"><label for="retire-reason">Reason for retiring this memory</label><textarea required maxlength="2048" id="retire-reason"></textarea><p class="err" role="alert"></p><div class="actionbar"><button type="button" id="cancel-retire" class="secondary">Cancel</button><button class="primary">Confirm retirement</button></div></form>');
  el('cancel-retire').onclick=()=>openMemory(key);
  el('retire-reason').focus();
  el('retire-form').onsubmit=async e=>{
    e.preventDefault();const button=e.submitter;button.disabled=true;
    try{await api('retire',{request_id:id,key,reason:el('retire-reason').value.trim()});dialog.close();toast('Memory retired');await render()}
    catch(error){err(error)}finally{button.disabled=false}
  };
}
const importTypes={files:'Documents',conversation:'Conversation',provider:'Provider export',github:'GitHub'};

function importFrame(form){
  return `<div class="import-layout"><section class="panel import-box"><div class="import-tabs">${Object.entries(importTypes).map(([id,label])=>`<button type="button" data-import-tab="${id}" aria-pressed="${importTab===id}" class="${importTab===id?'selected':''}">${label}</button>`).join('')}</div>${form}</section></div>`;
}
function importFooter(action){return `<p class="err" id="import-error" role="alert"></p><div class="actionbar"><button class="primary" type="submit">${action}</button></div>`}
function documentForm(){
  return `<form id="ingest-form" class="ingest-form"><h2>Import documents</h2><p class="hint">Select UTF-8 text or Markdown files. Each document keeps its filename and original text.</p>
    <label class="file-pick" for="documents"><span class="file-icon">${icon('sources')}</span><strong>Choose documents</strong><span>TXT, MD, MARKDOWN</span>
    <input id="documents" required type="file" multiple accept=".txt,.md,.markdown"></label><div id="file-summary" aria-live="polite"></div>${importFooter('Import documents')}</form>`;
}
function turnFields(role,body,i,date=''){
  return `<fieldset class="turn"><legend>Turn ${i+1}</legend><div class="turnhead"><label>Speaker <select data-turn-role><option value="user">User</option><option value="assistant" ${role==='assistant'?'selected':''}>Assistant</option></select></label><button type="button" class="minor" data-remove-turn>Remove</button></div>
    <label>Message <textarea data-turn-body required rows="2">${safe(body)}</textarea></label><details class="turn-time"${date?' open':''}><summary>Effective time (optional)</summary><label>Timestamp with timezone<input data-turn-time placeholder="2026-04-10T15:00:00Z" value="${safe(date)}"></label></details></fieldset>`;
}
function conversationForm(){
  return `<form id="ingest-form" class="ingest-form"><h2>Capture conversation</h2><p class="hint">Preserve turn order and speaker attribution. User statements can contribute profile facts.</p><label for="thread-name">Conversation name</label><input id="thread-name" required maxlength="512" placeholder="e.g. Account handoff">
    <div class="transcript-load"><button class="secondary" type="button" id="choose-transcript">Load JSON transcript</button><input type="file" hidden id="transcript-file" accept=".json,application/json"></div><div id="turns">${turnFields('user','',0)}</div><button type="button" class="secondary" id="add-turn">＋ Add turn</button>${importFooter('Save conversation')}</form>`;
}
function providerForm(){
  return `<form id="ingest-form" class="ingest-form"><h2>Import provider export</h2><p class="hint">Import one provider scope as attributed sources. Add other scopes in their own workspaces.</p>
    <label for="provider">Provider</label><select id="provider"><option value="mem0">Mem0</option><option value="supermemory">Supermemory</option></select><label for="provider-scope">Source scope</label><input id="provider-scope" required maxlength="256" placeholder="User ID or container tag"><label for="provider-file">JSON export</label><input id="provider-file" required type="file" accept=".json,application/json">${importFooter('Import records')}</form>`;
}
function githubForm(){
  return `<form id="ingest-form" class="ingest-form"><h2>Connect a GitHub file</h2><p class="hint">Track a text or Markdown file in a public repository. Sync it to capture the latest revision.</p>
    <label for="repository">Repository</label><input id="repository" required placeholder="owner/repository"><label for="repo-path">File path</label><input id="repo-path" required placeholder="docs/guide.md"><label for="repo-ref">Branch or tag <small>(optional)</small></label><input id="repo-ref" placeholder="Default branch">${importFooter('Connect file')}</form><section class="connected"><h3>Connected files</h3><div id="connected-files" aria-live="polite">Loading…</div></section>`;
}
async function refreshGithub(revision){
 const list=await api('github_list');
 if(serial!==revision||!el('connected-files'))return;
 el('connected-files').innerHTML=list.items.length?list.items.map(c=>`<div class="connection-row"><div><strong>${safe(c.repository)}</strong><p class="key">${safe(c.path)}</p><small>${safe(c.ref||'Default branch')} · Last synced ${safe(shownDate(c.last_synced_at))}</small></div><div class="inline"><button class="secondary" data-sync="${safe(c.id)}">Sync</button><button class="minor" data-disconnect="${safe(c.id)}">Disconnect</button></div></div>`).join(''):'<p class="note">No connected files in this workspace.</p>';
}
async function importPage(revision){
 const forms={files:documentForm,conversation:conversationForm,provider:providerForm,github:githubForm};
 content.innerHTML=importFrame(forms[importTab]());
 if(scope==='sample')el('ingest-form').insertAdjacentHTML('afterbegin','<p class="sample-note">This workspace is temporary. Choose My workspace to keep imported data.</p>');
 content.querySelectorAll('[data-import-tab]').forEach(b=>b.onclick=()=>{if(b.dataset.importTab!==importTab){importTab=b.dataset.importTab;conversationRequest=null;render()}});
 if(importTab==='files')el('documents').onchange=()=>{el('file-summary').textContent=Array.from(el('documents').files,f=>f.name).join(' · ')};
 if(importTab==='conversation'){
   conversationRequest=crypto.randomUUID();
   const turns=el('turns');
   el('choose-transcript').onclick=()=>el('transcript-file').click();
   el('transcript-file').onchange=async e=>{
     const input=e.target,file=input.files[0];if(!file)return;
     try{
       if(file.size>14000000)throw Error('Choose a transcript under 14 MB.');
       const data=JSON.parse(await file.text()),messages=Array.isArray(data)?data:data?.messages;
       if(!Array.isArray(messages)||!messages.length||messages.length>200||messages.some(m=>!m||!['user','assistant'].includes(m.role)||typeof m.content!=='string'||!m.content.trim()||m.effective_at!=null&&typeof m.effective_at!=='string'))throw Error('Use 1–200 messages with role user or assistant, content, and an optional timestamp.');
       if(serial!==revision)return;
       turns.innerHTML=messages.map((m,i)=>turnFields(m.role,m.content,i,m.effective_at||'')).join('');
       if(!el('thread-name').value)el('thread-name').value=file.name;
       el('import-error').textContent='';conversationRequest=crypto.randomUUID();
     }catch(error){if(serial===revision)el('import-error').textContent=error instanceof SyntaxError?'Choose a valid JSON transcript.':error.message}
     finally{input.value=''}
   };
   el('add-turn').onclick=()=>{const index=turns.children.length;if(index>=200)return;turns.insertAdjacentHTML('beforeend',turnFields(index%2?'assistant':'user','',index));turns.lastElementChild.querySelector('textarea').focus()};
   turns.onclick=e=>{const b=e.target.closest('[data-remove-turn]');if(!b)return;const group=b.closest('fieldset');if(turns.children.length===1){group.querySelector('textarea').value='';return}group.remove();Array.from(turns.children).forEach((item,i)=>item.querySelector('legend').textContent=`Turn ${i+1}`)};
 }
 const form=el('ingest-form');
 form.onsubmit=async event=>{
   event.preventDefault();
   const button=event.submitter,error=el('import-error'),space=scope;button.disabled=true;error.textContent='';
   try{
     let result;
     if(importTab==='files'){
       const files=Array.from(el('documents').files);if(!files.length||files.length>100)throw Error('Choose 1–100 files.');
       if(files.reduce((n,f)=>n+f.size,0)>14000000)throw Error('Split this import into batches under 14 MB.');
       const input=await Promise.all(files.map(async file=>({name:file.name,text:await file.text()})));
       result=await api('import_documents',{files:input},space);
     }else if(importTab==='conversation'){
       const messages=Array.from(form.querySelectorAll('fieldset'),turn=>({role:turn.querySelector('select').value,content:turn.querySelector('textarea').value,effective_at:turn.querySelector('[data-turn-time]').value.trim()||undefined}));
       result=await api('capture_conversation',{source:el('thread-name').value.trim(),request_id:conversationRequest,messages},space);
     }else if(importTab==='provider'){
       const file=el('provider-file').files[0];if(file.size>14000000)throw Error('Choose an export under 14 MB.');
       const provider=el('provider').value,sourceScope=el('provider-scope').value.trim();
       let payload;try{payload=JSON.parse(await file.text())}catch{throw Error('The file must contain a valid JSON export.')}
       result=await api('import_provider',{provider,scope:sourceScope,payload},space);
     }else{
       const repository=el('repository').value.trim();
       if(!/^[a-z\d.-]+\/[\w.-]+$/i.test(repository))throw Error('Enter a repository as owner/repository.');
       const git=await api('github_connect',{repository,path:el('repo-path').value.trim(),ref:el('repo-ref').value.trim()||undefined},space);
       result={imported:git.imported?1:0,replayed:git.imported?0:1};
     }
     if(serial!==revision)return;
     const message=`${result.imported} imported · ${result.replayed} already present${result.outside_scope?` · ${result.outside_scope} outside scope`:''}`;
     toast(message);
     await render();
     if(page==='import'&&scope===space)el('ingest-form')?.insertAdjacentHTML('beforeend',`<div class="receipt"><strong>${safe(message)}</strong> ${buttonLink('sources','View sources')}</div>`);
   }catch(e){error.textContent=e.message}finally{button.disabled=false}
 };
 if(importTab==='github'){
   try{await refreshGithub(revision)}catch(error){if(serial===revision)el('connected-files').textContent=error.message}
   if(serial!==revision)return;
   el('connected-files').onclick=async e=>{
     const button=e.target.closest('button');if(!button)return;button.disabled=true;
     try{
       if(button.dataset.sync){const r=await api('github_sync',{id:button.dataset.sync});toast(r.imported?'New revision imported':'Already up to date')}
       else if(button.dataset.disconnect){await api('github_disconnect',{id:button.dataset.disconnect});toast('File disconnected')}
       await refreshGithub(revision);
     }catch(error){toast(error.message)}finally{button.disabled=false}
   };
 }
}
