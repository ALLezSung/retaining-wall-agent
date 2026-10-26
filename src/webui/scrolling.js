// Follow a generating answer, never a report being expanded or inspected.
// This controls only our page's scroll position; no Streamlit internals are patched.
(() => {
  if (window.designChatScroll) {
    window.designChatScroll.refresh();
    return;
  }
  let main, observer, resizeObserver, frame;
  let session, userCount = 0, following = true, wasGenerating = false;
  let initial = true;
  const update = () => {
    frame = undefined;
    if (!main?.isConnected) return;
    const currentSession = main.querySelector('[data-chat-session]')?.dataset.chatSession;
    if (currentSession !== undefined && currentSession !== session) {
      session = currentSession;
      userCount = 0;
      following = true;
      initial = true;
    }
    const count = main.querySelectorAll('[data-testid=stChatMessageAvatarUser]').length;
    const generating = !!main.querySelector('.agent-live');
    const newMessage = count > userCount;
    if (newMessage || (generating && !wasGenerating)) following = true;
    if (following && (initial || newMessage || generating || wasGenerating)) {
      main.scrollTop = main.scrollHeight;
    }
    // Keep this monotonic across Streamlit's intermediate rerender deltas.
    userCount = Math.max(userCount, count);
    wasGenerating = generating;
    initial = false;
  };
  const schedule = () => {
    if (frame === undefined) frame = requestAnimationFrame(update);
  };
  const refresh = () => {
    const next = document.querySelector('main.stMain');
    if (!next) return;
    if (main !== next) {
      observer?.disconnect();
      resizeObserver?.disconnect();
      main = next;
      observer = new MutationObserver(schedule);
      observer.observe(main, {childList:true, subtree:true, characterData:true,
                             attributes:true, attributeFilter:['data-chat-session']});
      resizeObserver = new ResizeObserver(schedule);
      const content = main.querySelector('[data-testid=stMainBlockContainer]');
      if (content) resizeObserver.observe(content);
    }
    schedule();
  };
  const isContent = target => target instanceof Element && main?.contains(target)
    && !target.closest('[data-testid=stBottom]');
  document.addEventListener('wheel', event => {
    if (isContent(event.target) && event.deltaY < 0) following = false;
  }, {capture:true, passive:true});
  document.addEventListener('pointerdown', event => {
    if (isContent(event.target)) following = false;
  }, {capture:true, passive:true});
  document.addEventListener('keydown', event => {
    if (isContent(event.target) && ['ArrowUp','PageUp','Home'].includes(event.key)) following = false;
  }, {capture:true});
  document.addEventListener('scroll', event => {
    if (event.target === main && wasGenerating
        && main.scrollHeight - main.clientHeight - main.scrollTop <= 4) following = true;
  }, {capture:true, passive:true});
  window.addEventListener('resize', schedule, {passive:true});
  window.designChatScroll = {refresh};
  refresh();
})();
