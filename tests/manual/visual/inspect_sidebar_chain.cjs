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
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(800);
  await evaluate(`document.querySelector('button[aria-label="Expand sidebar"]')?.click()`);
  await pause(800);
  console.log(await evaluate(`JSON.stringify(['.st-key-sidebar_content','.st-key-sidebar_model_controls'].map(sel=>{let e=document.querySelector(sel),a=[];for(let i=0;e&&i<6;i++,e=e.parentElement){let c=getComputedStyle(e);a.push({tag:e.tagName,test:e.dataset.testid,cls:e.className,display:c.display,height:c.height,flex:c.flex,min:c.minHeight,overflow:c.overflowY});}return a;}))`));
  const screenshot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(outputDir+'/sidebar-mobile-open.png',Buffer.from(screenshot.data,'base64'));
  ws.close();
})().catch(console.error);
