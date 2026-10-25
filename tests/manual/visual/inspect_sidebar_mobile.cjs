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
  console.log(await evaluate(`JSON.stringify({footer:document.querySelector('.st-key-sidebar_model_controls')?.getBoundingClientRect().toJSON(),side:document.querySelector('[data-testid=stSidebar]')?.getBoundingClientRect().toJSON(),body:document.querySelector('.st-key-sidebar_content')?.getBoundingClientRect().toJSON(),user:document.querySelector('[data-testid=stSidebarUserContent]')?.getBoundingClientRect().toJSON(),css:document.querySelector('style')?.textContent.slice(0,80),error:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText)})`));
  const screenshot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(outputDir+'/sidebar-mobile-open.png',Buffer.from(screenshot.data,'base64'));
  ws.close();
})().catch(console.error);
