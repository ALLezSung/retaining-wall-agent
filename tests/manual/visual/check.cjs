const fs = require('node:fs');
const outputDir = require('node:path').resolve(__dirname, '../../.runtime/visual');
fs.mkdirSync(outputDir, {recursive: true});
(async () => {
  const targets = await (await fetch('http://127.0.0.1:9227/json')).json();
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
  await call('Page.navigate',{url:'http://127.0.0.1:8517'});
  for(let n=0;n<50;n++) {
    if(await evaluate("Boolean(document.querySelector('[data-testid=stChatInputTextArea]'))")) break;
    await pause(300);
  }
  const capture = async name => {
    const result = await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(outputDir+'/'+name+'.png',Buffer.from(result.data,'base64'));
  };
  await pause(500); await capture('welcome');
  await evaluate(`(() => {
    const input=document.querySelector('[data-testid=stChatInputTextArea]');
    const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
    setter.call(input,'请核算这组参数，并说明所选规范的限制。');
    input.dispatchEvent(new Event('input',{bubbles:true})); input.focus();
  })()`);
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await pause(1500); await capture('running');
  for(let n=0;n<70;n++) {
    if(await evaluate("!document.querySelector('.agent-live') && document.querySelectorAll('[data-testid=stDownloadButton]').length===2 && ![...document.querySelectorAll('button')].some(e=>e.innerText==='Stop')")) break;
    await pause(300);
  }
  await pause(600); await evaluate("document.querySelector('[data-testid=stMain]').scrollTop=0"); await pause(200); await capture('complete');
  await evaluate("[...document.querySelectorAll('summary')].find(e=>e.innerText.includes('计算书'))?.click()");
  await pause(350); await capture('book');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(400); await capture('mobile');
  console.log(await evaluate("JSON.stringify({title:document.title,headings:[...document.querySelectorAll('h1,h3')].map(e=>e.innerText),expanders:[...document.querySelectorAll('summary')].map(e=>e.innerText),downloads:document.querySelectorAll('[data-testid=stDownloadButton]').length,errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText),overflow:document.documentElement.scrollWidth>innerWidth})"));
  ws.close();
})().catch(error => { console.error(error); process.exitCode=1; });
