'use strict';
// Execute shipped UI callbacks with deterministic delayed local HTTP fixtures.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {test} from 'node:test';
import crypto from 'node:crypto';
const testDirectory=path.dirname(path.resolve(process.argv[1]));
const copy=v=>JSON.parse(JSON.stringify(v));
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.attributes={};this.disabled=false;this.value='';this.classList={toggle(){},add(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(k,fn){this.listeners[k]=fn;}
  setAttribute(k,v){this.attributes[k]=v;}
  removeAttribute(k){delete this.attributes[k];}
  querySelectorAll(){return this.children.flatMap(n=>n instanceof Element?[n,...n.querySelectorAll()]:[]);}
  querySelector(){return null;}
  closest(){return this;}
  focus(){}
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=()=>new Promise(r=>setImmediate(r));
const answer={website_status:'no_site_found',notes:'Restored submitted text',evidence_url:'https://example.com/evidence',evidence_observed_at:'2026-01-01'};
function harness(){
  const nodes=new Map(),calls=[],events={},root=new Element('div');
  const document={createElement:t=>new Element(t),createTextNode:t=>Object.assign(new Element('text'),{textContent:t}),querySelector:s=>{if(!nodes.has(s))nodes.set(s,new Element('div'));const n=nodes.get(s);n.content='token';return n;},querySelectorAll:s=>s.includes('.investigation')?root.querySelectorAll():[]};
  const snapshot={business:{id:1,name:'Fictional Paint',review:{},reviews:[],observations:[],excluded_reasons:[]},review_revision:1,draft_revision:0,source_revision:'source',can_review:true,draft:null,questions:{website:copy(answer)},latest_session:{session_id:'session-1',can_undo:true,undone:false}};
  const server={snapshot,override:null},response=v=>({ok:true,status:200,json:async()=>copy(v?.business&&!v.undo_outcome?{...v,undo_outcome:{session_id:v.latest_session?.session_id,found:true,undone:v.latest_session?.undone,can_undo:v.latest_session?.can_undo,undo_result:v.latest_session?.undo_result??null}}:v)});
  function applyUndo(){snapshot.review_revision++;snapshot.draft_revision++;snapshot.latest_session={session_id:'session-1',can_undo:false,undone:true};snapshot.draft={mode:'workspace',step:1,questions:{website:copy(answer)}};}
  const fetch=async(route,opts)=>{const body=opts.body?JSON.parse(opts.body):undefined;calls.push({route,body});if(server.override){const v=server.override(route,body);if(v)return v;}if(route.endsWith('/undo')){assert.equal(snapshot.latest_session.undone,false,'No duplicate Undo');applyUndo();return response({});}if(route.split('?')[0].endsWith('/investigation'))return response(snapshot);if(route==='/api/state')return response({counts:{},version:'fixture'});if(route.endsWith('/draft')){snapshot.draft=copy(body.draft);snapshot.draft_revision++;return response(snapshot);}return response({records:[],matching_ids:[],total:0,facets:{}});};
  const context={document,window:{addEventListener:(k,fn)=>events[k]=fn},fetch,Node:Element,URL,URLSearchParams,crypto,setTimeout:()=>1,clearTimeout(){},queueMicrotask(){},console};
  vm.createContext(context);
  let source=fs.readFileSync(path.join(testDirectory,'../prospect/web/app.js'),'utf8');
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'').replace(/\}\)\(\);\s*$/,'globalThis.ui={S,setupInvestigation,renderQuestion,undoInvestigation,persistDraft,guard,openDetail,openUndoPreview,cancelUndoPreview,renderUndoPreview,renderInvestigation,undoResultSummary};\n})();');
  vm.runInContext(source,context);
  const ui=context.ui;ui.S.state={counts:{}};ui.S.detail=copy(snapshot.business);ui.setupInvestigation(copy(snapshot));ui.S.investigation.undoPreview={eligible:true,preview_token:"fixture-token",session_id:"session-1",review_revision:1,draft_revision:0,source_revision:"source",overlap_questions:[],localSequence:0,localDraftRevision:0};
  const form=ui.renderQuestion(ui.S.detail,'website');root.append(form);const notes=form.querySelectorAll().find(n=>n.tagName==='textarea');
  return {ui,server,calls,notes,root,nodes,events,response,applyUndo};
}
function type(h,value='Raced text'){h.notes.value=value;h.notes.listeners.input();}
const mutations=h=>h.calls.filter(c=>c.route.endsWith('/undo'));
const writes=h=>h.calls.filter(c=>c.route.endsWith('/draft'));

test('locks full POST + snapshot + summary transition and keeps existing disabled controls disabled',async()=>{
  const h=harness(),post=deferred(),read=deferred(),summary=deferred();const disabled=new Element('input');disabled.disabled=true;h.root.append(disabled);
  h.server.override=(r)=>r.endsWith('/undo')?post.promise:r.split('?')[0].endsWith('/investigation')?read.promise:r==='/api/state'?summary.promise:null;
  const pending=h.ui.undoInvestigation();assert.equal(h.notes.disabled,true);await tick();assert.equal(h.ui.S.reviewSaving,true);
  let navigated=false;await h.ui.guard(()=>{navigated=true;});assert.equal(navigated,false);
  h.applyUndo();post.resolve(h.response({}));await tick();assert.equal(h.notes.disabled,true);
  read.resolve(h.response(h.server.snapshot));await tick();assert.equal(h.notes.disabled,true);
  summary.resolve(h.response({counts:{},version:'fixture'}));await pending;
  assert.equal(h.notes.disabled,false);assert.equal(disabled.disabled,true);assert.equal(h.ui.S.investigation.edits.website.notes,answer.notes);assert.equal(h.ui.S.dirty,false);
});
for(const phase of ['POST','snapshot','summary'])test(`queued input during ${phase} is retained unsaved for explicit comparison`,async()=>{
  const h=harness(),hold=deferred();h.server.override=r=>(phase==='POST'&&r.endsWith('/undo'))||(phase==='snapshot'&&r.split('?')[0].endsWith('/investigation'))||(phase==='summary'&&r==='/api/state')?hold.promise:null;
  const pending=h.ui.undoInvestigation();await tick();type(h);
  if(phase==='POST')h.applyUndo();hold.resolve(h.response(phase==='snapshot'?h.server.snapshot:phase==='summary'?{counts:{},version:'fixture'}:{}));await pending;
  assert.equal(h.ui.S.investigation.edits.website.notes,'Raced text');assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.investigation.conflict,true);assert.match(h.ui.S.investigation.error,/not saved/);assert.equal(writes(h).length,0);
  await assert.rejects(h.ui.persistDraft(),/Resolve the conflict/);let warned=false;h.events.beforeunload({preventDefault(){warned=true;}});assert.equal(warned,true);
});
test('lost success response is reconciled without duplicate mutation and restart loads restored draft',async()=>{
  const h=harness();h.server.override=r=>{if(r.endsWith('/undo')){h.applyUndo();return Promise.reject(Error('Response lost'));}return null;};
  await h.ui.undoInvestigation();assert.equal(mutations(h).length,1);assert.equal(h.ui.S.dirty,false);assert.equal(h.ui.S.investigation.latest_session.undone,true);
  await h.ui.undoInvestigation();assert.equal(mutations(h).length,1);
  h.ui.setupInvestigation(copy(h.server.snapshot));assert.equal(h.ui.S.investigation.edits.website.notes,answer.notes);assert.equal(h.ui.S.investigation.latest_session.can_undo,false);
});
test('uncertain POST and failed read retain text, block draft writes, and check outcome before retry',async()=>{
  const h=harness(),post=deferred();h.server.override=r=>r.endsWith('/undo')?post.promise:r.split('?')[0].endsWith('/investigation')?Promise.reject(Error('Read unavailable')):null;
  const pending=h.ui.undoInvestigation();await tick();type(h);h.applyUndo();post.reject(Error('Response lost'));await assert.rejects(pending,/outcome is not confirmed/);
  assert.ok(h.ui.S.investigation.undoPending);assert.equal(h.ui.S.dirty,true);assert.equal(h.notes.disabled,false);await assert.rejects(h.ui.persistDraft(),/not confirmed/);assert.equal(writes(h).length,0);
  h.server.override=null;await h.ui.undoInvestigation();assert.equal(mutations(h).length,1);assert.equal(h.ui.S.investigation.undoPending,null);assert.equal(h.ui.S.investigation.edits.website.notes,'Raced text');assert.equal(h.ui.S.investigation.conflict,true);
});
test('confirmed rejected mutation retains raced input and permits normal draft save',async()=>{
  const h=harness(),post=deferred();h.server.override=r=>r.endsWith('/undo')?post.promise:null;
  const pending=h.ui.undoInvestigation();await tick();type(h);post.reject(Error('Request rejected'));await assert.rejects(pending,/Request rejected/);
  assert.equal(h.ui.S.investigation.undoPending,null);assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.investigation.edits.website.notes,'Raced text');assert.equal(h.notes.disabled,false);
  await h.ui.persistDraft();assert.equal(h.server.snapshot.draft.questions.website.notes,'Raced text');assert.equal(h.ui.S.dirty,false);
});
test('failed reconciliation then confirmed untouched session requires explicit new attempt',async()=>{
  const h=harness();h.server.override=r=>r.endsWith('/undo')?Promise.reject(Error('Offline')):r.split('?')[0].endsWith('/investigation')?Promise.reject(Error('Offline')):null;
  await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);h.server.override=null;
  await assert.rejects(h.ui.undoInvestigation(),/not recorded/);assert.equal(mutations(h).length,1);assert.equal(h.ui.S.dirty,false);
  h.ui.S.investigation.undoPreview={eligible:true,preview_token:"fixture-token",session_id:"session-1",review_revision:1,draft_revision:0,source_revision:"source",overlap_questions:[],localSequence:0,localDraftRevision:0};await h.ui.undoInvestigation();assert.equal(mutations(h).length,2);assert.equal(h.ui.S.investigation.latest_session.undone,true);
});
test('late old-business read never replaces newer business or input',async()=>{
  const h=harness(),read=deferred();h.server.override=r=>r.split('?')[0].endsWith('/investigation')?read.promise:null;
  const pending=h.ui.undoInvestigation();await tick();const other=copy(h.server.snapshot);other.business.id=2;other.draft={questions:{website:{...answer,notes:'Other business'}}};h.ui.S.detail=other.business;h.ui.setupInvestigation(other);
  const newer=h.ui.S.investigation;read.resolve(h.response(h.server.snapshot));await pending;
  assert.equal(h.ui.S.detail.id,2);assert.equal(h.ui.S.investigation,newer);assert.equal(newer.edits.website.notes,'Other business');
});
test('in-flight detail navigation started before Undo cannot supersede locked desk',async()=>{
  const h=harness(),oldRead=deferred(),post=deferred();let reads=0;h.server.override=r=>r.split('?')[0].endsWith('/investigation')&&++reads===1?oldRead.promise:r.endsWith('/undo')?post.promise:null;
  const navigation=h.ui.openDetail(2);const undo=h.ui.undoInvestigation();await tick();const other=copy(h.server.snapshot);other.business.id=2;oldRead.resolve(h.response(other));await navigation;assert.equal(h.ui.S.detail.id,1);
  h.applyUndo();post.resolve(h.response({}));await undo;assert.equal(h.ui.S.detail.id,1);
});
test('existing drafts and contact-held business never issue Undo',async()=>{
  const h=harness();type(h);await assert.rejects(h.ui.undoInvestigation(),/Input changed/);assert.equal(mutations(h).length,0);assert.equal(writes(h).length,0);
  h.ui.S.investigation.can_review=false;await h.ui.undoInvestigation();assert.equal(mutations(h).length,0);
});


test('queued old-form input after restoration remains unsaved and cannot contaminate another business',async()=>{
  const h=harness();await h.ui.undoInvestigation();type(h,'Late queued text');
  assert.equal(h.ui.S.investigation.edits.website.notes,'Late queued text');assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.investigation.conflict,true);assert.equal(writes(h).length,0);
  const other=copy(h.server.snapshot);other.business.id=2;other.draft.questions.website.notes='Business two';h.ui.S.detail=other.business;h.ui.setupInvestigation(other);type(h,'Wrong business stale event');
  assert.equal(h.ui.S.investigation.edits.website.notes,'Business two');assert.equal(h.ui.S.dirty,false);
});
test('successful mutation with failed snapshot remains recoverable by read only',async()=>{
  const h=harness();h.server.override=r=>r.split('?')[0].endsWith('/investigation')?Promise.reject(Error('Snapshot failed')):null;
  await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.investigation.undoPending.session_id,'session-1');
  h.server.override=null;await h.ui.undoInvestigation();assert.equal(mutations(h).length,1);assert.equal(h.ui.S.investigation.edits.website.notes,answer.notes);assert.equal(h.ui.S.dirty,false);
});
test('workspace summary failure reports Undo success truthfully and shows restored draft',async()=>{
  const h=harness();h.server.override=r=>r==='/api/state'?Promise.reject(Error('Summary failed')):null;
  await h.ui.undoInvestigation();assert.equal(h.ui.S.investigation.edits.website.notes,answer.notes);assert.equal(h.ui.S.dirty,false);assert.match(h.nodes.get('#notice').textContent,/Last save undone.*summary could not refresh/);assert.equal(h.ui.S.reviewSaving,false);
});
test('unsaved input blocks confirmation without implicitly saving',async()=>{
  const h=harness();type(h);h.server.override=r=>r.endsWith('/draft')?Promise.reject(Error('Draft failed')):null;
  await assert.rejects(h.ui.undoInvestigation(),/Input changed/);assert.equal(mutations(h).length,0);assert.equal(writes(h).length,0);assert.equal(h.notes.disabled,false);assert.equal(h.ui.S.investigation.edits.website.notes,'Raced text');assert.equal(h.ui.S.dirty,true);
});
