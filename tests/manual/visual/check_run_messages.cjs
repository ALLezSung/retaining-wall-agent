const fs = require('node:fs');
const outputDir = require('node:path').resolve(__dirname, '../../.runtime/visual');
fs.mkdirSync(outputDir, {recursive: true});
(async () => {
  const targets = await (await fetch('http://127.0.0.1:9229/json')).json();
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
  await call('Page.navigate',{url:'http://127.0.0.1:8519'});
  for(let n=0;n<50;n++) {
    if(await evaluate("Boolean(document.querySelector('[data-testid=stChatInputTextArea]'))")) break;
    await pause(300);
  }
  const capture = async name => {
    const result = await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(outputDir+'/'+name+'.png',Buffer.from(result.data,'base64'));
  };
  await pause(800);
  await capture('run-notes-desktop');
  console.log(await evaluate(`JSON.stringify({notes:[...document.querySelectorAll('[class*="st-key-run_notes_"] li')].map(e=>({text:e.innerText,color:getComputedStyle(e).color})),answer:[...document.querySelectorAll('[data-testid=stChatMessage] [data-testid=stMarkdownContainer]')].filter(e=>e.innerText.startsWith('调整后的方案')).map(e=>({text:e.innerText,color:getComputedStyle(e).color,listed:!!e.closest('li')})),errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText)})`));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(400);await capture('run-notes-mobile');
  ws.close();
})().catch(error=>{console.error(error);process.exitCode=1;});
