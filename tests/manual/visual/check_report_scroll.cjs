// Offline browser regression for report expansion, tabs, and the chat composer.
const fs = require('node:fs');
const path = require('node:path');
const output = path.resolve(__dirname, '../../.runtime/visual/scroll');
fs.mkdirSync(output, {recursive:true});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const targets = await (await fetch('http://127.0.0.1:19231/json')).json();
  const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, {once:true}));
  const pending = new Map();
  let id = 0;
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!pending.has(message.id)) return;
    const {resolve,reject} = pending.get(message.id);
    pending.delete(message.id);
    message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
  });
  const call = (method, params={}) => new Promise((resolve,reject) => {
    pending.set(++id,{resolve,reject});
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
  const metrics = () => evaluate(`(()=>{
    const main=document.querySelector('main.stMain');
    const block=document.querySelector('[data-testid=stMainBlockContainer]');
    const bottom=document.querySelector('[data-testid=stBottom]');
    const input=document.querySelector('[data-testid=stChatInput]');
    const report=[...document.querySelectorAll('[data-testid=stExpander]')].find(e=>e.querySelector('summary')?.innerText.includes('计算书'));
    const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,width:r.width}};
    return {viewport:{width:innerWidth,height:innerHeight},scrollTop:main.scrollTop,scrollHeight:main.scrollHeight,clientHeight:main.clientHeight,
      main:rect(main),block:{...rect(block),scrollHeight:block.scrollHeight,overflow:getComputedStyle(block).overflow},
      bottom:rect(bottom),input:rect(input),report:rect(report),errors:[...document.querySelectorAll('[data-testid=stException]')].map(e=>e.innerText)};
  })()`);
  const screenshot = async name => {
    const result = await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));
  };
  const assert = (condition, message, detail) => {
    if (!condition) throw new Error(message+' '+JSON.stringify(detail));
  };
  const wheel = async delta => {
    const position = await evaluate("(()=>{const r=document.querySelector('main.stMain').getBoundingClientRect();return {x:r.left+r.width/2,y:innerHeight/2}})()");
    await call('Input.dispatchMouseEvent',{type:'mouseWheel',...position,deltaX:0,deltaY:delta});
    await pause(250);
  };
  const reportClick = async target => {
    const position = await evaluate(`(()=>{
      const report=[...document.querySelectorAll('[data-testid=stExpander]')].find(e=>e.querySelector('summary')?.innerText.includes('计算书'));
      const element=${target};
      element.scrollIntoView({block:'center',behavior:'instant'});
      const r=element.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};
    })()`);
    await pause(100);
    const before = await metrics();
    await call('Input.dispatchMouseEvent',{type:'mousePressed',...position,button:'left',clickCount:1});
    await call('Input.dispatchMouseEvent',{type:'mouseReleased',...position,button:'left',clickCount:1});
    return before;
  };
  const results=[];
  await call('Page.enable');
  for (const [width,height] of [[1440,900],[3323,1787],[390,844]]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<700});
    await call('Page.navigate',{url:'http://127.0.0.1:18521'});
    await waitFor("!!window.designChatScroll&&[...document.querySelectorAll('summary')].some(e=>e.innerText.includes('计算书'))&&!!document.querySelector('[data-testid=stChatInputTextArea]')");
    await pause(500);
    const closed = await reportClick("report.querySelector('summary')");
    await pause(600);
    const expanded = await metrics();
    assert(Math.abs(expanded.scrollTop-closed.scrollTop)<=2,'Expanding report moved the reading position',{closed,expanded});
    assert(expanded.report.height>closed.report.height+200,'Report did not expand',expanded);
    const samples=[];
    for (const tab of [2,5,0,2]) {
      const before = await reportClick(`report.querySelectorAll('[role=tab]')[${tab}]`);
      await pause(500);
      const switched = await metrics();
      assert(switched.scrollTop<=before.scrollTop+2,'Changing chapter forced a scroll to bottom',{before,switched});
      assert(switched.block.scrollHeight<=Math.ceil(switched.block.height)+1,'Report content overflowed a shrunken body',switched);
      assert(Math.abs(switched.bottom.bottom-height)<=2,'Composer left the viewport bottom',switched);
      await evaluate("document.querySelector('main.stMain').scrollTop=1000000");
      await pause(150);
      const atEnd=await metrics();
      if (atEnd.scrollTop>20) {
        await wheel(-300);
        const up=await metrics();
        await pause(600);
        const held=await metrics();
        assert(up.scrollTop<atEnd.scrollTop-10,'Mouse wheel could not scroll up',{atEnd,up});
        assert(Math.abs(held.scrollTop-up.scrollTop)<=2,'Reading position was pulled back to bottom',{up,held});
        await wheel(10000);
      }
      const end=await metrics();
      const visible=await evaluate("(()=>{const el=[...document.querySelectorAll('summary')].find(e=>e.innerText.includes('系统状态'));return el.getBoundingClientRect().bottom<=document.querySelector('[data-testid=stBottom]').getBoundingClientRect().top})()");
      assert(visible,'Last card is hidden behind composer',end);
      assert(!end.errors.length,'Page exception',end);
      samples.push({tab,scrollTop:end.scrollTop,scrollHeight:end.scrollHeight});
    }
    await screenshot('fixed-'+width);
    results.push({viewport:{width,height},expandedScroll:expanded.scrollTop,samples});
  }
  // Generating answers still follow. A deliberate upward wheel must suspend
  // following while more tokens arrive, and remain suspended after completion.
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:'http://127.0.0.1:18521'});
  await waitFor("!!window.designChatScroll&&!!document.querySelector('[data-testid=stChatInputTextArea]')");
  await evaluate(`(()=>{const input=document.querySelector('[data-testid=stChatInputTextArea]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'测试生成时自动跟随与主动上滚');input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();})()`);
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await waitFor("!!document.querySelector('.agent-live')&&document.querySelector('main.stMain').innerText.includes('滚动跟随验证第 20 行')");
  await pause(50);
  const following=await metrics();
  assert(following.scrollHeight-following.clientHeight-following.scrollTop<100,'New output did not follow',following);
  await wheel(-500);
  const reading=await metrics();
  await pause(700);
  const later=await metrics();
  assert(later.scrollHeight>reading.scrollHeight,'Streaming stopped before scroll test',{reading,later});
  assert(Math.abs(later.scrollTop-reading.scrollTop)<5,'Streaming overrode deliberate upward scroll',{reading,later});
  await waitFor("!document.querySelector('.agent-live')&&document.querySelector('main.stMain').innerText.includes('滚动跟随验证第 60 行')");
  await pause(700);
  const completed=await metrics();
  assert(Math.abs(completed.scrollTop-reading.scrollTop)<5,'Completion forced reader to bottom',{reading,completed});
  await screenshot('stream-reading');
  const result={results,streaming:{followed:true,upwardScrollPreserved:true,completedScrollPreserved:true}};
  fs.writeFileSync(path.join(output,'regression-result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
  ws.close();
})().catch(error=>{console.error(error);process.exit(1);});
