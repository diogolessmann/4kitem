/* CAMALEAO — cliente (canvas 2D top-down, polling ~200ms, interpolacao).
   Tema neon-arcade AmbitiON. 100% touch. Sem asset externo (tudo em codigo).
   O servidor e a autoridade: aqui so desenha o que ele manda + predicao local
   suave do proprio avatar. Anti-leak: o caçador so ve `props` (indistinguiveis).
*/
(function () {
  'use strict';
  var API = '/arena/camaleao/api/v1';
  var TOKEN = window.CAM_TOKEN || null;
  var PID = null, HOST = false;

  var scene = null;                 // backdrop {id,w,h,skins,walls}
  var snap = null;                  // ultimo snapshot
  var phase = 'boot';
  var me = { x: 360, y: 540, tx: 360, ty: 540, role: 'lobby' };
  var skinSel = null, locked = false, lastMoveAt = 0;
  var render = { seekers: {}, props: {} };   // suavizacao por id
  var cooldownUntil = 0;
  var flashes = [];                 // efeitos de tela {x,y,t,kind}

  var DPR = Math.min(window.devicePixelRatio || 1, 2);
  var canvas = document.getElementById('cam-canvas');
  var ctx = canvas.getContext('2d');
  var W = 0, H = 0, sc = 1, ox = 0, oy = 0;

  var COL = { bg:'#07060f', grid:'#171335', wall:'#241a4d', ci:'#22d3ee', ro:'#a855f7',
             ro2:'#f472b6', am:'#fbbf24', gr:'#34d399', ink:'#f5f3ff', red:'#ff3b6b' };

  // ───────────────────────── util ─────────────────────────
  function $(id){ return document.getElementById(id); }
  function show(id,on){ var e=$(id); if(e) e.style.display = on?'':'none'; }
  function clamp(v,a,b){ return v<a?a:(v>b?b:v); }
  function dist(ax,ay,bx,by){ var dx=ax-bx,dy=ay-by; return Math.sqrt(dx*dx+dy*dy); }
  function nickSalvo(){ try{ return localStorage.getItem('cam_nick')||''; }catch(e){ return ''; } }
  function salvaNick(n){ try{ localStorage.setItem('cam_nick', n); }catch(e){} }

  function api(path, body, cb){
    fetch(API+path, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(Object.assign({v:1}, body||{}))})
      .then(function(r){ return r.json().then(function(j){ cb(j, r.status); }, function(){ cb(null, r.status); }); })
      .catch(function(){ cb(null, 0); });
  }

  // ───────────────────────── audio (WebAudio) ─────────────────────────
  var actx=null;
  function beep(freq,dur,type,vol){
    try{ if(!actx){ var AC=window.AudioContext||window.webkitAudioContext; if(!AC) return; actx=new AC(); }
      if(actx.state==='suspended') actx.resume();
      var o=actx.createOscillator(), g=actx.createGain();
      o.type=type||'square'; o.frequency.value=freq; g.gain.value=vol||0.06;
      o.connect(g); g.connect(actx.destination); var n=actx.currentTime;
      g.gain.setValueAtTime(g.gain.value,n); g.gain.exponentialRampToValueAtTime(0.0001,n+dur);
      o.start(n); o.stop(n+dur);
    }catch(e){}
  }
  function sfxTap(){ beep(180,0.05,'sine',0.03); }
  function sfxHit(){ beep(523,0.10,'triangle',0.09); setTimeout(function(){beep(784,0.16,'triangle',0.09);},90); }
  function sfxMiss(){ beep(160,0.18,'sawtooth',0.07); }
  function sfxCamo(){ beep(440,0.12,'sine',0.05); }
  function sfxStart(){ beep(330,0.08,'square',0.06); setTimeout(function(){beep(660,0.12,'square',0.06);},110); }
  function vibra(ms){ try{ if(navigator.vibrate) navigator.vibrate(ms); }catch(e){} }

  // ───────────────────────── bootstrap ─────────────────────────
  if (TOKEN) { entrar(); } else { show('cam-create', true); }

  $('cam-btn-create').addEventListener('click', function(){
    var n=($('cam-nick').value||'').trim().slice(0,24) || 'Anfitrião'; salvaNick(n);
    api('/room/create', {nick:n}, function(r,st){
      if(r&&r.token){ TOKEN=r.token; PID=r.pid; HOST=true;
        try{ history.replaceState(null,'','/arena/camaleao/j/'+TOKEN); }catch(e){}
        show('cam-create',false); comecar();
      } else { toast(st===503?'Servidor cheio, tente já já':'Não deu pra criar'); }
    });
  });

  function entrar(){
    var n = nickSalvo() || 'Convidado';
    api('/room/'+TOKEN+'/join', {nick:n}, function(r,st){
      if(r&&r.pid){ PID=r.pid; HOST=!!r.host; comecar(); }
      else if(st===409 && r){ telaMsg(r.erro==='cheio'?'Essa sala está cheia 😅':'A partida já começou — peça um novo link'); }
      else if(st===404){ telaMsg('Sala não encontrada ou já encerrou'); }
      else { telaMsg('Sem conexão. Recarregue.'); }
    });
  }

  function comecar(){ ajustar(); loopPoll(); requestAnimationFrame(loopRender); }

  // ───────────────────────── viewport ─────────────────────────
  function ajustar(){
    W = canvas.width = Math.floor(window.innerWidth*DPR);
    H = canvas.height = Math.floor(window.innerHeight*DPR);
    canvas.style.width = window.innerWidth+'px'; canvas.style.height = window.innerHeight+'px';
    if(scene){ sc = Math.min(W/scene.w, H/scene.h); ox=(W-scene.w*sc)/2; oy=(H-scene.h*sc)/2; }
  }
  window.addEventListener('resize', ajustar);
  window.addEventListener('orientationchange', function(){ setTimeout(ajustar,150); });
  function w2sx(x){ return ox+x*sc; } function w2sy(y){ return oy+y*sc; }
  function s2wx(sx){ return (sx*DPR-ox)/sc; } function s2wy(sy){ return (sy*DPR-oy)/sc; }

  // ───────────────────────── polling ─────────────────────────
  function loopPoll(){ sync(); setTimeout(loopPoll, 200); }
  function sync(){
    if(!PID||!TOKEN) return;
    var input = { x: Math.round(me.x), y: Math.round(me.y) };
    if(me.role==='hider'){ input.skin = skinSel; input.locked = camoAtivo(); }
    api('/room/'+TOKEN+'/sync', {pid:PID, input:input}, onSnap);
  }
  function onSnap(r, st){
    if(st===409){ location.reload(); return; }
    if(st===404){ telaMsg('A partida encerrou (o servidor pode ter reiniciado). Crie uma sala nova.'); return; }
    if(!r){ return; }
    if(r.erro){ if(r.erro==='nao_autorizado'){ entrar(); } return; }
    snap = r; aplicar(r);
  }

  function aplicar(r){
    if(!scene || scene.id!==r.scene_id){ carregarCena(r.scene_id); }
    me.role = r.you.role;
    if(typeof r.you.skin==='string') skinSel = r.you.skin;
    // reconcilia a propria posicao: confia no local, mas gruda no servidor se divergir muito
    if(dist(me.x,me.y,r.you.x,r.you.y) > 55){ me.x=r.you.x; me.y=r.you.y; me.tx=me.x; me.ty=me.y; }
    // outros caçadores (suavizados por pid)
    var vistos={};
    (r.seekers||[]).forEach(function(s){ var id=s.pid||('s'+Math.round(s.x)+'_'+Math.round(s.y));
      vistos[id]=1; var t=render.seekers[id]; if(!t){ t=render.seekers[id]={x:s.x,y:s.y}; } t.tx=s.x; t.ty=s.y; t.nick=s.nick; });
    Object.keys(render.seekers).forEach(function(id){ if(!vistos[id]) delete render.seekers[id]; });
    // props (caçador) / decoys (hider) — suavizados por id
    var lista = r.props || r.decoys || [];
    var pv={};
    lista.forEach(function(p){ pv[p.id]=1; var t=render.props[p.id]; if(!t){ t=render.props[p.id]={x:p.x,y:p.y}; } t.tx=p.x; t.ty=p.y; t.sprite=p.sprite; t.decoy=!!r.decoys; });
    Object.keys(render.props).forEach(function(id){ if(!pv[id]) delete render.props[id]; });
    cooldownUntil = Date.now() + (r.you.cooldown_ms||0);
    if(phase!==r.phase){ trocouFase(phase, r.phase); phase=r.phase; }
    atualizarUI(r);
  }

  function carregarCena(id){
    fetch(API+'/scene/'+id).then(function(r){return r.json();}).then(function(j){
      if(j&&j.scene){ scene=j.scene; ajustar(); montarSkinbar(); }
    }).catch(function(){});
  }

  function trocouFase(de, para){
    if(para==='hiding'){ sfxStart(); toast('Escondam-se! 🦎'); }
    if(para==='seeking'){ sfxStart(); toast(me.role==='seeker'?'CAÇAR! 🔦':'Fiquem quietos… 🤫'); }
    if(para==='result'){ vibra(30); }
  }

  // ───────────────────────── UI (DOM overlays) ─────────────────────────
  function atualizarUI(r){
    show('cam-create', false);
    var lobby = r.phase==='lobby', result = r.phase==='result';
    var blind = (r.phase==='hiding' && r.you.role==='seeker');
    show('cam-lobby', lobby);
    show('cam-result', result);
    show('cam-blind', blind);
    show('cam-hud', !lobby && !result);
    show('cam-skinbar', r.phase==='hiding' && r.you.role==='hider' && !!scene);
    show('cam-crosshair', r.phase==='seeking' && r.you.role==='seeker');
    if(lobby){
      HOST = !!r.host;
      $('cam-lobby-count').textContent = r.n_jogadores + (r.n_jogadores===1?' jogador':' jogadores');
      var ul=$('cam-lobby-players'); ul.innerHTML='';
      (r.jogadores||[]).forEach(function(p){ var li=document.createElement('div'); li.className='cam-pl';
        li.textContent='🦎 '+p.nick + (p.pid===r.host_pid?'  👑':''); ul.appendChild(li); });
      var enough = r.n_jogadores >= (r.min_jogadores||2);
      show('cam-btn-start', HOST);
      var bs=$('cam-btn-start'); bs.disabled = !enough; bs.textContent = enough?'▶ COMEÇAR':'Esperando jogadores…';
      $('cam-hint-nohost').style.display = HOST?'none':'';
    }
    if(blind){ $('cam-blind-timer').textContent = Math.ceil((r.t_left_ms||0)/1000)+'s'; }
    if(!lobby && !result){
      var mm = r.phase==='hiding'?'ESCONDER':(r.phase==='seeking'?'CAÇAR':'');
      $('cam-hud-phase').textContent = mm;
      $('cam-hud-timer').textContent = Math.ceil((r.t_left_ms||0)/1000)+'s';
      $('cam-hud-left').textContent = '🦎 '+r.restantes;
      $('cam-hud-score').textContent = (r.you.role==='seeker'?'🔦 ':'🙈 ')+r.you.score;
      $('cam-hud').className = 'cam-hud '+(r.phase==='seeking'&&r.t_left_ms<10000?'urg':'');
    }
    if(result){ montarResultado(r.result||{}); }
  }

  function montarSkinbar(){
    var bar=$('cam-skins'); if(!bar||!scene) return; bar.innerHTML='';
    scene.skins.forEach(function(s){ var b=document.createElement('button'); b.className='cam-skin'; b.dataset.s=s;
      var cv=document.createElement('canvas'); cv.width=52; cv.height=52; desenhaSprite(cv.getContext('2d'),s,26,28,20,1);
      b.appendChild(cv);
      b.onclick=function(){ skinSel=s; lastMoveAt=Date.now(); marcarSkin(); sfxTap(); };
      bar.appendChild(b);
    });
    if(!skinSel) skinSel = scene.skins[0]; marcarSkin();
  }
  function marcarSkin(){ var bs=document.querySelectorAll('.cam-skin'); for(var i=0;i<bs.length;i++){ bs[i].classList.toggle('sel', bs[i].dataset.s===skinSel); } }

  var camoTravado=false;
  function camoAtivo(){ return camoTravado || (Date.now()-lastMoveAt > 1500); }
  $('cam-btn-lock').addEventListener('click', function(){ camoTravado=!camoTravado; if(camoTravado){ me.tx=me.x; me.ty=me.y; sfxCamo(); toast('Camuflado! 🫥'); } this.classList.toggle('on', camoTravado); });

  function montarResultado(res){
    var venc = res.vencedor==='cacadores'?'🔦 Caçadores venceram!':(res.vencedor==='escondidos'?'🦎 Escondidos venceram!':'Fim!');
    $('cam-res-title').textContent = venc;
    var ul=$('cam-res-list'); ul.innerHTML='';
    (res.jogadores||[]).forEach(function(p,i){ var li=document.createElement('div'); li.className='cam-res-row'+(i===0?' top':'');
      var pa = p.papel==='seeker'?'🔦':(p.sobreviveu?'🦎':'💥');
      li.innerHTML='<span>'+(i===0?'👑 ':'')+pa+' '+esc(p.nick)+'</span><b>'+p.pontos+'</b>'; ul.appendChild(li); });
  }
  function esc(s){ return (s||'').replace(/[<>&]/g,function(c){return {'<':'&lt;','>':'&gt;','&':'&amp;'}[c];}); }

  // share
  function shareLink(){ var url = location.origin+'/arena/camaleao/j/'+(TOKEN||'');
    var t='🦎 Bora jogar Camaleão comigo? Esconde-esconde de camuflagem, entra pelo link 👉 '; location.href='https://wa.me/?text='+encodeURIComponent(t+url); }
  $('cam-btn-share').addEventListener('click', shareLink);
  $('cam-btn-share2').addEventListener('click', shareLink);
  $('cam-btn-start').addEventListener('click', function(){ if(!HOST) return; sfxStart(); api('/room/'+TOKEN+'/start',{pid:PID},function(r){ if(r&&r.ok===false){ toast(msgErro(r.erro)); } }); });
  $('cam-btn-again').addEventListener('click', function(){ show('cam-result',false); toast('Voltando pro lobby…'); });
  $('cam-copy').addEventListener('click', function(){ var url=location.origin+'/arena/camaleao/j/'+(TOKEN||''); try{navigator.clipboard.writeText(url);}catch(e){} this.textContent='Copiado!'; var b=this; setTimeout(function(){b.textContent='Copiar link';},1400); });
  function msgErro(e){ return {poucos:'Precisa de pelo menos 2 jogadores', so_host:'Só o anfitrião começa', ja_comecou:'Já começou'}[e]||'Não deu'; }

  function toast(t){ var el=$('cam-toast'); el.textContent=t; el.classList.add('on'); clearTimeout(el._t); el._t=setTimeout(function(){ el.classList.remove('on'); },1600); }
  function telaMsg(t){ show('cam-create',false); show('cam-lobby',false); show('cam-hud',false); var m=$('cam-fatal'); $('cam-fatal-txt').textContent=t; m.style.display=''; }

  // ───────────────────────── input (tap-to-walk + cutucar) ─────────────────────────
  canvas.addEventListener('pointerdown', function(e){
    if(!scene || phase==='lobby' || phase==='result') return;
    if(actx&&actx.state==='suspended') actx.resume();
    var wx = clamp(s2wx(e.clientX), 20, scene.w-20), wy = clamp(s2wy(e.clientY), 20, scene.h-20);
    if(me.role==='seeker' && phase==='seeking'){
      // acha o prop mais perto do toque; se houver, cutuca
      var best=null, bd=1e9;
      for(var id in render.props){ var p=render.props[id]; var d=dist(p.x,p.y,wx,wy); if(d<bd){ bd=d; best=id; } }
      me.tx=wx; me.ty=wy; lastMoveAt=Date.now(); sfxTap();
      if(best && bd<40){ cutucar(best); }
    } else if(me.role==='hider' && (phase==='hiding'||phase==='seeking')){
      if(snap && snap.you && !snap.you.alive) return;      // já achado, não anda
      me.tx=wx; me.ty=wy; lastMoveAt=Date.now(); camoTravado=false; var bl=$('cam-btn-lock'); if(bl) bl.classList.remove('on'); sfxTap();
    }
  });
  function cutucar(id){
    if(Date.now()<cooldownUntil){ toast('Recarregando… ⏳'); return; }
    api('/room/'+TOKEN+'/tag', {pid:PID, target_id:id}, function(r){
      if(!r) return;
      if(r.result==='hit'){ sfxHit(); vibra([20,40,30]); var p=render.props[id]; if(p) flashes.push({x:p.x,y:p.y,t:Date.now(),kind:'hit'}); toast('ACHOU! +'+ (r.score!=null?'': '') +'🎉'); delete render.props[id]; }
      else if(r.result==='miss'){ sfxMiss(); vibra(60); cooldownUntil=Date.now()+(r.cooldown_ms||3500); var q=render.props[id]; if(q) flashes.push({x:q.x,y:q.y,t:Date.now(),kind:'miss'}); toast('Era um objeto de verdade 😬'); }
      else if(r.result==='far'){ toast('Chega mais perto 👣'); }
      else if(r.result==='cooldown'){ toast('Recarregando… ⏳'); }
    });
  }

  // ───────────────────────── render loop ─────────────────────────
  var last=0;
  function loopRender(ts){
    requestAnimationFrame(loopRender);
    var dt = Math.min((ts-last)/1000, 0.05); last=ts;
    passo(dt);
    if(scene && phase!=='lobby' && phase!=='result') desenha();
    else limpa();
  }
  function passo(dt){
    if(!scene) return;
    // meu avatar caminha ate o alvo
    var pode = (me.role==='hider'&&snap&&snap.you&&snap.you.alive&&(phase==='hiding'||phase==='seeking')) || (me.role==='seeker'&&phase==='seeking');
    if(pode){ var spd = (me.role==='seeker'?280:250); var d=dist(me.x,me.y,me.tx,me.ty);
      if(d>1){ var step=Math.min(d, spd*dt); me.x+=(me.tx-me.x)*(step/d); me.y+=(me.ty-me.y)*(step/d); lastMoveAt=Date.now(); } }
    // suaviza outros
    for(var id in render.seekers){ var s=render.seekers[id]; s.x+=((s.tx||s.x)-s.x)*Math.min(1,dt*10); s.y+=((s.ty||s.y)-s.y)*Math.min(1,dt*10); }
    for(var pid in render.props){ var p=render.props[pid]; p.x+=((p.tx||p.x)-p.x)*Math.min(1,dt*10); p.y+=((p.ty||p.y)-p.y)*Math.min(1,dt*10); }
    flashes = flashes.filter(function(f){ return Date.now()-f.t < 600; });
  }
  function limpa(){ ctx.clearRect(0,0,W,H); }

  function desenha(){
    ctx.fillStyle=COL.bg; ctx.fillRect(0,0,W,H);
    desenhaChao();
    // objetos
    if(me.role==='seeker' && phase==='seeking'){
      for(var id in render.props){ var p=render.props[id]; desenhaSpriteWorld(p.sprite||'barril', p.x, p.y, false); }
    } else {
      for(var id2 in render.props){ var d=render.props[id2]; desenhaSpriteWorld(d.sprite||'barril', d.x, d.y, false); }
    }
    // outros caçadores
    for(var sid in render.seekers){ var s=render.seekers[sid]; desenhaCacador(s.x,s.y,false); }
    // meu avatar
    if(me.role==='seeker'){ desenhaCacador(me.x,me.y,true); }
    else if(snap&&snap.you&&snap.you.alive){
      if(camoAtivo() && phase==='seeking'){ desenhaSpriteWorld(skinSel||'barril', me.x, me.y, true); }
      else { desenhaCamaleao(me.x, me.y, camoAtivo()); }
    }
    // flashes
    flashes.forEach(function(f){ var a=1-(Date.now()-f.t)/600; ctx.save(); ctx.globalAlpha=Math.max(0,a);
      ctx.strokeStyle=f.kind==='hit'?COL.ro2:COL.red; ctx.lineWidth=4*DPR; ctx.beginPath();
      ctx.arc(w2sx(f.x),w2sy(f.y),(1-a)*70*sc+10,0,7); ctx.stroke(); ctx.restore(); });
    // cooldown ring no meu avatar (caçador)
    if(me.role==='seeker' && Date.now()<cooldownUntil){ var cd=(cooldownUntil-Date.now()); ctx.save();
      ctx.strokeStyle=COL.red; ctx.lineWidth=3*DPR; ctx.globalAlpha=0.8; ctx.beginPath();
      ctx.arc(w2sx(me.x),w2sy(me.y),34*sc, -1.57, -1.57+6.28*(cd/3500)); ctx.stroke(); ctx.restore(); }
  }

  function desenhaChao(){
    var g=28; ctx.strokeStyle=COL.grid; ctx.lineWidth=1*DPR; ctx.globalAlpha=0.6; ctx.beginPath();
    for(var x=0;x<=scene.w;x+=g){ ctx.moveTo(w2sx(x),w2sy(0)); ctx.lineTo(w2sx(x),w2sy(scene.h)); }
    for(var y=0;y<=scene.h;y+=g){ ctx.moveTo(w2sx(0),w2sy(y)); ctx.lineTo(w2sx(scene.w),w2sy(y)); }
    ctx.stroke(); ctx.globalAlpha=1;
    // borda da arena
    ctx.strokeStyle=COL.ro; ctx.lineWidth=3*DPR; ctx.shadowColor=COL.ro; ctx.shadowBlur=12*DPR;
    ctx.strokeRect(w2sx(0),w2sy(0),scene.w*sc,scene.h*sc); ctx.shadowBlur=0;
    (scene.walls||[]).forEach(function(wl){ ctx.fillStyle=COL.wall; ctx.fillRect(w2sx(wl.x),w2sy(wl.y),wl.w*sc,wl.h*sc);
      ctx.strokeStyle=COL.ci; ctx.lineWidth=2*DPR; ctx.strokeRect(w2sx(wl.x),w2sy(wl.y),wl.w*sc,wl.h*sc); });
  }

  function desenhaSpriteWorld(type,x,y,mine){ desenhaSprite(ctx, type, w2sx(x), w2sy(y), 22*sc, DPR, mine); }

  // sprites desenhados em codigo (neon). tam ~ raio r.
  function desenhaSprite(c, type, x, y, r, dpr, mine){
    dpr=dpr||1; c.save(); c.translate(x,y);
    var glow = mine?COL.gr:COL.ci; c.shadowColor=glow; c.shadowBlur=(mine?14:6)*dpr; c.lineWidth=2*dpr;
    if(type==='barril'){ c.fillStyle='#3a2f6e'; c.strokeStyle=COL.ci; rr(c,-r*0.6,-r,r*1.2,r*2,6); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(-r*0.6,-r*0.2); c.lineTo(r*0.6,-r*0.2); c.moveTo(-r*0.6,r*0.4); c.lineTo(r*0.6,r*0.4); c.stroke(); }
    else if(type==='caixa'){ c.fillStyle='#4a2f5e'; c.strokeStyle=COL.ro2; rr(c,-r*0.85,-r*0.85,r*1.7,r*1.7,4); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(-r*0.85,-r*0.2); c.lineTo(r*0.85,-r*0.2); c.stroke(); }
    else if(type==='planta'){ c.strokeStyle=COL.gr; c.fillStyle='#123d2e'; rr(c,-r*0.5,r*0.2,r,r*0.8,3); c.fill(); c.stroke();
      c.fillStyle='#1f7a54'; leaf(c,0,-r*0.2,r*0.9); leaf(c,-r*0.5,0,r*0.7); leaf(c,r*0.5,0,r*0.7); }
    else if(type==='extintor'){ c.fillStyle='#7a1330'; c.strokeStyle=COL.red; rr(c,-r*0.45,-r*0.7,r*0.9,r*1.7,5); c.fill(); c.stroke();
      c.fillStyle=COL.ink; rr(c,-r*0.2,-r,r*0.4,r*0.4,2); c.fill(); }
    else if(type==='cone'){ c.fillStyle='#7a4a10'; c.strokeStyle=COL.am; c.beginPath(); c.moveTo(0,-r); c.lineTo(r*0.8,r*0.8); c.lineTo(-r*0.8,r*0.8); c.closePath(); c.fill(); c.stroke();
      c.strokeStyle=COL.ink; c.beginPath(); c.moveTo(-r*0.4,0); c.lineTo(r*0.4,0); c.stroke(); }
    else if(type==='tv'){ c.fillStyle='#122a3a'; c.strokeStyle=COL.ci; rr(c,-r*0.9,-r*0.7,r*1.8,r*1.4,4); c.fill(); c.stroke();
      c.fillStyle=COL.ci; c.globalAlpha=0.35; rr(c,-r*0.7,-r*0.5,r*1.4,r*1.0,2); c.fill(); c.globalAlpha=1; }
    else { c.fillStyle=COL.ro; c.beginPath(); c.arc(0,0,r*0.8,0,7); c.fill(); }
    c.restore();
  }
  function rr(c,x,y,w,h,rad){ c.beginPath(); c.moveTo(x+rad,y); c.arcTo(x+w,y,x+w,y+h,rad); c.arcTo(x+w,y+h,x,y+h,rad); c.arcTo(x,y+h,x,y,rad); c.arcTo(x,y,x+w,y,rad); c.closePath(); }
  function leaf(c,x,y,s){ c.beginPath(); c.ellipse(x,y,s*0.28,s*0.5,0,0,7); c.fill(); }

  function desenhaCamaleao(x,y,camo){
    var sx=w2sx(x), sy=w2sy(y), r=22*sc; var wig=camo?0:Math.sin(Date.now()/120)*2*sc;
    ctx.save(); ctx.translate(sx,sy+wig);
    ctx.globalAlpha = camo?0.5:1; ctx.shadowColor=COL.ro2; ctx.shadowBlur=(camo?4:16)*DPR;
    // cauda
    ctx.strokeStyle=COL.ro2; ctx.lineWidth=4*DPR; ctx.beginPath(); ctx.arc(r*0.7,r*0.2,r*0.5,-1.2,2.2); ctx.stroke();
    // corpo
    ctx.fillStyle=camo?'#5a3a6e':COL.ro2; ctx.beginPath(); ctx.ellipse(0,0,r*0.95,r*0.7,0,0,7); ctx.fill();
    // crista
    ctx.fillStyle=COL.ro; for(var i=-1;i<=1;i++){ ctx.beginPath(); ctx.moveTo(i*r*0.28-r*0.15,-r*0.6); ctx.lineTo(i*r*0.28,-r*1.0); ctx.lineTo(i*r*0.28+r*0.15,-r*0.6); ctx.fill(); }
    // olho
    ctx.shadowBlur=0; ctx.fillStyle=COL.ink; ctx.beginPath(); ctx.arc(-r*0.5,-r*0.15,r*0.32,0,7); ctx.fill();
    ctx.fillStyle='#07060f'; var dx=clamp((me.tx-x)/60,-1,1)*r*0.12, dy=clamp((me.ty-y)/60,-1,1)*r*0.12; ctx.beginPath(); ctx.arc(-r*0.5+dx,-r*0.15+dy,r*0.14,0,7); ctx.fill();
    // sorriso
    ctx.strokeStyle='#07060f'; ctx.lineWidth=2*DPR; ctx.beginPath(); ctx.arc(-r*0.15,r*0.15,r*0.3,0.1,1.2); ctx.stroke();
    ctx.restore(); ctx.globalAlpha=1; ctx.shadowBlur=0;
  }
  function desenhaCacador(x,y,mine){
    var sx=w2sx(x), sy=w2sy(y), r=22*sc;
    ctx.save(); ctx.translate(sx,sy);
    if(mine){ // cone de lanterna
      ctx.fillStyle=COL.ci; ctx.globalAlpha=0.10; ctx.beginPath(); ctx.arc(0,0,90*sc,0,7); ctx.fill(); ctx.globalAlpha=1; }
    ctx.shadowColor=COL.ci; ctx.shadowBlur=(mine?16:8)*DPR;
    ctx.fillStyle=mine?COL.ci:'#2a6f80'; ctx.beginPath(); ctx.arc(0,0,r*0.8,0,7); ctx.fill();
    ctx.fillStyle=COL.ink; ctx.beginPath(); ctx.arc(0,-r*0.5,r*0.3,0,7); ctx.fill(); // "lanterna" na cabeça
    ctx.shadowBlur=0; ctx.fillStyle='#07060f'; ctx.beginPath(); ctx.arc(-r*0.25,-r*0.1,r*0.12,0,7); ctx.arc(r*0.25,-r*0.1,r*0.12,0,7); ctx.fill();
    ctx.restore(); ctx.shadowBlur=0;
  }

  // primeiro toque libera audio no mobile
  document.body.addEventListener('touchstart', function(){ if(actx&&actx.state==='suspended') actx.resume(); }, {passive:true, once:true});
})();
