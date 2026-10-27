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
  const ui=context.ui;ui.S.state={counts:{}};ui.S.detail=copy(snapshot.business);ui.setupInvestigation(copy(snapshot));
  const form=ui.renderQuestion(ui.S.detail,'website');root.append(form);const notes=form.querySelectorAll().find(n=>n.tagName==='textarea');
  ui.S.investigation.undoArea=new Element("div");return {ui,server,calls,notes,root,nodes,events,response,applyUndo};
}
function type(h,value='Raced text'){h.notes.value=value;h.notes.listeners.input();}
const mutations=h=>h.calls.filter(c=>c.route.endsWith('/undo'));
const writes=h=>h.calls.filter(c=>c.route.endsWith('/draft'));

const descendants=root=>[root,...root.querySelectorAll()];
const text=root=>descendants(root).map(n=>n.textContent||'').join('\n');
const find=(root,label)=>{const result=descendants(root).find(n=>n.tagName==='button'&&n.textContent===label);assert.ok(result,label);return result;};
const click=n=>n.listeners.click({currentTarget:n});
const fit={fit_status:'fit',notes:'Unrelated fit draft',evidence_url:'https://example.com/fit',evidence_observed_at:'2026-01-02'};
const saved=(value,notes)=>({answers:{website_status:value},notes,basis_known:true,evidence_url:'https://example.com/saved',evidence_observed_at:'2026-01-03'});
function feature(drafts={}){
  const h=harness(),s=h.server.snapshot;s.draft=Object.keys(drafts).length?{questions:copy(drafts),mode:'workspace',step:1}:null;
  s.draft_revision=Object.keys(drafts).length?2:0;
  h.ui.setupInvestigation(copy(s));h.ui.S.investigation.undoArea=new Element('div');h.root.replaceChildren(h.ui.renderQuestion(h.ui.S.detail,'website'));h.notes=h.root.querySelectorAll().find(n=>n.tagName==='textarea');
  const overlaps=Object.keys(drafts).filter(q=>q==='website');
  const preview={business_id:1,eligible:true,session_id:'session-1',preview_token:'token-exact',review_revision:1,draft_revision:s.draft_revision,source_revision:'source',before_review_revision:0,before_context_unknown:false,current_saved_answers:{website:saved('no_site_found','Current check')},before_saved_answers:{website:saved('uncertain','Previous check')},submitted_questions:{website:copy(answer)},restored_draft_questions:overlaps.length?{}:{website:copy(answer)},retained_draft_questions:copy(drafts),unrelated_questions:Object.keys(drafts).filter(q=>q!=='website'),overlap_questions:overlaps,current_draft_context:{draft_conflicts:{fit:{changed:true,unknown:false},website:{unknown:true,changed:false}},source_changed:true,review_changed:true,review_context_unknown:true},session_source_revision:'old-source',can_review:true,excluded_reasons:[]};
  const undo=()=>{
    const result={restored_decisions:['website'],session_restored_questions:overlaps.length?[]:['website'],retained_questions:Object.keys(drafts),overlap_questions:overlaps};
    s.review_revision++;s.draft_revision++;s.draft={mode:'workspace',step:1,questions:{website:copy(answer),...copy(drafts)}};s.latest_session={session_id:'session-1',can_undo:false,undone:true,undo_result:result};return {session_id:'session-1',undo_result:result};
  };
  h.server.override=(route,body)=>route.endsWith('/undo-preview')?h.response(preview):route.endsWith('/undo')?h.response(undo()):null;
  return {...h,preview,undo};
}

test('opening and cancelling preview performs GET only and compares actual bases',async()=>{
  const h=feature({fit});await h.ui.openUndoPreview();const box=h.ui.S.investigation.undoArea;
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].body,undefined);assert.match(h.calls[0].route,/undo-preview$/);
  assert.match(text(box),/Current check/);assert.match(text(box),/Previous check/);assert.match(text(box),/Restored submitted text/);assert.match(text(box),/Unrelated fit draft/);assert.match(text(box),/unrelated unfinished input retained/);assert.match(text(box),/Saved-review context changed/);assert.match(text(box),/Source evidence changed/);assert.match(text(box),/Current unfinished input has changed source context/);assert.match(text(box),/unknown saved-review context/);
  await click(find(box,'Cancel preview'));assert.equal(h.ui.S.investigation.undoPreview,null);assert.equal(h.calls.some(c=>c.body),false);assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);
});
test('opening dirty preview never auto-saves; explicit save draft action permits a fresh GET',async()=>{
  const h=feature();type(h);await h.ui.openUndoPreview();assert.equal(h.calls.length,0);const box=h.ui.S.investigation.undoArea;assert.ok(find(box,'Save draft first'));
  await click(find(box,'Save draft first'));assert.equal(writes(h).length,1);assert.equal(h.server.snapshot.draft.questions.website.notes,'Raced text');assert.ok(h.ui.S.investigation.undoPreview);
});
test('advanced edits or in-flight autosave block preview without starting a write',async()=>{
  const h=feature();h.ui.S.advancedDirty=true;await h.ui.openUndoPreview();assert.equal(h.calls.length,0);assert.match(text(h.ui.S.investigation.undoArea),/advanced review edits/);
  h.ui.S.advancedDirty=false;h.ui.S.investigation.saving=Promise.resolve();await h.ui.openUndoPreview();assert.equal(h.calls.length,0);assert.equal(h.ui.S.investigation.undoPreview,null);
});
test('unrelated persisted draft survives token-confirmed Undo and setup after restart',async()=>{
  const h=feature({fit});await h.ui.openUndoPreview();await click(find(h.ui.S.investigation.undoArea,'Confirm Undo'));
  const post=mutations(h)[0].body;assert.deepEqual(post,{session_id:'session-1',review_revision:1,preview_token:'token-exact',draft_revision:2,source_revision:'source',keep_questions:[]});assert.equal(writes(h).length,0);
  assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);assert.equal(h.ui.S.investigation.edits.website.notes,answer.notes);assert.equal(h.ui.S.dirty,false);
  h.ui.setupInvestigation(copy(h.server.snapshot));assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);assert.equal(h.ui.S.investigation.latest_session.undo_result.retained_questions[0],'fit');
});
test('overlap confirmation is explicit and retains newer input',async()=>{
  const newer={...answer,notes:'Newer website text'};const h=feature({fit,website:newer});await h.ui.openUndoPreview();const box=h.ui.S.investigation.undoArea,confirm=find(box,'Confirm Undo');assert.equal(confirm.disabled,true);
  await assert.rejects(h.ui.undoInvestigation(),/Explicitly keep/);assert.equal(mutations(h).length,0);
  const choice=descendants(box).find(n=>n.tagName==='input'&&n.type==='checkbox');assert.ok(choice);assert.equal(choice.checked,false);choice.checked=true;choice.listeners.change();assert.equal(confirm.disabled,false);
  await click(confirm);assert.deepEqual(mutations(h)[0].body.keep_questions,['website']);assert.equal(h.ui.S.investigation.edits.website.notes,newer.notes);assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);assert.equal(writes(h).length,0);
});
test('cancelling overlap never changes stored input or sends confirmation',async()=>{
  const h=feature({website:{...answer,notes:'Newer'}}),before=copy(h.server.snapshot);await h.ui.openUndoPreview();await click(find(h.ui.S.investigation.undoArea,'Cancel preview'));assert.deepEqual(h.server.snapshot,before);assert.equal(mutations(h).length,0);assert.equal(writes(h).length,0);
});
test('local input after preview blocks confirmation even after its autosave completes',async()=>{
  const h=feature();await h.ui.openUndoPreview();type(h,'New after preview');await h.ui.persistDraft();await assert.rejects(h.ui.undoInvestigation(),/Input changed/);assert.equal(mutations(h).length,0);assert.equal(h.ui.S.investigation.edits.website.notes,'New after preview');assert.equal(h.ui.S.investigation.undoPreview,null);
});
test('stale server confirmation rejects safely and requires a fresh preview',async()=>{
  const h=feature({fit});await h.ui.openUndoPreview();const old=h.server.override;h.server.override=(r,b)=>r.endsWith('/undo')?{ok:false,status:409,json:async()=>({error:'Undo preview changed. Open a fresh preview.'})}:old(r,b);
  await assert.rejects(h.ui.undoInvestigation(),/preview changed/);assert.equal(h.ui.S.investigation.undoPending,null);assert.equal(h.ui.S.investigation.undoPreview,null);assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);assert.equal(writes(h).length,0);
  await assert.rejects(h.ui.undoInvestigation(),/fresh Undo preview/);assert.equal(mutations(h).length,1);
});
test('delayed preview ignores cancellation, local input and another-business navigation',async()=>{
  for(const action of ['cancel','input','navigate']){
    const h=feature(),read=deferred();h.server.override=r=>r.endsWith('/undo-preview')?read.promise:null;const pending=h.ui.openUndoPreview();
    if(action==='cancel')await click(find(h.ui.S.investigation.undoArea,'Cancel preview'));
    if(action==='input')type(h);
    if(action==='navigate'){h.ui.S.detail={id:2};h.ui.setupInvestigation({...copy(h.server.snapshot),business:{id:2}});}
    read.resolve(h.response(h.preview));await pending;assert.equal(h.ui.S.investigation.undoPreview??null,null);assert.equal(mutations(h).length,0);assert.equal(writes(h).length,0);
  }
});
test('ineligible held preview has no confirm and invents no previous evidence',async()=>{
  const h=feature();h.preview.eligible=false;h.preview.reason='Contact exclusion';h.preview.before_context_unknown=true;h.preview.before_saved_answers=null;await h.ui.openUndoPreview();assert.match(text(h.ui.S.investigation.undoArea),/Contact exclusion/);assert.equal(descendants(h.ui.S.investigation.undoArea).some(n=>n.textContent==='Confirm Undo'),false);assert.equal(h.calls.some(c=>c.body),false);
});
test('lost Undo response plus failed snapshot retains unrelated drafts and recovers by GET only',async()=>{
  const h=feature({fit});await h.ui.openUndoPreview();h.server.override=r=>{if(r.endsWith('/undo')){h.undo();return Promise.reject(Error('Lost response'));}if(r.split('?')[0].endsWith('/investigation'))return Promise.reject(Error('Read failed'));return null;};
  await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);assert.equal(h.ui.S.dirty,true);
  h.server.override=null;await h.ui.undoInvestigation();assert.equal(mutations(h).length,1);assert.equal(writes(h).length,0);assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);assert.equal(h.ui.S.investigation.edits.website.notes,answer.notes);
});

test('HTTP error after a committed Undo still reconciles matching undone session',async()=>{
  const h=feature({fit});await h.ui.openUndoPreview();h.server.override=r=>{if(r.endsWith('/undo')){h.undo();return {ok:false,status:400,json:async()=>({error:'Injected lost-success response'})};}return null;};
  await h.ui.undoInvestigation();assert.equal(mutations(h).length,1);assert.equal(h.ui.S.investigation.latest_session.undone,true);assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);assert.equal(h.ui.S.dirty,false);
});
test('cancel restores focus to its preview trigger and no late read reopens it',async()=>{
  const h=feature(),read=deferred();let focused=false;h.ui.S.investigation.undoTrigger={focus(){focused=true;}};h.server.override=r=>r.endsWith('/undo-preview')?read.promise:null;
  const pending=h.ui.openUndoPreview();await click(find(h.ui.S.investigation.undoArea,'Cancel preview'));assert.equal(focused,true);read.resolve(h.response(h.preview));await pending;assert.equal(h.ui.S.investigation.undoPreview,null);
});
test('input during preview read replaces loading UI with clear invalidation and fresh action',async()=>{
  const h=feature(),read=deferred();h.server.override=r=>r.endsWith('/undo-preview')?read.promise:null;const pending=h.ui.openUndoPreview();type(h);read.resolve(h.response(h.preview));await pending;
  assert.match(text(h.ui.S.investigation.undoArea),/preview is invalid/);assert.ok(find(h.ui.S.investigation.undoArea,'Load fresh preview'));assert.equal(text(h.ui.S.investigation.undoArea).includes('Loading read-only'),false);assert.equal(writes(h).length,0);
});
test('result labels stored context flags at Undo time separately from present context',()=>{
  const h=feature(),summary=h.ui.undoResultSummary({restored_decisions:['website'],session_restored_questions:['website'],retained_questions:['fit'],before_context_unknown:true,review_changed:true,review_context_unknown:true,source_changed:true});
  assert.match(text(summary),/previous review snapshot unknown/);assert.match(text(summary),/saved-review context changed/);assert.match(text(summary),/baseline unknown/);assert.match(text(summary),/source context changed and unaccepted/);assert.match(text(summary),/context at Undo time/);assert.match(text(summary),/Current context appears/);
});

test('closing preview after an ambiguous committed Undo preserves warning and read-only recovery',async()=>{
  const h=feature({fit});await h.ui.openUndoPreview();h.server.override=r=>{if(r.endsWith('/undo')){h.undo();return Promise.reject(Error('Lost response'));}if(r.split('?')[0].endsWith('/investigation'))return Promise.reject(Error('Read failed'));return null;};
  await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);const inv=h.ui.S.investigation,pending=inv.undoPending;
  assert.ok(find(inv.undoArea,'Check Undo outcome'));assert.equal(descendants(inv.undoArea).some(n=>n.textContent==='Confirm Undo'),false);
  let focused=false;inv.undoTrigger={focus(){focused=true;}};await click(find(inv.undoArea,'Cancel preview'));
  assert.equal(inv.undoPending,pending);assert.equal(h.ui.S.dirty,true);assert.equal(inv.undoPreview,null);assert.equal(inv.undoArea.hidden,true);assert.equal(focused,true);assert.equal(inv.undoTrigger.textContent,'Check Undo outcome');
  assert.match(h.nodes.get('#notice').textContent,/Preview closed.*outcome is not confirmed/);assert.doesNotMatch(h.nodes.get('#notice').textContent,/No review or draft was changed/);
  assert.ok(find(h.nodes.get('[data-draft-error]'),'Check Undo outcome'));await assert.rejects(h.ui.persistDraft(),/not confirmed/);
  h.server.override=null;await h.ui.openUndoPreview();assert.equal(mutations(h).length,1);assert.equal(h.ui.S.investigation.latest_session.undone,true);assert.equal(h.ui.S.dirty,false);assert.equal(h.ui.S.investigation.edits.fit.notes,fit.notes);
});
