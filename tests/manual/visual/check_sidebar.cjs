const fs = require('node:fs');
const outputDir = require('node:path').resolve(__dirname, '../../.runtime/visual');
fs.mkdirSync(outputDir, {recursive: true});
(async () => {
  const targets = await (await fetch('http://127.0.0.1:9228/json')).json();
  const target = targets.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, {once:true}));
  let counter = 0;
  const pending = new Map();
  ws.addEventListener('message', event => {
    const packet = JSON.parse(event.data);
    if (packet.id && pending.has(packet.id)) {
      const callback = pending.get(packet.id); pending.delete(packet.id);
      packet.error ? callback.reject(packet.error) : callback.resolve(packet.result);
    }
  });
  const call = (method, params={}) => new Promise((resolve,reject) => {
    const id = ++counter; pending.set(id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}));
  });
  const evaluate = async expression => (await call('Runtime.evaluate', {expression,returnByValue:true,awaitPromise:true})).result.value;
  await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', {width:1440,height:1050,deviceScaleFactor:1,mobile:false});
  const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
  await call('Page.navigate',{url:'http://127.0.0.1:8518'});
  for(let n=0;n<50;n++) {
    if(await evaluate("Boolean(document.querySelector('[data-testid=stChatInputTextArea]'))")) break;
    await pause(300);
  }
  const capture = async name => {
    const result = await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(outputDir+'/'+name+'.png',Buffer.from(result.data,'base64'));
  };
  await pause(700);
  console.log(await evaluate(`JSON.stringify({footer:(()=>{const e=document.querySelector('.st-key-sidebar_model_controls');return e&&{top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom};})(),containers:[...document.querySelectorAll('.st-key-conversation_history, .st-key-conversation_history *')].filter(e=>e.scrollHeight>e.clientHeight+10).map(e=>({cls:e.className,height:e.clientHeight,scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY})),errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText)})`));
  await capture('sidebar-final');
  await evaluate(`[...document.querySelectorAll('button')].find(e=>e.innerText==='当前挡墙方案').click()`);
  await pause(500); await capture('sidebar-inline');
  await evaluate(`(()=>{const input=[...document.querySelectorAll('input')].find(e=>e.getAttribute('aria-label')==='对话标题');const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;setter.call(input,'现场方案 A');input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();})()`);
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await pause(600);
  console.log(await evaluate("JSON.stringify({renamed:[...document.querySelectorAll('button')].some(e=>e.innerText==='现场方案 A'),mainControls:document.querySelector('[data-testid=stMain]')?.querySelectorAll('[data-testid=stSelectbox]').length})"));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(400);
  await evaluate(`document.querySelector('[data-testid=stSidebarCollapsedControl] button')?.click()`);
  await pause(400);
  console.log(await evaluate(`JSON.stringify({mobileFooter:document.querySelector('.st-key-sidebar_model_controls')?.getBoundingClientRect().bottom,viewport:innerHeight,historyScroll:(()=>{const e=document.querySelector('.st-key-conversation_history');e.scrollTop=e.scrollHeight;return e.scrollTop;})(),errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText)})`));
  await capture('sidebar-mobile');
  ws.close();
})().catch(error=>{console.error(error);process.exitCode=1;});
