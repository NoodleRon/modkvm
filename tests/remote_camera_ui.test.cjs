// Browser lifecycle tests use fake media tracks and uploads; no browser/camera is opened.
// Run: node --test tests/remote_camera_ui.test.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../portabotty.py'),'utf8');
const consoleHTML=source.split('CONSOLE = r"""')[1].split('"""')[0];
const script=consoleHTML.split('<script>')[1].split('</script>')[0];
const sharing=script.slice(script.indexOf('// ---- voluntary remote camera;'),script.indexOf('// ---- state'));
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate)};

function harness(options={}){
  const elements={},events={},requests=[],timers=new Map();let timer=0,permissionCalls=0,stops=0;
  const trackEvents={},track={stop(){stops++},addEventListener(k,f){trackEvents[k]=f}};
  const stream={getTracks:()=>[track],getVideoTracks:()=>[track]};
  const $=id=>elements[id]||(elements[id]={value:'Laptop camera',hidden:true,textContent:'',disabled:false,addEventListener(){}});
  const ctx={$,U:p=>p,hub:{sb:options.serverUp!==false,remote_camera_support:options.supported!==false,remote_cameras:options.enabled!==false},tick:0,viewCam:'',ls:{set(){}},poll(){},
    setTimeout:f=>{timers.set(++timer,f);return timer},clearTimeout:id=>timers.delete(id),
    AbortController,Error,Promise,encodeURIComponent,
    window:{isSecureContext:options.secure!==false},
    navigator:{mediaDevices:{async getUserMedia(constraints){
      permissionCalls++;assert.equal(constraints.audio,false);assert.ok(constraints.video);
      if(options.permission)return options.permission;
      if(options.denied)throw Error('Permission denied');return stream;
    }}},
    document:{hidden:false,addEventListener(k,f){events[k]=f},createElement(tag){
      if(tag==='video')return{videoWidth:1280,videoHeight:720,async play(){},pause(){}};
      if(tag==='canvas')return{getContext:()=>({drawImage(){}}),toBlob:f=>f({size:100})};
      throw Error('unexpected element');
    }},
    addEventListener(k,f){events[k]=f},
    async fetch(url,init){
      requests.push({url,init});
      if(url==='/remote/start')return{ok:!options.startError,json:async()=>options.startError?{ok:false,error:'name already sharing'}:{ok:true,name:'remote-laptop-camera',token:'temporary-test-token'}};
      if(url.includes('/frame/')&&options.uploadError)throw Error('connection lost');
      return{ok:true,json:async()=>({ok:true})};
    }
  };
  vm.createContext(ctx);vm.runInContext(sharing,ctx);
  return{ctx,$,events,requests,timers,stream,trackEvents,permissionCalls:()=>permissionCalls,stops:()=>stops};
}

test('full bundled console script parses',()=>{new vm.Script(script)});

test('capture requires a click; sharing is visible; stop releases tracks and permission',async()=>{
  const h=harness();assert.equal(h.permissionCalls(),0);assert.equal(h.requests.length,0);
  await h.$('share').onclick();await flush();
  assert.equal(h.permissionCalls(),1);assert.equal(h.$('sharing').hidden,false);
  assert.match(h.$('sharestate').textContent,/Sharing Laptop camera/);
  const frame=h.requests.find(r=>r.url.includes('/frame/'));
  assert.equal(frame.init.headers['X-PB-Camera'],'temporary-test-token');
  assert.equal(frame.init.headers['Content-Type'],'image/jpeg');
  h.$('stopshare').onclick();await flush();
  assert.equal(h.stops(),1);assert.equal(h.$('sharing').hidden,true);assert.equal(h.timers.size,0);
  assert.ok(h.requests.some(r=>r.url.includes('/stop/')));
  assert.equal(vm.runInContext('localShare',h.ctx),null);
});

test('hidden page and permission ending both stop capture',async()=>{
  for(const event of ['visibilitychange','ended','pagehide']){
    const h=harness();await h.$('share').onclick();await flush();
    if(event==='ended')h.trackEvents.ended();
    else{h.ctx.document.hidden=true;h.events[event]()}
    await flush();assert.equal(h.stops(),1);assert.equal(h.timers.size,0);
  }
});

test('server opt-in and secure context are checked before requesting media',async()=>{
  for(const options of [{enabled:false},{secure:false},{supported:false},{serverUp:false}]){
    const h=harness(options);await h.$('share').onclick();
    assert.equal(h.permissionCalls(),0);assert.equal(h.requests.length,0);
  }
});

test('setup status distinguishes offline, old backend, disabled sharing, and insecure context',()=>{
  for(const [options,message] of [[{serverUp:false},/stopped or unreachable/],[{supported:false},/older code/],[{enabled:false},/disabled on the server/],[{secure:false},/HTTPS or an SSH tunnel/],[{},/Ready to share/]]){
    const h=harness(options);vm.runInContext('cameraSetupStatus()',h.ctx);
    assert.match(h.$('sharestatus').textContent,message);
    assert.equal(h.permissionCalls(),0);
  }
});

test('permission refusal does not register a source or retry',async()=>{
  const h=harness({denied:true});await h.$('share').onclick();await flush();
  assert.equal(h.requests.length,0);assert.equal(h.$('sharing').hidden,true);
  assert.equal(h.$('share').disabled,false);assert.equal(h.timers.size,0);
});

test('upload failure or conflicting source stops tracks without retrying capture',async()=>{
  for(const options of [{uploadError:true},{startError:true}]){
    const h=harness(options);await h.$('share').onclick();await flush();
    assert.equal(h.stops(),1);assert.equal(h.$('sharing').hidden,true);
    assert.equal(h.permissionCalls(),1);assert.equal(h.timers.size,0);
  }
});

test('cancel while permission is pending also stops late-arriving tracks',async()=>{
  let resolve;const permission=new Promise(r=>resolve=r),h=harness({permission});
  const pending=h.$('share').onclick();h.$('stopshare').onclick();resolve(h.stream);
  await pending;await flush();assert.equal(h.stops(),1);assert.equal(h.requests.length,0);
});

test('polling preserves a selected disconnected remote view; it resumes on the same row',async()=>{
  const h=harness(),pollCode=script.slice(script.indexOf('function poll(){'),script.indexOf('function stream(force)'));
  h.ctx.fetch=async url=>({json:async()=>url==='/hub'?{sb:true,status:{}}:[
    {name:'local-camera',source:'local',present:true},
    {name:'remote-laptop-camera',source:'remote',present:false}
  ]});
  Object.assign(h.ctx,{cams:[],viewCam:'remote-laptop-camera',devs:[],links:{},vo:false,lastActive:undefined,cal:null,
    camOf:n=>h.ctx.cams.find(c=>c.name===n),dockSize(){},chips(){},stream(){},placeholder(){},render(){},maybeAutoCal(){}});
  for(const id of ['vo','kbi','sbi','ptr']){
    const el=h.$(id);el.classList={toggle(){}};el.style={};
  }
  h.ctx.document.body={classList:{toggle(){}}};
  vm.runInContext(pollCode,h.ctx);h.ctx.poll();await flush();
  assert.equal(h.ctx.viewCam,'remote-laptop-camera');
  h.ctx.cams[1].present=true;h.ctx.poll();await flush();
  assert.equal(h.ctx.viewCam,'remote-laptop-camera');
});
