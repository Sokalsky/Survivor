/* Timed, browser-local mock auctions. Never applies simulated events to a live session. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./draft-engine.js'));
  else root.SurvivorPractice = factory(root.SurvivorDraft);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (E) {
  'use strict';
  const ROUND_MS = 10000, RESULT_MS = 1800;
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  function noise(seed, key) {
    let n = 2166136261;
    for (const c of seed + ':' + key) n = Math.imul(n ^ c.charCodeAt(0), 16777619);
    return (n >>> 0) / 4294967296;
  }
  class Auction {
    constructor(session, team) {
      if (session.mode !== 'practice') throw Error('Mock auctions require a separate practice session.');
      this.session = session;
      this.team = team;
      this.running = false;
      this.snapshot = E.replay(session);
      if (!this.snapshot.teams[team]) throw Error('Choose a team for this mock draft.');
      const old = session.practice;
      const sameRound = old?.version === 1 && old.playerId === this.snapshot.nomination?.playerId;
      this.state = session.practice = {
        version: 1, seed: typeof old?.seed === 'string' ? old.seed.slice(0,100) : session.id,
        playerId: this.snapshot.nomination?.playerId || null,
        phase: this.snapshot.nomination ? 'auction' : 'ready',
        remainingMs: sameRound && Number.isFinite(old.remainingMs) ? clamp(old.remainingMs,0,ROUND_MS) : ROUND_MS,
        bidInMs: sameRound && Number.isFinite(old.bidInMs) ? clamp(old.bidInMs,0,1200) : 650,
        passedTeam: sameRound && old.passedTeam === team ? team : null,
        resultMs: RESULT_MS, lastResult: null, reason: ''
      };
      if (!this.snapshot.nomination) this.nominate();
    }
    emit(type, fields) {
      const event = {id:'mock-'+this.session.id+'-'+this.session.events.length, type, ...fields,
        source:'practice', at:new Date().toISOString()};
      this.snapshot = E.append(this.session,event);
    }
    canBuy(player, team, amount = this.session.settings.minimumBid) {
      const s = this.snapshot;
      return E.legalMax(s,team) >= amount && E.canFit([...s.teams[team].roster.map(r=>s.byId[r.playerId]),player],this.session.settings.rosterSlots);
    }
    nominate() {
      const s = this.snapshot, withdrawn = new Set(s.events.filter(e=>e.type==='withdraw').map(e=>e.playerId));
      const players = this.session.players.filter(p=>!s.taken[p.player_id] && !withdrawn.has(p.player_id))
        .sort((a,b)=>b.fair_value-a.fair_value || a.player.localeCompare(b.player));
      const player = players.find(p=>Object.keys(s.teams).some(t=>this.canBuy(p,t)));
      if (!player) {
        this.state.phase='complete'; this.state.playerId=null; this.running=false;
        this.state.reason=Object.keys(s.teams).every(t=>E.legalMax(s,t)===0) ? 'All rosters are full.' : 'No more eligible players remain in this mock.';
        return;
      }
      this.emit('nominate',{playerId:player.player_id});
      Object.assign(this.state,{phase:'auction',playerId:player.player_id,remainingMs:ROUND_MS,bidInMs:650,passedTeam:null});
    }
    start() { if (this.state.phase!=='complete') this.running=true; }
    pause() { this.running=false; }
    // Each opponent has a stable per-player limit, with room-wide and individual variation.
    limit(team) {
      const p=this.snapshot.byId[this.state.playerId];
      if (!p || team===this.team || !this.canBuy(p,team)) return 0;
      const f=E.fit(this.snapshot,p,team), market=E.market(this.snapshot,p).expected;
      const mood=.8 + noise(this.state.seed,p.player_id)*.32;
      const preference=.8 + noise(this.state.seed,team+':'+p.player_id)*.36;
      const budgetPace=this.snapshot.teams[team].remaining / Math.max(1,this.session.settings.rosterSize-this.snapshot.teams[team].roster.length);
      const value=(market*.78 + f.cap*.22)*f.fitFactor*mood*preference;
      return Math.min(E.legalMax(this.snapshot,team),Math.max(this.session.settings.minimumBid,Math.round(value),Math.min(3,Math.floor(budgetPace))));
    }
    botBid() {
      const n=this.snapshot.nomination;
      if (!n) return false;
      const next=Math.max(this.session.settings.minimumBid,n.amount+1);
      const bids=this.snapshot.bids.filter(b=>b.playerId===n.playerId).length;
      const candidates=Object.keys(this.snapshot.teams).filter(t=>t!==this.team && t!==n.leader)
        .map(team=>({team,limit:this.limit(team),order:noise(this.state.seed,team+':'+n.playerId+':'+bids)}))
        .filter(t=>t.limit>=next).sort((a,b)=>b.order-a.order);
      if (!candidates.length) return false;
      const bidder=candidates[0], expected=E.market(this.snapshot,this.snapshot.byId[n.playerId]).expected;
      const jump=Math.max(1,Math.round((bidder.limit-n.amount)*.36));
      const amount=Math.min(bidder.limit,n.amount ? n.amount+jump : Math.max(next,Math.round(expected*.35)));
      this.emit('bid',{playerId:n.playerId,team:bidder.team,amount:Math.max(next,amount)});
      return true;
    }
    bid(amount) {
      const n=this.snapshot.nomination;
      if (!this.running || this.state.phase!=='auction' || this.state.remainingMs<=0 || !n) throw Error('Start or resume the mock before bidding.');
      if (n.leader===this.team) throw Error('You already hold the highest bid.');
      if (this.state.passedTeam===this.team) throw Error('Rejoin this auction before bidding.');
      const next=Math.max(this.session.settings.minimumBid,n.amount+1);
      if (!Number.isInteger(amount) || amount<next) throw Error('Bid at least $'+next+'.');
      if (!this.canBuy(this.snapshot.byId[n.playerId],this.team,amount)) throw Error('That bid exceeds your budget or available roster slots.');
      this.emit('bid',{playerId:n.playerId,team:this.team,amount});
    }
    pass() {
      if (this.state.phase!=='auction') return;
      if (this.snapshot.nomination?.leader===this.team) throw Error('Your leading bid stays committed until someone outbids you.');
      this.state.passedTeam=this.state.passedTeam===this.team ? null : this.team;
    }
    close() {
      const n=this.snapshot.nomination;
      if (!n) return;
      const result={playerId:n.playerId,team:n.leader,amount:n.amount};
      if (n.leader) this.emit('sale',result);
      else this.emit('withdraw',{playerId:n.playerId});
      Object.assign(this.state,{phase:'result',remainingMs:0,resultMs:RESULT_MS,lastResult:result});
    }
    advance(ms) {
      if (!this.running || !Number.isFinite(ms) || ms<=0) return false;
      let changed=false;
      // Consume time in order: bids before the deadline, sale at zero, then a result pause.
      while (ms>0 && this.running) {
        if (this.state.phase==='result') {
          const used=Math.min(ms,this.state.resultMs);this.state.resultMs-=used;ms-=used;
          if (this.state.resultMs===0) {this.nominate();changed=true;}
        } else if (this.state.phase==='auction') {
          const used=Math.min(ms,this.state.remainingMs,this.state.bidInMs);
          this.state.remainingMs-=used;this.state.bidInMs-=used;ms-=used;
          if (this.state.remainingMs<=0) {this.close();changed=true;}
          else if (this.state.bidInMs<=0) {
            changed=this.botBid() || changed;
            this.state.bidInMs=700+Math.round(noise(this.state.seed,'tick:'+this.session.events.length)*350);
          }
        } else break;
      }
      return changed;
    }
  }
  return {Auction,ROUND_MS,RESULT_MS};
});
