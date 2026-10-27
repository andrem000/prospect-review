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

test('history loads only on expansion and all actions preserve actual local/advanced input with GETs only',async()=>{
 const h=historyHarness();assert.equal(h.calls.length,0);type(h,'Unfinished website input');
 const advanced=h.ui.reviewForm(h.ui.S.detail),form=descendants(advanced).find(n=>n.tagName==='form'),notes=descendants(form).find(n=>n.tagName==='textarea');notes.value='Unfinished advanced notes';form.listeners.input();
 const before=copy(h.ui.S.investigation.edits),advancedBefore=copy(h.ui.S.investigation.advancedInput),inv=h.ui.S.investigation;
 await open(h);expand(eventNodes(h)[0]);await click(find(h.history,'Inspect original save'));const original=eventNodes(h).at(-1);expand(original);
 filter(h).value='website';filter(h).listeners.change();await tick();await click(find(h.history,'Refresh history'));
 assert.equal(h.ui.S.investigation,inv);assert.deepEqual(copy(inv.edits),before);assert.deepEqual(copy(inv.advancedInput),advancedBefore);assert.equal(h.notes.value,'Unfinished website input');assert.equal(notes.value,'Unfinished advanced notes');assert.equal(h.ui.S.dirty,true);assert.equal(h.ui.S.advancedDirty,true);
 assert.ok(h.calls.length>=4);assert.equal(h.calls.every(c=>c.route.includes('/history?')&&c.body===undefined),true);
});

test('events retain stored order, show before/after checkedness and separate recorded/observed evidence',async()=>{
 const h=historyHarness();await open(h);const events=eventNodes(h);assert.equal(events[0].dataset.historyEvent,'review:3');assert.equal(events[1].dataset.historyEvent,'session:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
 events.forEach(expand);const shown=text(h.history);assert.match(shown,/Recorded.*2026-01-03/);assert.match(shown,/Evidence observed: 2026-01-02/);assert.match(shown,/Unchecked · no saved answer/);assert.match(shown,/Historical Undo event/);assert.match(shown,/may be inherited metadata/);assert.match(shown,/At Undo time/);assert.match(shown,/Record evidence is separate/);assert.match(shown,/Recorded question association: Website/);assert.equal(historyCalls(h).length,1);
 assert.equal(historyNodes(h).some(n=>n.tagName==='button'&&/Confirm Undo|Delete|Rollback/.test(n.textContent)),false);
});

test('same-value checks and evidence-only generic changes remain visible without fabricated origins',async()=>{
 const h=historyHarness(),same=saveEvent();same.before=copy(same.after);same.changed_questions=[];
 const generic={...saveEvent('review:7',7),kind:'saved_review',origin:'unknown',asserted_questions:[],changed_questions:[],before:{website:saved('no_site_found','Earlier basis')},after:{website:saved('no_site_found','Fresh basis')},association_unknown:true,matches_filter:'unknown',record_evidence:[{review_id:7,question:null,association_known:false,notes:'Generic evidence only',evidence_url:'https://example.com/generic',evidence_observed_at:null}],other_changes:[{field:'queue_hold',before:false,after:true},{field:'disposition',before:'pending',after:'rejected'}]};
 h.control.items=[same,generic];await open(h);eventNodes(h).forEach(expand);const shown=text(h.history);
 assert.match(shown,/Recorded check · no effective answer change/);assert.match(shown,/Saved value unchanged · evidence basis changed/);assert.match(shown,/Origin not recorded/);assert.match(shown,/Record question association: unknown/);assert.match(shown,/included conservatively/);assert.match(shown,/Earlier basis/);assert.match(shown,/Fresh basis/);assert.match(shown,/Hold: false → true/);assert.match(shown,/Disposition: pending → rejected/);assert.equal(/CLI|browser origin/i.test(shown),false);
});

test('unknown question basis does not attach generic record evidence as its answer basis',async()=>{
 const h=historyHarness(),event=saveEvent('review:8',8);event.origin='unknown';event.kind='saved_review';event.association_unknown=true;event.after.website={answers:{website_status:'uncertain'},basis_known:false,evidence_url:'https://example.com/not-proven',notes:'Do not claim association',evidence_observed_at:'2026-01-02'};event.record_evidence[0].notes='Inherited record metadata';event.record_evidence[0].association_known=false;event.record_evidence[0].question=null;
 h.control.items=[event];await open(h);expand(eventNodes(h)[0]);const shown=text(h.history);assert.match(shown,/Evidence basis unknown/);assert.match(shown,/No evidence or observation date can be reliably associated/);assert.match(shown,/Inherited record metadata/);assert.equal(shown.includes('Do not claim association'),false);assert.equal(historyNodes(h).some(n=>n.href==='https://example.com/not-proven'),false);
});

test('original save lookup uses only event_id and missing original has a clear read-only result',async()=>{
 const h=historyHarness();await open(h);expand(eventNodes(h)[0]);await click(find(h.history,'Inspect original save'));
 const query=new URL(historyCalls(h).at(-1).route,'http://local').searchParams;assert.deepEqual([...query.keys()],['event_id']);assert.equal(query.get('event_id'),'session:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');assert.match(text(h.history),/Original saved event/);
 h.control.override=q=>q.has('event_id')?h.response({business_id:1,selected_event_id:q.get('event_id'),items:[]}):null;
 await click(find(h.history,'Close original save'));await click(find(h.history,'Inspect original save'));assert.match(text(h.history),/original save is not available/);assert.equal(writes(h).length,0);
});

test('pagination is bounded to 20 and a changed revision resets to newest history',async()=>{
 const h=historyHarness();h.control.total=55;await open(h);assert.equal(find(h.history,'Newer events').disabled,true);await click(find(h.history,'Older events'));assert.equal(h.ui.S.investigation.history.offset,20);
 h.control.revision++;await click(find(h.history,'Older events'));assert.equal(h.ui.S.investigation.history.offset,0);assert.match(text(h.history),/Saved history changed/);
 const queries=historyCalls(h).map(c=>new URL(c.route,'http://local').searchParams);assert.deepEqual(queries.map(q=>q.get('offset')),['0','20','40','0']);assert.ok(queries.every(q=>q.get('limit')==='20'));
});

test('rapid filter changes ignore stale replies and never replace the current form',async()=>{
 const h=historyHarness(),old=deferred();await open(h);h.control.override=q=>q.get('question')==='website'?old.promise:null;
 const current=h.ui.S.investigation;filter(h).value='website';filter(h).listeners.change();filter(h).value='activity';filter(h).listeners.change();await tick();
 old.resolve(h.response({business_id:1,question:'website',history_revision:99,offset:0,limit:20,total:1,has_more:false,items:[saveEvent('review:99',99)]}));await tick();assert.equal(current.history.question,'activity');assert.equal(current.history.data.question,'activity');assert.equal(text(h.history).includes('review:99'),false);assert.equal(h.ui.S.investigation,current);
});

test('closed, rerendered or other-business history ignores old reads',async()=>{
 for(const action of ['close','rerender','navigate']){
  const h=historyHarness(),read=deferred();h.control.override=()=>read.promise;h.history.open=true;h.history.listeners.toggle();
  if(action==='close'){h.history.open=false;h.history.listeners.toggle();}
  if(action==='rerender'){h.history=h.ui.renderDecisionHistory(h.ui.S.detail);}
  if(action==='navigate'){h.ui.S.detail={id:2};h.ui.setupInvestigation({...copy(h.server.snapshot),business:{id:2},draft:{questions:{website:{...answer,notes:'Business two draft'}}}});}
  read.resolve(h.response({business_id:1,question:'all',history_revision:99,offset:0,limit:20,total:1,has_more:false,items:[saveEvent('review:99',99)]}));await tick();
  if(action==='navigate'){assert.equal(h.ui.S.investigation.edits.website.notes,'Business two draft');assert.equal(h.ui.S.dirty,false);}
  else if(action==='close')assert.equal(h.ui.S.investigation.history.data,null);
 }
});

test('history and original errors have independent retry controls and do not claim saves',async()=>{
 const h=historyHarness();h.control.override=()=>Promise.reject(Error('Read unavailable'));await open(h);assert.match(text(h.history),/Read unavailable/);assert.ok(find(h.history,'Retry history'));
 h.control.override=null;await click(find(h.history,'Retry history'));expand(eventNodes(h)[0]);h.control.override=q=>q.has('event_id')?Promise.reject(Error('Original unavailable')):null;
 await click(find(h.history,'Inspect original save'));assert.match(text(h.history),/Original unavailable/);h.control.override=null;await click(find(h.history,'Retry original save'));assert.match(text(h.history),/Original saved event/);assert.equal(h.ui.S.dirty,false);assert.equal(h.calls.every(c=>c.body===undefined),true);
});

test('held business history remains inspectable and unknown Undo origin invents no lookup',async()=>{
 const h=historyHarness();h.ui.S.investigation.can_review=false;const event=undoEvent();event.original_event_id=null;event.original_session_id=null;event.undo_result=null;h.control.items=[event];await open(h);expand(eventNodes(h)[0]);assert.match(text(h.history),/Original save association: not recorded/);assert.match(text(h.history),/Historical Undo summary: not recorded/);assert.equal(historyNodes(h).some(n=>n.tagName==='button'&&n.textContent==='Inspect original save'),false);assert.equal(h.calls.every(c=>c.body===undefined),true);
});

test('closing an in-flight changed filter cannot reopen cached results from a different filter',async()=>{
 const h=historyHarness(),read=deferred();await open(h);h.control.override=q=>q.get('question')==='fit'?read.promise:null;
 filter(h).value='fit';filter(h).listeners.change();h.history.open=false;h.history.listeners.toggle();
 assert.equal(h.ui.S.investigation.history.data,null);h.control.override=null;await open(h);assert.equal(h.ui.S.investigation.history.data.question,'fit');
 read.resolve(h.response({business_id:1,question:'fit',offset:0,limit:20,history_revision:99,total:1,has_more:false,items:[saveEvent('review:99',99)]}));await tick();assert.equal(h.ui.S.investigation.history.revision,3);assert.equal(h.calls.every(c=>c.body===undefined),true);
});

test('a newer revision seen through original lookup also resets history paging without losing inspection',async()=>{
 const h=historyHarness();h.control.total=55;await open(h);await click(find(h.history,'Older events'));expand(eventNodes(h)[0]);h.control.revision++;
 await click(find(h.history,'Inspect original save'));assert.equal(h.ui.S.investigation.history.offset,0);assert.match(text(h.history),/Saved history changed/);assert.match(text(h.history),/Original saved event/);assert.equal(h.ui.S.investigation.history.original.event.id,'session:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
});
