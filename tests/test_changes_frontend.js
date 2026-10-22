'use strict';
// Offline interaction checks of the shipped Changes UI and source-context guard.
(async()=>{
const {default:assert}=await import('node:assert/strict');
const {default:fs}=await import('node:fs');
const {default:path}=await import('node:path');
const {default:vm}=await import('node:vm');
const {test}=await import('node:test');
const crypto=await import('node:crypto');
class Element{
  constructor(tag){this.tagName=tag;this.dataset={};this.className='';this.children=[];this.listeners={};this.disabled=false;this.value='';this.classList={add(){},toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  addEventListener(name,fn){this.listeners[name]=fn;}
  querySelectorAll(){return this.children.flatMap(n=>n instanceof Element?[n,...n.querySelectorAll()]:[]);}
  querySelector(){return null;}
  closest(){return this;}
  focus(){}
}
const clone=v=>JSON.parse(JSON.stringify(v));
function harness(){
  const elements=new Map(),calls=[];
  const document={createTextNode:text=>Object.assign(new Element('text'),{textContent:text}),createElement:tag=>new Element(tag),querySelector:selector=>{if(!elements.has(selector))elements.set(selector,new Element('div'));const node=elements.get(selector);node.content='fixture-token';return node;},querySelectorAll:()=>[]};
  const sourceReason={id:'source-one',kind:'source_changed',question:'website',questions:['website'],label:'Listing fields changed',seen:false,source:'Fictional',source_id:'one',before_batch_id:1,after_batch_id:2,before_observed_at:'2025-01-01',after_observed_at:'2024-01-01',fields:[{field:'websites',before:[],after:['https://example.test']}],warnings:['Incoming evidence is older.']};
  const server={items:[{business_id:1,name:'Fictional Paint',city:'Demo',state:'CA',has_review:true,has_draft:true,can_review:true,excluded_reasons:[],reasons:[sourceReason,{id:'age-one',kind:'evidence_old',question:'activity',label:'Old evidence',seen:true,evidence_observed_at:'2024-01-01',evidence_age_days:600}]}],review:3,draft:2,source:'source-b',draftSource:'source-a',questions:{},savedDraft:{mode:'workspace',step:2,questions:{activity:{activity:'active',notes:'Retained draft'}}},failDraft:false,staleCompare:false};
  server.business={id:1,name:'Fictional Paint',review:{},observations:[],excluded_reasons:[],websites:['https://new-context.example']};
  const snapshot=()=>({business:clone(server.business),review_revision:server.review,draft_revision:server.draft,source_revision:server.source,draft_source_revision:server.draftSource,source_changed:server.source!==server.draftSource,draft:clone(server.savedDraft),questions:clone(server.questions),can_review:true});
  const response=(value,status=200)=>({ok:status===200,status,json:async()=>clone(value)});
  const fetch=async(route,options)=>{
    const body=options.body?JSON.parse(options.body):undefined;calls.push({route,body});
    if(route==='/api/changes')return response({items:server.items,counts:{pending:1,seen:1}});
    if(route==='/api/changes/seen'){for(const item of server.items)for(const reason of item.reasons)if(body.reason_ids.includes(reason.id))reason.seen=body.seen;return response({});}
    if(route==='/api/import/preview'){const waiting=server.previewPromise;server.previewPromise=null;return response(waiting?await waiting:{preview_id:'new-preview',columns:[],mapping:{},sample:[],can_import:true,total_rows:1,valid_rows:1,skipped_rows:0});}
    if(route==='/api/import/compare')return response({comparison_id:'comparison-fixture',summary:{new_businesses:0,changed_businesses:1},changes:[sourceReason]});
    if(route==='/api/import/commit')return server.staleCompare?response({error:'Comparison stale; compare again'},409):response({metrics:{inserted:0,duplicates:1}});
    if(route.endsWith('/draft')){if(server.failDraft)throw Error('Draft connection failed');server.savedDraft=clone(body.draft);server.draftSource=body.source_revision;server.draft++;return response({...snapshot(),draft:clone(server.savedDraft)});}
    if(route.endsWith('/investigation'))return response(snapshot());
    if(route==='/api/state')return response({counts:{total:1},version:'fixture',imports:[],exports:[]});
    return response({id:1,name:'Fictional Paint',review:{},observations:[],excluded_reasons:[]});
  };
  const context={document,fetch,window:{addEventListener(){},confirm:()=>true},crypto,Node:Element,URL,setTimeout:()=>1,clearTimeout(){},queueMicrotask(){},console};vm.createContext(context);
  let source=fs.readFileSync(path.join(path.dirname(path.resolve(process.argv[1])),'../prospect/web/app.js'),'utf8');
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'').replace(/\}\)\(\);\s*$/,'globalThis.ui={S,changesState,renderChanges,setupInvestigation,persistDraft,commitInvestigation,reviewUpdatedEvidence,importChoose,importPreview,importRights,navigate,guard,openDetail};\n})();');
  vm.runInContext(source,context,{filename:'app.js'});const ui=context.ui;ui.S.view='changes';ui.S.state={counts:{total:1}};
  return {ui,server,calls,elements,snapshot};
}
const descendants=root=>[root,...root.querySelectorAll()];
const text=root=>descendants(root).map(n=>n.textContent||'').join('\n');
const find=(root,label)=>{const node=descendants(root).find(n=>n.tagName==='button'&&n.textContent===label);assert.ok(node,`button ${label}`);return node;};
const click=async node=>node.listeners.click({currentTarget:node});
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('inbox filters displayed reasons and acknowledges only the clicked reason',async()=>{
  const {ui,server,calls,elements}=harness();await ui.renderChanges();const root=elements.get('#content');
  assert.match(text(root),/Unfinished draft present/);assert.match(text(root),/Incoming evidence is older/);assert.doesNotMatch(text(root),/Old evidence/);
  await click(find(root,'Mark seen'));
  assert.deepEqual(calls.find(c=>c.route==='/api/changes/seen').body,{reason_ids:['source-one'],seen:true});
  assert.equal(server.items[0].reasons[1].seen,true);
  assert.equal(calls.some(c=>c.route.endsWith('/review')||c.route.endsWith('/draft')),false);
  ui.changesState.filter='seen';await ui.renderChanges();assert.match(text(root),/Old evidence/);
  await click(find(root,'Restore pending'));assert.equal(server.items[0].reasons[0].seen,false);
});
test('prior-work filter hides untouched businesses while exclusions remain inspectable',async()=>{
  const {ui,server,elements}=harness();server.items[0].has_review=false;server.items[0].has_draft=false;ui.changesState.prior=true;await ui.renderChanges();assert.match(text(elements.get('#content')),/No attention matches/);
  ui.changesState.prior=false;server.items[0].can_review=false;server.items[0].excluded_reasons=['suppressed_or_contacted'];await ui.renderChanges();assert.match(text(elements.get('#content')),/Restrictions retained/);find(elements.get('#content'),'Inspect website');
});
test('inbox investigation chooses affected question and preserves return filters',async()=>{
  const {ui,elements}=harness();ui.changesState.filter='all';await ui.renderChanges();await click(find(elements.get('#content'),'Investigate website'));await settle();
  assert.equal(ui.S.investigation.step,1);assert.equal(ui.S.returnToChanges,true);assert.equal(ui.changesState.filter,'all');
  await click(find(elements.get('#content'),'← Back to changes'));await settle();assert.equal(ui.S.view,'changes');assert.equal(ui.changesState.filter,'all');
});

test('opening the desk displays the business bound to its source and review revisions',async()=>{
  const {ui,server,calls}=harness();ui.S.view='queue';await ui.openDetail(1);
  assert.deepEqual(clone(ui.S.detail.websites),['https://new-context.example']);
  assert.equal(ui.S.investigation.current_source_revision,server.source);
  assert.equal(calls.filter(c=>c.route==='/api/businesses/1/investigation').length,1);
  assert.equal(calls.some(c=>c.route==='/api/businesses/1'),false,'a separate business read could straddle an import');
});

test('a mismatched desk snapshot cannot replace existing displayed work',async()=>{
  const {ui,server,elements}=harness();ui.S.view='queue';ui.S.detail={id:9,name:'Retained work'};server.business.id=2;
  await ui.openDetail(1);
  assert.equal(ui.S.detail.id,9);assert.match(elements.get('#notice').textContent,/snapshot is incomplete/);
});
test('old source draft is retained and cannot commit until explicit evidence refresh',async()=>{
  const {ui,server,calls,snapshot}=harness();ui.S.view='queue';ui.S.detail={id:1};ui.setupInvestigation(snapshot());const original=clone(ui.S.investigation.edits);
  await ui.commitInvestigation(['activity']);assert.equal(calls.some(c=>c.route.endsWith('/investigation')&&c.body),false);
  await ui.reviewUpdatedEvidence();assert.deepEqual(clone(ui.S.investigation.edits),original);assert.equal(ui.S.investigation.source_revision,'source-b');assert.equal(server.draftSource,'source-b');assert.equal(ui.S.investigation.source_changed,false);
});
test('failed draft save blocks accepting changed evidence and retains input',async()=>{
  const {ui,server,snapshot}=harness();ui.S.view='queue';ui.S.detail={id:1};ui.setupInvestigation(snapshot());ui.S.investigation.sequence++;server.failDraft=true;await ui.reviewUpdatedEvidence();assert.equal(ui.S.investigation.source_revision,'source-a');assert.equal(ui.S.investigation.edits.activity.notes,'Retained draft');
});
test('failed acceptance save retains the old context until retry succeeds',async()=>{
  const {ui,server,snapshot}=harness();ui.S.view='queue';ui.S.detail={id:1};ui.setupInvestigation(snapshot());server.failDraft=true;await ui.reviewUpdatedEvidence();
  assert.equal(ui.S.investigation.source_revision,'source-a');assert.equal(ui.S.investigation.source_changed,true);assert.equal(ui.S.investigation.edits.activity.notes,'Retained draft');
  server.failDraft=false;await ui.reviewUpdatedEvidence();assert.equal(ui.S.investigation.source_revision,'source-b');assert.equal(ui.S.investigation.source_changed,false);
});
test('a cleared draft does not reuse its obsolete source context',()=>{
  const {ui,snapshot}=harness();const state=snapshot();state.draft=null;state.source_changed=false;ui.setupInvestigation(state);assert.equal(ui.S.investigation.source_revision,'source-b');
});
test('saved answers appear with affected changes without inventing a legacy evidence date',async()=>{
  const {ui,server,elements}=harness();server.items[0].saved_answers={website:{answers:{website_status:'no_site_found'},basis_known:false,evidence_observed_at:null}};await ui.renderChanges();
  const output=text(elements.get('#content'));assert.match(output,/Saved website answer/);assert.match(output,/no site found/);assert.match(output,/Evidence date\/basis unknown/);
});

for(const changed of ['content','filename','adapter','file'])test(`late preview cannot replace the current ${changed} input`,async()=>{
  const {ui,server,calls}=harness(),outer=new Element('div');ui.S.view='import';ui.S.importing={step:1,content:'Source A',filename:'a.csv',adapter:'canonical',preview:null,comparison:null};
  let resolvePreview;server.previewPromise=new Promise(resolve=>{resolvePreview=resolve;});ui.importChoose(outer);
  const nodes=descendants(outer),form=nodes.find(n=>n.tagName==='form'),content=nodes.find(n=>n.tagName==='textarea'),filename=nodes.find(n=>n.tagName==='input'&&n.type==='text'),adapter=nodes.find(n=>n.tagName==='select'),file=nodes.find(n=>n.type==='file');
  form.listeners.submit({preventDefault(){}});assert.equal(calls.find(c=>c.route==='/api/import/preview').body.content,'Source A');
  if(changed==='content'){content.value='Source B';content.listeners.input();}
  if(changed==='filename'){filename.value='b.csv';filename.listeners.input();}
  if(changed==='adapter'){adapter.value='osm';adapter.listeners.change();}
  if(changed==='file'){file.files=[{name:'b.csv',size:8,text:async()=>'Source B'}];await file.listeners.change();}
  resolvePreview({preview_id:'obsolete-a',columns:[],sample:[],can_import:true,total_rows:1,valid_rows:1,skipped_rows:0});await settle();
  assert.equal(ui.S.importing.preview,null);assert.equal(ui.S.importing.step,1);assert.equal(ui.S.importing.comparison,null);
  form.listeners.submit({preventDefault(){}});await settle();assert.equal(ui.S.importing.preview.preview_id,'new-preview');assert.equal(ui.S.importing.step,2);
});
test('an unfinished file read cannot overwrite newer pasted input or allow previewing the old file',async()=>{
  const {ui,calls}=harness(),outer=new Element('div');ui.S.view='import';ui.S.importing={step:1,content:'Previous source',filename:'old.csv',adapter:'canonical',preview:null};ui.importChoose(outer);
  const nodes=descendants(outer),form=nodes.find(n=>n.tagName==='form'),content=nodes.find(n=>n.tagName==='textarea'),file=nodes.find(n=>n.type==='file');let resolveFile;file.files=[{name:'late.csv',size:8,text:()=>new Promise(resolve=>{resolveFile=resolve;})}];const reading=file.listeners.change();
  form.listeners.submit({preventDefault(){}});assert.equal(calls.some(c=>c.route==='/api/import/preview'),false);
  content.value='New pasted source';content.listeners.input();resolveFile('Obsolete file');await reading;assert.equal(ui.S.importing.content,'New pasted source');assert.equal(ui.S.importing.filename,'old.csv');
});
test('changing a mapping while recheck is in flight rejects that response',async()=>{
  const {ui,server}=harness(),outer=new Element('div');ui.S.view='import';ui.S.importing={step:2,content:'Source A',filename:'a.csv',adapter:'canonical',preview:{preview_id:'first',columns:['name','other'],mapping:{name:'name'},sample:[],can_import:true,total_rows:1,valid_rows:1,skipped_rows:0}};ui.importPreview(outer);
  let resolvePreview;server.previewPromise=new Promise(resolve=>{resolvePreview=resolve;});const recheck=click(find(outer,'Apply mapping & recheck'));
  const mapping=descendants(outer).find(n=>n.className==='mapping-grid section-gap');mapping.listeners.change();resolvePreview({preview_id:'obsolete-mapping',columns:[],sample:[],can_import:true});await recheck;
  assert.equal(ui.S.importing.preview.preview_id,'first');assert.equal(find(outer,'Continue to source rights →').disabled,true);
});

test('source rights require comparison and source edits invalidate confirmation',async()=>{
  const {ui,calls}=harness();const outer=new Element('div');ui.S.importing={preview:{preview_id:'preview-one',valid_rows:1},rights:{source:'Fictional',license:'synthetic'},notice:'',filename:'fixture.csv'};ui.importRights(outer);
  const submit=descendants(outer).find(n=>n.textContent==='Confirm & import');assert.equal(submit.disabled,true);
  await click(find(outer,'Compare with workspace'));assert.equal(submit.disabled,false);assert.equal(ui.S.importing.comparison.comparison_id,'comparison-fixture');
  const source=descendants(outer).find(n=>n.tagName==='input'&&n.value==='Fictional');source.value='Different source';source.listeners.input();assert.equal(submit.disabled,true);assert.equal(ui.S.importing.comparison,null);
  assert.equal(calls.some(c=>c.route==='/api/import/commit'),false);
});
test('stale import comparison requires a new comparison before retry',async()=>{
  const {ui,server,calls}=harness();server.staleCompare=true;const outer=new Element('div');ui.S.importing={preview:{preview_id:'preview-one',valid_rows:1},rights:{source:'Fictional',license:'synthetic'},notice:'',filename:'fixture.csv'};ui.importRights(outer);await click(find(outer,'Compare with workspace'));
  const form=descendants(outer).find(n=>n.tagName==='form');await form.listeners.submit({preventDefault(){}});await settle();
  const commit=calls.find(c=>c.route==='/api/import/commit');assert.equal(commit.body.comparison_id,'comparison-fixture');assert.equal(ui.S.importing.comparison,null);assert.equal(find(outer,'Confirm & import').disabled,true);find(outer,'Compare with workspace');
});
})();
