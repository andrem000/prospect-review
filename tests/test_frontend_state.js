'use strict';
// Offline behavioral checks of the shipped frontend; no browser or npm packages.
(async () => {
const {default:assert} = await import('node:assert/strict');
const {default:fs} = await import('node:fs');
const {default:path} = await import('node:path');
const {default:vm} = await import('node:vm');
const {test} = await import('node:test');
const crypto = await import('node:crypto');
const testDirectory = path.dirname(path.resolve(process.argv[1]));
const clone = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={}; this.value=''; this.disabled=false; this.classList={add(){},toggle(){}}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  setAttribute() {}
  addEventListener(name, fn) { this.listeners[name]=fn; }
  querySelectorAll() { return this.children.flatMap(n=>n instanceof Element?[n,...n.querySelectorAll()]:[]); }
  closest() { return this; }
  focus() {}
  showModal() { this.open=true; }
  close() { this.open=false; }
}

function harness() {
  const elements = new Map();
  const document = {
    createElement: tag=>new Element(tag),
    querySelector: selector=>{
      if(!elements.has(selector))elements.set(selector,new Element('div'));
      const element=elements.get(selector);element.content='fixture-token';return element;
    },
    querySelectorAll: ()=>[],
  };
  const server={review:0,revision:0,draft:null,questions:{},commits:0,requests:new Map(),calls:[],failReads:false,failDrafts:0,loseCommitResponse:false};
  const snapshot=()=>({business:{id:1,name:'Fictional Paint',review:{},observations:[],excluded_reasons:[]},review_revision:server.review,draft_revision:server.revision,draft:clone(server.draft),questions:clone(server.questions),can_review:true,latest_session:null});
  const fetch=async (route,options)=>{
    const body=options.body?JSON.parse(options.body):undefined;
    server.calls.push({route,body});
    if(route.endsWith('/draft') && body){
      if(server.failDrafts-->0)throw Error('Injected draft connection failure');
      assert.equal(body.review_revision,server.review,'draft must use current review revision');
      assert.equal(body.draft_revision,server.revision,'draft must use current draft revision');
      server.draft=clone(body.draft);server.revision++;
      return response({review_revision:server.review,draft_revision:server.revision,draft:clone(server.draft)});
    }
    if(route.endsWith('/review') && body){server.review++;return response({review:{}});}
    if(route.endsWith('/investigation') && body){
      let result=server.requests.get(body.request_id);
      if(!result){
        assert.equal(body.review_revision,server.review);assert.equal(body.draft_revision,server.revision);
        server.review++;server.revision++;server.commits++;
        const remaining=Object.fromEntries(Object.entries(server.draft?.questions||{}).filter(([q])=>!(q in body.questions)));
        server.draft=Object.keys(remaining).length?{...server.draft,questions:remaining}:null;
        Object.assign(server.questions,clone(body.questions));
        result={review_revision:server.review,draft_revision:server.revision,session_id:'fixture-session',review:{},draft:clone(server.draft)};
        server.requests.set(body.request_id,result);
      }
      if(server.loseCommitResponse){server.loseCommitResponse=false;throw Error('Injected lost successful response');}
      return response(result);
    }
    if(server.failReads && route!=='/api/state')throw Error('Injected refresh failure');
    if(route.endsWith('/investigation'))return response(snapshot());
    if(route==='/api/state')return response({counts:{},version:'fixture'});
    return response({id:1,review:{}});
  };
  const response=value=>({ok:true,status:200,json:async()=>clone(value)});
  const context={document,fetch,window:{addEventListener(){}},crypto,Node:Element,URL,
    setTimeout:()=>1,clearTimeout(){},queueMicrotask(){},console};
  vm.createContext(context);
  let source=fs.readFileSync(path.join(testDirectory,'../prospect/web/app.js'),'utf8');
  source=source.replace(/^  state\(\)\.then\(render\).*$/m,'');
  source=source.replace(/\}\)\(\);\s*$/, 'globalThis.deskTest={S,setupInvestigation,persistDraft,commitInvestigation,changeDesk,reviewForm,guard,showDeskError,fail};\n})();');
  vm.runInContext(source,context,{filename:'app.js'});
  const desk=context.deskTest;
  desk.S.detail={id:1,review:{}};desk.S.list={matching_ids:[1,2]};desk.S.view='queue';
  desk.setupInvestigation(snapshot());
  return {desk,server,document,elements};
}

const website={website_status:'uncertain',notes:'Fictional website check'};
const activity={activity:'active',notes:'Unfinished fictional activity check'};
function pending(desk,server,{saved=true}={}) {
  const edits={website:clone(website),activity:clone(activity)};
  if(saved){server.draft={mode:'workspace',step:1,questions:edits};server.revision=1;}
  desk.setupInvestigation({review_revision:0,draft_revision:server.revision,draft:clone(server.draft),questions:{},can_review:true});
  desk.S.investigation.edits=clone(edits);
  if(!saved)desk.S.investigation.sequence++;
}

test('workspace save retains other unfinished answers despite a failed refresh',async()=>{
  const {desk,server}=harness();pending(desk,server);server.failReads=true;
  await desk.commitInvestigation(['website']).catch(()=>{});
  await desk.persistDraft();
  assert.equal(server.commits,1);
  assert.deepEqual(server.draft?.questions,{activity});
  assert.deepEqual(clone(desk.S.investigation.edits),{activity});
});

test('atomic remainder needs no frontend persistence after the review',async()=>{
  const {desk,server}=harness();pending(desk,server);server.failDrafts=1;
  await desk.commitInvestigation(['website']).catch(()=>{});
  await desk.persistDraft();
  assert.equal(server.commits,1);
  assert.deepEqual(server.draft?.questions,{activity});
  assert.equal(server.calls.filter(c=>c.route.endsWith('/draft')).length,0);
});

test('ambiguous successful retry clears its old error even if the following refresh fails',async()=>{
  const {desk,server}=harness();pending(desk,server);server.loseCommitResponse=true;server.failReads=true;
  await desk.commitInvestigation(['website']).catch(()=>{});
  await desk.commitInvestigation(['website']).catch(()=>{});
  assert.equal(server.commits,1);
  const commits=server.calls.filter(c=>c.body?.request_id);
  assert.equal(commits.length,2);
  assert.equal(commits[0].body.request_id,commits[1].body.request_id);
  assert.deepEqual(server.draft?.questions,{activity});
  assert.equal(desk.S.investigation.error,null);
  assert.equal(desk.S.investigation.conflict,false);
  assert.equal(desk.S.dirty,false);
});

function prepareAdvancedSave(desk) {
  const section=desk.reviewForm({id:1,review:{}});
  const nodes=section.querySelectorAll();const form=nodes.find(n=>n.tagName==='form');
  const selects=nodes.filter(n=>n.tagName==='select');selects[2].value='independent';
  const inputs=nodes.filter(n=>n.tagName==='input');
  inputs.find(n=>n.type==='url').value='https://example.com/fictional';
  nodes.find(n=>n.tagName==='textarea').value='Fictional advanced correction';
  form.listeners.input();
  return form;
}

test('advanced save flushes pending desk answers before changing the review',async()=>{
  const {desk,server}=harness();pending(desk,server,{saved:false});
  const form=prepareAdvancedSave(desk);
  await form.listeners.submit({preventDefault(){}});
  const writes=server.calls.filter(c=>c.body);
  assert.ok(writes.findIndex(c=>c.route.endsWith('/draft'))>=0,'pending answers must be written');
  assert.ok(writes.findIndex(c=>c.route.endsWith('/draft'))<writes.findIndex(c=>c.route.endsWith('/review')),'draft must reach storage before legacy review changes');
  assert.deepEqual(server.draft?.questions,{website,activity});
  assert.deepEqual(clone(desk.S.investigation.edits),{website,activity});
});

test('failed desk flush prevents advanced mutation and retains both sets of edits',async()=>{
  const {desk,server}=harness();pending(desk,server,{saved:false});
  const form=prepareAdvancedSave(desk);server.failDrafts=1;
  await form.listeners.submit({preventDefault(){}});
  assert.equal(server.review,0);
  assert.equal(server.calls.some(c=>c.route.endsWith('/review')),false);
  assert.equal(desk.S.advancedDirty,true);
  assert.deepEqual(clone(desk.S.investigation.edits),{website,activity});
});

test('question navigation protects unsaved advanced edits',async()=>{
  const {desk,server}=harness();pending(desk,server);
  desk.S.advancedDirty=true;desk.S.dirty=true;
  const step=desk.S.investigation.step;
  await desk.changeDesk(inv=>{inv.step=3;});
  assert.equal(desk.S.investigation.step,step,'navigation must wait for an explicit advanced-edit decision');
  assert.equal(desk.S.advancedDirty,true);
  assert.deepEqual(clone(desk.S.investigation.edits),{website,activity});
});

async function failedDraftWithRetry() {
  const h=harness();pending(h.desk,h.server,{saved:false});h.server.failDrafts=1;
  try {await h.desk.persistDraft();}catch(error){h.desk.showDeskError(error);}
  const retry=h.elements.get('[data-draft-error]').children.find(n=>n.tagName==='button');
  assert.ok(retry,'failed draft must offer a retry');
  return {...h,retry};
}

test('successful draft retry clears its own obsolete error notice',async()=>{
  const {retry,elements,server}=await failedDraftWithRetry();
  await retry.listeners.click();
  assert.deepEqual(server.draft?.questions,{website,activity});
  assert.equal(elements.get('#notice').className,'');
  assert.match(elements.get('#notice').textContent,/saved locally/i);
});

for(const message of ['Unrelated newer export failure','Injected draft connection failure']) {
  test(`draft recovery preserves a newer unrelated notice: ${message}`,async()=>{
    const {desk,retry,elements}=await failedDraftWithRetry();
    desk.fail(new Error(message));
    await retry.listeners.click();
    assert.equal(elements.get('#notice').className,'error','successful recovery must not clear another operation’s error');
    assert.equal(elements.get('#notice').textContent,message);
  });
}

test('advanced explicit activity sends its unchanged visible observation date',async()=>{
  const {desk,server}=harness();
  const section=desk.reviewForm({id:1,review:{activity:'active',evidence_observed_at:'2026-10-01'}});
  const nodes=section.querySelectorAll(),form=nodes.find(n=>n.tagName==='form');
  const activityLabel=nodes.find(n=>n.tagName==='label'&&n.children.some(c=>c.textContent==='Record a new activity check using this evidence'));
  assert.ok(activityLabel);activityLabel.children.find(n=>n.tagName==='input').checked=true;
  nodes.find(n=>n.tagName==='input'&&n.type==='url').value='https://example.com/new-activity';
  nodes.find(n=>n.tagName==='textarea').value='Explicit activity check on the displayed date';
  form.listeners.input();await form.listeners.submit({preventDefault(){}});
  const write=server.calls.find(c=>c.route.endsWith('/review'));
  assert.equal(write.body.changes.activity,'active');
  assert.equal(write.body.changes.evidence_observed_at,'2026-10-01');
});

})();
