'use strict';
// Offline behavioral tests of shipped UI; real-browser acceptance is separate.
(async()=>{
const {default:assert}=await import('node:assert/strict');
const {default:fs}=await import('node:fs');
const {default:path}=await import('node:path');
const {default:vm}=await import('node:vm');
const {test}=await import('node:test');
const crypto=await import('node:crypto');
const clone=v=>JSON.parse(JSON.stringify(v));
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.attributes={};this.value='';this.disabled=false;this.className='';this.classList={toggle(){},add(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  setAttribute(name,v){this.attributes[name]=v;}
  removeAttribute(name){delete this.attributes[name];}
  querySelectorAll(){return this.children.flatMap(n=>n instanceof Element?[n,...n.querySelectorAll()]:[]);}
  querySelector(){return null;}
  closest(){return this;}
  focus(){}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
const all=root=>[root,...root.querySelectorAll()];
const text=root=>all(root).map(n=>n.textContent||'').join('\n');
const find=(root,tag,label)=>{const n=all(root).find(n=>n.tagName===tag&&n.textContent===label);assert.ok(n,`${tag}: ${label}`);return n;};
const click=n=>n.listeners.click({currentTarget:n});
const settle=()=>new Promise(r=>setImmediate(r));
const website={website_status:'no_site_found',evidence_url:'https://example.com/draft',evidence_observed_at:'2026-01-01',notes:'My unfinished website text'};
const activity={activity:'unknown',evidence_url:'https://example.com/activity',evidence_observed_at:'2026-01-02',notes:'Unfinished activity text'};
const saved=(value,notes)=>({answers:{website_status:value},evidence_url:'https://example.com/saved',evidence_observed_at:'2026-01-03',notes,basis_known:true});
function harness(){
  const elements=new Map(),calls=[];
  const document={createElement:tag=>new Element(tag),createTextNode:value=>Object.assign(new Element('text'),{textContent:value}),querySelector:s=>{if(!elements.has(s))elements.set(s,new Element('div'));const n=elements.get(s);n.content='fixture-token';return n;},querySelectorAll:()=>[]};
  const conflict=q=>({baseline_revision:1,current_revision:3,baseline_saved_answer:q==='website'?saved('no_site_found','Earlier check'):null,current_saved_answer:q==='website'?saved('uncertain','Current correction'):null,changed:true,unknown:q!=='website'});
  const business={id:1,name:'Fictional Paint',city:'Austin',state:'TX',review:{website_status:'uncertain'},reviews:[],observations:[],excluded_reasons:[]};
  const server={snapshot:{business,review_revision:3,draft_revision:2,draft:{step:0,mode:'workspace',questions:{website:clone(website),activity:clone(activity)}},draft_review_revisions:{website:1,activity:null},draft_conflicts:{website:conflict('website'),activity:conflict('activity')},review_changed:true,review_context_unknown:true,source_revision:'new-source',draft_source_revision:'old-source',source_changed:true,can_review:true,questions:{website:{...website,website_status:'uncertain'}},saved_answers:{website:saved('uncertain','Current correction')},question_history:{}},items:[{id:1,name:'Fictional Paint',city:'Austin',state:'TX',questions:['website','activity'],can_review:true,source_changed:true,review_changed:true,review_context_unknown:true},{id:2,name:'Fictional Held',city:'Boston',state:'MA',questions:['fit'],can_review:false,source_changed:false,review_changed:false,review_context_unknown:false}],total:2,failDraft:false,failList:false,failRead:false};
  const response=v=>({ok:true,status:200,json:async()=>clone(v)});
  const fetch=async(route,options)=>{
    const body=options.body?JSON.parse(options.body):undefined;calls.push({route,body});
    if(route.startsWith('/api/drafts')){if(server.listRead)return server.listRead(route);if(server.failList)throw Error('List failed');return response({total:server.total,items:server.items,offset:0,limit:25});}
    if(route.endsWith('/draft')&&body){
      if(server.failDraft)throw Error('Draft failed');
      server.snapshot.draft=clone(body.draft);server.snapshot.draft_revision++;
      for(const [q,choice] of Object.entries(body.resolve_questions||{})){
        assert.equal(body.resolve_review_revision,server.snapshot.review_revision);
        if(choice==='current'){delete server.snapshot.draft.questions[q];delete server.snapshot.draft_conflicts[q];delete server.snapshot.draft_review_revisions[q];}
        else {server.snapshot.draft_conflicts[q]={...server.snapshot.draft_conflicts[q],baseline_revision:3,changed:false,unknown:false};server.snapshot.draft_review_revisions[q]=3;}
      }
      server.snapshot.review_changed=Object.values(server.snapshot.draft_conflicts).some(c=>c.changed||c.unknown);
      server.snapshot.draft_source_revision=body.source_revision;
      server.snapshot.source_changed=body.source_revision!==server.snapshot.source_revision;
      if(server.onDraft)await server.onDraft(body);
      return response(server.snapshot);
    }
    if(route.endsWith('/investigation')&&body){
      for(const q of Object.keys(body.questions))assert.equal(Boolean(server.snapshot.draft_conflicts[q]?.changed),false);
      server.snapshot.review_revision++;server.snapshot.draft_revision++;
      for(const q of Object.keys(body.questions)){delete server.snapshot.draft.questions[q];delete server.snapshot.draft_conflicts[q];delete server.snapshot.draft_review_revisions[q];}
      for(const c of Object.values(server.snapshot.draft_conflicts)){c.current_revision=server.snapshot.review_revision;c.changed=true;}
      server.snapshot.review_changed=Object.keys(server.snapshot.draft_conflicts).length>0;
      return response({...server.snapshot,session_id:'fixture-session',review:{}});
    }
    if(route.endsWith('/investigation')){if(server.failRead)throw Error('Refresh failed');return response(server.snapshot);}
    if(route==='/api/state')return response({counts:{total:2},version:'fixture'});
    return response({records:[],matching_ids:[],facets:{},total:0});
  };
  const context={document,fetch,window:{addEventListener(){}},Node:Element,URL,URLSearchParams,crypto,setTimeout:()=>1,clearTimeout(){},queueMicrotask(){},console};
  vm.createContext(context);
  let source=fs.readFileSync(path.join(path.dirname(path.resolve(process.argv[1])),'../prospect/web/app.js'),'utf8');
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'').replace(/\}\)\(\);\s*$/,'globalThis.ui={S,draftsState,setupInvestigation,renderUnfinished,loadDrafts,resumeDraft,renderDraftComparison,resolveQuestion,renderInvestigation,renderQuestion,commitInvestigation,discardQuestion,entryFor,navigate,reviewUpdatedEvidence};\n})();');
  vm.runInContext(source,context,{filename:'app.js'});
  const ui=context.ui;ui.S.state={counts:{total:2}};ui.S.view='queue';ui.S.detail=clone(business);ui.setupInvestigation(clone(server.snapshot));
  return {ui,server,calls,elements,response};
}

test('unfinished list labels matching totals and page statuses truthfully',async()=>{
  const {ui,elements,calls}=harness();ui.S.view='unfinished';ui.renderUnfinished();await settle();
  const output=text(elements.get('#content'));
  assert.match(output,/2 matching unfinished businesses/);assert.match(output,/On this page: 2 drafts/);
  assert.match(output,/1 unknown review context/);assert.match(output,/1 read-only/);
  assert.match(output,/Contact exclusion · read-only/);assert.match(output,/Inspect unfinished work/);
  assert.match(calls[0].route,/q=&offset=0&limit=25/);
  assert.equal(calls.some(c=>c.body),false);
});

test('resuming starts at first actual draft question and preserves search/page on return',async()=>{
  const {ui,elements,server}=harness();ui.draftsState.q='Paint';ui.draftsState.offset=25;server.total=50;
  await ui.resumeDraft({id:1,questions:['website','activity']});
  assert.equal(ui.S.investigation.step,1);assert.equal(ui.S.returnToDrafts,true);
  const desk=ui.renderInvestigation(ui.S.detail);await click(find(desk,'button','← Back to unfinished work'));await settle();
  assert.equal(ui.draftsState.q,'Paint');assert.equal(ui.S.view,'unfinished');
  assert.equal(ui.draftsState.offset,25);
  assert.match(text(elements.get('#content')),/Unfinished work/);
});

test('list ignores superseded delayed reads and keeps failed searches retryable',async()=>{
  const {ui,server,response}=harness();ui.S.view='unfinished';let finish;
  server.listRead=()=>new Promise(r=>{finish=r;});const target=new Element('div');
  const pending=ui.loadDrafts(target);server.listRead=null;server.total=0;server.items=[];ui.draftsState.q='none';await ui.loadDrafts(target);
  finish(response({total:1,items:[{id:1,name:'Old response',questions:['website']}]}));await pending;
  assert.match(text(target),/0 matching unfinished/);assert.doesNotMatch(text(target),/Old response/);
  server.failList=true;await ui.loadDrafts(target);assert.match(text(target),/List failed/);
  assert.equal(ui.draftsState.q,'none');server.failList=false;await click(find(target,'button','Retry loading unfinished work'));assert.match(text(target),/0 matching/);
});

test('comparison shows baseline current draft and separate truthful evidence',()=>{
  const {ui}=harness();const output=text(ui.renderDraftComparison('website'));
  assert.match(output,/Review context for this draft/);assert.match(output,/Earlier check/);
  assert.match(output,/Current correction/);assert.match(output,/My unfinished website text/);
  assert.match(output,/answer may be the same/);assert.match(output,/do not save a review or accept changed source evidence/);
  assert.match(text(ui.renderDraftComparison('activity')),/baseline is unknown/);
  assert.equal(all(ui.renderDraftComparison('website')).some(n=>n.tagName==='pre'),false);
});

test('keep resolves only selected question and preserves source acceptance and other context',async()=>{
  const {ui,calls}=harness();await ui.resolveQuestion('website','keep');
  const write=calls.find(c=>c.body?.resolve_questions);
  assert.deepEqual(write.body.resolve_questions,{website:'keep'});assert.equal(write.body.resolve_review_revision,3);
  assert.equal(ui.S.investigation.draft_conflicts.website.changed,false);
  assert.equal(ui.S.investigation.draft_conflicts.activity.unknown,true);
  assert.equal(ui.S.investigation.edits.website.notes,website.notes);assert.equal(ui.S.investigation.edits.activity.notes,activity.notes);
  assert.equal(ui.S.investigation.source_changed,true);
});

test('use current removes only that draft question; defer performs no write',async()=>{
  const {ui,calls}=harness();await click(find(ui.renderDraftComparison('website'),'button','Defer this question'));
  assert.equal(calls.length,0);assert.equal(ui.S.investigation.draft_conflicts.website.changed,true);
  await ui.resolveQuestion('website','current');
  assert.equal(ui.S.investigation.edits.website,undefined);assert.equal(ui.entryFor('website').website_status,'uncertain');
  assert.equal(ui.S.investigation.edits.activity.notes,activity.notes);
  assert.equal(ui.S.investigation.draft_conflicts.activity.unknown,true);
});

test('failed question resolution retains all text and can be retried',async()=>{
  const {ui,server}=harness();server.failDraft=true;
  await assert.rejects(ui.resolveQuestion('website','current'),/Draft failed/);
  assert.equal(ui.S.investigation.edits.website.notes,website.notes);assert.equal(ui.S.investigation.edits.activity.notes,activity.notes);
  assert.equal(ui.S.navigating,false);server.failDraft=false;await ui.resolveQuestion('website','current');
  assert.equal(ui.S.investigation.edits.website,undefined);
});

for(const choice of ['keep','current'])test(`newer input during ${choice} resolution is retained and remains unsaved`,async()=>{
  const {ui,server}=harness();server.onDraft=async body=>{if(body.resolve_questions){ui.S.investigation.edits.website.notes='Typed while resolving';ui.S.investigation.sequence++;}};
  await ui.resolveQuestion('website',choice);
  assert.equal(ui.S.investigation.edits.website.notes,'Typed while resolving');assert.equal(ui.S.dirty,true);
  assert.notEqual(ui.S.investigation.sequence,ui.S.investigation.savedSequence);
});

test('contact excluded draft is inspectable without resolution or review writes',async()=>{
  const {ui,calls}=harness();ui.S.investigation.can_review=false;
  assert.doesNotMatch(text(ui.renderDraftComparison('website')),/Keep my draft answer/);
  assert.match(text(ui.renderDraftComparison('website')),/inspection only/);
  await ui.resolveQuestion('website','keep');await ui.reviewUpdatedEvidence();await ui.commitInvestigation(['website']);assert.equal(calls.length,0);
});

test('accepting changed source evidence does not resolve question review context',async()=>{
  const {ui,calls}=harness();await ui.reviewUpdatedEvidence();
  assert.equal(ui.S.investigation.source_changed,false);
  assert.equal(ui.S.investigation.draft_conflicts.website.changed,true);
  assert.equal(ui.S.investigation.draft_conflicts.activity.unknown,true);
  assert.equal(calls.some(c=>c.body?.resolve_questions),false);
});

test('partial commit uses persisted remaining baselines without a remainder draft write',async()=>{
  const {ui,server,calls}=harness();await ui.resolveQuestion('website','keep');
  ui.S.investigation.source_changed=false;ui.S.investigation.source_revision='new-source';server.snapshot.source_changed=false;server.snapshot.draft_source_revision='new-source';
  calls.length=0;server.failRead=true;
  await ui.commitInvestigation(['website']);
  assert.equal(calls.filter(c=>c.route.endsWith('/draft')).length,0);
  assert.equal(ui.S.investigation.edits.website,undefined);assert.equal(ui.S.investigation.edits.activity.notes,activity.notes);
  assert.equal(ui.S.investigation.draft_review_revisions.activity,null);assert.equal(ui.S.investigation.draft_conflicts.activity.unknown,true);
  const before=calls.length;await ui.commitInvestigation(['activity']);assert.equal(calls.length,before);
});

test('discard confirmation is bounded to one question and cancel retains it',async()=>{
  const {ui,elements,calls}=harness();ui.discardQuestion('website');
  assert.equal(elements.get('#confirm-dialog').open,true);assert.match(elements.get('#confirm-dialog p').textContent,/Only this question/);
  await click(elements.get('#keep-editing'));assert.equal(ui.S.investigation.edits.website.notes,website.notes);assert.equal(calls.length,0);
  ui.discardQuestion('website');await click(elements.get('#discard-editing'));await settle();
  assert.equal(ui.S.investigation.edits.website,undefined);assert.equal(ui.S.investigation.edits.activity.notes,activity.notes);
});
})();
