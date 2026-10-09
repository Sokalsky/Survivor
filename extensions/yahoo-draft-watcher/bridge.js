/* Isolated-world bridge, injected only into the explicitly paired Survivor tab. */
(()=>{'use strict';if(globalThis.__survivorBridge)return;globalThis.__survivorBridge=true;let nonce=null;
 chrome.runtime.onMessage.addListener(message=>{
  if(message.type==='init'){nonce=message.nonce;window.postMessage({channel:'survivor-draft-extension',type:'hello',nonce},location.origin);return;}
  if(message.nonce!==nonce||!['events','heartbeat'].includes(message.type))return;
  window.postMessage({...message,channel:'survivor-draft-extension'},location.origin);
 });
 window.addEventListener('message',e=>{const m=e.data;if(e.source!==window||e.origin!==location.origin||m?.channel!=='survivor-draft-page'||m.nonce!==nonce||!['ready','ack'].includes(m.type))return;chrome.runtime.sendMessage(m).catch(()=>{});});
 /* Pairing may arrive while the page is still loading its saved projections. */
 setInterval(()=>{if(nonce)window.postMessage({channel:'survivor-draft-extension',type:'hello',nonce},location.origin);},4000);
})();
