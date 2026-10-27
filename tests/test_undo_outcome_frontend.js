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
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'').replace(/\}\)\(\);\s*$/,'globalThis.ui={S,setupInvestigation,renderQuestion,undoInvestigation,persistDraft,guard,openDetail,openUndoPreview,cancelUndoPreview,renderUndoPreview,renderInvestigation,undoResultSummary,reviewForm};\n})();');
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
// Reduced actual HTTP audit snapshots (.test-temp/v0.8-audit/backend-snapshots.json).
const audit={"old":{"review_revision":1,"draft_revision":1,"source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","draft_source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","can_review":true,"draft":null,"questions":{"website":{"website_status":"no_site_found","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02"}},"saved_answers":{"website":{"answers":{"website_status":"no_site_found","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true}},"latest_session":{"session_id":"d010e345b9d9b763f1ee049797021e1e","questions":{"website":{"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","website_status":"no_site_found"}},"review_revision":1,"undone":false,"can_undo":true,"undo_result":null},"draft_conflicts":{},"review_changed":false,"source_changed":false,"business":{"id":1,"name":"Fictional Cedar Painting","review":{"website_status":"no_site_found","verified_website":"","candidate_website":"","activity":"unknown","ownership":"unknown","residential":"unknown","service_fit":"unknown","delivery":"unknown","disposition":"pending","queue_hold":false,"evidence_observed_at":"2026-01-02","identity_status":"unknown","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","investigation_question":"website","investigation_session":"d010e345b9d9b763f1ee049797021e1e","activity_asserted":false,"activity_evidence_observed_at":null,"activity_evidence_url":null,"activity_evidence_notes":null,"created_at":"2026-10-27T23:17:41.654485+00:00"},"excluded_reasons":[],"reviews":[],"observations":[]}},"current":{"review_revision":2,"draft_revision":2,"source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","draft_source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","can_review":true,"draft":null,"questions":{"website":{"website_status":"uncertain","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02"}},"saved_answers":{"website":{"answers":{"website_status":"uncertain","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true}},"latest_session":{"session_id":"6be080a548f66c0c55b0b2805c4febc9","questions":{"website":{"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","website_status":"uncertain"}},"review_revision":2,"undone":false,"can_undo":true,"undo_result":null},"draft_conflicts":{},"review_changed":false,"source_changed":false,"business":{"id":1,"name":"Fictional Cedar Painting","review":{"website_status":"uncertain","verified_website":"","candidate_website":"","activity":"unknown","ownership":"unknown","residential":"unknown","service_fit":"unknown","delivery":"unknown","disposition":"pending","queue_hold":false,"evidence_observed_at":"2026-01-02","identity_status":"unknown","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","investigation_question":"website","investigation_session":"6be080a548f66c0c55b0b2805c4febc9","activity_asserted":false,"activity_evidence_observed_at":null,"activity_evidence_url":null,"activity_evidence_notes":null,"created_at":"2026-10-27T23:17:41.731892+00:00"},"excluded_reasons":[],"reviews":[],"observations":[]}},"undone":{"review_revision":3,"draft_revision":3,"source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","draft_source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","can_review":true,"draft":{"mode":"workspace","questions":{"website":{"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","website_status":"uncertain"}},"step":0},"questions":{"website":{"website_status":"no_site_found","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02"}},"saved_answers":{"website":{"answers":{"website_status":"no_site_found","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true}},"latest_session":{"session_id":"6be080a548f66c0c55b0b2805c4febc9","questions":{"website":{"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","website_status":"uncertain"}},"review_revision":2,"undone":true,"can_undo":false,"undo_result":{"before_context_unknown":false,"draft":{"mode":"workspace","questions":{"website":{"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","website_status":"uncertain"}},"step":0},"draft_conflicts":{"website":{"baseline_revision":3,"baseline_saved_answer":{"answers":{"candidate_website":"","verified_website":"","website_status":"no_site_found"},"basis_known":true,"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence"},"changed":false,"current_revision":3,"current_saved_answer":{"answers":{"candidate_website":"","verified_website":"","website_status":"no_site_found"},"basis_known":true,"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence"},"unknown":false}},"draft_review_revision":3,"draft_review_revisions":{"website":3},"draft_source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","overlap_questions":[],"restored_decisions":["website"],"retained_questions":[],"review_changed":false,"review_context_unknown":false,"session_restored_questions":["website"],"source_changed":false,"source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704"}},"draft_conflicts":{"website":{"baseline_revision":3,"current_revision":3,"baseline_saved_answer":{"answers":{"website_status":"no_site_found","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true},"current_saved_answer":{"answers":{"website_status":"no_site_found","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true},"changed":false,"unknown":false}},"review_changed":false,"source_changed":false,"business":{"id":1,"name":"Fictional Cedar Painting","review":{"activity":"unknown","activity_asserted":false,"activity_evidence_notes":null,"activity_evidence_observed_at":null,"activity_evidence_url":null,"candidate_website":"","delivery":"unknown","disposition":"pending","evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","identity_status":"unknown","investigation_question":"website","investigation_session":"d010e345b9d9b763f1ee049797021e1e","investigation_undo":"6be080a548f66c0c55b0b2805c4febc9","notes":"Fictional observed evidence","ownership":"unknown","queue_hold":false,"residential":"unknown","service_fit":"unknown","verified_website":"","website_status":"no_site_found","created_at":"2026-10-27T23:17:41.756480+00:00"},"excluded_reasons":[],"reviews":[],"observations":[]}},"newer":{"review_revision":4,"draft_revision":6,"source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","draft_source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","can_review":true,"draft":{"mode":"workspace","questions":{"fit":{"notes":"Newer persisted fit sentinel"}},"step":3},"questions":{"website":{"website_status":"no_site_found","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02"},"activity":{"activity":"unknown","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02"}},"saved_answers":{"website":{"answers":{"website_status":"no_site_found","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true},"activity":{"answers":{"activity":"unknown"},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true}},"latest_session":{"session_id":"a30f11c12149d404a94d65c7aa4a9c65","questions":{"activity":{"activity":"unknown","evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence"}},"review_revision":4,"undone":false,"can_undo":true,"undo_result":null},"draft_conflicts":{"fit":{"baseline_revision":4,"current_revision":4,"baseline_saved_answer":null,"current_saved_answer":null,"changed":false,"unknown":false}},"review_changed":false,"source_changed":false,"business":{"id":1,"name":"Fictional Cedar Painting","review":{"website_status":"no_site_found","verified_website":"","candidate_website":"","activity":"unknown","ownership":"unknown","residential":"unknown","service_fit":"unknown","delivery":"unknown","disposition":"pending","queue_hold":false,"evidence_observed_at":"2026-01-02","identity_status":"unknown","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","investigation_question":"activity","investigation_session":"a30f11c12149d404a94d65c7aa4a9c65","activity_asserted":true,"activity_evidence_observed_at":"2026-01-02","activity_evidence_url":"https://example.com/fictional-check","activity_evidence_notes":"Fictional observed evidence","created_at":"2026-10-27T23:17:41.821791+00:00"},"excluded_reasons":[],"reviews":[],"observations":[]}},"preview":{"business_id":1,"eligible":true,"reason":null,"session_id":"6be080a548f66c0c55b0b2805c4febc9","review_revision":2,"draft_revision":2,"source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","preview_token":"383675cbbc8d4129f889fe56cecc01d74c5a5a001ce1b88fa1867382cdc74578","before_review_revision":1,"before_context_unknown":false,"current_saved_answers":{"website":{"answers":{"website_status":"uncertain","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true}},"before_saved_answers":{"website":{"answers":{"website_status":"no_site_found","verified_website":"","candidate_website":""},"evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","evidence_observed_at":"2026-01-02","basis_known":true}},"submitted_questions":{"website":{"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","website_status":"uncertain"}},"restored_draft_questions":{"website":{"evidence_observed_at":"2026-01-02","evidence_url":"https://example.com/fictional-check","notes":"Fictional observed evidence","website_status":"uncertain"}},"retained_draft_questions":{},"unrelated_questions":[],"overlap_questions":[],"current_draft_context":{"draft":null,"draft_review_revision":null,"draft_review_revisions":{},"draft_conflicts":{},"review_changed":false,"review_context_unknown":false,"source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","draft_source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704","source_changed":false},"excluded_reasons":[],"can_review":true,"session_source_revision":"814b48094bb6e01b878c1581c03bb49cc7d299210c0c469d7c1a1a9d06749704"}};
function outcomeHarness(start='old',after='newer'){
 const h=harness();h.ui.S.detail=copy(audit[start].business);h.ui.setupInvestigation(copy(audit[start]));
 h.ui.S.investigation.undoArea=new Element('div');h.root.replaceChildren(h.ui.renderQuestion(h.ui.S.detail,'website'));h.notes=h.root.querySelectorAll().find(n=>n.tagName==='textarea');
 const control={latest:copy(audit[after]),outcome:{session_id:audit.preview.session_id,found:true,undone:true,can_undo:false,undo_result:{restored_decisions:['website']}},failRead:false,postError:false,onPost:null};
 h.server.override=(r,b)=>{
  if(r.endsWith('/undo-preview'))return h.response(audit.preview);
  if(r.endsWith('/undo')){assert.equal(b.session_id,audit.preview.session_id);control.onPost?.();return control.postError?Promise.reject(Error('Response lost')):h.response({draft:{questions:{website:{...answer,notes:'Obsolete receipt'}}}});}
  if(r.includes('/investigation?')){assert.equal(new URL(r,'http://local').searchParams.get('undo_session_id'),audit.preview.session_id);return control.failRead?Promise.reject(Error('Read unavailable')):h.response({...control.latest,undo_outcome:control.outcome});}
  if(r.endsWith('/investigation'))return h.response(control.latest);
  if(r.endsWith('/draft')){assert.equal(b.draft_revision,control.latest.draft_revision);control.latest.draft=copy(b.draft);control.latest.draft_revision++;return h.response(control.latest);}
  return null;
 };
 return {...h,control};
}
for(const after of ['undone','newer'])for(const postError of [false,true])test(`actual audit stale A/fresh B preview resolves B with current ${after}; lost response=${postError}`,async()=>{
 const h=outcomeHarness('old',after);h.control.postError=postError;await h.ui.openUndoPreview();await h.ui.undoInvestigation();
 assert.equal(mutations(h).length,1);assert.equal(h.ui.S.investigation.undoPending??null,null);assert.equal(h.ui.S.dirty,false);
 assert.deepEqual(copy(h.ui.S.investigation.edits),h.control.latest.draft.questions);assert.equal(h.ui.S.investigation.latest_session.session_id,h.control.latest.latest_session.session_id);
 assert.equal(writes(h).length,0);let left=false;await h.ui.guard(()=>{left=true;});assert.equal(left,true);
 h.ui.setupInvestigation(copy(h.control.latest));assert.deepEqual(copy(h.ui.S.investigation.edits),h.control.latest.draft.questions);
});

test('lost read plus typing resolves exact B, preserves newer fit, compares explicitly and restores save/navigation',async()=>{
 const h=outcomeHarness('current');h.control.failRead=true;h.control.postError=true;await h.ui.openUndoPreview();
 await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);type(h,'Local input after error');
 await assert.rejects(h.ui.persistDraft(),/not confirmed/);assert.equal(writes(h).length,0);
 h.control.failRead=false;await h.ui.undoInvestigation();const inv=h.ui.S.investigation;
 assert.equal(inv.undoPending??null,null);assert.equal(inv.conflict,true);assert.equal(h.ui.S.dirty,true);assert.equal(inv.edits.website.notes,'Local input after error');
 assert.deepEqual(copy(inv.edits.fit),h.control.latest.draft.questions.fit);assert.equal(mutations(h).length,1);assert.equal(writes(h).length,0);
 await assert.rejects(h.ui.persistDraft(),/Resolve the conflict/);
 await click(find(h.nodes.get('[data-draft-error]'),'Compare latest saved work'));
 await click(find(h.nodes.get('[data-draft-error]'),'Keep my answers as the draft'));
 assert.equal(writes(h).length,1);assert.equal(h.control.latest.draft.questions.website.notes,'Local input after error');assert.equal(h.control.latest.draft.questions.fit.notes,audit.newer.draft.questions.fit.notes);
 assert.equal(h.ui.S.dirty,false);assert.equal(inv.conflict,false);let left=false;await h.ui.guard(()=>{left=true;});assert.equal(left,true);
});

test('unchanged old question does not replace newer persisted question during raced typing recovery',async()=>{
 const h=outcomeHarness('current');h.ui.S.investigation.edits.fit={...audit.newer.draft.questions.fit,notes:'Old fit draft'};h.ui.S.investigation.savedSequence=h.ui.S.investigation.sequence;
 await h.ui.openUndoPreview();h.control.onPost=()=>type(h,'Queued website');await h.ui.undoInvestigation();
 assert.equal(h.ui.S.investigation.edits.website.notes,'Queued website');assert.deepEqual(copy(h.ui.S.investigation.edits.fit),h.control.latest.draft.questions.fit);
});

test('missing or mismatched outcome remains pending and read-only until exact historical success is known',async()=>{
 for(const bad of [{session_id:audit.preview.session_id,found:false,undone:null,can_undo:false,undo_result:null},{session_id:'wrong',found:true,undone:true}]){
 const h=outcomeHarness();await h.ui.openUndoPreview();h.control.outcome=bad;await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);
 type(h,'Kept input');await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);assert.equal(mutations(h).length,1);assert.equal(writes(h).length,0);assert.equal(h.ui.S.dirty,true);
 h.control.outcome={session_id:audit.preview.session_id,found:true,undone:true,can_undo:false,undo_result:null};await h.ui.undoInvestigation();assert.equal(h.ui.S.investigation.undoPending??null,null);assert.equal(h.ui.S.investigation.edits.website.notes,'Kept input');assert.equal(mutations(h).length,1);
 }
});

test('held current business still resolves historical success without enabling mutations',async()=>{
 const h=outcomeHarness();h.control.latest.can_review=false;h.control.latest.business.excluded_reasons=['contacted'];await h.ui.openUndoPreview();h.control.failRead=true;
 await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);h.ui.S.investigation.can_review=false;h.control.failRead=false;
 await h.ui.openUndoPreview();assert.equal(h.ui.S.investigation.can_review,false);assert.equal(h.ui.S.investigation.undoPending??null,null);assert.equal(h.ui.S.dirty,false);assert.equal(mutations(h).length,1);
 await h.ui.undoInvestigation();await h.ui.persistDraft();assert.equal(mutations(h).length,1);assert.equal(writes(h).length,0);
});

test('held success retains raced input dirty and offers saved-work comparison without write choice',async()=>{
 const h=outcomeHarness();h.control.latest.can_review=false;h.control.onPost=()=>type(h,'Local held text');await h.ui.openUndoPreview();await h.ui.undoInvestigation();
 assert.equal(h.ui.S.investigation.edits.website.notes,'Local held text');assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.investigation.undoPending??null,null);
 await click(find(h.nodes.get('[data-draft-error]'),'Compare latest saved work'));
 assert.equal(descendants(h.nodes.get('[data-draft-error]')).some(n=>n.textContent==='Keep my answers as the draft'),false);assert.ok(find(h.nodes.get('[data-draft-error]'),'Use newer saved work'));
});

test('later unrelated draft added during explicit comparison remains the default when keeping local typing',async()=>{
 const h=outcomeHarness('current');h.control.onPost=()=>type(h,'Local website');await h.ui.openUndoPreview();await h.ui.undoInvestigation();
 h.control.latest.draft.questions.fit.notes='Even newer fit during comparison';h.control.latest.draft_revision++;
 await click(find(h.nodes.get('[data-draft-error]'),'Compare latest saved work'));
 await click(find(h.nodes.get('[data-draft-error]'),'Keep my answers as the draft'));
 assert.equal(h.control.latest.draft.questions.fit.notes,'Even newer fit during comparison');assert.equal(h.control.latest.draft.questions.website.notes,'Local website');assert.equal(h.ui.S.dirty,false);
});

test('known untouched outcome requires a fresh explicit preview, never a recovery resend',async()=>{
 const h=outcomeHarness('current','current');h.control.outcome={session_id:audit.preview.session_id,found:true,undone:false,can_undo:true,undo_result:null};h.control.postError=true;
 await h.ui.openUndoPreview();await assert.rejects(h.ui.undoInvestigation(),/Response lost/);assert.equal(h.ui.S.investigation.undoPending,null);assert.equal(h.ui.S.investigation.undoPreview,null);
 await assert.rejects(h.ui.undoInvestigation(),/fresh Undo preview/);assert.equal(mutations(h).length,1);
 h.control.outcome.undone=true;h.control.postError=false;h.control.latest=copy(audit.undone);await h.ui.openUndoPreview();await h.ui.undoInvestigation();assert.equal(mutations(h).length,2);assert.equal(h.ui.S.dirty,false);
});
test('known failed Undo with newer held state displays current work and remains read-only',async()=>{
 const h=outcomeHarness();h.control.latest.can_review=false;h.control.outcome={session_id:audit.preview.session_id,found:true,undone:false,can_undo:false,undo_result:null};
 await h.ui.openUndoPreview();await assert.rejects(h.ui.undoInvestigation(),/not recorded/);
 assert.equal(h.ui.S.investigation.undoPending??null,null);assert.equal(h.ui.S.investigation.can_review,false);assert.deepEqual(copy(h.ui.S.investigation.edits),h.control.latest.draft.questions);assert.equal(h.ui.S.dirty,false);
 await h.ui.undoInvestigation();await h.ui.persistDraft();assert.equal(mutations(h).length,1);assert.equal(writes(h).length,0);
});

for(const held of [false,true])test(`advanced notes and selected values survive uncertain Undo recovery (held=${held})`,async()=>{
 const h=outcomeHarness('current');h.control.failRead=true;h.control.postError=true;await h.ui.openUndoPreview();await assert.rejects(h.ui.undoInvestigation(),/not confirmed/);
 const section=h.ui.reviewForm(h.ui.S.detail),form=descendants(section).find(n=>n.tagName==='form');
 const fields=descendants(form),notes=fields.find(n=>n.tagName==='textarea'),decision=fields.find(n=>n.tagName==='select');notes.value='Advanced notes typed after error';decision.value='site_found';form.listeners.input();
 await form.listeners.submit({preventDefault(){}});assert.equal(h.calls.some(c=>c.route.endsWith('/review')),false);
 assert.equal(h.ui.S.investigation.advancedInput.values.notes,'Advanced notes typed after error');
 h.control.latest.can_review=!held;h.control.failRead=false;await h.ui.undoInvestigation();assert.equal(h.ui.S.advancedDirty,true);assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.investigation.advancedInput.values.website_status,'site_found');
 if(!held){const recovered=descendants(h.ui.reviewForm(h.ui.S.detail));assert.equal(recovered.find(n=>n.tagName==='textarea').value,'Advanced notes typed after error');assert.equal(recovered.find(n=>n.tagName==='select').value,'site_found');}
 else assert.match(text(h.ui.renderInvestigation(h.ui.S.detail)),/Advanced notes typed after error/);
 assert.equal(mutations(h).length,1);assert.equal(writes(h).length,0);
});
