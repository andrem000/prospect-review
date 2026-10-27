'use strict';
(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const token = $('meta[name="session-token"]').content;
  const content = $('#content');
  const S = {view:'queue', state:null, list:null, selected:new Set(), detail:null, dirty:false, offset:0, limit:25, filters:{q:'',state:'',trade:'',review:'all',website:'',queue:'candidates',sort:'score'}, importing:{step:1,filename:'',content:'',adapter:'canonical',preview:null,rights:{},notice:''}};
  let detailSerial=0, noticeGeneration=0;
  let requestSerial = 0, fieldSerial = 0, searchTimer, pendingNavigation;
  const el = (tag, cls, text) => { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=String(text); return n; };
  const append = (parent, ...nodes) => { nodes.flat().filter(Boolean).forEach(n=>parent.append(n)); return parent; };
  const button = (text, fn, cls='') => { const b=el('button',cls,text); b.type='button'; b.addEventListener('click',fn); return b; };
  const human = v => String(v??'unknown').replaceAll('_',' ');
  const listValue = v => Array.isArray(v)?v.join(' · '):String(v??'');
  const dateText = v => v ? String(v).replace('T',' ').replace(/\.\d+/, '').replace('+00:00',' UTC') : 'Not recorded';
  function link(url, label=url) { try { const u=new URL(url); if(!['http:','https:'].includes(u.protocol))return el('span','',label); const a=el('a','',label); a.href=u.href; a.target='_blank'; a.rel='noopener noreferrer'; return a; } catch {return el('span','',label||'Not supplied');} }
  function notice(message, error=false) { const n=$('#notice'); n.textContent=message; n.className=error?'error':''; n.hidden=false;return ++noticeGeneration; }
  function fail(error) { return notice(error.message || 'Something went wrong. Please try again.',true); }
  async function api(path, body, binary=false) { const options={headers:{'X-Prospect-Token':token}}; if(body!==undefined){options.method='POST'; options.headers['Content-Type']='application/json'; options.body=JSON.stringify(body);} const response=await fetch(path,options); if(!response.ok){let message=`Request failed (${response.status}).`; try{message=(await response.json()).error||message;}catch{} const error=new Error(message);error.status=response.status;throw error;} return binary?response.blob():response.json(); }
  async function busy(b, work, label='Working…') { if(b.disabled)return; const original=b.textContent; b.disabled=true; b.textContent=label; try {await work();} catch(e){fail(e);} finally{b.disabled=false;b.textContent=original;} }
  function field(label, node, hint) { const wrap=el('div','field'),caption=el('label','',label);node.id=node.id||`field-${++fieldSerial}`;caption.htmlFor=node.id;append(wrap,caption,node);if(hint){const help=el('span','hint',hint);help.id=`${node.id}-hint`;node.setAttribute('aria-describedby',help.id);append(wrap,help);}return wrap; }
  function input(type, value='', required=false) {const n=el('input');n.type=type;n.value=value??'';n.required=required;return n;}
  function select(options, value) {const n=el('select'); options.forEach(o=>{const [v,t]=Array.isArray(o)?o:[o,human(o)];const option=el('option','',t);option.value=v;n.append(option);});n.value=value??'';return n;}
  function check(text, checked=false) {const label=el('label','check'),n=input('checkbox');n.checked=checked;append(label,n,el('span','',text));return {label,input:n};}
  function heading(title, subtitle, actions) {return append(el('div','page-heading'),append(el('div'),el('p','eyebrow','PROSPECT REVIEW'),el('h1','',title),el('p','subtitle',subtitle)),actions);}
  function panel(title, body, extra) {const p=el('section','panel');if(title)append(p,append(el('div','panel-heading'),el('h2','',title),extra));append(p,append(el('div','panel-body'),body));return p;}
  function jsonDetails(title,value) {const d=el('details');append(d,el('summary','',title),el('pre','',JSON.stringify(value,null,2)));return d;}
  function guard(fn) { if(S.investigation && !S.advancedDirty){
    if(S.reviewSaving || S.navigating){notice('Your work is saving. Please wait.');return;}
    S.navigating=true;
    const locked=Array.from(document.querySelectorAll('.investigation input,.investigation select,.investigation textarea,.investigation button')).filter(node=>!node.disabled);
    locked.forEach(node=>{node.disabled=true;});
    return persistDraft().then(()=>{resolveDeskNotice('Draft saved locally.');resolveResearchNotice('Draft saved locally.');return fn();}).catch(showDeskError).finally(()=>{S.navigating=false;locked.forEach(node=>{node.disabled=false;});});
    return;
  } if(S.reviewSaving){notice('Your review is saving. Please wait before changing views.');return;}if(!S.dirty){fn();return;} pendingNavigation=fn;$('#confirm-title').textContent='Keep your unsaved work?';$('#confirm-dialog p').textContent='This review has unsaved edits. You can return to it or discard the changes.';$('#discard-editing').textContent='Discard changes'; $('#confirm-dialog').showModal(); }
  $('#keep-editing').addEventListener('click',()=>{$('#confirm-dialog').close();pendingNavigation=null;});
  $('#discard-editing').addEventListener('click',()=>{$('#confirm-dialog').close();S.dirty=false;S.advancedDirty=false;resolveResearchNotice('Advanced review edits discarded.');const fn=pendingNavigation;pendingNavigation=null;if(fn)fn();});
  window.addEventListener('beforeunload',e=>{if(S.dirty){e.preventDefault();e.returnValue='';}});
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>guard(()=>navigate(b.dataset.view))));
  async function state() {S.state=await api('/api/state');$('#version').textContent=`Version ${S.state.version}`;$('#workspace-name').textContent=S.state.workspace||'Local workspace';}
  function navigate(view) { S.selectedChange=null; S.returnToChanges=false; S.returnToDrafts=false; S.view=view;detailSerial++;S.detail=null;S.investigation=null;S.dirty=false;document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);if(b.dataset.view===view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});render(); }
  function render() {content.replaceChildren(); if(!S.state)return; if(S.view==='queue')renderQueue();if(S.view==='unfinished')renderUnfinished();if(S.view==='import')renderImport();if(S.view==='exports')renderExports();if(S.view==='settings')renderSettings();if(S.view==='changes')renderChanges();}
  function stats() {const box=el('div','stats');[['total','Businesses'],['candidates','Candidates'],['reviewed','Reviewed'],['qualified','Qualified'],['excluded','Excluded']].forEach(([key,label])=>append(box,append(el('div',`stat ${key==='candidates'?'featured':''}`),el('strong','',S.state.counts[key]??0),el('span','',label))));return box;}
  const draftsState={q:'',offset:0,limit:25,generation:0};
  function renderUnfinished() {
    append(content,heading('Unfinished work','Resume saved question edits. Opening work does not change a decision.'));
    const search=input('search',draftsState.q);search.placeholder='Search business name, city or state';
    const results=el('div','unfinished-results');results.setAttribute('aria-live','polite');
    search.addEventListener('input',()=>{draftsState.q=search.value;draftsState.offset=0;clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadDrafts(results),250);});
    append(content,panel('',field('Search unfinished work',search)),results);
    loadDrafts(results);
  }
  async function loadDrafts(target) {
    const generation=++draftsState.generation;
    target.replaceChildren(el('p','small','Loading unfinished work…'));
    try {
      const query=new URLSearchParams({q:draftsState.q,offset:draftsState.offset,limit:draftsState.limit});
      const data=await api('/api/drafts?'+query);
      if(generation!==draftsState.generation||S.view!=='unfinished')return;
      if(data.total && draftsState.offset>=data.total){draftsState.offset=Math.floor((data.total-1)/draftsState.limit)*draftsState.limit;return loadDrafts(target);}
      if(!data.total)draftsState.offset=0;
      target.replaceChildren(el('p','worklist-total',`${data.total} matching unfinished ${data.total===1?'business':'businesses'} · saved question edits only`));
      const items=data.items||[];
      const count=test=>items.filter(test).length;
      append(target,el('p','small',`On this page: ${items.length} drafts · ${count(i=>i.review_changed)} saved-review context changed · ${count(i=>i.review_context_unknown)} unknown review context · ${count(i=>i.source_changed)} source changed · ${count(i=>!i.can_review)} read-only`));
      if(!items.length)append(target,panel('',el('p','callout','No unfinished question edits match this search. Navigation-only drafts are not included.')));
      for(const item of items){
        const body=append(el('div','unfinished-card'),el('p','small',[item.city,item.state].filter(Boolean).join(', ')),el('p','',`Unfinished: ${(item.questions||[]).map(q=>questionLabels[q]||human(q)).join(' · ')}`));
        const tags=el('div','tags');
        if(item.review_context_unknown)append(tags,el('span','pill warning','Review context unknown'));
        else if(item.review_changed)append(tags,el('span','pill warning','Saved-review context changed'));
        if(item.source_changed)append(tags,el('span','pill warning','Source evidence changed'));
        if(!item.can_review)append(tags,el('span','pill danger','Contact exclusion · read-only'));
        if(!item.review_changed&&!item.source_changed)append(tags,el('span','pill muted','Draft saved locally'));
        append(body,tags,button(item.can_review?'Resume unfinished work':'Inspect unfinished work',()=>guard(()=>resumeDraft(item)),'primary'));
        append(target,panel(item.name,body));
      }
      const previous=button('← Previous',()=>{draftsState.offset=Math.max(0,draftsState.offset-draftsState.limit);loadDrafts(target);});
      const next=button('Next →',()=>{draftsState.offset+=draftsState.limit;loadDrafts(target);});
      previous.disabled=draftsState.offset===0;next.disabled=draftsState.offset+draftsState.limit>=data.total;
      append(target,append(el('div','table-footer'),el('span','small',items.length?`${draftsState.offset+1}–${draftsState.offset+items.length} of ${data.total}`:'0 results'),append(el('div','actions'),previous,next)));
    }catch(error){if(generation===draftsState.generation&&S.view==='unfinished')target.replaceChildren(el('p','inline-error',error.message),button('Retry loading unfinished work',()=>loadDrafts(target)));}
  }
  async function resumeDraft(item) {
    S.view='queue';S.returnToDrafts=true;S.returnToChanges=false;
    await openDetail(item.id,(item.questions||[]).find(q=>questionOrder.includes(q)));
  }
  function queueFilterSummary(total) {
    const filters=[human(S.filters.queue)];
    for(const key of ['q','state','trade','website'])if(S.filters[key])filters.push(key==='q'?'“'+S.filters[key]+'”':human(S.filters[key]));
    if(S.filters.review!=='all')filters.push(human(S.filters.review));
    return filters.join(' · ')+(total===undefined?'':' · '+total+' businesses');
  }

  function renderQueue() {
    const guided=Boolean(S.detail && S.investigation?.mode==='guided');
    append(content,heading(guided?'Guided review':S.detail?'Investigation desk':'Review queue',guided?'One business. Four questions. Save only the evidence you checked.':'Turn listing signals into careful, evidence-backed decisions.',guided?null:append(el('div','actions'),button('Import data',()=>guard(()=>navigate('import')),'primary'))));
    if(!guided)append(content,stats());
    if(!S.state.counts.total){
      append(content,panel('',append(el('div','empty'),el('div','empty-icon','↥'),el('h2','','A clear workspace. A careful start.'),el('p','','Import a permitted dataset to begin reviewing businesses, or explore the workflow with fictional examples.'),append(el('div','actions'),button('Import a file',()=>navigate('import'),'primary'),button('Try fictional demo',e=>busy(e.currentTarget,async()=>{await api('/api/demo',{});await state();notice('Fictional examples loaded. No businesses have been researched or contacted.');render();},'Loading demo…'))),el('p','small','Local workspace · No accounts · No data uploads'))));return;
    }
    const layout=el('div','queue-layout'+(S.detail?' with-detail':'')+(guided?' guided-layout':''));
    const queue=el('section','panel queue-panel');
    append(content,layout);append(layout,queue);
    let filterContainer=queue;
    if(S.detail){
      const summary=el('p','queue-summary',queueFilterSummary());summary.dataset.queueSummary='';summary.setAttribute('aria-live','polite');
      const details=el('details','queue-filters');details.open=Boolean(S.queueFiltersOpen);
      details.addEventListener('toggle',()=>{S.queueFiltersOpen=details.open;});
      append(details,el('summary','','Queue filters'));
      append(queue,append(el('div','desk-queue-heading'),el('h2','','Business queue'),summary),details);
      filterContainer=details;
    }
    const filters=el('div','filters');
    const q=input('search',S.filters.q);q.placeholder='Search name, city or address';
    q.addEventListener('input',()=>{S.filters.q=q.value;clearTimeout(searchTimer);searchTimer=setTimeout(()=>{S.offset=0;loadList(queue);},250);});
    append(filters,field('Search businesses',q));
    for(const [key,label,values] of [['queue','Queue',[['candidates','Candidates'],['all','All businesses'],['qualified','Qualified'],['excluded','Excluded audit']]],['state','State',[]],['trade','Trade',[]]]){
      const node=select(key==='queue'?values:[['','All '+label.toLowerCase()+'s']],S.filters[key]);node.dataset.filter=key;
      node.addEventListener('change',()=>{S.filters[key]=node.value;S.offset=0;loadList(queue);});append(filters,field(label,node));
    }
    const secondary=el('div','filter-secondary');
    for(const [key,label,values] of [['review','Review',[['all','Any review state'],['unreviewed','Unreviewed'],['reviewed','Reviewed']]],['website','Website signal',[['','Any website signal']]],['sort','Sort',[['score','Highest priority'],['name','Business name'],['recent','Recent evidence']]]]){
      const node=select(values,S.filters[key]);node.dataset.filter=key;
      node.addEventListener('change',()=>{S.filters[key]=node.value;S.offset=0;loadList(queue);});append(secondary,field(label,node));
    }
    append(secondary,el('span','small','Priority is a heuristic, not verified absence.'));
    append(filterContainer,filters,secondary);append(queue,el('div','queue-results'));
    if(S.detail)append(layout,renderInvestigation(S.detail));
    loadList(queue);
  }
  async function loadList(queue) {const serial=++requestSerial;const results=$('.queue-results',queue);if(!results)return;if(!results.children.length)results.append(el('p','loading','Loading businesses…'));results.setAttribute('aria-busy','true');const params=new URLSearchParams({...S.filters,offset:String(S.offset),limit:String(S.limit)});try{const data=await api(`/api/businesses?${params}`);if(serial!==requestSerial)return;S.list=data;const queueSummary=$('[data-queue-summary]',queue);if(queueSummary)queueSummary.textContent=queueFilterSummary(data.total);for(const [key,facet] of [['state','states'],['trade','trades'],['website','websites']]){const n=$(`[data-filter="${key}"]`,queue);if(!n)continue;const options=[['',key==='website'?'Any website signal':`All ${key}s`],...(data.facets?.[facet]||[])];if(S.filters[key]&&!options.some(o=>(Array.isArray(o)?o[0]:o)===S.filters[key]))options.push(S.filters[key]);const replacement=select(options,S.filters[key]);n.replaceChildren(...Array.from(replacement.children));n.value=S.filters[key];}renderRows(results,data);}catch(e){if(serial!==requestSerial)return;results.replaceChildren(append(el('div','empty'),el('h3','','Could not load the queue'),el('p','',e.message),button('Try again',()=>loadList(queue))));}finally{if(serial===requestSerial)results.removeAttribute('aria-busy');}}
  function renderRows(target,data) {target.replaceChildren(); const bar=el('div','selection-bar');const count=el('span','small',`${S.selected.size} selected`);append(bar,count,button('Select all filtered',()=>{data.matching_ids.forEach(id=>S.selected.add(id));renderRows(target,data);}),button('Clear',()=>{S.selected.clear();renderRows(target,data);}),button('Export selected',()=>exportPrompt(Array.from(S.selected)),'primary'));bar.lastChild.disabled=!S.selected.size;
    if(S.detail){
      const tools=el('details','queue-tools');tools.open=Boolean(S.queueToolsOpen);
      target.classList.toggle('selection-open',tools.open);
      tools.addEventListener('toggle',()=>{S.queueToolsOpen=tools.open;target.classList.toggle('selection-open',tools.open);});
      append(tools,el('summary','','Select & export'+(S.selected.size?' · '+S.selected.size+' selected':'')),bar,button('Export all filtered',()=>exportPrompt(data.matching_ids),'export-filtered'));
      append(target,tools);
    }else append(target,bar);if(S.filters.queue==='excluded')append(target,el('p','callout warning','Excluded audit: these businesses are held or excluded from the candidate queue. Export does not clear contact restrictions.'));
    if(!data.records.length){append(target,append(el('div','empty'),el('h2','','No matching businesses'),el('p','','Try another search or broaden the queue filters.'),button('Reset filters',()=>guard(()=>{S.filters={q:'',state:'',trade:'',review:'all',website:'',queue:'candidates',sort:'score'};S.offset=0;render();}))));return;}
    const table=el('table'),head=el('thead'),hr=el('tr');const all=input('checkbox');all.setAttribute('aria-label','Select this page');all.checked=data.records.every(r=>S.selected.has(r.id));all.addEventListener('change',()=>{data.records.forEach(r=>all.checked?S.selected.add(r.id):S.selected.delete(r.id));renderRows(target,data);});append(hr,append(el('th'),all),el('th','','Business'),el('th','','Priority'),el('th','','Review'));append(head,hr);append(table,head);const body=el('tbody');for(const r of data.records){const tr=el('tr',S.detail?.id===r.id?'selected':'');const c=input('checkbox');c.setAttribute('aria-label',`Select ${r.name}`);c.checked=S.selected.has(r.id);c.addEventListener('change',()=>{c.checked?S.selected.add(r.id):S.selected.delete(r.id);renderRows(target,data);});const name=append(el('td'),button(r.name,()=>guard(()=>openDetail(r.id)),'record-name'),el('small','',[r.city,r.state].filter(Boolean).join(', ')),el('small','',listValue(r.trades)));const status=r.qualified?'Qualified':human(r.verification_status);append(tr,append(el('td'),c),name,append(el('td'),el('span','score',r.score),el('small','',human(r.website_signal))),append(el('td'),el('span',`pill ${r.excluded_reasons?.length?'danger':r.review_history_count?'':'muted'}`,status),el('small','',human(r.queue_status))));append(body,tr);}append(table,body);append(target,append(el('div','table-wrap'),table));const footer=el('div','table-footer');const previous=button('← Previous',()=>{S.offset=Math.max(0,S.offset-S.limit);loadList(target.closest('.queue-panel'));});previous.disabled=S.offset===0;const next=button('Next →',()=>{S.offset+=S.limit;loadList(target.closest('.queue-panel'));});next.disabled=S.offset+S.limit>=data.total;append(footer,el('span','',`${S.offset+1}–${Math.min(S.offset+S.limit,data.total)} of ${data.total} results`),append(el('div','actions'),previous,next),S.detail?null:button('Export all filtered',()=>exportPrompt(data.matching_ids)));append(target,footer);
  }
  async function deskSnapshot(id) {
    const investigation=await api(`/api/businesses/${id}/investigation`);
    if(!investigation.business||investigation.business.id!==id)throw new Error('The investigation snapshot is incomplete. Reload the application before reviewing.');
    return investigation;
  }
  async function openDetail(id,question,reasonId) {
    const serial=++detailSerial;
    try {
      const investigation=await deskSnapshot(id);
      if(serial!==detailSerial || S.view!=='queue')return;
      if(reasonId)S.selectedChange={business_id:id,reason_id:reasonId};
      else if(S.selectedChange?.business_id!==id || !S.returnToChanges)S.selectedChange=null;
      S.detail=investigation.business;
      S.nextCandidates=null;
      setupInvestigation(investigation);
      if(questionOrder.includes(question))S.investigation.step=questionOrder.indexOf(question);
      render();
      focusDesk();
    } catch(error) { fail(error); }
  }
  function definition(items) {const dl=el('dl','key-values');items.forEach(([label,value])=>append(dl,append(el('div'),el('dt','',label),append(el('dd'),value instanceof Node?value:document.createTextNode(value||'Not recorded')))));return dl;}
  function renderDetail(b) {const p=el('section','panel detail-panel');p.setAttribute('aria-label',`Review ${b.name}`);const top=el('div','detail-section');append(top,button('← Back to queue',()=>guard(()=>{S.detail=null;render();}),'mobile-back'),append(el('div','actions'),el('span',`pill ${b.qualified?'':'muted'}`,b.qualified?'Qualified':human(b.queue_status)),button('Close',()=>guard(()=>{S.detail=null;render();}))),el('h2','detail-title section-gap',b.name),el('p','detail-meta',[b.address,b.city,b.state,b.postcode].filter(Boolean).join(', ')),append(el('div','tags'),...(b.trades||[]).map(x=>el('span','pill muted',human(x)))));append(p,top);
    const machine=el('div','detail-section');append(machine,el('h3','','Machine signals'),definition([['Priority score',String(b.score)],['Website signal',human(b.website_signal)],['Activity',human(b.activity_assessment?.basis||b.activity)],['Latest evidence',dateText(b.latest_evidence_observed_at)]]),el('p','small','Signals guide review. Missing listing fields do not verify website absence.'));if(b.excluded_reasons?.length)append(machine,el('p','callout warning',`Excluded: ${b.excluded_reasons.map(human).join(' · ')}`));if(b.review_flags?.length)append(machine,append(el('div','tags'),...b.review_flags.map(x=>el('span','pill warning',human(x)))));append(machine,jsonDetails('Score and assessment details',{score_breakdown:b.score_breakdown,website:b.website_assessment,activity:b.activity_assessment,scope:b.scope_assessment}));append(p,machine);
    const evidence=el('div','detail-section');append(evidence,el('h3','','Source evidence'));for(const [title,values] of [['Websites',b.websites],['Social profiles',b.socials],['Phones',b.phones]]){if(values?.length){append(evidence,el('p','small',title));const ul=el('ul');values.forEach(v=>append(ul,append(el('li'),title==='Phones'?el('span','',v):link(v))));append(evidence,ul);}}if(b.source_url)append(evidence,link(b.source_url,'Open source listing ↗'));append(evidence,jsonDetails(`Original observations (${b.observations?.length||0})`,b.observations||[]));append(p,evidence,reviewForm(b));
    const history=el('div','detail-section');append(history,el('h3','',`Review history · ${b.reviews?.length||0}`));if(!b.reviews?.length)append(history,el('p','','No review recorded. Imported data has not been manually verified.'));for(const r of [...(b.reviews||[])].reverse()){const entry=el('article','history-entry');append(entry,el('time','',`Recorded ${dateText(r.created_at)}`),el('p','',`${human(r.website_status)} · ${human(r.activity)} · ${human(r.disposition)}`),el('p','small',`Evidence observed: ${dateText(r.evidence_observed_at)}`),el('p','',r.notes||''),r.evidence_url?link(r.evidence_url,'Review evidence ↗'):null,jsonDetails('Effective saved decision',r));append(history,entry);}append(p,history);return p;
  }
  function reviewForm(b) {const section=el('section','detail-section');append(section,el('h3','','Record a review'),el('p','','Change the decisions you checked. Evidence dates must reflect when the evidence was observed.'));const form=el('form'),grid=el('div','form-grid section-gap'),controls={},base=b.review||{};const defaults={website_status:'unreviewed',activity:'unknown',ownership:'unknown',residential:'unknown',service_fit:'unknown',delivery:'unknown',disposition:'pending',queue_hold:false,evidence_observed_at:null};const defs=[['website_status','Website decision',['unreviewed','no_site_found','site_found','uncertain']],['activity','Business activity',['unknown','active','closed']],['ownership','Ownership',['unknown','independent','chain']],['residential','Residential service',['unknown','yes','no']],['service_fit','Service fit',['unknown','yes','no']],['delivery','Delivery model',['unknown','mobile','fixed']],['disposition','Disposition',['pending','rejected','contacted']]];
    defs.forEach(([key,label,options])=>{const n=select(options,base[key]??defaults[key]);controls[key]=n;append(grid,field(label,n));});for(const [key,label,type] of [['verified_website','Confirmed website','url'],['candidate_website','Candidate website','url'],['evidence_observed_at','Evidence observed on','date'],['evidence_url','Evidence URL','url']]){const n=input(type,base[key]||'',key==='evidence_url');controls[key]=n;if(type==='date')n.max=new Date().toISOString().slice(0,10);append(grid,field(label,n,key==='evidence_observed_at'?'Use the observation date; never a guessed date.':undefined));}const notes=el('textarea');notes.value=base.notes||'';notes.required=true;controls.notes=notes;const nf=field('Evidence notes',notes,'Describe what you checked and the basis for these decisions.');nf.classList.add('span-2');append(grid,nf);
    const hold=check('Keep this business on hold',Boolean(base.queue_hold));controls.queue_hold=hold.input;const activityCheck=check('Record a new activity check using this evidence');const contactCheck=check('I acknowledge that “contacted” adds a durable contact exclusion.');append(form,grid,el('div','section-gap'),hold.label,el('div','section-gap'),activityCheck.label,el('p','small','Existing activity evidence is retained unless activity changes or this box is checked.'),el('div','section-gap'),contactCheck.label);const error=el('p','inline-error');error.setAttribute('role','alert');const save=el('button','primary','Save review');save.type='submit';append(form,append(el('div','form-actions'),save,button('Reset edits',()=>guard(()=>{S.dirty=false;render();}))),error);form.addEventListener('input',()=>{S.dirty=true;S.advancedDirty=true;});form.addEventListener('change',()=>{S.dirty=true;S.advancedDirty=true;});form.addEventListener('submit',async e=>{e.preventDefault();if(S.reviewSaving)return;if(S.investigation?.source_changed){error.textContent='Review updated source evidence before saving. Your edits are retained.';return;}error.textContent='';const changes={};for(const [key,n] of Object.entries(controls)){const value=n.type==='checkbox'?n.checked:n.value;const previous=base[key]??defaults[key]??'';if(value!==previous&&!(key==='evidence_observed_at'&&!value&&previous===null))changes[key]=key==='evidence_observed_at'?(value||null):value;}
      if(activityCheck.input.checked)changes.activity=controls.activity.value;if('activity' in changes)changes.evidence_observed_at=controls.evidence_observed_at.value||null;if(controls.disposition.value==='contacted'&&!contactCheck.input.checked){error.textContent='Acknowledge the durable contact exclusion before saving.';return;}if(!Object.keys(changes).length){error.textContent='No changes to save. Select a new activity check only when you have checked fresh activity evidence.';return;}changes.evidence_url=controls.evidence_url.value;changes.notes=controls.notes.value;if(('activity'in changes)&&controls.activity.value==='active'&&!controls.evidence_observed_at.value){error.textContent='An active activity check needs an explicit evidence observation date.';return;}S.reviewSaving=true;const locked=Array.from((form.closest('.investigation')||form).querySelectorAll('input,select,textarea,button')).filter(n=>n!==save&&!n.disabled);locked.forEach(n=>{n.disabled=true;});try{await busy(save,async()=>{await persistDraft().catch(error=>{showDeskError(error);throw error;});await api(`/api/businesses/${b.id}/review`,{changes,replace:false,source_revision:S.investigation?.source_revision,review_revision:S.investigation?.review_revision});S.dirty=false;await state();const latest=await deskSnapshot(b.id);S.detail=latest.business;S.advancedDirty=false;setupInvestigation(latest);notice('Review saved. Decisions and their evidence are recorded in history.');render();},'Saving…');}finally{S.reviewSaving=false;locked.forEach(n=>{n.disabled=false;});}});append(section,form);return section;
  }
  const questionOrder = ['identity', 'website', 'activity', 'fit'];
  const questionLabels={identity:'Identity',website:'Website',activity:'Activity',fit:'Service fit'};
  const answerLabels={identity_status:'Identity decision',website_status:'Website decision',activity:'Activity',ownership:'Ownership',residential:'Residential service',service_fit:'Service fit',delivery:'Delivery model',verified_website:'Confirmed website',candidate_website:'Candidate website',evidence_url:'Checked evidence URL',evidence_observed_at:'Evidence observed on',notes:'Evidence notes'};
  const choiceFields=new Set(['identity_status','website_status','activity','ownership','residential','service_fit','delivery']);
  const questionInfo = {
    identity: ['Are these sources the same business?', 'Compare names, location and contact details. A mismatch creates a hold; it never merges or splits records.'],
    website: ['Does this business have its own website?', 'A missing listing field is a lead to investigate, not proof of website absence.'],
    activity: ['Is the business currently active?', 'Use dated evidence of activity. Saving other questions does not refresh this check.'],
    fit: ['Does the business fit your review scope?', 'Check ownership, residential work, service and delivery against your configured scope.']
  };
  const questionFields = {
    identity: [['identity_status', 'Identity decision', [['', 'Choose an answer'], ['same_business', 'Same business'], ['different_businesses', 'Different businesses — hold'], ['uncertain', 'Uncertain']]]],
    website: [['website_status', 'Website decision', [['', 'Choose an answer'], ['no_site_found', 'No site found after checking'], ['site_found', 'Confirmed website found'], ['uncertain', 'Uncertain']]]],
    activity: [['activity', 'Activity decision', [['', 'Choose an answer'], ['active', 'Active'], ['closed', 'Closed'], ['unknown', 'Unknown']]]],
    fit: [['ownership','Ownership',['unknown','independent','chain']], ['residential','Residential service',['unknown','yes','no']], ['service_fit','Service fit',['unknown','yes','no']], ['delivery','Delivery model',['unknown','mobile','fixed']]]
  };
  let draftTimer;
  const copy = value => JSON.parse(JSON.stringify(value));
  // Session-only input state is separate from durable evidence and review drafts.
  const researchByBusiness = new Map();
  let activeResearchRequest = null;
  function researchState(id) {
    if(!researchByBusiness.has(id))researchByBusiness.set(id,{open:false,pasteOpen:false,url:'',content:'',observed_at:'',confirmed:false,records:[],loaded:false,loading:false,pending:false,error:'',showDismissed:false,request:null});
    return researchByBusiness.get(id);
  }
  function researchCurrent(id,inv,key) {
    return S.detail?.id===id && S.investigation===inv && questionOrder[inv.step]===key && !inv.summary;
  }
  function researchAllowed() { return Boolean(S.investigation?.can_review) && !S.reviewSaving; }
  function resolveResearchNotice(message) {
    if(S.advancedDirty)return;
    if(S.researchErrorNotice===noticeGeneration && $('#notice').className==='error')notice(message);
    S.researchErrorNotice=null;
  }

  async function loadResearch(id,r,refresh) {
    if(r.loading)return;
    const generation=r.generation||0;
    r.loading=true;r.error='';refresh();
    try {const result=await api(`/api/businesses/${id}/research`);if(generation===(r.generation||0)){r.records=result.records;r.loaded=true;}}
    catch(error){if(generation===(r.generation||0))r.error='Saved evidence could not be loaded. '+error.message;}
    finally{r.loading=false;refresh();}
  }

  async function requestResearch(id,r,mode,refresh) {
    if(!researchAllowed() || S.detail?.id!==id)return;
    if(activeResearchRequest){r.error='A page check is already running. Wait for it to finish, then retry.';refresh();return;}
    if(!r.confirmed){r.error='Confirm permission to retain the page evidence before checking.';refresh();return;}
    const payload={mode,url:r.url.trim(),retention_confirmed:true};
    if(mode==='paste'){payload.content=r.content;payload.observed_at=r.observed_at;}
    const signature=JSON.stringify(payload);
    if(r.request?.signature!==signature)r.request={signature,body:{...payload,request_id:crypto.randomUUID()}};
    // Reuse the exact request after an ambiguous failure; never duplicate a capture.
    activeResearchRequest=r;r.pending=true;r.error='';refresh();
    try {
      const result=await api(`/api/businesses/${id}/research`,r.request.body);
      r.generation=(r.generation||0)+1;
      r.records=[result.record,...r.records.filter(card=>card.id!==result.record.id)];r.loaded=true;r.request=null;
      if(S.detail?.id===id)resolveResearchNotice('Page evidence saved. Review the captured clues before deciding.');
    }catch(error){r.error='Page check unavailable. This says nothing about whether a website exists. '+error.message;}
    finally{activeResearchRequest=null;r.pending=false;refresh();}
  }

  async function dismissResearch(id,r,record,refresh) {
    if(r.mutating)return;r.mutating=true;r.error='';refresh();
    try {
      const result=await api(`/api/businesses/${id}/research/${encodeURIComponent(record.id)}/dismiss`,{dismissed:!record.dismissed});
      r.generation=(r.generation||0)+1;
      r.records=r.records.map(item=>item.id===record.id?result.record:item);
      if(S.detail?.id===id)resolveResearchNotice('Research card visibility updated.');
    }catch(error){r.error='Could not change card visibility. '+error.message;}
    finally{r.mutating=false;refresh();}
  }

  async function applyResearch(id,key,record,refresh) {
    const inv=S.investigation,r=researchState(id);
    let copied=false;
    const eligible=()=>researchCurrent(id,inv,key) && researchAllowed() && !S.advancedDirty && !record.stale && !record.dismissed && ['identity','website'].includes(key);
    if(S.advancedDirty){S.researchErrorNotice=notice('Save or discard your advanced review edits before using a research proposal.',true);return;}
    if(!eligible() || r.applying)return;
    const startingNotice=noticeGeneration;
    r.applying=true;r.error='';refresh();
    try {
      // Recheck server-computed staleness/exclusions before copying evidence.
      const latest=await api(`/api/businesses/${id}/research`);
      r.records=latest.records;
      record=latest.records.find(item=>item.id===record.id);
      if(!record || !eligible())throw new Error('This proposal is no longer available for the selected question. Reload the business to check its current status.');
      const proposal=record.proposals?.[key];
      if(!proposal)throw new Error('This page has no proposal for this question.');
      await persistDraft();
      if(!eligible())return;
      const current=entryFor(key);
      if(Object.values(current).some(value=>value!=='' && value!==null && value!==undefined) && !window.confirm(`Replace the current ${questionLabels[key].toLowerCase()} answer draft with this proposal? Other questions will be retained. No review will be saved.`))return;
      const fields=key==='identity'?['identity_status','evidence_url','evidence_observed_at','notes']:['website_status','candidate_website','verified_website','evidence_url','evidence_observed_at','notes'];
      inv.edits[key]=Object.fromEntries(fields.filter(field=>Object.hasOwn(proposal,field)).map(field=>[field,proposal[field]]));
      copied=true;
      changedDraft();await persistDraft();
      if(researchCurrent(id,inv,key) && !S.advancedDirty && (startingNotice===noticeGeneration || S.researchErrorNotice===noticeGeneration)){notice('Proposal copied to this question’s editable draft. Check the evidence, then save the question when ready.');S.researchErrorNotice=null;}
    }catch(error){r.error='Proposal not saved as a review. '+error.message;if(inv.error && S.investigation===inv)showDeskError(error);}
    finally{r.applying=false;if(copied && researchCurrent(id,inv,key) && !S.advancedDirty)render();else refresh();}
  }

  function renderResearch(b,key) {
    const id=b.id,inv=S.investigation,r=researchState(id),slot=el('div','research-slot');
    if(r.lastInvestigation!==inv){r.lastInvestigation=inv;r.loaded=false;}
    const refresh=()=>{if(researchCurrent(id,inv,key))draw();};
    function draw() {
      slot.replaceChildren();
      const details=el('details','research-assistance');details.open=r.open;
      append(details,el('summary','','Research assistance'),el('p','small research-intro','Check one supplied public page for identity and website clues. Proposals are editable drafts; you decide what to save.'));
      details.addEventListener('toggle',()=>{
        const opened=details.open&&!r.open;r.open=details.open;
        if(opened)loadResearch(id,r,refresh);
      });
      if(!inv.can_review)append(details,el('p','callout warning','Contact exclusion retained. Saved evidence can be inspected or dismissed; new checks and proposal use are unavailable.'));
      else {
        const form=el('form','research-form'),url=input('url',r.url,true);
        url.placeholder='https://example.com/about';url.addEventListener('input',()=>{r.url=url.value;});
        const confirmation=check('I have permission to retain selected page evidence in this local workspace.',r.confirmed);confirmation.input.required=true;
        confirmation.input.addEventListener('change',()=>{r.confirmed=confirmation.input.checked;});
        const fetchButton=el('button','primary',r.pending?'Checking page…':'Fetch & check page');fetchButton.type='submit';fetchButton.disabled=r.pending;
        append(form,field('Public page URL',url,'A single deliberate fetch. No background search or linked-page crawling.'),confirmation.label,fetchButton);
        form.addEventListener('submit',event=>{event.preventDefault();requestResearch(id,r,'fetch',refresh);});
        const paste=el('details','research-paste');paste.open=r.pasteOpen;
        paste.addEventListener('toggle',()=>{r.pasteOpen=paste.open;});
        const saved=el('textarea');saved.rows=5;saved.value=r.content;saved.maxLength=524288;saved.spellcheck=false;
        saved.addEventListener('input',()=>{r.content=saved.value;});
        const observed=input('date',r.observed_at);observed.max=new Date().toISOString().slice(0,10);
        observed.addEventListener('input',()=>{r.observed_at=observed.value;});
        const analyze=button('Analyze saved page',()=>{
          if(!url.reportValidity() || !confirmation.input.reportValidity())return;
          if(!r.content.trim() || !r.observed_at){r.error='Provide saved page content and the date you observed it.';refresh();return;}
          if(!observed.reportValidity())return;
          requestResearch(id,r,'paste',refresh);
        });analyze.disabled=r.pending;
        append(paste,el('summary','','Use saved page text instead'),el('p','small','Paste text or HTML you already saved from the source URL above. This option does not fetch the page or independently verify the supplied content.'),field('Saved page text / HTML',saved,'Up to 512 KiB. Only selected evidence and its content hash are retained.'),field('Page observed on',observed,'Use the actual nonfuture date you observed this saved page.'),analyze);
        append(form,paste);append(details,form);
      }
      const status=el('p','small research-status',r.pending?'Checking the supplied page… You can keep editing your question.':r.loading?'Loading saved evidence…':'');status.setAttribute('role','status');status.setAttribute('aria-live','polite');append(details,status);
      if(r.error){const error=el('p','inline-error',r.error);error.setAttribute('role','alert');append(details,error);}
      const dismissed=r.records.filter(record=>record.dismissed).length;
      append(details,append(el('div','research-toolbar'),el('h4','',`Saved evidence · ${r.records.length}`),button(r.loading?'Loading…':'Reload evidence',()=>loadResearch(id,r,refresh))));
      if(dismissed)append(details,button(r.showDismissed?`Hide dismissed (${dismissed})`:`Show dismissed (${dismissed})`,()=>{r.showDismissed=!r.showDismissed;refresh();},'research-show-dismissed'));
      if(r.loaded&&!r.records.length)append(details,el('p','small','No page evidence saved for this business yet.'));
      for(const record of r.records.filter(record=>!record.dismissed||r.showDismissed))append(details,researchCard(id,key,r,record,refresh));
      append(details,el('p','small research-footnote','Research cards stay in this workspace and are not included in CSV/JSON exports. Saved review answers retain their normal evidence URL and notes. A loaded page alone does not prove current activity.'));
      append(slot,details);
    }
    draw();
    if(r.open&&!r.loaded&&!r.loading)queueMicrotask(()=>loadResearch(id,r,refresh));
    return slot;
  }

  function researchCard(id,key,r,record,refresh) {
    const card=el('article',`evidence-card research-card${record.dismissed?' research-dismissed':''}`);
    append(card,append(el('div','tags'),el('span','pill muted',record.mode==='paste'?'Saved page · user supplied':'Fetched public page'),record.stale?el('span','pill warning','Stale · business inputs changed'):null,record.dismissed?el('span','pill muted','Dismissed'):null),el('h4','',record.title||'Captured page'),link(record.final_url||record.url,'Open captured source ↗'),el('p','small',record.final_url||record.url),el('p','small',`Page observed: ${dateText(record.observed_at)}`),el('p','small',`Captured / analyzed locally: ${dateText(record.captured_at)}`));
    if(record.mode==='paste')append(card,el('p','small','Basis: supplied saved content; no independent fetch.'));
    if(record.final_url&&record.final_url!==record.url)append(card,append(el('p','small'),el('span','','Requested source: '),link(record.url)));
    if(record.excerpt)append(card,el('blockquote','research-excerpt',record.excerpt));
    for(const [kind,label] of [['match','Matching clues'],['conflict','Conflicting clues'],['unknown','Missing / uncertain clues']]) {
      const signals=(record.signals||[]).filter(signal=>signal.kind===kind);
      if(!signals.length)continue;
      const group=el('div',`research-clues ${kind}`),list=el('ul');
      signals.forEach(signal=>append(list,append(el('li'),el('strong','',signal.label||human(signal.field)),el('span','',` — ${signal.detail||'No detail supplied'}`))));
      append(card,append(group,el('h5','',label),list));
    }
    if(record.limitations?.length)append(card,append(el('div','research-limitations'),el('h5','','Limits of this check'),...record.limitations.map(text=>el('p','small',text))));
    for(const question of ['identity','website']) {
      const proposal=record.proposals?.[question];if(!proposal)continue;
      const box=el('div','research-proposal');append(box,el('h5','',`${questionLabels[question]} proposal · ${human(proposal[question==='identity'?'identity_status':'website_status'])}`));
      if(proposal.candidate_website||proposal.verified_website)append(box,el('p','small',proposal.verified_website||proposal.candidate_website));
      append(box,el('p','small',proposal.notes||'Check the captured clues before deciding.'));
      if(question===key){const use=button('Use proposal in this question’s draft',()=>applyResearch(id,key,record,refresh));use.disabled=!researchAllowed()||record.stale||record.dismissed||!r.loaded||r.loading||r.applying;append(box,use);}
      else append(box,el('p','small',`Open the ${questionLabels[question].toLowerCase()} question to use this proposal.`));
      append(card,box);
    }
    append(card,jsonDetails('Capture provenance',{mode:record.mode,requested_url:record.url,final_url:record.final_url,observed_at:record.observed_at,captured_at:record.captured_at,content_sha256:record.content_sha256,links:record.links||[]}),button(record.dismissed?'Restore card':'Dismiss card',()=>dismissResearch(id,r,record,refresh)));
    return card;
  }

  function setupInvestigation(data) {
    clearTimeout(draftTimer);
    S.investigation = {
      ...data, source_revision:data.draft ? (data.draft_source_revision ?? data.source_revision) : data.source_revision, current_source_revision:data.source_revision, mode:data.draft?.mode || 'workspace', step:data.draft?.step || 0,
      edits:copy(data.draft?.questions || {}), sequence:0, savedSequence:0,
      saving:null, error:null, conflict:false, summary:false, request:null
    };
    S.dirty=false;
    S.advancedDirty=false;
  }

  function draftPayload(inv) {
    return {mode:inv.mode, step:inv.step, questions:copy(inv.edits)};
  }
  function questionConflict(inv,key) {
    const context=inv.draft_conflicts?.[key];
    return context?Boolean(context.changed||context.unknown):Boolean(inv.review_changed&&!inv.draft_conflicts);
  }
  function updateDraftContext(inv,saved) {
    for(const key of ['draft_review_revision','draft_review_revisions','draft_conflicts','review_changed','source_changed'])if(key in saved)inv[key]=saved[key];
    if('source_revision' in saved)inv.current_source_revision=saved.source_revision;
    if('draft_source_revision' in saved)inv.source_revision=saved.draft_source_revision??saved.source_revision;
  }

  function draftStatus() {
    const inv=S.investigation;
    if(!inv)return;
    document.querySelectorAll('[data-draft-status]').forEach(node=>{
      node.textContent=inv.review_changed && inv.sequence===inv.savedSequence ? 'Saved draft retained · compare its review context' : inv.error ? 'Draft not saved · your input is kept here' : inv.saving ? 'Saving draft locally…' : inv.sequence!==inv.savedSequence ? 'Unsaved changes…' : inv.draft || Object.keys(inv.edits).length ? 'Draft saved in this workspace' : 'No unfinished draft';
    });
  }

  function changedDraft() {
    const inv=S.investigation;
    inv.sequence++;
    inv.request=null;
    S.dirty=true;
    draftStatus();
    clearTimeout(draftTimer);
    draftTimer=setTimeout(()=>persistDraft().catch(showDeskError),450);
  }

  async function persistDraft() {
    const inv=S.investigation;
    if(!inv || !S.detail || !inv.can_review)return;
    if(inv.undoPending)throw new Error('Undo outcome is not confirmed. Check Undo again before saving or leaving this work.');
    if(inv.saving)return inv.saving;
    if(inv.sequence===inv.savedSequence)return;
    if(inv.conflict)throw new Error('Another review or draft changed. Resolve the conflict below before continuing.');
    const id=S.detail.id;
    inv.saving=(async()=>{
      while(inv.sequence!==inv.savedSequence) {
        const sequence=inv.sequence;
        const payload=draftPayload(inv);
        try {
          const saved=await api(`/api/businesses/${id}/draft`,{
            review_revision:inv.review_revision, draft_revision:inv.draft_revision, source_revision:inv.source_revision, draft:payload
          });
          Object.assign(inv,{review_revision:saved.review_revision,draft_revision:saved.draft_revision,draft:saved.draft,savedSequence:sequence,error:null,source_changed:saved.source_changed??inv.source_changed,current_source_revision:saved.source_revision??inv.current_source_revision});
          updateDraftContext(inv,saved);
          S.dirty=inv.sequence!==inv.savedSequence || Boolean(S.advancedDirty);
        } catch(error) {
          inv.error=error.message;
          inv.conflict=error.status===409;
          throw error;
        }
      }
    })();
    draftStatus();
    try {await inv.saving;} finally {inv.saving=null;draftStatus();}
  }

  function resolveDeskNotice(message) {
    const current=$('#notice');
    if(S.deskErrorNotice===noticeGeneration && current.className==='error')notice(message);
    S.deskErrorNotice=null;
  }

  function showDeskError(error) {
    S.deskErrorNotice=fail(error);
    const inv=S.investigation;
    if(!inv)return;
    inv.error=error.message;
    if(error.status===409)inv.conflict=true;
    const box=document.querySelector('[data-draft-error]');
    if(!box)return;
    box.replaceChildren(el('p','inline-error',error.message));
    if(inv.undoPending){append(box,button('Check Undo outcome',()=>undoInvestigation().catch(showDeskError)));draftStatus();return;}
    if(inv.source_changed&&!inv.conflict){append(box,button('Review updated evidence',()=>reviewUpdatedEvidence()));draftStatus();return;}
    if(inv.conflict) {
      append(box,el('p','small','Your answers are retained. Load the newer review for comparison, then explicitly keep your draft or use the newer draft.'),button('Compare latest saved work',async()=>{
        try {
          const latest=await deskSnapshot(S.detail.id);
          if(latest.source_revision!==undefined&&latest.source_revision!==inv.source_revision&&latest.review_revision===inv.review_revision&&latest.draft_revision===inv.draft_revision){S.detail=latest.business;refreshEvidenceModel(inv,latest);inv.source_changed=true;inv.current_source_revision=latest.source_revision;inv.conflict=false;inv.error=null;inv.request=null;render();focusDesk();return;}
          box.replaceChildren(el('p','callout warning','Compare before replacing unfinished input. No answers have been submitted.'));
          for(const key of questionOrder.filter(q=>inv.edits[q]||latest.draft?.questions?.[q]))append(box,el('h4','',questionLabels[key]),append(el('div','comparison-grid'),comparisonCard('Current saved answer',key,latest.saved_answers?.[key]),comparisonCard('Saved unfinished input',key,latest.draft?.questions?.[key],true),comparisonCard('Your retained input',key,inv.edits[key],true)));
          append(box,button('Keep my answers as the draft',async()=>{
            S.detail=latest.business;inv.review_revision=latest.review_revision;inv.draft_revision=latest.draft_revision;
            inv.source_changed=latest.source_revision!==inv.source_revision;inv.current_source_revision=latest.source_revision;
            refreshEvidenceModel(inv,latest);
            // Updating transport tokens preserves each question's durable
            // baseline. Resolution belongs to its visible comparison controls.
            inv.draft_conflicts=latest.draft_conflicts;inv.review_changed=latest.review_changed;
            inv.conflict=false;inv.error=null;inv.sequence++;inv.request=null;
            try {await persistDraft();resolveDeskNotice('Conflict resolved. Your answers are saved as the local draft.');render();focusDesk();}catch(e){showDeskError(e);}
          }),button('Use newer saved work',()=>{
            pendingNavigation=()=>{if(S.investigation!==inv)return;S.detail=latest.business;setupInvestigation(latest);resolveDeskNotice('The saved draft is loaded. Review context is shown for each question.');render();focusDesk();};
            $('#confirm-title').textContent='Replace your local unfinished input?';$('#confirm-dialog p').textContent='The saved draft shown in this comparison will replace your local question edits. Saved review history remains unchanged.';$('#discard-editing').textContent='Replace local input with saved draft';$('#confirm-dialog').showModal();
          }));
        }catch(e){fail(e);}
      }));
    } else append(box,button('Retry saving draft',async()=>{
      try{await persistDraft();resolveDeskNotice('Draft saved locally.');box.replaceChildren();}catch(e){showDeskError(e);}
    }));
    draftStatus();
  }

  function focusDesk() {
    const node=document.querySelector('.desk-title');
    node?.focus({preventScroll:true});
  }

  async function changeDesk(update) {
    if(S.advancedDirty){guard(()=>changeDesk(update));return;}
    if(S.reviewSaving || S.navigating || S.deskMoving)return;
    S.deskMoving=true;
    try {
      update(S.investigation);
      if(S.investigation.can_review){changedDraft();await persistDraft();}
      render();focusDesk();
    }catch(error){showDeskError(error);}finally{S.deskMoving=false;}
  }

  function entryFor(key) {
    const inv=S.investigation;
    return copy(inv.edits[key] || inv.questions[key] || {});
  }
  function discardQuestion(key,advance=false) {
    const discard=()=>changeDesk(i=>{delete i.edits[key];if(advance){if(i.step===3)i.summary=true;else i.step++;}});
    if(!S.investigation.edits[key])return discard();
    pendingNavigation=discard;
    $('#confirm-title').textContent=`Discard ${questionLabels[key].toLowerCase()} draft edits?`;
    $('#confirm-dialog p').textContent='Only this question’s unfinished edits will be removed. Other draft questions and saved review history are retained.';
    $('#discard-editing').textContent='Discard this question’s edits';
    $('#confirm-dialog').showModal();
  }

  function renderInvestigation(b) {
    const inv=S.investigation;
    const desk=el('section','panel investigation');
    desk.setAttribute('aria-label',`Investigate ${b.name}`);
    const title=el('h2','desk-title',b.name);title.tabIndex=-1;
    const modes=append(el('div','desk-modes'),...['workspace','guided'].map(mode=>{
      const node=button(mode==='workspace'?'Investigation desk':'Guided review',()=>changeDesk(i=>{i.mode=mode;i.summary=false;}));
      node.setAttribute('aria-pressed',String(inv.mode===mode));return node;
    }));
    append(desk,append(el('header','desk-head'),append(el('div'),el('p','eyebrow','ONE BUSINESS · FOUR QUESTIONS'),title,el('p','detail-meta',[b.address,b.city,b.state].filter(Boolean).join(', ')),append(el('div','tags'),el('span','pill',human(b.queue_status)),...(b.trades||[]).map(t=>el('span','pill muted',human(t))))),append(el('div','desk-head-actions'),button(S.returnToDrafts?'← Back to unfinished work':S.returnToChanges?'← Back to changes':'← Queue',()=>guard(()=>{if(S.returnToDrafts){navigate('unfinished');}else if(S.returnToChanges){navigate('changes');}else{S.detail=null;S.investigation=null;S.selectedChange=null;S.returnToChanges=false;render();}})),modes)));
    if(inv.source_changed)append(desk,append(el('div','callout warning'),el('p','','Source evidence changed since this draft began. Your answers are retained. Review the updated evidence before saving a decision.'),inv.can_review?button('Review updated evidence',()=>reviewUpdatedEvidence()):null));
    if(inv.review_changed)append(desk,el('p','callout warning','Saved-review context changed or is unknown for unfinished questions. Compare each affected question; saving an unaffected question leaves the others unfinished.'));
    if(b.excluded_reasons?.length)append(desk,el('p',`desk-restriction callout ${inv.can_review?'warning':''}`,inv.can_review?`Excluded from candidates: ${b.excluded_reasons.map(human).join(' · ')}. Evidence can be corrected below.`:'Durable contact exclusion · read-only. This business cannot be reviewed or contacted through this workflow.'));
    const nav=el('nav','question-nav');nav.setAttribute('aria-label','Review questions');
    questionOrder.forEach((key,index)=>{
      const status=inv.edits[key]?(questionConflict(inv,key)?'Compare draft':'Draft'):inv.questions[key]?'Saved':'Unchecked';
      const node=button(`${index+1}. ${questionLabels[key]} · ${status}`,()=>changeDesk(i=>{i.step=index;i.summary=false;}));
      if(inv.step===index&&!inv.summary)node.setAttribute('aria-current','step');
      append(nav,node);
    });
    append(desk,nav);
    if(inv.summary)append(desk,renderSessionSummary(b));
    else {
      const key=questionOrder[inv.step];
      append(desk,append(el('div',`desk-body ${inv.mode==='guided'?'guided':''}`),renderEvidence(b,key),renderQuestion(b,key)));
    }
    const status=el('p','draft-status');status.dataset.draftStatus='';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const errors=el('div','desk-errors');errors.dataset.draftError='';errors.setAttribute('role','alert');
    append(desk,append(el('div','desk-footer'),status,el('p','small','Local draft · no outreach · no contact permission granted')),errors);
    if(inv.latest_session?.can_undo && inv.can_review)append(desk,append(el('div','undo-bar'),el('p','small','Undo restores the previous decisions. History and durable exclusions remain.'),button('Undo last save',e=>busy(e.currentTarget,()=>undoInvestigation(),'Restoring…'))));
    const advanced=el('details','advanced-review');
    append(advanced,el('summary','','Advanced review & full history'));
    if(inv.can_review)append(advanced,el('p','callout','Advanced controls include hold and disposition. Unfinished desk answers remain saved separately.'),reviewForm(b));
    append(advanced,jsonDetails('Current evaluated record',b.review||{}),jsonDetails('All review history',b.reviews||[]),jsonDetails('Assessment and score details',{score:b.score_breakdown,website:b.website_assessment,activity:b.activity_assessment,scope:b.scope_assessment}));
    append(desk,advanced);
    queueMicrotask(()=>{draftStatus();if(inv.error)showDeskError(new Error(inv.error));});
    return desk;
  }

  function refreshEvidenceModel(inv,latest) {
    for(const key of ['questions','saved_answers','question_history','evidence','can_review','latest_session'])inv[key]=latest[key];
    for(const key of ['draft_conflicts','draft_review_revisions','review_changed'])if(key in latest)inv[key]=latest[key];
  }

  function lazyDetails(title,build,cls='') {
    const details=el('details',cls);let loaded=false;
    append(details,el('summary','',title));
    details.addEventListener('toggle',()=>{if(details.open&&!loaded){loaded=true;append(details,build());}});
    return details;
  }

  function knownObservationDate(value) {
    if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;
    const parsed=new Date(value+'T00:00:00Z');
    return !Number.isNaN(parsed.getTime())&&parsed.toISOString().slice(0,10)===value&&value<=new Date().toISOString().slice(0,10);
  }

  function answerDefinition(entry,key) {
    const fields=questionFields[key].map(([field])=>field).concat(key==='website'?['verified_website','candidate_website']:[]);
    return definition(fields.map(field=>[human(field),human(entry[field]||'not supplied')]));
  }

  function renderSavedEvidence(key) {
    const inv=S.investigation,saved=inv.saved_answers?.[key],box=el('section','effective-answer');
    append(box,el('h4','','Effective saved answer'));
    if(!saved)append(box,el('p','small','No saved answer for this question. Imported observations are not a human check.'));
    else {
      append(box,answerDefinition(saved.answers||{},key),el('p','basis-status',saved.basis_known?'Evidence association known':'Evidence basis unknown'));
      append(box,el('p','small',knownObservationDate(saved.evidence_observed_at)?`Evidence observed: ${saved.evidence_observed_at}`:'Evidence observation date: unknown'));
      if(saved.basis_known)append(box,saved.evidence_url?link(saved.evidence_url,'Saved checked evidence ↗'):el('p','small','Checked evidence URL: unknown'),saved.notes?el('p','evidence-notes',saved.notes):el('p','small','Checked evidence notes: unknown'));
      else append(box,el('p','small','No evidence can be reliably associated with this answer. Check the evidence before saving a new decision.'));
    }
    const draft=inv.edits[key];
    if(draft)append(box,append(el('article','draft-comparison'),el('h4','','Unfinished draft · separate from effective answer'),answerDefinition(draft,key),el('p','small',`Draft evidence observed: ${draft.evidence_observed_at||'Not supplied'}`),draft.notes?el('p','evidence-notes',draft.notes):null,el('p','small','These edits have not replaced the effective saved answer.')));
    if(draft&&inv.draft_conflicts?.[key])append(box,renderDraftComparison(key));
    const historical=inv.question_history?.[key],current=inv.questions?.[key]||{};
    if(historical&&Object.keys({...historical,...current}).some(field=>(historical[field]||'')!==(current[field]||'')))append(box,lazyDetails('Earlier guided answer · superseded history',()=>append(el('div','historical-answer'),answerDefinition(historical,key),el('p','small',`Earlier evidence observed: ${historical.evidence_observed_at||'Unknown'}`),el('p','evidence-notes',historical.notes||'No notes recorded'),historical.evidence_url?link(historical.evidence_url,'Earlier checked evidence ↗'):null,el('p','small','Retained for history; this is not the effective answer or its evidence basis.'))));
    return box;
  }
  function comparisonCard(title,key,saved,isDraft=false) {
    const card=append(el('article','comparison-card'),el('h5','',title));
    if(!saved){append(card,el('p','small','No saved answer can be established for this context.'));return card;}
    append(card,answerDefinition(isDraft?saved:saved.answers||{},key),el('p','small',isDraft?'Unsubmitted input':saved.basis_known?'Evidence association known':'Evidence basis unknown'),el('p','small',knownObservationDate(saved.evidence_observed_at)?`Evidence observed: ${saved.evidence_observed_at}`:'Evidence observation date: unknown'),saved.evidence_url?link(saved.evidence_url,'Checked evidence ↗'):el('p','small','Checked evidence URL: unknown'),el('p','evidence-notes',saved.notes||'Checked evidence notes: unknown'));
    return card;
  }
  function renderDraftComparison(key) {
    const inv=S.investigation,context=inv.draft_conflicts[key],section=el('section','question-comparison');
    append(section,el('h4','',`${questionLabels[key]} · draft context comparison`),el('p','small',context.unknown?'The draft baseline is unknown. Compare the current saved work before deciding.':context.changed?'Saved-review context changed since this question’s accepted baseline. The answer may be the same; its context still needs review.':'The saved-review context matches this draft question.'));
    append(section,append(el('div','comparison-grid'),comparisonCard('Review context for this draft',key,context.baseline_saved_answer),comparisonCard('Current saved answer',key,context.current_saved_answer),comparisonCard('Your unfinished answer',key,inv.edits[key],true)));
    if(questionConflict(inv,key)&&inv.can_review){
      append(section,el('p','small','These choices prepare only this question. They do not save a review or accept changed source evidence.'),append(el('div','comparison-actions'),button('Keep my draft answer',e=>busy(e.currentTarget,()=>resolveQuestion(key,'keep'),'Keeping…'),'primary'),button('Use current saved answer',e=>busy(e.currentTarget,()=>resolveQuestion(key,'current'),'Loading…')),button('Defer this question',()=>{notice(`${questionLabels[key]} remains unfinished. Other question edits are retained.`);})));
    }else if(!inv.can_review)append(section,el('p','small','Contact exclusion retained. This draft is available for inspection only.'));
    return section;
  }
  async function resolveQuestion(key,choice) {
    const inv=S.investigation,id=S.detail.id;
    if(!inv.can_review||S.reviewSaving||S.navigating)return;
    S.navigating=true;
    const locked=Array.from(document.querySelectorAll('.investigation input,.investigation select,.investigation textarea,.investigation button')).filter(node=>!node.disabled);
    locked.forEach(node=>{node.disabled=true;});
    try {
      await persistDraft();
      const sequence=inv.sequence;
      const saved=await api(`/api/businesses/${id}/draft`,{review_revision:inv.review_revision,draft_revision:inv.draft_revision,source_revision:inv.source_revision,draft:draftPayload(inv),resolve_review_revision:inv.review_revision,resolve_questions:{[key]:choice}});
      if(S.investigation!==inv||S.detail?.id!==id)return;
      Object.assign(inv,{review_revision:saved.review_revision,draft_revision:saved.draft_revision,draft:saved.draft,error:null,conflict:false,request:null});
      updateDraftContext(inv,saved);
      if(inv.sequence===sequence){inv.edits=copy(saved.draft?.questions||{});inv.savedSequence=sequence;S.dirty=Boolean(S.advancedDirty);}
      else {S.dirty=true;notice('The question was resolved; newer input is retained and still needs saving.');}
      if(choice==='current'&&!(key in inv.edits))inv.questions[key]=copy(inv.saved_answers?.[key]?{...inv.saved_answers[key].answers,...Object.fromEntries(['evidence_url','notes','evidence_observed_at'].map(f=>[f,inv.saved_answers[key][f]||'']))}:{});
      resolveDeskNotice(choice==='keep'?'Draft answer kept for this question.':'Current saved answer loaded for this question.');
      render();focusDesk();
    }catch(error){showDeskError(error);throw error;}
    finally{S.navigating=false;locked.forEach(node=>{node.disabled=false;});}
  }

  function selectedChangeContext(key) {
    const selected=S.selectedChange,inv=S.investigation;
    if(!selected||selected.business_id!==S.detail?.id)return null;
    if(!inv.evidence)return el('p','callout warning','Selected change context could not be refreshed. Reload saved evidence to compare the current reason.');
    const reason=(inv.evidence.change_reasons||[]).find(r=>r.id===selected.reason_id);
    if(!reason)return el('p','callout','Selected change is no longer current. Its saved decisions and source history remain available; no attention was automatically marked seen.');
    const questions=reason.questions?.length?reason.questions:[reason.question||'identity'];
    if(!questions.includes(key))return el('p','small',`Selected change concerns: ${questions.map(q=>questionLabels[q]||human(q)).join(', ')}. Open that question to compare its context.`);
    return append(el('section','selected-change'),el('p','eyebrow','SELECTED CHANGE · CURRENT CONTEXT'),changeReason(reason));
  }

  function observationCard(o,key,b,latest=false) {
    const card=el('article','evidence-card');
    append(card,el('p','eyebrow',latest?'LATEST IMPORTED FOR THIS SOURCE':'EARLIER IMPORT · RETAINED HISTORY'),el('h4','',o.name||b.name),el('p','evidence-location',[o.address,o.city,o.state].filter(Boolean).join(', ')||'Location not supplied'));
    if(key==='identity')append(card,el('p','',`Phones: ${listValue(o.phones)||'Not supplied'}`));
    if(key==='website')append(card,el('p','',`Website fields: ${listValue(o.websites)||'Not supplied — absence is unverified'}`));
    if(key==='activity')append(card,el('p','',`Source activity signal: ${o.activity?human(o.activity):'Not supplied'}`));
    if(key==='fit')append(card,el('p','',`Categories: ${listValue(o.categories)||'Not supplied'}`));
    append(card,el('p','small',`Import batch: ${o.batch_id??'Not recorded'} · Observed by source: ${dateText(o.observed_at)}`),el('p','small','Import order is separate from observation date.'),o.source_url?link(o.source_url,'Open source ↗'):el('p','small','Source URL not supplied'),lazyDetails('Inspect original observation',()=>el('pre','',JSON.stringify(o,null,2))));
    return card;
  }

  function sourceGroups(b) {
    if(S.investigation.evidence)return S.investigation.evidence.source_groups||[];
    const groups=new Map();
    for(const o of b.observations||[]){const id=JSON.stringify([o.source,o.source_id]);if(!groups.has(id))groups.set(id,{source:o.source,source_id:o.source_id,history:[],observation_count:0});const group=groups.get(id);if(group.latest)group.history.unshift(group.latest);group.latest=o;group.observation_count++;}
    return [...groups.values()];
  }

  function renderEvidence(b,key) {
    const area=el('section','desk-evidence');
    append(area,el('p','eyebrow',`QUESTION ${S.investigation.step+1} OF 4`),el('h3','desk-question',questionInfo[key][0]),el('p','desk-help',questionInfo[key][1]),selectedChangeContext(key),renderSavedEvidence(key));
    if(key==='identity'||key==='website')append(area,renderResearch(b,key));
    append(area,el('h4','source-heading','Latest imported observations by source'),el('p','small','Each source identity stays separate. Missing fields do not retract earlier evidence or establish absence or closure.'));
    const groups=sourceGroups(b);
    if(!groups.length)append(area,el('p','callout warning','No original observations were supplied. Add your checked evidence to make a decision.'));
    for(const group of groups){
      const section=el('section','source-group');
      append(section,el('h4','',`${group.source||'Source not supplied'} · ${group.source_id||'Source identity not supplied'}`),el('p','small',`${group.observation_count??1} retained observations`),observationCard(group.latest,key,b,true));
      if(group.history?.length)append(section,lazyDetails(`Earlier imports (${group.history.length})`,()=>group.history.map(o=>observationCard(o,key,b)),'source-history'));
      append(area,section);
    }
    const signals=S.investigation.evidence?.retained_signals||[];
    if(signals.length){const retained=append(el('section','retained-signals'),el('h4','','Retained signals affecting screening'));
      for(const signal of signals)append(retained,append(el('article'),el('h5','',human(signal.kind)),el('p','',signal.value||'Value not supplied'),el('p','small',signal.explanation||''),el('p','small',[signal.source,signal.source_id,signal.batch_id!==undefined?`Import ${signal.batch_id}`:null,signal.observed_at?`Observed: ${dateText(signal.observed_at)}`:null].filter(Boolean).join(' · '))));
      append(area,retained);
    }
    if(b.websites?.length||b.socials?.length)append(area,lazyDetails('Accumulated business links · includes retained history',()=>[...(b.websites||[]),...(b.socials||[])].map(url=>append(el('p'),link(url))),'accumulated-links'));
    const entries=(b.reviews||[]).filter(r=>r.investigation_question===key);
    append(area,lazyDetails(`${questionLabels[key]} review trail · ${entries.length}`,()=>entries.length?entries.slice().reverse().map(r=>append(el('article','history-entry'),el('time','',`Recorded: ${dateText(r.created_at)}`),el('p','',r.notes||''),el('p','small',`Evidence observed: ${dateText(r.evidence_observed_at)}`),r.evidence_url?link(r.evidence_url,'Checked evidence ↗'):null,jsonDetails('Historical question entry',r))):el('p','small','No question-specific review saved. Earlier reviews remain in full history.'),'question-trail'));
    append(area,button('Refresh saved evidence',()=>guard(async()=>{
      const inv=S.investigation,{step,mode,sequence}=inv;
      const latest=await deskSnapshot(b.id);
      if(S.investigation!==inv||S.detail?.id!==b.id)return;
      S.detail=latest.business;
      if(inv.sequence!==sequence){
        // A delayed read cannot replace input entered after its initial flush.
        refreshEvidenceModel(inv,latest);
        inv.source_changed=latest.source_revision!==inv.source_revision;
        inv.current_source_revision=latest.source_revision;
        inv.review_changed=Boolean(inv.review_changed||latest.review_changed||latest.review_revision!==inv.review_revision);
        if(inv.review_changed)inv.conflict=true;
        notice('Saved evidence refreshed. Your newer input is retained; check its draft status before closing.');
      }else{setupInvestigation(latest);Object.assign(S.investigation,{step,mode});}
      render();focusDesk();
    })));
    return area;
  }

  function renderQuestion(b,key) {
    const inv=S.investigation;
    const side=el('section','desk-decision');
    append(side,el('p','eyebrow',inv.mode==='guided'?'YOUR ANSWER':'RECORD A DECISION'));
    if(!inv.can_review){append(side,el('h3','','Contact exclusion retained'),el('p','desk-help','Review controls are read-only for this business. Inspect its sources and history.'));return side;}
    const form=el('form'), entry=entryFor(key), controls={};
    const saveInput=(name,value)=>{
      entry[name]=value;
      if(S.investigation!==inv){
        // An already queued event can arrive after the restored desk replaced
        // its form. Retain it only on that business, with explicit comparison.
        if(inv.undoRestored&&S.detail?.id===b.id&&S.investigation){
          S.investigation.edits[key]=copy(entry);changedDraft();
          S.investigation.conflict=true;
          showDeskError(new Error('Input arrived after Undo. It is retained here but is not saved. Compare latest saved work before keeping it.'));
        }
        return;
      }
      inv.edits[key]=copy(entry);
      changedDraft();
    };
    questionFields[key].forEach(([name,label,options])=>{
      const node=select(options,entry[name]??(key==='fit'?'unknown':''));
      controls[name]=node;entry[name]=node.value;node.required=true;
      node.addEventListener('change',()=>saveInput(name,node.value));append(form,field(label,node));
    });
    if(key==='website')for(const [name,label] of [['verified_website','Confirmed website'],['candidate_website','Candidate website']]){
      const node=input('url',entry[name]||'');controls[name]=node;
      node.addEventListener('input',()=>saveInput(name,node.value));append(form,field(label,node));
    }
    for(const [name,label,type] of [['evidence_url','Checked evidence URL','url'],['evidence_observed_at','Evidence observed on','date']]){
      const node=input(type,entry[name]||'',true);controls[name]=node;
      if(type==='date')node.max=new Date().toISOString().slice(0,10);
      node.addEventListener('input',()=>saveInput(name,node.value));
      append(form,field(label,node,type==='date'?'Enter the actual date you observed this evidence.':'Use a specific HTTP(S) source you checked.'));
    }
    const notes=el('textarea');notes.required=true;notes.rows=4;notes.value=entry.notes||'';controls.notes=notes;
    notes.addEventListener('input',()=>saveInput('notes',notes.value));
    append(form,field('Evidence notes',notes,'Explain the basis for this answer.'));
    const submit=el('button','primary',inv.mode==='guided'?'Keep answer & continue →':'Save this question');submit.type='submit';
    append(form,append(el('div','decision-actions'),submit,button(inv.mode==='guided'?'Skip this question':'Discard this question’s edits',()=>discardQuestion(key,inv.mode==='guided')),button('Save draft & defer',()=>guard(()=>{if(S.returnToDrafts)navigate('unfinished');else{S.detail=null;S.investigation=null;S.selectedChange=null;S.returnToChanges=false;render();}}))));
    if(inv.mode==='guided')append(form,button('Review session summary',()=>changeDesk(i=>{i.summary=true;}),'summary-link'));
    form.addEventListener('submit',e=>{
      e.preventDefault();
      if(S.reviewSaving)return;
      const answer={};Object.entries(controls).forEach(([name,node])=>{answer[name]=node.value;});
      if(key==='website'&&answer.website_status==='site_found'&&!answer.verified_website){notice('Add the confirmed website before saving this answer.',true);controls.verified_website.focus();return;}
      const edited=Boolean(inv.edits[key]);
      if(inv.mode==='guided')changeDesk(i=>{if(edited)i.edits[key]=answer;if(i.step===3)i.summary=true;else i.step++;});
      else if(edited){inv.edits[key]=answer;changedDraft();busy(submit,()=>commitInvestigation([key]),'Saving question…');}
      else notice('This question has no new changes. Edit an answer or its checked evidence to save a new review.');
    });
    append(side,form);return side;
  }

  function renderSessionSummary(b) {
    const inv=S.investigation, section=el('section','session-summary');
    append(section,el('p','eyebrow','GUIDED REVIEW'),el('h3','desk-question','Review your session'),el('p','desk-help','Only the checked or edited answers below will be submitted together. Skipped questions keep their existing decisions.'));
    questionOrder.forEach((key,index)=>{
      const answer=inv.edits[key];
      const row=el('article','summary-card');
      append(row,append(el('div','summary-top'),el('h3','',questionLabels[key]),el('span',`pill ${answer?'':'muted'}`,answer?'New answer':'Skipped · previous decision retained')));
      if(answer)append(row,definition(Object.entries(answer).map(([name,value])=>[answerLabels[name]||human(name),['evidence_url','verified_website','candidate_website'].includes(name)&&value?link(value):choiceFields.has(name)?human(value):String(value||'Not supplied')])));
      append(row,append(el('div','actions'),button(answer?'Edit answer':'Check this question',()=>changeDesk(i=>{i.step=index;i.summary=false;})),answer?button('Skip',()=>discardQuestion(key)):null));append(section,row);
    });
    const finish=button('Finish & save review',e=>busy(e.currentTarget,()=>commitInvestigation(Object.keys(inv.edits)),'Saving review…'),'primary');
    finish.disabled=!inv.can_review || !Object.keys(inv.edits).length;
    append(section,append(el('div','actions section-gap'),finish,button('Save draft & defer',()=>guard(()=>{if(S.returnToDrafts)navigate('unfinished');else{S.detail=null;S.investigation=null;S.selectedChange=null;S.returnToChanges=false;render();}}))));
    return section;
  }

  async function commitInvestigation(keys) {
    if(S.reviewSaving)return;
    const inv=S.investigation,id=S.detail.id;
    if(!inv.can_review)return;
    const unresolved=keys.filter(key=>questionConflict(inv,key));
    if(unresolved.length){inv.step=questionOrder.indexOf(unresolved[0]);inv.summary=false;notice('Compare this question’s draft context before saving. Your other edits are retained.',true);render();focusDesk();return;}
    if(inv.source_changed){showDeskError(new Error('Source evidence changed. Review updated evidence before saving.'));return;}
    const queueIds=S.list?.matching_ids||[];
    S.nextCandidates=queueIds.slice(queueIds.indexOf(id)+1);
    S.reviewSaving=true;
    // Lock inputs while committing so a successful response cannot discard later typing.
    const locked=Array.from(document.querySelectorAll('.investigation input,.investigation select,.investigation textarea,.investigation button')).filter(node=>!node.disabled);
    locked.forEach(node=>{node.disabled=true;});
    try {
      await persistDraft();
      if(inv.source_changed)throw new Error('Source evidence changed. Your draft is retained. Review updated evidence before saving.');
      const questions=Object.fromEntries(keys.filter(key=>inv.edits[key]).map(key=>[key,copy(inv.edits[key])]));
      if(!Object.keys(questions).length)throw new Error('There are no newly checked answers to submit.');
      inv.request=inv.request||{review_revision:inv.review_revision,draft_revision:inv.draft_revision,source_revision:inv.source_revision,request_id:crypto.randomUUID(),questions};
      const result=await api(`/api/businesses/${id}/investigation`,inv.request);
      // The backend preserves remaining questions and their original baselines
      // atomically. Never re-save them against the newly submitted review.
      Object.assign(inv,{review_revision:result.review_revision,draft_revision:result.draft_revision,draft:result.draft??null,request:null,error:null,conflict:false,edits:copy(result.draft?.questions||{}),questions:{...inv.questions,...questions},latest_session:{session_id:result.session_id,can_undo:true}});
      updateDraftContext(inv,result);
      inv.saved_answers={...inv.saved_answers};inv.question_history={...inv.question_history,...questions};inv.evidence=null;
      for(const [key,answer] of Object.entries(questions))inv.saved_answers[key]={answers:Object.fromEntries(questionFields[key].map(([field])=>[field,answer[field]]).concat(key==='website'?[['verified_website',answer.verified_website||''],['candidate_website',answer.candidate_website||'']]:[])),basis_known:true,evidence_url:answer.evidence_url,notes:answer.notes,evidence_observed_at:answer.evidence_observed_at};
      inv.sequence++;
      inv.savedSequence=inv.sequence;
      S.dirty=false;
      notice('Review saved. Checked answers and their individual evidence are recorded.');
      try {
        const latest=await deskSnapshot(id);
        S.detail=latest.business;setupInvestigation(latest);
        await state();render();focusDesk();
      }catch(refreshError){
        render();focusDesk();
        notice('Review saved and remaining draft retained. Refresh could not finish: '+refreshError.message,true);
      }
      showCompletion(result.session_id);
    }catch(error){showDeskError(error);throw error;}
    finally{S.reviewSaving=false;locked.forEach(node=>{node.disabled=false;});}
  }

  function showCompletion(sessionId) {
    if(S.returnToChanges||S.returnToDrafts)return;
    const footer=document.querySelector('.desk-footer');if(!footer)return;
    append(footer,button('Next business →',()=>guard(nextBusiness),'primary'));
  }

  async function nextBusiness() {
    try {
      const ids=S.list?.matching_ids||[];
      const current=ids.indexOf(S.detail.id);
      const remaining=(S.nextCandidates||ids.slice(current+1)).filter(id=>ids.includes(id));
      for(const id of remaining){
        const investigation=await api(`/api/businesses/${id}/investigation`);
        if(!investigation.can_review)continue;
        S.offset=Math.floor(ids.indexOf(id)/S.limit)*S.limit;
        await openDetail(id);return;
      }
      notice('No further reviewable businesses in this filtered queue.');
      S.detail=null;S.investigation=null;S.selectedChange=null;S.returnToChanges=false;render();
    }catch(error){fail(error);}
  }

  async function undoInvestigation() {
    if(S.reviewSaving)return;
    const inv=S.investigation,id=S.detail?.id;
    if(!inv?.can_review||!id)return;
    const session=inv.undoPending||inv.latest_session;
    if(!session||(!inv.undoPending&&!session.can_undo))return;
    const current=()=>S.investigation===inv&&S.detail?.id===id;
    // Cancel older detail reads and lock the entire transition, including its
    // initial draft flush and dependent reads. A queued event is still retained.
    detailSerial++;
    S.reviewSaving=true;
    const locked=Array.from(document.querySelectorAll('.investigation input,.investigation select,.investigation textarea,.investigation button')).filter(node=>!node.disabled);
    locked.forEach(node=>{node.disabled=true;});
    let sequence=inv.undoPending?.sequence??inv.sequence;
    try {
      let latest,mutationError;
      if(inv.undoPending){
        // A previous request may have committed despite a lost response. Read
        // first; only an explicitly untouched matching session may be retried.
        latest=await deskSnapshot(id);
        if(!current())return;
        if(latest.latest_session?.session_id!==session.session_id)throw new Error('Undo outcome cannot be confirmed for this session. Reload saved work before continuing.');
        if(!latest.latest_session.undone){
          inv.undoPending=null;S.dirty=inv.sequence!==inv.savedSequence||Boolean(S.advancedDirty);
          if(!latest.latest_session.can_undo)throw new Error('The saved review changed. This Undo is no longer available.');
          throw new Error('Undo was not recorded. Your input is retained. Check it before trying Undo again.');
        }
      }else{
        await persistDraft();
        if(!current())return;
        if(Object.keys(inv.edits).length||S.advancedDirty)throw new Error('Save or discard your unfinished question edits before undoing the last review.');
        sequence=inv.sequence;
        inv.undoPending={session_id:session.session_id,sequence};S.dirty=true;
        clearTimeout(draftTimer);
        try{await api(`/api/businesses/${id}/investigation/undo`,{session_id:session.session_id,review_revision:inv.review_revision});}
        catch(error){mutationError=error;}
        if(!current())return;
        latest=await deskSnapshot(id);
        if(!current())return;
        if(latest.latest_session?.session_id!==session.session_id)throw new Error('Undo outcome cannot be confirmed for this session. Reload saved work before continuing.');
        if(!latest.latest_session.undone){
          inv.undoPending=null;S.dirty=inv.sequence!==inv.savedSequence||Boolean(S.advancedDirty);
          throw mutationError||new Error('Undo was not recorded. Your input is retained.');
        }
      }
      let summaryError;
      try{await state();}catch(error){summaryError=error;}
      if(!current())return;
      inv.undoPending=null;
      // A queued input event must never become a saved claim or overwrite the
      // restored server draft. Keep its old context for explicit comparison.
      if(inv.sequence!==sequence||S.advancedDirty){
        inv.conflict=true;inv.error='Last save undone. Newer input is retained here but is not saved. Compare latest saved work before keeping it.';S.dirty=true;
        showDeskError(new Error(inv.error));return;
      }
      inv.undoRestored=true;
      S.detail=latest.business;setupInvestigation(latest);
      render();focusDesk();
      notice(summaryError?'Last save undone and restored draft saved. Workspace summary could not refresh: '+summaryError.message:'Last save undone. Submitted answers are restored as an editable local draft; history and durable exclusions remain.',Boolean(summaryError));
    }catch(error){
      if(current()){
        if(inv.undoPending){S.dirty=true;error=new Error('Undo outcome is not confirmed. Your input is retained here. Check Undo outcome before retrying or closing. '+error.message);}
        showDeskError(error);
      }
      throw error;
    }finally{S.reviewSaving=false;locked.forEach(node=>{node.disabled=false;});}
  }

  const changesState={filter:'pending',prior:false,offset:0,limit:25,generation:0};
  function displayedChangeReasons(item) {
    return item.reasons.filter(r=>changesState.filter==='all'||Boolean(r.seen)===(changesState.filter==='seen'));
  }
  function changeReason(reason) {
    const box=el('article','change-reason');
    append(box,el('h4','',reason.label||human(reason.kind)),el('p','small',reason.seen?'Seen · attention only':'Pending attention'));
    if(reason.source)append(box,el('p','small',`${reason.source} · ${reason.source_id||'Source identity not supplied'}`));
    if(reason.before_batch_id||reason.after_batch_id)append(box,el('p','small',`Previous import ${reason.before_batch_id??'not supplied'} · Incoming import ${reason.after_batch_id??'not supplied'}`));
    if(reason.fields?.length){const table=el('table','change-values');append(table,append(el('thead'),append(el('tr'),el('th','','Field'),el('th','','Previously supplied'),el('th','','Incoming observation'))));const body=el('tbody');for(const value of reason.fields)append(body,append(el('tr'),el('td','',human(value.field)),el('td','',listValue(value.before)||'Not supplied'),el('td','',listValue(value.after)||'Not supplied in this import')));append(box,append(table,body));}
    if(reason.before_observed_at!==undefined||reason.after_observed_at!==undefined)append(box,el('p','small',`Previous observed: ${dateText(reason.before_observed_at)} · Incoming observed: ${dateText(reason.after_observed_at)}`));
    for(const [key,label] of [['before_source_url','Previous source'],['after_source_url','Incoming source']])if(reason[key])append(box,link(reason[key],label+' ↗'));
    for(const key of ['evidence_observed_at','observed_at'])if(reason[key])append(box,el('p','small',`Evidence observed: ${dateText(reason[key])}`));
    if(reason.evidence_age_days!==undefined&&reason.evidence_age_days!==null)append(box,el('p','small',`Evidence age: ${reason.evidence_age_days} days`));
    if(reason.evidence_observed_at===null)append(box,el('p','small','Evidence observation date: unknown'));
    if(reason.evidence_url)append(box,link(reason.evidence_url,'Checked evidence ↗'));
    if(reason.saved_answer)append(box,jsonDetails('Saved answer',reason.saved_answer));
    if(reason.evidence_basis)append(box,jsonDetails('Evidence basis',reason.evidence_basis));
    if(reason.threshold_days!==undefined)append(box,el('p','small',`Reminder window: ${reason.threshold_days} days`));
    for(const warning of reason.warnings||[])append(box,el('p','callout warning',typeof warning==='string'?warning:JSON.stringify(warning)));
    if(reason.details)append(box,jsonDetails('Evidence details',reason.details));
    return box;
  }
  async function renderChanges(){
    const generation=++changesState.generation;
    append(content,heading('Changes','Listing changes and evidence reminders. Marking seen does not save a review or refresh evidence.',button('Go to queue',()=>navigate('queue'))));
    const filters=el('div','filters'),filter=select([['pending','Pending'],['seen','Seen'],['all','All attention']],changesState.filter),prior=check('Only businesses with saved review or draft',changesState.prior);
    filter.addEventListener('change',()=>{changesState.filter=filter.value;changesState.offset=0;render();});prior.input.addEventListener('change',()=>{changesState.prior=prior.input.checked;changesState.offset=0;render();});append(content,append(filters,field('Attention status',filter),prior.label,button('Refresh changes',()=>render())));
    const results=el('div','changes-results');append(content,results);results.setAttribute('aria-live','polite');append(results,el('p','','Loading changes…'));
    try{const data=await api('/api/changes');if(S.view!=='changes'||generation!==changesState.generation)return;results.replaceChildren(el('p','small',`${data.counts.pending} businesses with pending reasons · ${data.counts.seen} with seen reasons`));
      const items=data.items.filter(item=>(!changesState.prior||item.has_review||item.has_draft)&&displayedChangeReasons(item).length);
      changesState.offset=Math.min(changesState.offset,Math.max(0,Math.floor((items.length-1)/changesState.limit)*changesState.limit));
      if(!items.length)append(results,el('p','callout','No attention matches these filters. New businesses remain in the review queue.'));
      for(const item of items.slice(changesState.offset,changesState.offset+changesState.limit)){
        const reasons=displayedChangeReasons(item),body=el('div');append(body,el('p','small',[item.city,item.state].filter(Boolean).join(', ')),el('p','small',`${item.has_review?'Saved review present':'No saved review'} · ${item.has_draft?'Unfinished draft present':'No unfinished draft'}`));
        if(item.excluded_reasons?.length)append(body,el('p','callout warning',`Restrictions retained: ${item.excluded_reasons.map(human).join(' · ')}`));
        for(const reason of reasons){const card=changeReason(reason);append(card,button(reason.seen?'Restore pending':'Mark seen',e=>busy(e.currentTarget,async()=>{await api('/api/changes/seen',{reason_ids:[reason.id],seen:!reason.seen});render();})));const questions=reason.questions?.length?reason.questions:[reason.question||'identity'];for(const question of [...new Set(questions)])if(questionOrder.includes(question)){const saved=item.saved_answers?.[question];if(saved)append(card,append(el('div','saved-change-answer'),el('h5','',`Saved ${questionLabels[question].toLowerCase()} answer`),definition(Object.entries(saved.answers||{}).map(([key,value])=>[human(key),human(value)])),el('p','small',saved.basis_known?(knownObservationDate(saved.evidence_observed_at)?`Evidence observed: ${saved.evidence_observed_at}`:'Evidence observation date: unknown · association known'):'Evidence date/basis unknown'),saved.evidence_url?link(saved.evidence_url,'Saved checked evidence ↗'):null,saved.notes?el('p','small',saved.notes):null));append(card,button(`${item.can_review?'Investigate':'Inspect'} ${questionLabels[question].toLowerCase()}`,()=>guard(async()=>{S.view='queue';S.returnToChanges=true;await openDetail(item.business_id,question,reason.id);})));}
          append(body,card);}
        append(results,panel(item.name,body));
      }
      const previous=button('← Previous',()=>{changesState.offset=Math.max(0,changesState.offset-changesState.limit);render();}),next=button('Next →',()=>{changesState.offset+=changesState.limit;render();});previous.disabled=changesState.offset===0;next.disabled=changesState.offset+changesState.limit>=items.length;append(results,append(el('div','actions'),previous,el('span','small',`${items.length} businesses`),next));
    }catch(error){if(S.view==='changes'&&generation===changesState.generation)results.replaceChildren(el('p','inline-error',error.message),button('Try again',()=>render()));}
  }
  async function reviewUpdatedEvidence(){
    const inv=S.investigation,id=S.detail.id,previousRevision=S.investigation.source_revision;let accepting=false;
    if(!inv.can_review){notice('Contact exclusion retained. This draft is available for inspection only.');return;}
    if(S.advancedDirty){notice('Save or discard advanced edits before reviewing updated evidence. Your input is retained.',true);return;}
    try{await persistDraft();const latest=await deskSnapshot(id);
      if(S.investigation!==inv||S.detail.id!==id)return;
      if(latest.review_revision!==inv.review_revision||latest.draft_revision!==inv.draft_revision){const error=new Error('Saved work changed too. Compare the latest saved work before keeping your answers.');error.status=409;throw error;}
      S.detail=latest.business;refreshEvidenceModel(inv,latest);accepting=true;inv.source_revision=latest.source_revision;inv.current_source_revision=latest.source_revision;inv.source_changed=false;inv.request=null;inv.error=null;inv.sequence++;await persistDraft();notice('Updated source evidence loaded. Your draft answers are retained; inspect the evidence before saving.');render();focusDesk();
    }catch(error){if(accepting){inv.source_revision=previousRevision;inv.source_changed=true;}showDeskError(error);}
  }
  function renderImport() {const im=S.importing;append(content,heading('Import data','Bring a permitted source into your local workspace.'));const outer=el('div','narrow'),steps=el('div','steps');['Choose data','Preview & map','Rights & confirm'].forEach((x,i)=>append(steps,append(el('div',`step ${im.step===i+1?'active':''}`),el('b','',`0${i+1}`),el('span','',x))));append(content,outer);append(outer,steps);if(im.step===1)importChoose(outer);if(im.step===2)importPreview(outer);if(im.step===3)importRights(outer);}
  function invalidateImport(im) {
    im.generation=(im.generation||0)+1;im.preview=null;im.comparison=null;im.fileReading=false;
    return im.generation;
  }
  function importChoose(outer){
    const im=S.importing,form=el('form'),file=input('file');file.accept='.csv,.json,.jsonl,.ndjson,.geojson';
    const adapter=select([['canonical','CSV / JSON / JSONL'],['overture','Overture Places'],['osm','OpenStreetMap']],im.adapter);
    const filename=input('text',im.filename);filename.placeholder='businesses.csv';
    const text=el('textarea');text.rows=8;text.value=im.content;text.spellcheck=false;text.placeholder='Paste UTF-8 CSV, JSON, or JSONL content';
    const advanced=el('details');advanced.open=Boolean(im.content);
    append(advanced,el('summary','','Advanced: paste source text'),el('div','section-gap'),field('Filename / format label',filename),el('div','section-gap'),field('Source content',text));
    adapter.addEventListener('change',()=>{im.adapter=adapter.value;invalidateImport(im);});
    text.addEventListener('input',()=>{im.content=text.value;invalidateImport(im);});
    filename.addEventListener('input',()=>{im.filename=filename.value;invalidateImport(im);});
    file.addEventListener('change',async()=>{
      const generation=invalidateImport(im),f=file.files[0];if(!f)return;
      if(f.size>6*1024*1024){fail(new Error('Choose a file smaller than 6 MiB so the JSON request remains within the local import limit.'));file.value='';return;}
      im.fileReading=true;
      try{
        const source=await f.text();
        if(S.importing!==im||generation!==im.generation)return;
        im.content=source;im.filename=f.name;text.value=source;filename.value=f.name;
        notice(`Selected ${f.name}. Preview it before importing.`);
      }catch(error){if(generation===im.generation)fail(error);}
      finally{if(generation===im.generation)im.fileReading=false;}
    });
    const submit=el('button','primary','Preview data →');submit.type='submit';
    append(form,field('Source format',adapter),el('div','section-gap'),field('Choose a local file',file,'Up to 5,000 source rows per import. Your file stays on this machine.'),advanced,el('p','callout','Preview first. Import requires an explicit source-rights declaration and confirmation.'),submit);
    form.addEventListener('submit',e=>{
      e.preventDefault();
      if(im.fileReading){notice('Wait for the selected file to finish reading before previewing.');return;}
      if(!im.content.trim()||!im.filename.trim()){fail(new Error('Choose a file or provide both a filename and pasted content.'));return;}
      busy(submit,async()=>{
        const generation=im.generation||0,payload={filename:im.filename,content:im.content,adapter:im.adapter};
        im.comparison=null;const preview=await api('/api/import/preview',payload);
        if(S.importing!==im||generation!==(im.generation||0)||im.step!==1||payload.filename!==im.filename||payload.content!==im.content||payload.adapter!==im.adapter){notice('Source changed while previewing. Preview the current input again.');return;}
        im.preview=preview;im.step=2;if(S.view==='import')render();
      },'Reading source…');
    });
    append(outer,panel('Choose your source',form));
  }
  function importPreview(outer){const im=S.importing,p=im.preview,body=el('div');append(body,el('p','',im.filename),append(el('div','import-facts'),...[[p.total_rows,'Source rows'],[p.valid_rows,'Valid rows'],[p.skipped_rows,'Skipped rows']].map(([n,l])=>append(el('div'),el('strong','',n),el('span','small',l)))));if(p.warnings?.length)append(body,el('p','callout warning',p.warnings.join(' · ')));if(p.errors?.length){const ul=el('ul');p.errors.slice(0,30).forEach(e=>append(ul,el('li','',`Row ${e.row}: ${e.message}`)));append(body,el('h3','','Fix these source issues before import'),ul);}
    if(im.adapter==='canonical'){append(body,el('h3','','Map source columns'),el('p','small','Map only fields present in your source. Dates, identities and locations are never inferred.'));const mapping=el('div','mapping-grid section-gap'),controls={};for(const key of ['source_id','name','brand','country','state','city','address','postcode','categories','phones','websites','socials','activity','observed_at','source_url']){const n=select([['','Not mapped'],...p.columns],p.mapping?.[key]||'');controls[key]=n;append(mapping,field(human(key),n));}append(body,mapping,el('div','section-gap'),button('Apply mapping & recheck',e=>busy(e.currentTarget,async()=>{const map={};Object.entries(controls).forEach(([k,n])=>{if(n.value)map[k]=n.value;});const generation=im.generation||0,payload={filename:im.filename,content:im.content,adapter:im.adapter,mapping:map};im.comparison=null;const preview=await api('/api/import/preview',payload);if(S.importing!==im||generation!==(im.generation||0)||im.step!==2||payload.filename!==im.filename||payload.content!==im.content||payload.adapter!==im.adapter){notice('Source or mapping changed while previewing. Recheck the current input.');return;}im.preview=preview;if(S.view==='import')render();},'Rechecking…')));mapping.addEventListener('change',()=>{im.generation=(im.generation||0)+1;im.comparison=null;continueButton.disabled=true;mappingNote.textContent='Mapping changed. Apply mapping & recheck before continuing.';});}
    const mappingNote=el('p','small section-gap');append(body,mappingNote,jsonDetails('Normalized sample · first 5 rows',p.sample));const continueButton=button('Continue to source rights →',()=>{im.step=3;render();},'primary');continueButton.disabled=!p.can_import;append(body,append(el('div','actions section-gap'),button('← Choose data',()=>{im.step=1;render();}),continueButton));if(!p.can_import)append(body,el('p','inline-error','This preview cannot be imported. Correct the source or mapping and preview again.'));append(outer,panel('Check the preview',body));}
  function importRights(outer){const im=S.importing,form=el('form'),grid=el('div','form-grid'),inputs={};for(const [key,label,type]of [['source','Source name','text'],['license','License identifier','text'],['terms_url','Source terms URL','url'],['attribution','Required attribution','text'],['reviewed_at','Rights reviewed on','date']]){const n=input(type,im.rights[key]||'',true);inputs[key]=n;append(grid,field(label,n));n.addEventListener('input',()=>{im.rights[key]=n.value;if(key==='source'){im.comparison=null;renderImportComparison();}});}const notices=el('textarea');notices.value=im.notice;notices.placeholder='Paste the source license, terms, and required notice text.';notices.addEventListener('input',()=>{im.notice=notices.value;});append(form,el('p','callout warning','Declare only rights you have verified. A public listing alone does not establish permission to retain, export or use data for lead discovery.'),grid,el('div','section-gap'),field('Source notice text',notices,'Required for non-synthetic sources. Notice files are generated locally; no paths are opened.'));for(const [key,label]of [['retention_permitted','I have verified permission to retain this source data.'],['export_permitted','I have verified permission to export this source data.'],['lead_discovery_permitted','I have verified permission to use this data for lead discovery.']]){const c=check(label,im.rights[key]===true);c.input.required=true;inputs[key]=c.input;c.input.addEventListener('change',()=>{im.rights[key]=c.input.checked;});append(form,el('div','section-gap'),c.label);}const comparison=el('div','import-comparison');let comparisonSerial=0;
    function renderImportComparison(){comparisonSerial++;comparison.replaceChildren(el('p','small','Compare this file with the workspace before confirming import.'));submit.disabled=!im.comparison;confirm.input.checked=false;confirm.input.disabled=!im.comparison;append(comparison,button('Compare with workspace',e=>busy(e.currentTarget,async()=>{const serial=++comparisonSerial;const source=inputs.source.value;if(!source.trim())throw new Error('Supply a source name before comparing.');const preview=im.preview;const result=await api('/api/import/compare',{preview_id:preview.preview_id,source});if(S.importing!==im||im.preview!==preview||serial!==comparisonSerial||source!==inputs.source.value)return;im.comparison=result;drawImportComparison();},'Comparing…')));}
    function drawImportComparison(){comparison.replaceChildren();const result=im.comparison;append(comparison,el('h3','','Comparison with saved evidence'),definition(Object.entries(result.summary||{}).map(([k,v])=>[human(k),String(v)])),el('p','small','Import retains saved answers and drafts. Incoming listing evidence may change screening.'));for(const reason of result.changes||[])append(comparison,changeReason(reason));if(result.changes_truncated)append(comparison,el('p','small','First 200 changes shown. The complete inbox is available after import.'));submit.disabled=false;confirm.input.disabled=false;}
    const confirm=check(`Import ${im.preview.valid_rows} validated rows from ${im.filename} into this workspace.`);confirm.input.required=true;append(form,comparison,el('div','section-gap'),confirm.label);const submit=el('button','primary','Confirm & import');submit.type='submit';append(form,append(el('div','actions section-gap'),button('← Back to preview',()=>{im.step=2;render();}),submit));form.addEventListener('submit',e=>{e.preventDefault();const rights={};Object.entries(inputs).forEach(([k,n])=>{rights[k]=n.type==='checkbox'?n.checked:n.value;});if(rights.license.toLowerCase()!=='synthetic'&&!notices.value.trim()){fail(new Error('Provide source notice text for this non-synthetic source.'));return;}busy(submit,async()=>{if(!im.comparison)throw new Error('Compare the import with the current workspace before confirming.');let result;try{result=await api('/api/import/commit',{preview_id:im.preview.preview_id,comparison_id:im.comparison.comparison_id,rights,notices:notices.value.trim()?[{name:'source-notice.txt',text:notices.value}]:[]});}catch(error){if(error.status===409){im.comparison=null;renderImportComparison();}throw error;}S.importing={step:1,filename:'',content:'',adapter:'canonical',preview:null,rights:{},notice:''};await state();navigate('changes');notice(`Import complete. ${metricsText(result.metrics)}`);append(content,panel('Import complete',append(el('div','actions'),button('Review changes',()=>navigate('changes'),'primary'),button('Go to queue',()=>navigate('queue')))));},'Importing…').finally(()=>{if(!im.comparison)submit.disabled=true;});});append(outer,panel('Source rights & confirmation',form));if(im.comparison)drawImportComparison();else renderImportComparison();}
  function metricsText(metrics){const labels={records_read:'Rows read',inserted:'New businesses',duplicates:'Matched existing',non_us_or_unsupported_geography:'Skipped geography',suppression_keys_loaded:'Exclusion keys loaded',unsafe_link_values_ignored:'Unsafe links ignored'};return Object.entries(labels).filter(([key])=>metrics?.[key]!==undefined).map(([key,label])=>`${label}: ${metrics[key]}`).join(' · ');}
  function exportPrompt(ids){if(!ids.length){fail(new Error('Select at least one business to export.'));return;}const dialog=el('dialog');append(dialog,el('h2','',`Export ${ids.length} businesses`),el('p','section-gap','The ZIP bundle includes the current evaluated records and their source provenance. Excluded records retain their restrictions.'));const format=select([['csv','CSV + provenance'],['json','JSON + provenance']],'csv');append(dialog,field('Export format',format));const submit=button('Create export',()=>busy(submit,async()=>{const result=await api('/api/exports',{ids,format:format.value});await state();dialog.close();dialog.remove();notice(`Export created: ${result.count} businesses. Available in Exports.`);await downloadExport(result.id,result.filename);if(S.view==='exports')render();},'Creating…'),'primary');append(dialog,append(el('div','actions section-gap'),button('Cancel',()=>{dialog.close();dialog.remove();}),submit));document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();}
  async function downloadExport(id,filename){const blob=await api(`/api/exports/${encodeURIComponent(id)}/download`,undefined,true);const url=URL.createObjectURL(blob);const a=el('a');a.href=url;a.download=filename?.endsWith('.zip')?filename:`${filename||'prospect-export'}.zip`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
  function renderExports(){append(content,heading('Exports','Portable records, with the evidence and source provenance that travel with them.'));const rows=S.state.exports||[];if(!rows.length){append(content,panel('',append(el('div','empty'),el('div','empty-icon','↧'),el('h2','','Your exports will live here'),el('p','','Select businesses in the review queue or explicitly export all filtered results. Every download includes a provenance sidecar.'),button('Go to review queue',()=>navigate('queue'),'primary'))));return;}const list=el('div','export-list');for(const r of [...rows].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))){const item=el('article','panel export-item');append(item,append(el('div'),el('h3','',r.filename),el('p','',`${r.count} businesses · ${String(r.format).toUpperCase()} · ${dateText(r.created_at)}`),r.contains_excluded_records?el('p','readonly-label','Includes excluded records · audit use'):null),button('Download ZIP',e=>busy(e.currentTarget,()=>downloadExport(r.id,r.filename),'Downloading…')));append(list,item);}append(content,list,el('p','callout','Exports are saved in this workspace and remain available after restart. A download never sends messages or clears contact exclusions.'));}
  function renderSettings(){append(content,heading('Settings','Keep the review scope explicit and preserve contact controls.'));const layout=el('div','settings-grid'),config=S.state.config,form=el('form'),regions=input('text',(config.regions||[]).map(r=>r.state).join(', ')),freshness=input('number',config.freshness_days??180,true);freshness.min='0';freshness.max='3650';const advisory=input('number',config.advisory_review_days??180,true);advisory.min='0';advisory.max='3650';append(form,field('Advisory review reminder (days)',advisory,'Identity, website and fit evidence reminders only; does not change qualification.'),el('div','section-gap'));append(form,field('US states',regions,'Comma-separated two-letter codes. Leave blank for all supported US states. Saving preserves city restrictions for existing states.'),el('div','section-gap'),field('Activity freshness window (days)',freshness));const trades=el('div','trade-checks section-gap'),tradeControls={};const allTrades={...(S.state.default_config?.trades||{}),...config.trades};Object.keys(allTrades).forEach(k=>{const c=check(human(k),Object.hasOwn(config.trades||{},k));tradeControls[k]=c.input;append(trades,c.label);});append(form,el('h3','section-gap','Enabled trade groups'),trades);const advanced=el('details'),json=el('textarea');json.rows=12;json.value=JSON.stringify(config,null,2);json.spellcheck=false;const useAdvanced=check('Use this advanced JSON instead of the basic controls');append(advanced,el('summary','','Advanced configuration'),el('div','section-gap'),useAdvanced.label,el('div','section-gap'),field('Full configuration JSON',json));append(form,advanced);const save=el('button','primary section-gap','Save settings');save.type='submit';append(form,save);form.addEventListener('submit',e=>{e.preventDefault();busy(save,async()=>{let updated;if(useAdvanced.input.checked){try{updated=JSON.parse(json.value);}catch{throw new Error('Advanced configuration must be valid JSON.');}}else{const states=[...new Set(regions.value.split(',').map(s=>s.trim().toUpperCase()).filter(Boolean))];if(states.some(s=>!/^[A-Z]{2}$/.test(s)))throw new Error('Use two-letter state codes separated by commas.');updated={...config,regions:states.map(s=>(config.regions||[]).find(r=>r.state===s)||{state:s}),freshness_days:Number(freshness.value),advisory_review_days:Number(advisory.value),trades:Object.fromEntries(Object.entries(tradeControls).filter(([,c])=>c.checked).map(([k])=>[k,allTrades[k]]))};}await api('/api/config',{config:updated});await state();notice('Settings saved. Existing review and contact controls are preserved.');render();},'Saving…');});append(layout,panel('Review scope',form));
    const right=el('div','stack'),exclusionForm=el('form'),file=input('file');file.accept='.csv,.json,.jsonl,.ndjson';let fileContent='',filename='';const loaded=el('p','small');file.addEventListener('change',async()=>{const f=file.files[0];if(!f)return;if(f.size>6*1024*1024){file.value='';fail(new Error('Choose an exclusions file smaller than 6 MiB.'));return;}try{fileContent=await f.text();filename=f.name;loaded.textContent=`Ready: ${f.name}`;}catch(e){fail(e);}});append(exclusionForm,el('p','','Import durable exclusions for previously contacted or suppressed businesses. This adds controls; it does not send anything.'),el('p','callout','Each row needs a phone; a dedicated domain or specific profile URL; source + source_id; or full name + address + city + state. Optional: reason. Use CSV, JSON, or JSONL.'),field('Exclusions file',file),loaded);const submit=el('button','section-gap','Import exclusions');submit.type='submit';append(exclusionForm,submit);exclusionForm.addEventListener('submit',e=>{e.preventDefault();if(!fileContent){fail(new Error('Choose an exclusions file first.'));return;}busy(submit,async()=>{const result=await api('/api/exclusions',{filename,content:fileContent});await state();notice(`Exclusions imported. ${metricsText(result.metrics||result)}`);render();},'Importing…');});append(right,panel('Contact exclusions',exclusionForm));const imports=el('div');if(!S.state.imports?.length)append(imports,el('p','','No sources imported yet.'));for(const r of [...(S.state.imports||[])].sort((a,b)=>String(b.imported_at).localeCompare(String(a.imported_at)))){append(imports,el('h3','',r.source),el('p','small',`Imported ${dateText(r.imported_at)}`));}append(right,panel('Source history',imports));append(layout,right);append(content,layout);}
  state().then(render).catch(e=>{content.replaceChildren(append(el('div','empty'),el('h2','','Unable to open this workspace'),el('p','',e.message),button('Retry',()=>location.reload(),'primary')));});
})();
