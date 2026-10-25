const fs = require('node:fs');
const outputDir = require('node:path').resolve(__dirname, '../../.runtime/visual');
fs.mkdirSync(outputDir, {recursive: true});
(async () => {
  const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
  let targets;
  for(let i=0;i<30;i++) {
    try { targets=await (await fetch('http://127.0.0.1:9229/json')).json(); break; } catch { await pause(200); }
  }
  const target = targets.find(t=>t.type==='page' && t.url.startsWith('http://127.0.0.1:8519'));
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
  let counter=0;
  const pending=new Map();
  ws.addEventListener('message',event=>{
    const packet=JSON.parse(event.data);
    if(packet.id&&pending.has(packet.id)) {
      const callback=pending.get(packet.id);pending.delete(packet.id);
      packet.error?callback.reject(packet.error):callback.resolve(packet.result);
    }
  });
  const call=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++counter;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));
  });
  const evaluate=async expression=>{
    const answer=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(answer.exceptionDetails)throw new Error(JSON.stringify(answer.exceptionDetails));
    return answer.result.value;
  };
  const until=async(expression,label)=>{
    for(let i=0;i<100;i++){if(await evaluate(expression))return;await pause(150);}
    throw new Error('Timed out: '+label);
  };
  const capture=async name=>{
    const result=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(outputDir+'/review-'+name+'.png',Buffer.from(result.data,'base64'));
  };
  const key=async(name,code=name,number=undefined)=>{
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:name,code,...(number?{windowsVirtualKeyCode:number}:{})});
    await call('Input.dispatchKeyEvent',{type:'keyUp',key:name,code,...(number?{windowsVirtualKeyCode:number}:{})});
  };
  const setText=async(selector,value)=>evaluate(`(()=>{
    const input=document.querySelector(${JSON.stringify(selector)});
    const prototype=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,${JSON.stringify(value)});
    input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();
  })()`);
  console.log(await evaluate(`JSON.stringify({
    inputs:[...document.querySelectorAll('[data-testid=stPopoverBody] input')].map(e=>({type:e.type,role:e.getAttribute('role'),html:e.outerHTML.slice(0,600)})),sliderHTML:document.querySelector('[data-testid=stSlider]')?.outerHTML.slice(-8000),popoverNodes:[...document.querySelectorAll('[data-testid]')].filter(e=>/popover|slider/i.test(e.dataset.testid)).map(e=>({testid:e.dataset.testid,text:e.innerText.slice(0,200)})),
    roles:[...document.querySelectorAll('[role]')].filter(e=>/slider|dialog/i.test(e.role)).map(e=>({role:e.role,html:e.outerHTML.slice(0,500)})),
    buttons:[...document.querySelectorAll('.st-key-composer button')].map(e=>({text:e.innerText,expanded:e.getAttribute('aria-expanded'),disabled:e.disabled})),
    history:[...document.querySelectorAll('.st-key-conversation_history [data-testid=stHorizontalBlock]')].slice(0,2).map(e=>({height:e.getBoundingClientRect().height,html:e.outerHTML.slice(0,1500)})),exceptions:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText)
  })`));
  await capture('debug-app');
  ws.close();
})().catch(error=>{console.error(error);process.exit(1);});
