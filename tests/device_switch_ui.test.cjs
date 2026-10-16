// Synthetic device clicks, delayed status responses and canceled images. No browser or hardware.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../portabotty.py'),'utf8');
const script=source.split('CONSOLE = r"""')[1].split('"""')[0].split('<script>')[1].split('</script>')[0];
const statusCode=script.slice(script.indexOf('function chips(){'),script.indexOf('function placeholder(){'));
const apiCode=script.slice(script.indexOf('// ---- api ('),script.indexOf('// ---- voluntary remote camera;'));
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate)};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{resolve,promise}};
const devices=[{addr:'AA',n:1,name:'A'},{addr:'BB',n:2,name:'B'}];

function harness(){
  const els={},requests=[],commands=[];
  function element(id){return{id,dataset:{},children:[],style:{},classList:{toggle(){}},append(c){c.parentNode=this;this.children.push(c)},
    set textContent(value){this._text=value;this.children=[]},get textContent(){return this._text||''},
    insertBefore(c,before){const old=this.children.indexOf(c);if(old>=0)this.children.splice(old,1);const at=this.children.indexOf(before);this.children.splice(at<0?this.children.length:at,0,c);c.parentNode=this},
    remove(){const p=this.parentNode;if(p)p.children.splice(p.children.indexOf(this),1)},
    removeAttribute(k){delete this[k]},getAttribute(k){return this[k]??null},
    replaceWith(other){els[this.id]=other}}}
  const $=id=>els[id]||(els[id]=element(id));
  const ctx={$,U:p=>p,hub:{sb:true},devs:devices,cams:[{name:'cam-a',present:true},{name:'cam-b',present:true}],
    links:{AA:'cam-a',BB:'cam-b'},viewCam:'cam-a',lastActive:'AA',vo:false,cal:null,nonce:0,tick:0,camKey:'',
    pollPending:null,pollAgain:false,pollEpoch:0,
    AbortController,setTimeout:()=>1,clearTimeout(){},
    esc:String,ls:{set(){}},IC:{},document:{body:{classList:{toggle(){}}},createElement:()=>element('')},
    camOf:name=>ctx.cams.find(c=>c.name===name),cameraSetupStatus(){},dockSize(){},placeholder(){},render(){},maybeAutoCal(){},
    send(p,b){const d=deferred();commands.push({p,b,...d});return d.promise},
    fetch(url){const d=deferred();requests.push({url,...d});return d.promise},
  };
  vm.createContext(ctx);vm.runInContext(statusCode,ctx);
  return{ctx,$,requests,commands,async respond(i,data){requests[i].resolve({json:async()=>data});await flush()}};
}

test('top-bar clicks select a stable device address, not a snapshot list number',()=>{
  const h=harness();h.ctx.chips();h.$('dev').children[1].onclick();
  assert.equal(h.commands[0].p,'use');assert.equal(h.commands[0].b.device,'BB');
});

test('a status refresh keeps device button nodes so a click in progress is not discarded',()=>{
  const h=harness();h.ctx.chips();const buttons=[...h.$('dev').children];
  h.ctx.devs=devices.map((d,i)=>({...d,active:i===1}));h.ctx.chips();
  assert.equal(h.$('dev').children.length,2);
  assert.equal(h.$('dev').children[0],buttons[0]);assert.equal(h.$('dev').children[1],buttons[1]);
});

test('overlapping interval/click polls coalesce and apply the latest requested refresh last',async()=>{
  const h=harness();h.ctx.poll();h.ctx.poll();h.ctx.poll();
  assert.equal(h.requests.length,1,'only one status request may be in flight');
  await h.respond(0,{sb:false,status:{active:'AA',devices},links:{AA:'cam-a',BB:'cam-b'}});
  assert.equal(h.requests.length,2,'one follow-up refresh replaces repeated requests');
  await h.respond(1,{sb:false,status:{active:'BB',devices},links:{AA:'cam-a',BB:'cam-b'}});
  assert.equal(h.ctx.lastActive,'BB');
  assert.equal(h.requests.length,2);
});

test('a device click invalidates an older status snapshot before it can change the view',async()=>{
  const h=harness();h.ctx.poll();h.ctx.chips();h.$('dev').children[1].onclick();
  await h.respond(0,{sb:false,status:{active:'OLD',devices},links:{OLD:'wrong-camera'}});
  assert.equal(h.ctx.lastActive,'AA');assert.equal(h.ctx.viewCam,'cam-a');
  h.commands[0].resolve({ok:true});await flush();
  assert.equal(h.requests.length,2);
  await h.respond(1,{sb:false,status:{active:'BB',devices},links:{BB:'cam-b'}});
  assert.equal(h.ctx.lastActive,'BB');assert.equal(h.ctx.viewCam,'cam-b');
});

test('an old camera-list callback cannot restart a stream after a new device click',async()=>{
  const h=harness();h.ctx.tick=0;h.ctx.poll();
  await h.respond(0,{sb:true,status:{active:'AA',devices},links:{AA:'cam-a',BB:'cam-b'}});
  assert.equal(h.requests[1].url,'/cams');
  h.ctx.chips();h.$('dev').children.at(-1).onclick();
  await h.respond(1,[{name:'wrong-camera',present:true}]);
  assert.equal(h.ctx.viewCam,'cam-a');assert.equal(h.$('cam').src,undefined);
  h.commands[0].resolve({ok:true});await flush();
});

test('a canceled image error cannot clear the newly selected camera stream',()=>{
  const h=harness();h.ctx.stream();const old=h.$('cam'),oldError=old.onerror;
  h.ctx.viewCam='cam-b';h.ctx.stream();const current=h.$('cam'),url=current.src,key=h.ctx.camKey;
  assert.ok(url.includes('cam-b'));
  oldError();
  assert.equal(h.$('cam').src,url);assert.equal(h.ctx.camKey,key);
});

test('device and keyboard actions retain their existing serial order',async()=>{
  const requests=[],ctx={U:p=>p,fetch(url){const d=deferred();requests.push({url,...d});return d.promise}};
  vm.createContext(ctx);vm.runInContext(apiCode,ctx);
  const pending=vm.runInContext("Promise.all([send('use',{device:'AA'}),send('type',{text:'synthetic'}),send('use',{device:'BB'})])",ctx);
  await flush();assert.deepEqual(requests.map(r=>r.url),['/kb/use']);
  requests[0].resolve({json:async()=>({ok:true})});await flush();
  assert.deepEqual(requests.map(r=>r.url),['/kb/use','/kb/type']);
  requests[1].resolve({json:async()=>({ok:true})});await flush();
  assert.deepEqual(requests.map(r=>r.url),['/kb/use','/kb/type','/kb/use']);
  requests[2].resolve({json:async()=>({ok:true})});await pending;
});
