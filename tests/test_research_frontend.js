'use strict';
// Offline behavioral checks against shipped code. Fixtures are fictional.
(async()=>{
const {default:assert}=await import('node:assert/strict');
const {default:fs}=await import('node:fs');
const {default:path}=await import('node:path');
const {default:vm}=await import('node:vm');
const {test}=await import('node:test');
const crypto=await import('node:crypto');
const clone=value=>JSON.parse(JSON.stringify(value));
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.value='';this.disabled=false;this.classList={add(){},toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(event,fn){this.listeners[event]=fn;}
  setAttribute(name,value){this[name]=value;}
  querySelectorAll(){return this.children.flatMap(node=>node instanceof Element?[node,...node.querySelectorAll()]:[]);}
  showModal(){this.open=true;}
  close(){this.open=false;}
  focus(){}
  reportValidity(){return true;}
}
const proposal={identity_status:'uncertain',evidence_url:'https://example.com/fictional',evidence_observed_at:'2026-10-20',notes:'Fictional supplied page; phone missing.'};
const record=()=>({id:'capture-1',business_id:1,mode:'paste',url:'https://example.com/fictional',title:'Fictional page',observed_at:'2026-10-20',captured_at:'2026-10-22T12:00:00Z',excerpt:'Fictional page excerpt',signals:[{kind:'unknown',field:'phone',label:'Phone',detail:'No phone published.'}],proposals:{identity:clone(proposal)},limitations:['Saved content is user supplied.'],dismissed:false,stale:false});
function harness(){
  const elements=new Map(),server={records:[record()],calls:[],revision:0,draft:null,failDraft:false,failResearch:false,waitResearch:null,waitRead:null,confirms:0,confirm:true};
  const document={createElement:tag=>new Element(tag),querySelector:selector=>{if(!elements.has(selector))elements.set(selector,new Element('div'));const node=elements.get(selector);node.content='fixture';return node;},querySelectorAll:()=>[]};
  const response=value=>({ok:true,json:async()=>clone(value)});
  const fetch=async(route,options)=>{
    const body=options.body?JSON.parse(options.body):undefined;server.calls.push({route,body});
    if(route.endsWith('/research') && body){if(server.waitResearch)await server.waitResearch;if(server.failResearch)throw Error('Fixture unavailable');return response({record:server.records[0]});}
    if(route.endsWith('/research')){if(server.waitRead)await server.waitRead;return response({records:server.records});}
    if(route.endsWith('/dismiss')){server.records[0].dismissed=body.dismissed;return response({record:server.records[0]});}
    if(route.endsWith('/draft')){if(server.failDraft)throw Error('Fixture draft unavailable');server.draft=clone(body.draft);server.revision++;return response({review_revision:0,draft_revision:server.revision,draft:server.draft});}
    return response({});
  };
  const context={document,fetch,window:{addEventListener(){},confirm(){server.confirms++;return server.confirm;}},crypto,URL,setTimeout:()=>1,clearTimeout(){},queueMicrotask(){},console};
  vm.createContext(context);
  let source=fs.readFileSync(path.join(path.dirname(path.resolve(process.argv[1])),'../prospect/web/app.js'),'utf8');
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'');
  source=source.replace(/\}\)\(\);\s*$/,'globalThis.researchTest={S,setupInvestigation,researchState,requestResearch,loadResearch,dismissResearch,applyResearch,renderResearch,researchCard,fail};\n})();');
  vm.runInContext(source,context,{filename:'app.js'});
  const desk=context.researchTest;desk.S.detail={id:1};desk.setupInvestigation({can_review:true,review_revision:0,draft_revision:0,questions:{},draft:null});
  return {desk,server,elements};
}

test('proposal becomes only the selected editable draft, retaining activity and requiring replacement confirmation',async()=>{
  const {desk,server}=harness(),inv=desk.S.investigation;
  inv.edits={identity:{notes:'Existing answer'},activity:{activity:'active',notes:'Keep dated evidence'}};inv.sequence++;
  await desk.applyResearch(1,'identity',record(),()=>{});
  assert.equal(server.confirms,1);assert.deepEqual(clone(inv.edits.identity),proposal);
  assert.deepEqual(clone(inv.edits.activity),{activity:'active',notes:'Keep dated evidence'});
  assert.deepEqual(server.draft.questions,clone(inv.edits));
  assert.ok(server.calls.every(call=>!call.route.endsWith('/investigation')&&!call.route.endsWith('/review')));
  assert.equal(server.calls.filter(call=>call.route.endsWith('/draft')).length,2,'flush before replacement and revision-aware save after');
});
test('cancel replacement and unsaved advanced edits both leave answers untouched',async()=>{
  const {desk,server}=harness(),inv=desk.S.investigation;inv.edits.identity={notes:'Keep this'};server.confirm=false;
  await desk.applyResearch(1,'identity',record(),()=>{});assert.equal(inv.edits.identity.notes,'Keep this');assert.equal(server.draft,null);
  const count=server.calls.length;desk.S.advancedDirty=true;
  await desk.applyResearch(1,'identity',record(),()=>{});assert.equal(server.calls.length,count);assert.equal(desk.S.advancedDirty,true);
});
test('failed draft flush keeps every answer and prevents proposal replacement',async()=>{
  const {desk,server}=harness(),inv=desk.S.investigation;inv.edits={identity:{notes:'Original'},fit:{notes:'Unfinished fit'}};inv.sequence++;server.failDraft=true;
  await desk.applyResearch(1,'identity',record(),()=>{});
  assert.deepEqual(clone(inv.edits),{identity:{notes:'Original'},fit:{notes:'Unfinished fit'}});
  assert.match(desk.researchState(1).error,/draft unavailable/);
});
test('discarding advanced edits clears only its own obsolete research warning',async()=>{
  const {desk,elements}=harness();desk.S.advancedDirty=true;desk.S.dirty=true;
  await desk.applyResearch(1,'identity',record(),()=>{});
  assert.equal(elements.get('#notice').className,'error');
  elements.get('#discard-editing').listeners.click();
  assert.equal(elements.get('#notice').className,'');assert.match(elements.get('#notice').textContent,/discarded/);
});
test('successful capture preserves genuinely pending advanced warning and newer unrelated errors',async()=>{
  const {desk,elements}=harness(),r=desk.researchState(1);r.confirmed=true;r.url='https://example.com/fictional';desk.S.advancedDirty=true;
  await desk.applyResearch(1,'identity',record(),()=>{});
  await desk.requestResearch(1,r,'fetch',()=>{});
  assert.match(elements.get('#notice').textContent,/Save or discard/);
  desk.S.advancedDirty=false;
  await desk.requestResearch(1,r,'fetch',()=>{});
  assert.equal(elements.get('#notice').className,'');assert.match(elements.get('#notice').textContent,/Page evidence saved/);
  desk.S.advancedDirty=true;await desk.applyResearch(1,'identity',record(),()=>{});desk.fail(new Error('Unrelated export error'));
  desk.S.advancedDirty=false;await desk.requestResearch(1,r,'fetch',()=>{});
  assert.equal(elements.get('#notice').textContent,'Unrelated export error');assert.equal(elements.get('#notice').className,'error');
});
test('failed proposal autosave retains the new draft and unrelated answers for retry',async()=>{
  const {desk,server}=harness(),inv=desk.S.investigation;inv.edits.activity={activity:'active',notes:'Keep activity'};server.failDraft=true;
  await desk.applyResearch(1,'identity',record(),()=>{});
  assert.deepEqual(clone(inv.edits.identity),proposal);assert.deepEqual(clone(inv.edits.activity),{activity:'active',notes:'Keep activity'});
  assert.equal(desk.researchState(1).applying,false);assert.match(desk.researchState(1).error,/draft unavailable/);
  assert.equal(server.calls.some(call=>call.route.endsWith('/review')),false);
});
test('stale, dismissed, excluded and wrong-question proposals cannot apply',async()=>{
  for(const mode of ['stale','dismissed','excluded','wrongQuestion']){
    const {desk,server}=harness();if(mode==='excluded')desk.S.investigation.can_review=false;
    else if(mode==='wrongQuestion')desk.S.investigation.step=1;
    else server.records[0][mode]=true;
    await desk.applyResearch(1,'identity',server.records[0],()=>{});
    assert.deepEqual(clone(desk.S.investigation.edits),{},mode);assert.equal(server.draft,null,mode);
  }
});
test('fresh server staleness supersedes a previously usable card',async()=>{
  const {desk,server}=harness(),old=record();server.records[0].stale=true;
  await desk.applyResearch(1,'identity',old,()=>{});
  assert.equal(server.draft,null);assert.match(desk.researchState(1).error,/no longer available/);
});
test('switching businesses during proposal read ignores its result',async()=>{
  const {desk,server}=harness();let release;server.waitRead=new Promise(resolve=>{release=resolve;});
  const pending=desk.applyResearch(1,'identity',record(),()=>{});
  desk.S.detail={id:2};desk.setupInvestigation({can_review:true,questions:{},draft:null,review_revision:0,draft_revision:0});release();await pending;
  assert.deepEqual(clone(desk.S.investigation.edits),{});assert.equal(server.draft,null);
});
test('explicit retention is required; failed requests retain form inputs and reuse exact idempotency payload',async()=>{
  const {desk,server}=harness(),r=desk.researchState(1);Object.assign(r,{url:'https://example.com/saved',content:'Fictional saved text',observed_at:'2026-10-20'});
  await desk.requestResearch(1,r,'paste',()=>{});assert.equal(server.calls.length,0);
  r.confirmed=true;server.failResearch=true;
  await desk.requestResearch(1,r,'paste',()=>{});await desk.requestResearch(1,r,'paste',()=>{});
  assert.deepEqual(server.calls[0].body,server.calls[1].body);assert.equal(r.content,'Fictional saved text');assert.equal(r.observed_at,'2026-10-20');
  assert.match(r.error,/says nothing about whether a website exists/);
  r.content='Changed supplied text';await desk.requestResearch(1,r,'paste',()=>{});
  assert.notEqual(server.calls[1].body.request_id,server.calls[2].body.request_id);
});
test('one in-flight check, per-business results, and no unrelated pane refresh after navigation',async()=>{
  const {desk,server}=harness(),r=desk.researchState(1);r.confirmed=true;r.url='https://example.com/one';
  let release;server.waitResearch=new Promise(resolve=>{release=resolve;});
  const slot=desk.renderResearch({id:1},'identity');const original=slot.children[0];original.open=true;original.listeners.toggle();
  const request=desk.requestResearch(1,r,'fetch',()=>{});
  desk.S.detail={id:2};desk.setupInvestigation({can_review:true,questions:{}});
  const second=desk.researchState(2);second.confirmed=true;second.url='https://example.com/two';await desk.requestResearch(2,second,'fetch',()=>{});
  assert.match(second.error,/already running/);release();await request;
  assert.equal(r.records[0].business_id,1);assert.equal(second.records.length,0);
});
test('dismiss is reversible and never changes review drafts',async()=>{
  const {desk,server}=harness(),r=desk.researchState(1);r.records=clone(server.records);
  await desk.dismissResearch(1,r,r.records[0],()=>{});assert.equal(r.records[0].dismissed,true);
  await desk.dismissResearch(1,r,r.records[0],()=>{});assert.equal(r.records[0].dismissed,false);assert.equal(server.draft,null);
});
test('remote HTML and unsafe source links are rendered as inert text, never injected',()=>{
  const {desk}=harness(),r=desk.researchState(1),remote=record();r.loaded=true;
  remote.title='<img src=x onerror=alert(1)>';remote.excerpt='<script>bad()</script>';remote.final_url='javascript:alert(1)';
  const card=desk.researchCard(1,'identity',r,remote,()=>{}),nodes=card.querySelectorAll();
  assert.ok(nodes.some(node=>node.textContent===remote.title));assert.ok(nodes.some(node=>node.textContent===remote.excerpt));
  assert.ok(nodes.every(node=>!node.innerHTML));assert.ok(nodes.every(node=>!String(node.href||'').startsWith('javascript:')));
});
test('evidence refresh preserves the rest of the desk including advanced edit DOM',async()=>{
  const {desk}=harness();desk.S.advancedDirty=true;
  const slot=desk.renderResearch({id:1},'identity'),summary=slot.children[0];
  const inv=desk.S.investigation;summary.open=true;await summary.listeners.toggle();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(desk.S.investigation,inv);assert.equal(desk.S.advancedDirty,true);
  assert.ok(slot.children[0]!==summary,'only research slot replaced');
});
})();
