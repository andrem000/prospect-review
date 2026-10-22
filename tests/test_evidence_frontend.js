'use strict';
// Execute the shipped UI offline; these DOM probes do not replace a browser.
(async()=>{
const {default:assert}=await import('node:assert/strict');
const {default:fs}=await import('node:fs');
const {default:path}=await import('node:path');
const {default:vm}=await import('node:vm');
const {test}=await import('node:test');
const crypto=await import('node:crypto');
const clone=value=>JSON.parse(JSON.stringify(value));
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.attributes={};this.className='';this.value='';this.classList={toggle(){},add(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(name,value){this.attributes[name]=value;}
  removeAttribute(name){delete this.attributes[name];}
  addEventListener(name,fn){this.listeners[name]=fn;}
  querySelectorAll(){return this.children.flatMap(n=>n instanceof Element?[n,...n.querySelectorAll()]:[]);}
  querySelector(){return null;}
  closest(){return this;}
  focus(){}
}
const all=root=>[root,...root.querySelectorAll()];
const text=root=>all(root).map(n=>n.textContent||'').join('\n');
const find=(root,tag,label)=>{const node=all(root).find(n=>n.tagName===tag&&n.textContent===label);assert.ok(node,`${tag}: ${label}`);return node;};
const click=node=>node.listeners.click({currentTarget:node});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const entry={website_status:'no_site_found',notes:'Historical guided notes',evidence_url:'https://example.com/old',evidence_observed_at:'2026-01-01'};
function harness(){
  const elements=new Map(),calls=[];
  const document={createElement:tag=>new Element(tag),createTextNode:value=>Object.assign(new Element('text'),{textContent:value}),querySelector:selector=>{if(!elements.has(selector))elements.set(selector,new Element('div'));const node=elements.get(selector);node.content='test-token';return node;},querySelectorAll:()=>[]};
  const corrected={website_status:'uncertain',candidate_website:'https://candidate.example.com',notes:'',evidence_url:'',evidence_observed_at:''};
  const reason={id:'change:one',question:'website',questions:['website'],kind:'source_changed',label:'Exact source correction',seen:false,source:'Demo directory',source_id:'one',before_batch_id:1,after_batch_id:3,before_observed_at:'2026-01-01',after_observed_at:'2025-01-01',before_source_url:'https://example.com/before',after_source_url:'https://example.com/after',fields:[{field:'websites',before:['https://old.example.com'],after:[]}],warnings:['Incoming evidence is older.']};
  const observation=(name,batch,observed,websites)=>({name,batch_id:batch,observed_at:observed,websites,source:'Demo directory',source_id:'one',source_url:'https://example.com/listing'});
  const first=observation('Original A',1,'2026-01-01',['https://old.example.com']),middle=observation('Middle B',2,'2026-04-01',['https://middle.example.com']),last=observation('Latest imported A',3,'2025-01-01',[]);
  const business={id:1,name:'Fictional Paint',review:{website_status:'uncertain'},observations:[first,middle,last],websites:['https://old.example.com'],excluded_reasons:[],reviews:[]};
  const server={snapshot:{business,review_revision:3,draft_revision:2,source_revision:'source-b',draft_source_revision:'source-b',source_changed:false,draft:null,questions:{website:corrected},question_history:{website:entry},saved_answers:{website:{answers:{website_status:'uncertain',verified_website:'',candidate_website:corrected.candidate_website},basis_known:false,evidence_url:null,notes:null,evidence_observed_at:null}},can_review:true,evidence:{source_groups:[{source:'Demo directory',source_id:'one',latest:last,history:[middle,first],observation_count:3}],change_reasons:[reason],retained_signals:[{kind:'website',value:'https://old.example.com',explanation:'A retained earlier website signal still affects screening.',source:'Demo directory',batch_id:1}]}},failDraft:false,failRead:false};
  const response=(value,status=200)=>({ok:status===200,status,json:async()=>clone(value)});
  const fetch=async(route,options)=>{const body=options.body?JSON.parse(options.body):undefined;calls.push({route,body});
    if(route.endsWith('/draft')){if(server.failDraft)throw Error('Injected autosave failure');server.snapshot.draft=clone(body.draft);server.snapshot.draft_revision++;server.snapshot.draft_source_revision=body.source_revision;return response({...server.snapshot,source_changed:body.source_revision!==server.snapshot.source_revision});}
    if(route.endsWith('/investigation')&&body){server.snapshot.review_revision++;server.snapshot.draft_revision++;server.snapshot.draft=null;Object.assign(server.snapshot.questions,clone(body.questions));for(const [key,answer] of Object.entries(body.questions))server.snapshot.saved_answers[key]={answers:{website_status:answer.website_status},basis_known:true,notes:answer.notes,evidence_url:answer.evidence_url,evidence_observed_at:answer.evidence_observed_at};if(server.failAfterCommit)server.failRead=true;return response({review_revision:server.snapshot.review_revision,draft_revision:server.snapshot.draft_revision,session_id:'saved-session',review:{}});}
    if(route.endsWith('/investigation')){if(server.onRead)server.onRead();if(server.failRead)throw Error('Injected read failure');return response(server.snapshot);}
    if(route==='/api/changes')return response({items:[],counts:{pending:0,seen:0}});
    if(route==='/api/state')return response({counts:{total:1},version:'fixture'});
    if(route.startsWith('/api/research'))return response({items:[]});
    return response({records:[],matching_ids:[],facets:{},total:0});
  };
  const context={document,fetch,window:{addEventListener(){}},crypto,Node:Element,URL,setTimeout:()=>1,clearTimeout(){},queueMicrotask(){},console};vm.createContext(context);
  let source=fs.readFileSync(path.join(path.dirname(path.resolve(process.argv[1])),'../prospect/web/app.js'),'utf8');
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'').replace(/\}\)\(\);\s*$/,'globalThis.ui={S,setupInvestigation,renderEvidence,renderQuestion,renderInvestigation,entryFor,openDetail,navigate,changesState,reviewUpdatedEvidence,showDeskError,persistDraft,commitInvestigation};\n})();');
  vm.runInContext(source,context,{filename:'app.js'});
  const ui=context.ui;ui.S.view='queue';ui.S.state={counts:{total:1}};ui.S.detail=clone(business);ui.setupInvestigation(clone(server.snapshot));
  return {ui,server,calls,elements,reason};
}

test('current correction, draft and superseded guided history remain distinct',()=>{
  const {ui}=harness();
  assert.equal(ui.entryFor('website').website_status,'uncertain');
  assert.equal(ui.entryFor('website').candidate_website,'https://candidate.example.com');
  ui.S.investigation.edits.website={...entry,notes:'Unfinished private draft'};
  const area=ui.renderEvidence(ui.S.detail,'website'),output=text(area);
  assert.match(output,/Effective saved answer/);assert.match(output,/uncertain/);assert.match(output,/Evidence basis unknown/);
  assert.match(output,/Unfinished draft · separate from effective answer/);assert.match(output,/Unfinished private draft/);
  assert.doesNotMatch(output,/Historical guided notes/);
  const history=all(area).find(n=>n.tagName==='details'&&n.children[0].textContent==='Earlier guided answer · superseded history');
  history.open=true;history.listeners.toggle();assert.match(text(history),/Historical guided notes/);
  assert.equal(ui.S.investigation.saved_answers.website.answers.website_status,'uncertain');
});

test('notes-only input starts from effective correction and never inherits historical evidence',()=>{
  const {ui}=harness();const form=ui.renderQuestion(ui.S.detail,'website');
  const notes=all(form).find(n=>n.tagName==='textarea');notes.value='Only notes changed';notes.listeners.input();
  assert.equal(ui.S.investigation.edits.website.website_status,'uncertain');
  assert.equal(ui.S.investigation.edits.website.candidate_website,'https://candidate.example.com');
  assert.equal(ui.S.investigation.edits.website.evidence_url,'');
  assert.equal(ui.S.investigation.edits.website.evidence_observed_at,'');
});

test('known association with a missing or invalid date is explicitly undated',()=>{
  const {ui}=harness();const saved=ui.S.investigation.saved_answers.website;saved.basis_known=true;
  for(const date of [null,'2026-02-30','2099-01-01']){saved.evidence_observed_at=date;const output=text(ui.renderEvidence(ui.S.detail,'website'));assert.match(output,/Evidence association known/);assert.match(output,/Evidence observation date: unknown/);}
});

test('latest imported defaults stay bounded while complete source history opens lazily once',()=>{
  const {ui}=harness();const group=ui.S.investigation.evidence.source_groups[0];
  group.history=Array.from({length:150},(_,i)=>({...group.history[i%2],name:`Earlier ${i}`}));group.observation_count=151;
  const area=ui.renderEvidence(ui.S.detail,'website');assert.equal(all(area).filter(n=>n.className==='evidence-card').length,1);
  assert.match(text(area),/Latest imported A/);assert.doesNotMatch(text(area),/Earlier 0/);assert.match(text(area),/Import batch: 3 · Observed by source: 2025-01-01/);
  assert.match(text(area),/absence is unverified/);assert.match(text(area),/retained earlier website signal still affects screening/);
  const history=all(area).find(n=>n.className==='source-history');assert.equal(history.tagName,'details');assert.equal(history.children[0].tagName,'summary');
  history.open=true;history.listeners.toggle();assert.equal(all(history).filter(n=>n.className==='evidence-card').length,150);
  history.open=false;history.listeners.toggle();history.open=true;history.listeners.toggle();assert.equal(all(history).filter(n=>n.className==='evidence-card').length,150);
});

test('disagreeing source aliases remain separate and inert source content stays text',()=>{
  const {ui}=harness();const group=clone(ui.S.investigation.evidence.source_groups[0]);group.source='Second source';group.source_id='two';group.latest.name='<script>untrusted</script>';group.latest.websites=['https://second.example.com'];group.history=[];
  ui.S.investigation.evidence.source_groups.push(group);const area=ui.renderEvidence(ui.S.detail,'website');
  assert.equal(all(area).filter(n=>n.className==='source-group').length,2);assert.match(text(area),/<script>untrusted<\/script>/);assert.equal(all(area).some(n=>n.tagName==='script'),false);
});

test('selected reason uses the current snapshot and preserves Changes filters and page',async()=>{
  const {ui,server,calls}=harness();ui.S.returnToChanges=true;ui.changesState.filter='seen';ui.changesState.prior=true;ui.changesState.offset=25;
  await ui.openDetail(1,'website','change:one');
  const output=text(ui.renderEvidence(ui.S.detail,'website'));assert.match(output,/Exact source correction/);assert.match(output,/Previously supplied/);assert.match(output,/https:\/\/old.example.com/);assert.match(output,/Not supplied in this import/);assert.match(output,/Incoming evidence is older/);
  assert.equal(ui.S.selectedChange.reason_id,'change:one');assert.equal(ui.changesState.offset,25);
  assert.equal(calls.some(c=>c.route==='/api/changes/seen'),false);
  server.snapshot.evidence.change_reasons=[];await ui.openDetail(1,'website');
  assert.match(text(ui.renderEvidence(ui.S.detail,'website')),/Selected change is no longer current/);
  ui.navigate('changes');assert.equal(ui.changesState.filter,'seen');assert.equal(ui.changesState.prior,true);assert.equal(ui.changesState.offset,25);assert.equal(ui.S.selectedChange,null);
});

test('opening another business clears unrelated selected-change context',async()=>{
  const {ui,server}=harness();ui.S.selectedChange={business_id:1,reason_id:'change:one'};ui.S.returnToChanges=true;
  server.snapshot.business.id=2;await ui.openDetail(2,'website');assert.equal(ui.S.selectedChange,null);
});

for(const canReview of [true,false])test(`read-only evidence refresh retains question and mode without a draft (can_review=${canReview})`,async()=>{
  const {ui,server,calls}=harness();
  server.snapshot.can_review=canReview;ui.S.investigation.can_review=canReview;
  assert.equal(server.snapshot.draft,null);
  ui.S.investigation.step=1;ui.S.investigation.mode='guided';
  ui.S.selectedChange={business_id:1,reason_id:'change:one'};ui.S.returnToChanges=true;
  const area=ui.renderEvidence(ui.S.detail,'website');
  await click(find(area,'button','Refresh saved evidence'));await settle();
  assert.equal(ui.S.investigation.step,1);assert.equal(ui.S.investigation.mode,'guided');
  assert.equal(ui.S.investigation.can_review,canReview);
  assert.deepEqual(clone(ui.S.selectedChange),{business_id:1,reason_id:'change:one'});
  assert.equal(ui.S.returnToChanges,true);
  assert.equal(calls.some(c=>c.body!==undefined),false,'refresh must remain read-only when there are no edits');
});

test('source refresh updates all evidence summaries while retaining a draft and acceptance guard',async()=>{
  const {ui,server}=harness();ui.S.investigation.edits.activity={activity:'active',notes:'Keep activity draft'};ui.S.investigation.source_revision='source-a';ui.S.investigation.source_changed=true;
  server.snapshot.saved_answers.website.answers.website_status='site_found';server.snapshot.question_history.website.notes='New history';server.snapshot.evidence.change_reasons=[];
  await ui.reviewUpdatedEvidence();
  assert.equal(ui.S.investigation.saved_answers.website.answers.website_status,'site_found');assert.equal(ui.S.investigation.question_history.website.notes,'New history');assert.deepEqual(clone(ui.S.investigation.evidence.change_reasons),[]);
  assert.equal(ui.S.investigation.edits.activity.notes,'Keep activity draft');assert.equal(ui.S.investigation.source_revision,'source-b');
});

test('failed source acceptance retains draft input and old source token for retry',async()=>{
  const {ui,server}=harness();ui.S.investigation.edits.website={...entry,notes:'Retain after failure'};ui.S.investigation.source_revision='source-a';ui.S.investigation.source_changed=true;server.failDraft=true;
  await ui.reviewUpdatedEvidence();assert.equal(ui.S.investigation.source_revision,'source-a');assert.equal(ui.S.investigation.source_changed,true);assert.equal(ui.S.investigation.edits.website.notes,'Retain after failure');
});

test('keeping retained answers after a review conflict refreshes all comparison models',async()=>{
  const {ui,server,elements}=harness();ui.S.investigation.edits.activity={activity:'active',notes:'Retain comparison draft'};
  server.snapshot.review_revision=4;server.snapshot.saved_answers.website.answers.website_status='site_found';server.snapshot.question_history.website.notes='Latest history';server.snapshot.evidence.change_reasons=[];
  const error=Error('Review conflict');error.status=409;ui.showDeskError(error);
  const box=elements.get('[data-draft-error]');await click(find(box,'button','Compare latest saved work'));
  await click(find(box,'button','Keep my answers as the draft'));
  assert.equal(ui.S.investigation.saved_answers.website.answers.website_status,'site_found');assert.equal(ui.S.investigation.question_history.website.notes,'Latest history');assert.deepEqual(clone(ui.S.investigation.evidence.change_reasons),[]);assert.equal(ui.S.investigation.edits.activity.notes,'Retain comparison draft');
  await settle();
});
test('selected reason remains attached across an explicit guided save without acknowledgement',async()=>{
  const {ui,calls}=harness();ui.S.selectedChange={business_id:1,reason_id:'change:one'};ui.S.returnToChanges=true;
  ui.S.investigation.edits.website={...entry,website_status:'uncertain',notes:'New check'};
  await ui.commitInvestigation(['website']);
  assert.equal(ui.S.selectedChange.reason_id,'change:one');assert.match(text(ui.renderEvidence(ui.S.detail,'website')),/Exact source correction/);
  assert.equal(calls.some(c=>c.route==='/api/changes/seen'),false);assert.equal(ui.S.investigation.saved_answers.website.notes,'New check');
});

test('successful save with failed refresh keeps its saved summary and labels reason context unavailable',async()=>{
  const {ui,server}=harness();server.failAfterCommit=true;ui.S.selectedChange={business_id:1,reason_id:'change:one'};ui.S.returnToChanges=true;
  ui.S.investigation.edits.website={...entry,website_status:'uncertain',notes:'Authoritative saved check'};
  await ui.commitInvestigation(['website']);
  assert.equal(ui.S.investigation.saved_answers.website.notes,'Authoritative saved check');assert.equal(ui.S.investigation.saved_answers.website.basis_known,true);
  assert.match(text(ui.renderEvidence(ui.S.detail,'website')),/Selected change context could not be refreshed/);
  assert.doesNotMatch(text(ui.renderEvidence(ui.S.detail,'website')),/Selected change is no longer current/);
});


test('delayed saved-evidence refresh preserves text typed after its initial flush',async()=>{
  const {ui,server,calls}=harness();
  server.snapshot.draft={step:1,mode:'workspace',questions:{website:{...entry,notes:'Previously saved draft'}}};
  ui.setupInvestigation(clone(server.snapshot));
  const area=ui.renderEvidence(ui.S.detail,'website');
  server.onRead=()=>{
    const form=ui.renderQuestion(ui.S.detail,'website');
    const notes=all(form).find(n=>n.tagName==='textarea');
    notes.value='New text while refresh is pending';notes.listeners.input();
  };
  await click(find(area,'button','Refresh saved evidence'));
  assert.equal(ui.S.investigation.edits.website.notes,'New text while refresh is pending');
  assert.equal(ui.S.dirty,true);assert.equal(calls.filter(c=>c.route.endsWith('/draft')).length,0);
  server.onRead=null;await ui.persistDraft();
  assert.equal(server.snapshot.draft.questions.website.notes,'New text while refresh is pending');
});

test('failed refresh keeps input and allows retry without leaving navigation locked',async()=>{
  const {ui,server}=harness();
  ui.S.investigation.edits.website={...entry,notes:'Retain failed refresh'};
  server.failRead=true;
  await click(find(ui.renderEvidence(ui.S.detail,'website'),'button','Refresh saved evidence'));
  assert.equal(ui.S.investigation.edits.website.notes,'Retain failed refresh');assert.equal(ui.S.navigating,false);
  server.failRead=false;
  server.snapshot.draft={step:1,mode:'workspace',questions:{website:{...entry,notes:'Retain failed refresh'}}};
  await click(find(ui.renderEvidence(ui.S.detail,'website'),'button','Refresh saved evidence'));
  assert.equal(ui.S.investigation.edits.website.notes,'Retain failed refresh');
});

test('resumed changed-review draft blocks commit until an explicit comparison resolves it',async()=>{
  const {ui,server,calls,elements}=harness();
  Object.assign(server.snapshot,{draft:{step:1,mode:'workspace',questions:{website:entry}},draft_review_revision:1,review_changed:true});
  ui.setupInvestigation(clone(server.snapshot));
  await ui.commitInvestigation(['website']);
  assert.equal(calls.some(c=>c.route.endsWith('/investigation')&&c.body),false);
  const box=elements.get('[data-draft-error]');
  await click(find(box,'button','Compare latest saved work'));
  await click(find(box,'button','Keep my answers as the draft'));
  const write=calls.find(c=>c.route.endsWith('/draft'));
  assert.equal(write.body.resolve_review_revision,3);
  assert.equal(write.body.source_revision,'source-b');
  assert.equal(ui.S.investigation.edits.website.website_status,'no_site_found');
});

})();
