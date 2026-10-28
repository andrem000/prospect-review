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
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'').replace(/\}\)\(\);\s*$/,'globalThis.ui={S,setupInvestigation,renderQuestion,undoInvestigation,persistDraft,guard,openDetail,openUndoPreview,cancelUndoPreview,renderUndoPreview,renderInvestigation,undoResultSummary,reviewForm,renderDecisionHistory};\n})();');
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
const saved=(status,notes='Observed basis',date='2026-01-02')=>({answers:{website_status:status},basis_known:true,evidence_url:'https://example.com/evidence',notes,evidence_observed_at:date});
const saveEvent=(id='session:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',rev=2)=>({id,kind:'investigation_save',origin:'guided',review_ids:[rev],first_revision:rev,last_revision:rev,recorded_at:'2026-01-03T10:00:00Z',recorded_until:'2026-01-03T10:00:00Z',session_id:id.split(':')[1],original_session_id:null,original_event_id:null,asserted_questions:['website'],changed_questions:['website'],association_unknown:false,matches_filter:'known',before:{},after:{website:saved('no_site_found')},record_evidence:[{review_id:rev,question:'website',association_known:true,evidence_url:'https://example.com/evidence',notes:'Record note',evidence_observed_at:'2026-01-02'}],other_changes:[],undo_result:null});
const undoEvent=()=>({...saveEvent('review:3',3),kind:'undo',origin:'unknown',original_session_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',original_event_id:'session:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',before:{website:saved('no_site_found')},after:{},asserted_questions:[],undo_result:{restored_decisions:['website'],session_restored_questions:['website'],retained_questions:['fit'],overlap_questions:[],source_changed:true,review_changed:true,review_context_unknown:true,before_context_unknown:false}});
function historyHarness(){
 const h=harness(),control={revision:3,items:[undoEvent(),saveEvent()],total:null,override:null};
 h.server.override=(r,b)=>{
  if(!r.includes('/history?'))return null;
  const url=new URL(r,'http://local'),query=url.searchParams;
  if(control.override){const result=control.override(query,r);if(result)return result;}
  const id=query.get('event_id');
  if(id)return h.response({business_id:1,history_revision:control.revision,question:'all',offset:0,limit:1,total:1,has_more:false,selected_event_id:id,items:[saveEvent(id)]});
  const offset=Number(query.get('offset')),total=control.total??control.items.length;
  return h.response({business_id:1,history_revision:control.revision,question:query.get('question'),offset,limit:20,total,has_more:offset+20<total,items:copy(control.items)});
 };
 const history=h.ui.renderDecisionHistory(h.ui.S.detail);h.root.append(history);
 return {...h,history,control};
}
const open=async h=>{h.history.open=true;h.history.listeners.toggle();await tick();};
const expand=node=>{node.open=true;node.listeners.toggle();};
const historyNodes=h=>descendants(h.history);
const eventNodes=h=>historyNodes(h).filter(n=>n.className==='history-event');
const historyCalls=h=>h.calls.filter(c=>c.route.includes('/history?'));
const filter=h=>historyNodes(h).find(n=>n.tagName==='select');

// The deterministic history fixture above is reused from test_history_frontend.js.
// These assertions exercise shipped event rendering and history action callbacks.
const correction=(route='cli',supplied=['website_status'],mode='merge')=>({
 ...saveEvent('review:7',7),kind:'saved_review',origin:route,asserted_questions:[],
 correction_provenance:{version:1,route,mode,supplied_fields:supplied},
 association_unknown:false,record_evidence:[{review_id:7,question:null,association_known:false,notes:'Generic record note',evidence_url:'https://example.com/generic',evidence_observed_at:null}]
});
const show=async event=>{const h=historyHarness();h.control.items=[event];await open(h);expand(eventNodes(h)[0]);return h;};

test('CLI same-value supplied decision is separate from effective changes and checked evidence',async()=>{
 const event=correction();event.before=copy(event.after);event.changed_questions=[];
 const h=await show(event),shown=text(h.history);
 assert.match(shown,/CLI correction/);assert.match(shown,/Merge correction · omitted fields retain/);
 assert.match(shown,/Explicitly supplied fields: Website decision/);assert.match(shown,/Declared decision fields: Website decision/);
 assert.match(shown,/Effective answer value changes: none/);assert.match(shown,/do not establish a human check/);
 assert.match(shown,/Record question association: unknown/);assert.doesNotMatch(shown,/Recorded check · no effective answer change/);
});

test('advanced explicit default remains supplied with no effective value change',async()=>{
 const event=correction('advanced',['activity']);event.before={activity:{answers:{activity:'unknown'},basis_known:false}};event.after=copy(event.before);event.changed_questions=[];
 const h=await show(event),shown=text(h.history);
 assert.match(shown,/Advanced review correction/);assert.match(shown,/Explicitly supplied fields: Activity/);
 assert.match(shown,/Effective answer value changes: none/);assert.match(shown,/Evidence basis unknown/);
 assert.doesNotMatch(shown,/Recorded check/);assert.equal(historyCalls(h).length,1);
});

test('notes and evidence only correction identifies record-level input and unassociated evidence',async()=>{
 const event=correction('cli',['evidence_observed_at','evidence_url','notes']);event.changed_questions=[];event.before=copy(event.after);
 const h=await show(event),shown=text(h.history);
 assert.match(shown,/Notes\/evidence-only submission · no decision fields explicitly supplied/);
 assert.match(shown,/Explicitly supplied fields: Evidence observed on, Evidence URL, Notes/);
 assert.match(shown,/Effective answer value changes: none/);assert.match(shown,/Generic record evidence remains separate/);
 assert.doesNotMatch(shown,/Declared decision fields:/);assert.match(shown,/Record question association: unknown/);
});

test('mixed correction distinguishes declared fields, value changes, and basis-only changes',async()=>{
 const event=correction('advanced',['activity','notes','queue_hold','website_status']);
 event.before={website:saved('no_site_found','Old basis'),activity:{answers:{activity:'unknown'},basis_known:false}};
 event.after={website:saved('site_found','Old basis'),activity:{answers:{activity:'unknown'},basis_known:true,notes:'New basis'}};
 event.changed_questions=['website'];event.other_changes=[{field:'queue_hold',before:false,after:true}];
 const shown=text((await show(event)).history);
 assert.match(shown,/Explicitly supplied fields: Activity, Notes, Hold, Website decision/);
 assert.match(shown,/Declared decision fields: Activity, Website decision/);
 assert.match(shown,/Effective answer value changes: Website\./);assert.match(shown,/Saved value unchanged · evidence basis changed/);
 assert.match(shown,/Hold: false → true/);assert.doesNotMatch(shown,/Recorded check/);
});

test('replace correction explains omissions without labeling resets as explicit submissions',async()=>{
 const event=correction('cli',['notes'],'replace');event.before={website:saved('site_found')};event.after={website:{answers:{website_status:'unreviewed'},basis_known:false}};
 const shown=text((await show(event)).history);
 assert.match(shown,/Replace correction · omitted fields use replacement defaults and may reset previous values/);
 assert.match(shown,/Explicitly supplied fields: Notes\./);assert.match(shown,/Notes\/evidence-only submission/);
 assert.match(shown,/Effective answer value changes: Website\./);assert.doesNotMatch(shown,/Declared decision fields:/);
 const empty=correction('advanced',[],'replace');assert.match(text((await show(empty)).history),/No fields explicitly supplied/);
});

test('legacy unknown and malformed or future envelopes do not fabricate a trusted route',async()=>{
 const invalid=[null,{version:2,route:'cli',mode:'merge',supplied_fields:['activity']},
  {version:1,route:'browser',mode:'merge',supplied_fields:['activity']},
  {version:1,route:'cli',mode:'reset',supplied_fields:['activity']},
  {version:1,route:'cli',mode:'merge',supplied_fields:'activity'},
  {version:1,route:'cli',mode:'merge',supplied_fields:['activity','activity']},
  {version:1,route:'cli',mode:'merge',supplied_fields:['website_status','activity']},
  {version:1,route:'cli',mode:'merge',supplied_fields:['activity'],future:true},
  {version:1,route:'advanced',mode:'merge',supplied_fields:['activity']}];
 for(const value of invalid){const event=correction();event.correction_provenance=value;event.origin=value===null?'unknown':'cli';event.matches_filter='unknown';const shown=text((await show(event)).history);assert.match(shown,/Origin not recorded/);assert.match(shown,/included conservatively/);assert.doesNotMatch(shown,/CLI correction|Advanced review correction|Explicitly supplied fields/);}
 const event=correction();delete event.correction_provenance;event.origin='unknown';assert.match(text((await show(event)).history),/Origin not recorded/);
});

test('guided and Undo classification wins over inherited correction metadata and CLI origin',async()=>{
 for(const event of [saveEvent(),undoEvent()]){
  event.correction_provenance=correction().correction_provenance;
  const h=await show(event),shown=text(h.history);
  assert.doesNotMatch(shown,/CLI correction|Advanced review correction|Explicitly supplied fields/);
  assert.match(shown,event.kind==='undo'?/Historical Undo event/:/Investigation save/);
 }
 const event=undoEvent();event.origin='cli';event.correction_provenance=correction().correction_provenance;
 const shown=text((await show(event)).history);assert.match(shown,/Origin not recorded/);assert.doesNotMatch(shown,/CLI correction/);
});

test('hostile supplied fields fail closed and historical text and unsafe links remain text-only',async()=>{
 const event=correction('cli',['<img src=x onerror=alert(1)>']);event.record_evidence[0].notes='<script>hostile record</script>';event.record_evidence[0].evidence_url='javascript:alert(1)';
 const h=await show(event),shown=text(h.history);
 assert.match(shown,/Origin not recorded/);assert.doesNotMatch(shown,/<img/);assert.match(shown,/<script>hostile record<\/script>/);
 assert.equal(historyNodes(h).some(n=>n.tagName==='img'||n.tagName==='script'||n.href?.startsWith('javascript:')),false);
});

test('known correction filters are GET-only and preserve question, advanced and newer draft input',async()=>{
 const h=historyHarness();h.control.items=[correction()];type(h,'Question draft retained');
 const advanced=h.ui.reviewForm(h.ui.S.detail),form=descendants(advanced).find(n=>n.tagName==='form'),notes=descendants(form).find(n=>n.tagName==='textarea');notes.value='Advanced retained';form.listeners.input();
 const inv=h.ui.S.investigation,before=copy(inv.edits),advancedBefore=copy(inv.advancedInput);await open(h);
 // Fixture models the API's known field association. The renderer must use its response.
 h.control.override=q=>h.response({business_id:1,history_revision:3,question:q.get('question'),offset:0,limit:20,total:q.get('question')==='activity'?0:1,has_more:false,items:q.get('question')==='activity'?[]:[correction()]});
 filter(h).value='activity';filter(h).listeners.change();await tick();assert.match(text(h.history),/No saved decision history matches/);
 filter(h).value='website';filter(h).listeners.change();await tick();expand(eventNodes(h)[0]);await click(find(h.history,'Refresh history'));
 assert.equal(h.ui.S.investigation,inv);assert.deepEqual(copy(inv.edits),before);assert.deepEqual(copy(inv.advancedInput),advancedBefore);assert.equal(h.notes.value,'Question draft retained');assert.equal(notes.value,'Advanced retained');
 assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.advancedDirty,true);assert.ok(h.calls.every(c=>c.route.includes('/history?')&&c.body===undefined));
});

test('delayed correction reads cannot overwrite newer filter generations or newer input',async()=>{
 const h=historyHarness(),old=deferred();h.control.items=[correction()];await open(h);h.control.override=q=>q.get('question')==='website'?old.promise:null;
 filter(h).value='website';filter(h).listeners.change();type(h,'Newer question input');filter(h).value='activity';filter(h).listeners.change();await tick();
 old.resolve(h.response({business_id:1,question:'website',history_revision:99,offset:0,limit:20,total:1,has_more:false,items:[correction('advanced',['website_status'])]}));await tick();
 assert.equal(h.ui.S.investigation.history.question,'activity');assert.equal(h.ui.S.investigation.history.data.question,'activity');assert.equal(h.notes.value,'Newer question input');assert.equal(h.ui.S.investigation.edits.website.notes,'Newer question input');assert.equal(h.ui.S.dirty,true);assert.equal(writes(h).length,0);
});
