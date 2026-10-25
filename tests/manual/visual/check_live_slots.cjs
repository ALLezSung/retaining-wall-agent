const fs = require('node:fs');
const outputDir = require('node:path').resolve(__dirname, '../../.runtime/visual');
fs.mkdirSync(outputDir, {recursive: true});
(async () => {
  const targets = await (await fetch('http://127.0.0.1:9230/json')).json();
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
  await call('Page.navigate',{url:'http://127.0.0.1:8520'});
  for(let n=0;n<50;n++) {
    if(await evaluate("Boolean(document.querySelector('[data-testid=stChatInputTextArea]'))")) break;
    await pause(300);
  }
  const capture = async name => {
    const result = await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(outputDir+'/'+name+'.png',Buffer.from(result.data,'base64'));
  };
  await evaluate(`(()=>{
    window.slotSamples=[];window.slotCase='';window.finalRoot=null;window.finalRootChanged=false;
    window.slotTimer=setInterval(()=>{
      if(!document.querySelector('.agent-live'))return;
      const chat=[...document.querySelectorAll('[data-testid=stChatMessage]')].at(-1);
      if(!chat)return;
      const notes=[...chat.querySelectorAll('[class*="st-key-run_notes_"] li')].map(e=>e.innerText);
      const bodies=[...chat.querySelectorAll('[data-testid=stMarkdownContainer]')].filter(e=>!e.closest('[class*="st-key-run_notes_"]')&&!e.querySelector('.agent-live'));
      for(const body of bodies){
        const text=body.innerText;
        if(!text)continue;
        const style=getComputedStyle(body);
        const finalText='最终回答'.startsWith(text)||text.startsWith('最终回答');
        if(finalText){if(window.finalRoot&&window.finalRoot!==body)window.finalRootChanged=true;window.finalRoot=body;}
        window.slotSamples.push({mode:window.slotCase,text,color:style.color,font:style.fontSize,listed:!!body.closest('li'),duplicate:notes.includes(text),finalText});
      }
    },16);
  })()`);
  async function runCase(prompt,mode,expectedRequests){
    await evaluate(`window.slotCase=${JSON.stringify(mode)};window.finalRoot=null;window.finalRootChanged=false;`);
    await evaluate(`(()=>{const input=document.querySelector('[data-testid=stChatInputTextArea]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,${JSON.stringify(prompt)});input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();})()`);
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    for(let n=0;n<200;n++){
      if(await evaluate(`document.body.innerText.includes('FAKE_MODEL_REQUESTS: ${expectedRequests}')&&!document.querySelector('.agent-live')&&![...document.querySelectorAll('button')].some(e=>e.innerText==='Stop')`))break;
      await pause(100);
    }
    const result=await evaluate(`(()=>{const samples=window.slotSamples.filter(s=>s.mode===${JSON.stringify(mode)});const finals=samples.filter(s=>s.finalText);const last=[...document.querySelectorAll('[data-testid=stChatMessage]')].at(-1);return {mode:${JSON.stringify(mode)},samples:samples.length,firstFinal:finals[0]?.text,bodyColors:[...new Set(samples.map(s=>s.color))],bodyFonts:[...new Set(samples.map(s=>s.font))],bad:samples.filter(s=>s.listed||s.duplicate).length,finalRootChanged:window.finalRootChanged,notes:last?.querySelectorAll('[class*="st-key-run_notes_"] li').length,content:last?.innerText,errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText),requestsMatch:document.body.innerText.includes('FAKE_MODEL_REQUESTS: ${expectedRequests}')};})()`);
    if(!result.requestsMatch||result.errors.length||result.bad||result.bodyColors.some(c=>c!=='rgb(50, 47, 41)')||result.bodyFonts.some(f=>f!=='16px'))throw new Error(JSON.stringify(result));
    if(mode==='normal'&&(result.notes!==2||result.firstFinal!=='最'||result.finalRootChanged))throw new Error(JSON.stringify(result));
    if(mode==='interrupt'&&(!result.content.includes('连接中断')||!result.content.includes('中断回答')||result.notes!==2))throw new Error(JSON.stringify(result));
    if(mode==='limit'&&(result.content.split('本轮已达到工具调用轮次上限').length!==2||result.notes!==3))throw new Error(JSON.stringify(result));
    delete result.content;console.log(JSON.stringify(result));
    await capture('stable-slots-'+mode);
  }
  await runCase('请核对当前案例。','normal',3);
  await runCase('测试中断。','interrupt',6);
  await runCase('测试上限。','limit',10);
  await evaluate('clearInterval(window.slotTimer)');
  ws.close();
})().catch(error=>{console.error(error);process.exitCode=1;});
