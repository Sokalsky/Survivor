/* Durable real-draft uploads. Local capture never waits for the network. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SurvivorRecorder=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const FIELDS=['id','type','playerId','team','amount','at','source','history','recovered','targetId','sourcePlayer','sourceTeam','assignments','teamNames','ownTeam'];
  const clean=e=>Object.fromEntries(FIELDS.filter(k=>e[k]!=null).map(k=>[k,e[k]]));
  class Recorder {
    constructor({getSession,getPending=()=>0,onChange=()=>{},isTest=false,storage=globalThis.localStorage,keyStorage=globalThis.sessionStorage,fetcher=globalThis.fetch.bind(globalThis)}) {
      Object.assign(this,{getSession,getPending,onChange,isTest,storage,keyStorage,fetcher});
      this.enabledId=storage.getItem('survivor.recording.enabled')||null;this.revision=0;this.savedRevision=0;this.busy=false;this.timer=null;this.blocked=false;this.verifiedId=null;this.serverStatus='recording';
      this.message=isTest?'Yahoo mock · never saved to league history':'Local copy only · enable real-draft recording';
    }
    token(){return this.keyStorage.getItem('survivor.recording.key')||'';}
    allowed(s){return !this.isTest&&s?.mode==='live'&&s.purpose==='real'&&!s.events.some(e=>['practice','mock','simulated'].includes(e.source));}
    notify(message){this.message=message;this.onChange(this);}
    async request(path,body){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
      try{
        const response=await this.fetcher('/api/drafts'+path,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+this.token()},body:JSON.stringify(body),signal:controller.signal});
        const data=await response.json();if(!response.ok){const e=new Error(data.error||'Draft save failed');e.status=response.status;throw e;}return data;
      }finally{clearTimeout(timer);}
    }
    baseline(s){const b=JSON.parse(JSON.stringify(s));b.events=[];delete b.practice;return b;}
    async start(key){
      const s=this.getSession();if(!this.allowed(s))throw Error('Mock and practice drafts cannot be recorded as real league history.');
      if(key)this.keyStorage.setItem('survivor.recording.key',key);
      if(!this.token())throw Error('Enter your league recording key.');
      this.blocked=false;this.enabledId=s.id;this.storage.setItem('survivor.recording.enabled',s.id);this.verifiedId=null;
      await this.flush();
    }
    schedule(delay=300){
      const s=this.getSession();if(!this.allowed(s)||this.enabledId!==s.id||this.blocked)return;
      if(!this.token()){this.notify('Recording paused · enter the recording key to save pending events');return;}
      if(this.verifiedId===s.id&&this.revision===s.events.length){this.notify(this.serverStatus==='complete'?'Draft complete · saved to league history':'Saved '+this.revision+' events · rosters and projections saved');return;}
      if(this.busy||this.timer)return;
      if(delay<10000)this.notify('Saving draft · '+Math.max(0,s.events.length-this.revision)+' events pending');
      this.timer=setTimeout(()=>{this.timer=null;this.flush().catch(()=>{});},delay);
    }
    async flush(){
      const s=this.getSession();if(!this.allowed(s)||this.enabledId!==s.id||this.busy)return;
      if(!this.token())throw Error('Enter your league recording key to resume saving.');
      this.busy=true;let failed=false;
      try{
        if(this.verifiedId!==s.id){const ack=await this.request('',{session:this.baseline(s)});if(ack.revision>s.events.length)throw Object.assign(new Error('The saved draft is ahead of this browser. Restore it from Saved drafts.'),{status:409});this.revision=0;this.savedRevision=ack.revision;this.serverStatus=ack.status;this.verifiedId=s.id;}
        // Replay from zero once per page load: the server verifies every shared event before appending.
        while(this.enabledId===s.id&&this.getSession().id===s.id&&this.revision<s.events.length){
          const start=this.revision,batch=s.events.slice(start,start+100).map(clean);
          const ack=await this.request('/'+encodeURIComponent(s.id)+'/events',{offset:start,events:batch});
          if(!Number.isInteger(ack.revision)||ack.revision<start+batch.length||ack.revision>s.events.length)throw Object.assign(new Error('Saved event cursor differs from this browser. Restore the saved draft.'),{status:409});
          // A retry may acknowledge more than this batch; still verify subsequent local batches.
          this.revision=start+batch.length;this.savedRevision=ack.revision;this.serverStatus=ack.status;
        }
        if(this.enabledId===s.id&&this.getSession().id===s.id)this.notify(this.serverStatus==='complete'?'Draft complete · saved to league history':'Saved '+this.revision+' events · rosters and projections saved');
      }catch(e){failed=true;this.blocked=[400,401,403,409,413].includes(e.status);this.notify((this.blocked?'Recording needs attention · ':'Save interrupted; local events retained · ')+(e.name==='AbortError'?'request timed out':e.message));throw e;
      }finally{this.busy=false;if(!this.blocked&&this.getSession().id===s.id&&(failed||this.revision<s.events.length))this.schedule(failed?10000:300);}
    }
    async setStatus(status){
      if(this.getPending())throw Error('Resolve captured updates before changing draft status.');
      if(this.busy)throw Error('Wait for the current save to finish.');
      if(status==='recording'&&this.verifiedId===this.getSession().id&&this.serverStatus==='complete'){const reopened=await this.request('/'+encodeURIComponent(this.verifiedId)+'/status',{status,revision:this.savedRevision});this.serverStatus=reopened.status;this.blocked=false;}
      await this.flush();const s=this.getSession();if(!this.allowed(s)||this.enabledId!==s.id)throw Error('Enable recording for this real draft first.');
      const result=await this.request('/'+encodeURIComponent(s.id)+'/status',{status,revision:this.revision});this.serverStatus=result.status;this.blocked=false;
      this.notify(status==='complete'?'Draft complete · saved to league history':'Recording resumed');this.schedule();
    }
    retry(){this.blocked=false;this.verifiedId=null;this.schedule(0);}
    stop(){if(this.timer)clearTimeout(this.timer);this.timer=null;this.enabledId=null;this.storage.removeItem('survivor.recording.enabled');this.notify('Recording paused · local events are retained');}
  }
  return {Recorder,clean};
});
