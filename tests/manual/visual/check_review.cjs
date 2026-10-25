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
  await call('Page.enable'); await call('Page.bringToFront');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1050,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:'http://127.0.0.1:8519'});
  await until("Boolean(document.querySelector('.st-key-composer textarea'))",'composer');
  await pause(450);
  const initial=await evaluate(`(()=>{
    const history=document.querySelector('.st-key-conversation_history');
    const nodes=[history,...history.querySelectorAll('*')];
    const scroll=nodes.find(e=>e.scrollHeight>e.clientHeight+10&&['auto','scroll'].includes(getComputedStyle(e).overflowY));
    const input=document.querySelector('.st-key-composer [data-testid=stChatInput]').getBoundingClientRect();
    const settings=document.querySelector('.st-key-composer [data-testid=stPopover]').getBoundingClientRect();
    const active=history.querySelector('button[kind=primary]');
    return {historyButtons:[...history.querySelectorAll('button')].filter(b=>b.innerText!=='×').length,
      scrollable:!!scroll,scrollHeight:scroll?.scrollHeight,height:scroll?.clientHeight,
      current:active?.innerText,activeBackground:active?getComputedStyle(active).backgroundColor:null,
      selectorRight:settings.left>=input.right-1,composerBottom:document.querySelector('.st-key-composer').getBoundingClientRect().bottom};
  })()`);
  if(!initial.scrollable||initial.historyButtons<=5||!initial.selectorRight)throw new Error(JSON.stringify(initial));
  await capture('desktop');
  await evaluate(`(()=>{
    const history=document.querySelector('.st-key-conversation_history');
    const scroll=[history,...history.querySelectorAll('*')].find(e=>e.scrollHeight>e.clientHeight+10&&['auto','scroll'].includes(getComputedStyle(e).overflowY));
    scroll.scrollTop=scroll.scrollHeight;scroll.dispatchEvent(new Event('scroll',{bubbles:true}));
  })()`);
  await pause(200);await capture('history');
  await evaluate("[...document.querySelector('.st-key-conversation_history').querySelectorAll('button')].filter(b=>b.innerText!=='×').at(-1).click()");
  await until("Boolean(document.querySelector('[role=dialog] input'))",'rename dialog');
  await capture('rename');
  await setText('[role=dialog] input','核算方案 · 已命名');
  await evaluate("[...document.querySelector('[role=dialog]').querySelectorAll('button')].find(b=>b.innerText==='保存标题').click()");
  await until("!document.querySelector('[role=dialog]')&&document.querySelector('.st-key-conversation_history button[kind=primary]')?.innerText==='核算方案 · 已命名'",'saved title');
  await setText('.st-key-composer textarea','保留这段草稿');
  await evaluate("document.querySelector('.st-key-composer [data-testid=stPopover] button').click()");
  await until("Boolean(document.querySelector('[data-testid=stPopoverBody] input[type=range]'))",'reasoning slider');
  await evaluate("document.querySelector('[data-testid=stPopoverBody] input[type=range]').focus()");
  await key('End','End',35);
  await until("document.querySelector('.st-key-composer [data-testid=stPopover] button').innerText.includes('最高')",'effort summary');
  const draft=await evaluate("document.querySelector('.st-key-composer textarea').value");
  if(draft!=='保留这段草稿')throw new Error('Draft was lost: '+draft);
  await capture('popover');
  await evaluate("document.querySelector('[data-testid=stPopoverBody] [data-testid=stSelectbox] input').focus()");
  await key('ArrowDown','ArrowDown',40);
  await until("[...document.querySelectorAll('[role=option]')].some(e=>e.innerText.includes('Demo Pro'))",'model options');
  await key('End','End',35); await key('Enter','Enter',13);
  await until("document.querySelector('.st-key-composer [data-testid=stPopover] button').innerText.includes('Demo Pro')",'model selection');
  await evaluate("document.querySelector('[data-testid=stPopoverBody] input[type=range]').focus()");
  await key('End','End',35);
  await until("document.querySelector('.st-key-composer [data-testid=stPopover] button').innerText.includes('高')",'new effort');
  await evaluate("document.querySelector('.st-key-composer [data-testid=stPopover] button').click()");
  await setText('.st-key-composer textarea','请计算这组参数。');
  await key('Enter','Enter',13);
  await until("Boolean(document.querySelector('.agent-live'))",'streaming');
  await capture('streaming');
  await until("!document.querySelector('.agent-live')&&document.querySelectorAll('[data-testid=stDownloadButton]').length===2",'finished calculation');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(500);await capture('mobile');
  const final=await evaluate(`(()=>{
    const input=document.querySelector('.st-key-composer [data-testid=stChatInput]').getBoundingClientRect();
    const settings=document.querySelector('.st-key-composer [data-testid=stPopover]').getBoundingClientRect();
    return {selector:document.querySelector('.st-key-composer [data-testid=stPopover] button').innerText,
      draftPreserved:true,renamed:document.querySelector('.st-key-conversation_history button[kind=primary]')?.innerText,
      mobileSelectorRight:settings.left>=input.right-1,overflow:document.documentElement.scrollWidth>innerWidth,
      errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText)};
  })()`);
  if(final.errors.length||final.overflow||!final.mobileSelectorRight)throw new Error(JSON.stringify(final));
  console.log(JSON.stringify({initial,final}));
  ws.close();
})().catch(error=>{console.error(error);process.exit(1);});
