const fs = require('node:fs');
const outputDir = require('node:path').resolve(__dirname, '../../.runtime/visual');
fs.mkdirSync(outputDir, {recursive: true});
(async () => {
  const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
  let targets;
  for(let i=0;i<30;i++) {
    try { targets=await (await fetch('http://127.0.0.1:9229/json')).json(); break; } catch { await pause(200); }
  }
  const target = targets.find(t=>t.type==='page'&&t.url.startsWith('http://127.0.0.1:8519')) || targets.find(t=>t.type==='page');
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
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1050,deviceScaleFactor:1,mobile:false});
  await evaluate(`(()=>{
    const style=document.createElement('style');
    style.textContent='.st-key-conversation_history [data-testid="stHorizontalBlock"]{height:42px;min-height:42px}.st-key-conversation_history [data-testid="stButton"],.st-key-conversation_history .stButton button{width:100%}';
    document.head.append(style);
    const sheet=[...document.styleSheets].find(s=>s.href===null&&s.cssRules);
    for(const rule of ['.st-key-conversation_history,.st-key-conversation_history [data-testid=stVerticalBlock]{gap:6px!important}', '.st-key-conversation_history [data-testid=stHorizontalBlock]:has(button[kind=primary]){background:#e5d5c7;border-radius:8px;box-shadow:inset 3px 0 #bd684b}', '.st-key-conversation_history [data-testid=stHorizontalBlock]{height:42px!important;min-height:42px!important;max-height:42px!important;padding:0!important;flex:0 0 42px!important;box-sizing:border-box}', '.st-key-conversation_history [data-testid=stButton],.st-key-conversation_history [data-testid=stButton]>div,.st-key-conversation_history .stButton button{width:100%!important}']) sheet.insertRule(rule,sheet.cssRules.length);
    const history=document.querySelector('.st-key-conversation_history');
    const scroll=[history,...history.querySelectorAll('*')].find(e=>e.scrollHeight>e.clientHeight+10&&['auto','scroll'].includes(getComputedStyle(e).overflowY));
    scroll.scrollTop=0;
  })()`);
  await pause(350);
  console.log(await evaluate(`JSON.stringify((()=>{
    const history=document.querySelector('.st-key-conversation_history');
    const bounds=history.getBoundingClientRect();
    const titles=[...history.querySelectorAll('[data-testid=stColumn]:first-child button')].filter(b=>b.getBoundingClientRect().height>0);
    return {visible:titles.filter(b=>{const rect=b.getBoundingClientRect();return rect.top>=bounds.top&&rect.bottom<=bounds.bottom}).length,
      positions:titles.slice(0,6).map(e=>({top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom})),bounds:{top:bounds.top,bottom:bounds.bottom},rows:[...history.querySelectorAll('[data-testid=stHorizontalBlock]')].slice(0,6).map(e=>e.getBoundingClientRect().height),
      styleRule:[...document.querySelectorAll('style')].filter(e=>e.textContent.includes('agent-turn')).map(e=>e.textContent.match(/st-key-conversation_history[^}]*}/g)),computed:[...history.querySelectorAll('[data-testid=stHorizontalBlock]')].slice(0,1).map(e=>({height:getComputedStyle(e).height,box:getComputedStyle(e).boxSizing,padding:getComputedStyle(e).padding,minHeight:getComputedStyle(e).minHeight,match:e.matches('.st-key-conversation_history [data-testid=stHorizontalBlock]')})),activeWidth:history.querySelector('button[kind=primary]').getBoundingClientRect().width};
  })())`));
  await capture('final-desktop');
  await evaluate("document.querySelector('.st-key-composer [data-testid=stPopover] button').click()");
  await pause(250);await capture('final-popover');
  ws.close();
})().catch(error=>{console.error(error);process.exit(1);});
