const fs = require('node:fs');
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
  console.log(await evaluate("JSON.stringify([...document.querySelectorAll('button')].slice(0,12).map(e=>({text:e.innerText,label:e.getAttribute('aria-label'),test:e.getAttribute('data-testid'),html:e.outerHTML.slice(0,450)})))"));
  ws.close();
})().catch(console.error);
