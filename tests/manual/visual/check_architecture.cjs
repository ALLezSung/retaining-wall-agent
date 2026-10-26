// Offline CDP smoke: generic actions, streamed answer, downloads, narrow layout.
// Start preview.py on 18520 and a dedicated headless browser on 19230.
const fs = require('node:fs');
const path = require('node:path');
const output = path.resolve(__dirname, '../../.runtime/visual/architecture');
fs.mkdirSync(output, {recursive: true});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const targets = await (await fetch('http://127.0.0.1:19230/json')).json();
  const target = targets.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, {once:true}));
  let sequence = 0;
  const pending = new Map();
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (pending.has(message.id)) {
      const {resolve,reject} = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
    }
  });
  const call = (method, params={}) => new Promise((resolve,reject) => {
    const id = ++sequence;
    pending.set(id,{resolve,reject});
    ws.send(JSON.stringify({id,method,params}));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const waitFor = async expression => {
    for (let i=0;i<160;i++) {
      if (await evaluate(expression)) return;
      await pause(250);
    }
    throw new Error('Timed out: '+expression);
  };
  const screenshot = async name => {
    const result = await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));
  };
  await call('Page.enable');
  await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:output});
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1050,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:'http://127.0.0.1:18520'});
  await waitFor("Boolean(document.querySelector('[data-testid=stChatInputTextArea]'))");
  await evaluate(`window.businessStages=[];window.partialSeen=false;window.sampleTimer=setInterval(()=>{const live=document.querySelector('.agent-live');if(live)window.businessStages.push(live.innerText);const last=[...document.querySelectorAll('[data-testid=stChatMessage]')].at(-1);if(live&&last?.innerText.includes('已完成这组参数'))window.partialSeen=true;},30)`);
  await evaluate(`(()=>{const input=document.querySelector('[data-testid=stChatInputTextArea]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'请计算当前教学案例');input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();})()`);
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await waitFor("document.querySelectorAll('[data-testid=stDownloadButton]').length===2&&!document.querySelector('.agent-live')");
  await evaluate("[...document.querySelectorAll('summary')].find(e=>e.innerText.includes('计算书')).click()");
  await pause(250);
  await screenshot('desktop');
  const desktop = await evaluate(`({errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText),downloads:document.querySelectorAll('[data-testid=stDownloadButton]').length,overflow:document.documentElement.scrollWidth>innerWidth+1,partialSeen:window.partialSeen,businessStage:window.businessStages.some(t=>t.includes('校验参数')||t.includes('计算当前方案'))})`);
  await evaluate("document.querySelector('[data-testid=stDownloadButton] button').click()");
  for (let i=0;i<60 && !fs.existsSync(path.join(output,'wall_result.json'));i++) await pause(100);
  const download = JSON.parse(fs.readFileSync(path.join(output,'wall_result.json'),'utf8'));
  if (download.sliding_ratio!==1.595) throw new Error('Unexpected downloaded calculation');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(700);
  await screenshot('mobile');
  const mobile = await evaluate(`({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,errors:document.querySelectorAll('[data-testid=stException]').length,downloads:document.querySelectorAll('[data-testid=stDownloadButton]').length})`);
  await evaluate('clearInterval(window.sampleTimer)');
  if (desktop.errors.length || desktop.overflow || !desktop.partialSeen || !desktop.businessStage || mobile.errors || mobile.scrollWidth>mobile.width+1) throw new Error(JSON.stringify({desktop,mobile}));
  const result = {desktop,mobile,download:{sliding_ratio:download.sliding_ratio,overturning_ratio:download.overturning_ratio}};
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
  ws.close();
})().catch(error=>{console.error(error);process.exitCode=1;});
