/* CAMALEAO — cliente v2 (canvas 2D top-down, CAMERA QUE SEGUE, arte neon rica).
   Polling ~200ms + interpolacao. Servidor e a autoridade; aqui desenha o que ele
   manda + predicao local suave do proprio avatar. Anti-leak: o caçador so ve
   `props` (indistinguiveis). v2: mapa grande + camera + sprites detalhados +
   ambiencia + particulas + shake + minimapa.
*/
(function () {
  'use strict';
  var API = '/arena/camaleao/api/v1';
  var TOKEN = window.CAM_TOKEN || null;
  var PID = null, HOST = false;

  var scene = null, snap = null, phase = 'boot';
  var me = { x: 700, y: 1000, tx: 700, ty: 1000, role: 'lobby' };
  var skinSel = null, camoTravado = false, lastMoveAt = 0;
  var render = { seekers: {}, props: {} };
  var cooldownUntil = 0;
  var parts = [];                 // particulas {x,y,vx,vy,t,life,col,r}
  var shake = 0;
  var PALETTE = [], meuTint = null, meuCamo = 0, dicaTint = -1;   // PINTURA
  var sprCache = {}, CS = 96, CR = 34;                           // cache offscreen por (type,tint)

  var DPR = Math.min(window.devicePixelRatio || 1, 2);
  var canvas = document.getElementById('cam-canvas');
  var ctx = canvas.getContext('2d');
  var WC = 0, HC = 0, ZOOM = 1;                 // CSS px; ZOOM = CSS px por unidade de mundo
  var cam = { x: 700, y: 1000 };
  var VIEWMIN = 560;                            // unidades de mundo visiveis no lado curto
  var R = 30;                                   // raio base do sprite (unidades de mundo)

  var COL = { bg:'#07060f', bg2:'#0c0b1d', grid:'#1a1640', ci:'#22d3ee', ro:'#a855f7',
             ro2:'#f472b6', am:'#fbbf24', gr:'#34d399', ink:'#f5f3ff', red:'#ff3b6b' };

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

  // ── audio ──
  var actx=null;
  function beep(f,d,t,v){ try{ if(!actx){ var AC=window.AudioContext||window.webkitAudioContext; if(!AC) return; actx=new AC(); }
    if(actx.state==='suspended') actx.resume();
    var o=actx.createOscillator(), g=actx.createGain(); o.type=t||'square'; o.frequency.value=f; g.gain.value=v||0.06;
    o.connect(g); g.connect(actx.destination); var n=actx.currentTime; g.gain.setValueAtTime(g.gain.value,n);
    g.gain.exponentialRampToValueAtTime(0.0001,n+d); o.start(n); o.stop(n+d); }catch(e){} }
  function sfxTap(){ beep(180,0.05,'sine',0.03); }
  function sfxHit(){ beep(523,0.10,'triangle',0.09); setTimeout(function(){beep(784,0.16,'triangle',0.09);},90); }
  function sfxMiss(){ beep(150,0.18,'sawtooth',0.07); }
  function sfxCamo(){ beep(440,0.12,'sine',0.05); }
  function sfxStart(){ beep(330,0.08,'square',0.06); setTimeout(function(){beep(660,0.12,'square',0.06);},110); }
  function vibra(m){ try{ if(navigator.vibrate) navigator.vibrate(m); }catch(e){} }

  // ── bootstrap ──
  var modoJoin = false;
  if (TOKEN) { if(nickSalvo()){ entrar(); } else { modoEntrar(); } }
  else { show('cam-create', true); }
  function modoEntrar(){                    // convidado sem apelido salvo: pede o nome
    modoJoin = true;
    var sub=document.querySelector('#cam-create .cam-sub'); if(sub) sub.innerHTML='Escolhe teu apelido e<br>entra na sala 👇';
    var fine=document.querySelector('#cam-create .cam-fine'); if(fine) fine.textContent='você foi convidado pra jogar';
    $('cam-btn-create').textContent='ENTRAR';
    var pre=nickSalvo(); if(pre) $('cam-nick').value=pre;
    show('cam-create', true); try{$('cam-nick').focus();}catch(e){}
  }
  $('cam-btn-create').addEventListener('click', function(){
    var n=($('cam-nick').value||'').trim().slice(0,24);
    if(!n){ toast('Escreve teu apelido 🙂'); try{$('cam-nick').focus();}catch(e){} return; }
    salvaNick(n);
    if(modoJoin){ show('cam-create',false); entrar(); return; }
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

  // ── viewport / camera ──
  function ajustar(){
    WC = window.innerWidth; HC = window.innerHeight;
    canvas.width = Math.floor(WC*DPR); canvas.height = Math.floor(HC*DPR);
    canvas.style.width = WC+'px'; canvas.style.height = HC+'px';
    ZOOM = clamp(Math.min(WC,HC)/VIEWMIN, 0.5, 1.15);
  }
  window.addEventListener('resize', ajustar);
  window.addEventListener('orientationchange', function(){ setTimeout(ajustar,150); });
  function w2sx(x){ return (x-cam.x)*ZOOM + WC/2; }
  function w2sy(y){ return (y-cam.y)*ZOOM + HC/2; }
  function s2wx(cx){ return (cx - WC/2)/ZOOM + cam.x; }
  function s2wy(cy){ return (cy - HC/2)/ZOOM + cam.y; }

  // ── polling ──
  function loopPoll(){ sync(); setTimeout(loopPoll, 200); }
  function sync(){
    if(!PID||!TOKEN) return;
    var input = { x: Math.round(me.x), y: Math.round(me.y) };
    if(me.role==='hider'){ input.skin = skinSel; input.tint = meuTint; input.locked = camoAtivo(); }
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
    // eco do servidor só PREENCHE quando o local ainda não escolheu — senão o poll reverte o toque do jogador
    if(!skinSel && typeof r.you.skin==='string') skinSel = r.you.skin;
    if(meuTint==null && typeof r.you.tint==='number'){ meuTint = r.you.tint; marcarTinta(); }
    meuCamo = r.you.camo||0; dicaTint = (typeof r.you.dica_tint==='number')?r.you.dica_tint:-1;
    if(dist(me.x,me.y,r.you.x,r.you.y) > 60){ me.x=r.you.x; me.y=r.you.y; me.tx=me.x; me.ty=me.y; }
    var vistos={};
    (r.seekers||[]).forEach(function(s){ var id=s.pid||('s'+Math.round(s.x)+'_'+Math.round(s.y));
      vistos[id]=1; var t=render.seekers[id]; if(!t){ t=render.seekers[id]={x:s.x,y:s.y}; } t.tx=s.x; t.ty=s.y; t.nick=s.nick; });
    Object.keys(render.seekers).forEach(function(id){ if(!vistos[id]) delete render.seekers[id]; });
    var lista = r.props || r.decoys || [];
    var pv={};
    lista.forEach(function(p){ pv[p.id]=1; var t=render.props[p.id]; if(!t){ t=render.props[p.id]={x:p.x,y:p.y}; } t.tx=p.x; t.ty=p.y; t.sprite=p.sprite; t.tint=p.tint; });
    Object.keys(render.props).forEach(function(id){ if(!pv[id]) delete render.props[id]; });
    cooldownUntil = Date.now() + (r.you.cooldown_ms||0);
    if(phase!==r.phase){ trocouFase(phase, r.phase); phase=r.phase; }
    atualizarUI(r);
  }
  function carregarCena(id){
    fetch(API+'/scene/'+id).then(function(r){return r.json();}).then(function(j){
      if(j&&j.scene){ scene=j.scene; PALETTE=scene.palette||[]; sprCache={}; ajustar(); cam.x=me.x; cam.y=me.y; montarSkinbar(); montarTintas(); }
    }).catch(function(){});
  }
  function trocouFase(de, para){
    var pc = window.matchMedia && matchMedia('(pointer:fine)').matches;
    if(para==='hiding'){ sfxStart(); cam.x=me.x; cam.y=me.y; me.tx=me.x; me.ty=me.y;
      meuTint=null; camoTravado=false; var bl=$('cam-btn-lock'); if(bl) bl.classList.remove('on');   // re-adota a cor do spawn da rodada nova
      toast('Escondam-se! 🦎'+(pc?' · WASD anda':'')); }
    if(para==='seeking'){ sfxStart(); cam.x=me.x; cam.y=me.y; toast(me.role==='seeker'?('CAÇAR! 🔦'+(pc?' · WASD anda':'')):'Fiquem quietos… 🤫'); }
    if(para==='result'){ vibra(30); }
  }

  // ── UI (DOM overlays) ──
  function atualizarUI(r){
    show('cam-create', false);
    var lobby=r.phase==='lobby', result=r.phase==='result', blind=(r.phase==='hiding'&&r.you.role==='seeker');
    show('cam-lobby', lobby); show('cam-result', result); show('cam-blind', blind);
    show('cam-hud', !lobby && !result);
    show('cam-skinbar', r.phase==='hiding' && r.you.role==='hider' && !!scene);
    show('cam-camo', !lobby && !result && r.you.role==='hider');
    show('cam-crosshair', r.phase==='seeking' && r.you.role==='seeker');
    if(lobby){
      HOST=!!r.host;
      $('cam-lobby-count').textContent = r.n_jogadores + (r.n_jogadores===1?' jogador':' jogadores');
      var ul=$('cam-lobby-players'); ul.innerHTML='';
      (r.jogadores||[]).forEach(function(p){ var li=document.createElement('div'); li.className='cam-pl';
        li.textContent='🦎 '+p.nick + (p.pid===r.host_pid?'  👑':''); ul.appendChild(li); });
      var enough = r.n_jogadores >= (r.min_jogadores||2);
      show('cam-btn-start', HOST); var bs=$('cam-btn-start'); bs.disabled=!enough;
      bs.textContent = enough?'▶ COMEÇAR':'Esperando jogadores…';
      $('cam-hint-nohost').style.display = HOST?'none':'';
    }
    if(blind){ $('cam-blind-timer').textContent = Math.ceil((r.t_left_ms||0)/1000)+'s'; }
    if(!lobby && !result){
      $('cam-hud-phase').textContent = r.phase==='hiding'?'ESCONDER':(r.phase==='seeking'?'CAÇAR':'');
      $('cam-hud-timer').textContent = Math.ceil((r.t_left_ms||0)/1000)+'s';
      $('cam-hud-left').textContent = '🦎 '+r.restantes;
      $('cam-hud-score').textContent = (r.you.role==='seeker'?'🔦 ':'🙈 ')+r.you.score;
      $('cam-hud').className = 'cam-hud '+(r.phase==='seeking'&&r.t_left_ms<10000?'urg':'');
      if(r.you.role==='hider'){
        var camo=r.you.camo||0;
        $('cam-camo-fill').style.width=camo+'%';
        $('cam-camo-fill').style.background = camo>=75?'#34d399':(camo>=40?'#fbbf24':'#f87171');
        $('cam-camo-lbl').textContent = camo+'% · '+(camo>=75?'Você sumiu! 🫥':(camo>=40?'Dá pra te achar 👀':'Você destoa 🔴'));
        marcarTinta();
      }
    }
    if(result){ montarResultado(r.result||{}); }
  }
  function montarSkinbar(){
    var bar=$('cam-skins'); if(!bar||!scene) return; bar.innerHTML='';
    ['camaleao'].concat(scene.skins).forEach(function(s){ var b=document.createElement('button'); b.className='cam-skin'; b.dataset.s=s;
      var cv=document.createElement('canvas'); cv.width=52; cv.height=52; var cc=cv.getContext('2d'); cc.translate(26,27); desenhaForma(cc,s,19);
      b.appendChild(cv);
      b.onclick=function(){ skinSel=s; lastMoveAt=Date.now(); marcarSkin(); sfxTap(); };
      bar.appendChild(b);
    });
    if(!skinSel) skinSel = scene.skins[0]; marcarSkin();
  }
  function marcarSkin(){ var bs=document.querySelectorAll('.cam-skin'); for(var i=0;i<bs.length;i++){ bs[i].classList.toggle('sel', bs[i].dataset.s===skinSel); } }
  // FITA DE TINTAS: 8 swatches = a paleta da cena; tap repinta na hora (splatter+som+vibra). A bolinha da dica_tint pulsa.
  function montarTintas(){
    var bar=$('cam-tints'); if(!bar) return; bar.innerHTML='';
    (PALETTE||[]).forEach(function(rgb,idx){ var b=document.createElement('button'); b.className='cam-tint'; b.dataset.i=idx;
      b.style.background='rgb('+rgb[0]+','+rgb[1]+','+rgb[2]+')';
      b.onclick=function(){ meuTint=idx; lastMoveAt=Date.now(); marcarTinta(); pintar(); };
      bar.appendChild(b); });
    marcarTinta();
  }
  function marcarTinta(){ var bs=document.querySelectorAll('.cam-tint'); for(var i=0;i<bs.length;i++){ var idx=+bs[i].dataset.i; bs[i].classList.toggle('sel', idx===meuTint); bs[i].classList.toggle('dica', idx===dicaTint); } }
  function pintar(){ var col=(meuTint!=null&&PALETTE[meuTint])?('rgb('+PALETTE[meuTint].join(',')+')'):COL.gr; explode(me.x, me.y, col, 12); sfxCamo(); vibra(20); }
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
  function shareLink(){ var url=location.origin+'/arena/camaleao/j/'+(TOKEN||'');
    location.href='https://wa.me/?text='+encodeURIComponent('🦎 Bora jogar Camaleão comigo? Esconde-esconde de camuflagem, entra pelo link 👉 '+url); }
  $('cam-btn-share').addEventListener('click', shareLink);
  $('cam-btn-share2').addEventListener('click', shareLink);
  $('cam-btn-start').addEventListener('click', function(){
    if(!HOST){ toast('Só o anfitrião começa'); return; }
    sfxStart();
    api('/room/'+TOKEN+'/start',{pid:PID},function(r,st){
      if(!r){ toast('Sem conexão — tenta de novo'); return; }
      if(st===404){ telaMsg('A partida encerrou (o servidor reiniciou). Crie uma sala nova.'); return; }
      if(r.ok===false){ toast(msgErro(r.erro)); }
    });
  });
  $('cam-btn-again').addEventListener('click', function(){ show('cam-result',false); toast('Voltando pro lobby…'); });
  $('cam-copy').addEventListener('click', function(){ var url=location.origin+'/arena/camaleao/j/'+(TOKEN||''); try{navigator.clipboard.writeText(url);}catch(e){} this.textContent='Copiado!'; var b=this; setTimeout(function(){b.textContent='Copiar link';},1400); });
  function msgErro(e){ return {poucos:'Precisa de pelo menos 2 jogadores', so_host:'Só o anfitrião começa', ja_comecou:'Já começou'}[e]||'Não deu'; }
  function toast(t){ var el=$('cam-toast'); el.textContent=t; el.classList.add('on'); clearTimeout(el._t); el._t=setTimeout(function(){ el.classList.remove('on'); },1600); }
  function telaMsg(t){ show('cam-create',false); show('cam-lobby',false); show('cam-hud',false); var m=$('cam-fatal'); $('cam-fatal-txt').textContent=t; m.style.display=''; }

  // ── input ──
  canvas.addEventListener('pointerdown', function(e){
    if(!scene || phase==='lobby' || phase==='result') return;
    if(actx&&actx.state==='suspended') actx.resume();
    var wx = clamp(s2wx(e.clientX), 24, scene.w-24), wy = clamp(s2wy(e.clientY), 24, scene.h-24);
    if(me.role==='seeker' && phase==='seeking'){
      var best=null, bd=1e9;
      for(var id in render.props){ var p=render.props[id]; var d=dist(p.x,p.y,wx,wy); if(d<bd){ bd=d; best=id; } }
      me.tx=wx; me.ty=wy; lastMoveAt=Date.now(); sfxTap();
      if(best && bd<44){ cutucar(best); }
    } else if(me.role==='hider' && (phase==='hiding'||phase==='seeking')){
      if(snap && snap.you && !snap.you.alive) return;
      me.tx=wx; me.ty=wy; lastMoveAt=Date.now(); camoTravado=false; var bl=$('cam-btn-lock'); if(bl) bl.classList.remove('on'); sfxTap();
    }
  });
  // ── movimento fluido: arrastar guia (mouse/dedo) + WASD/setas no PC ──
  canvas.style.touchAction='none';
  var dragging=false;
  canvas.addEventListener('pointerdown', function(e){ dragging=true; try{ canvas.setPointerCapture(e.pointerId); }catch(_){}; });
  canvas.addEventListener('pointermove', function(e){
    if(!dragging || !scene || phase==='lobby' || phase==='result') return;
    if(me.role==='seeker' && phase!=='seeking') return;
    if(me.role==='hider' && snap && snap.you && !snap.you.alive) return;
    me.tx = clamp(s2wx(e.clientX), 24, scene.w-24); me.ty = clamp(s2wy(e.clientY), 24, scene.h-24);
    if(me.role==='hider'){ camoTravado=false; var bl=$('cam-btn-lock'); if(bl) bl.classList.remove('on'); }
  });
  addEventListener('pointerup', function(){ dragging=false; });
  addEventListener('pointercancel', function(){ dragging=false; });
  var KEYS={};
  function kdir(k){ k=(k||'').toLowerCase(); return {w:'u',arrowup:'u',s:'d',arrowdown:'d',a:'l',arrowleft:'l',d:'r',arrowright:'r'}[k]; }
  addEventListener('keydown', function(e){
    if(e.target && /INPUT|TEXTAREA/.test(e.target.tagName||'')) return;
    var d=kdir(e.key); if(!d) return; e.preventDefault(); KEYS[d]=1;
    if(actx&&actx.state==='suspended') actx.resume();
    if(me.role==='hider'){ camoTravado=false; var bl=$('cam-btn-lock'); if(bl) bl.classList.remove('on'); }
  });
  addEventListener('keyup', function(e){ var d=kdir(e.key); if(!d) return; delete KEYS[d];
    if(!KEYS.u&&!KEYS.d&&!KEYS.l&&!KEYS.r){ me.tx=me.x; me.ty=me.y; } });   // soltou = para seco (sem deslizar)
  function cutucar(id){
    if(Date.now()<cooldownUntil){ toast('Recarregando… ⏳'); return; }
    api('/room/'+TOKEN+'/tag', {pid:PID, target_id:id}, function(r){
      if(!r) return; var p=render.props[id];
      if(r.result==='hit'){ sfxHit(); vibra([20,40,30]); shake=Math.max(shake,10); if(p) explode(p.x,p.y,COL.ro2,16); toast('ACHOU! 🎉'); delete render.props[id]; }
      else if(r.result==='miss'){ sfxMiss(); vibra(60); shake=Math.max(shake,6); cooldownUntil=Date.now()+(r.cooldown_ms||3500); if(p) explode(p.x,p.y,COL.red,8); toast('Era um objeto de verdade 😬'); }
      else if(r.result==='far'){ toast('Chega mais perto 👣'); }
      else if(r.result==='cooldown'){ toast('Recarregando… ⏳'); }
    });
  }
  function explode(x,y,col,n){ for(var i=0;i<n;i++){ var a=Math.random()*6.28, sp=40+Math.random()*90;
    parts.push({x:x,y:y,vx:Math.cos(a)*sp,vy:Math.sin(a)*sp,t:0,life:0.5+Math.random()*0.3,col:col,r:2+Math.random()*3}); } }

  // ── render loop ──
  var last=0;
  function loopRender(ts){
    requestAnimationFrame(loopRender);
    var dt = Math.min((ts-last)/1000, 0.05); last=ts||0;
    passo(dt);
    if(scene && phase!=='lobby' && phase!=='result') desenha();
    else { ctx.setTransform(1,0,0,1,0,0); ctx.clearRect(0,0,canvas.width,canvas.height); }
  }
  function passo(dt){
    if(!scene) return;
    // teclado: segurar = renovar o alvo um passo à frente (anda contínuo na velocidade cheia)
    var kx=(KEYS.r?1:0)-(KEYS.l?1:0), ky=(KEYS.d?1:0)-(KEYS.u?1:0);
    if(kx||ky){ var kn=Math.hypot(kx,ky)||1;
      me.tx=clamp(me.x+kx/kn*48, 24, scene.w-24); me.ty=clamp(me.y+ky/kn*48, 24, scene.h-24); }
    var pode = (me.role==='hider'&&snap&&snap.you&&snap.you.alive&&(phase==='hiding'||phase==='seeking')) || (me.role==='seeker'&&phase==='seeking');
    if(pode){ var spd=(me.role==='seeker'?280:250), d=dist(me.x,me.y,me.tx,me.ty);
      if(d>1){ var step=Math.min(d, spd*dt); me.x+=(me.tx-me.x)*(step/d); me.y+=(me.ty-me.y)*(step/d); lastMoveAt=Date.now(); } }
    // camera segue o jogador (clampada ao mapa)
    var vw=WC/ZOOM, vh=HC/ZOOM;
    var tx = scene.w<=vw ? scene.w/2 : clamp(me.x, vw/2, scene.w-vw/2);
    var ty = scene.h<=vh ? scene.h/2 : clamp(me.y, vh/2, scene.h-vh/2);
    if(dist(cam.x,cam.y,tx,ty)>900){ cam.x=tx; cam.y=ty; } else { var k=Math.min(1,dt*9); cam.x+=(tx-cam.x)*k; cam.y+=(ty-cam.y)*k; }
    for(var id in render.seekers){ var s=render.seekers[id]; s.x+=((s.tx||s.x)-s.x)*Math.min(1,dt*10); s.y+=((s.ty||s.y)-s.y)*Math.min(1,dt*10); }
    for(var pid in render.props){ var p=render.props[pid]; p.x+=((p.tx||p.x)-p.x)*Math.min(1,dt*10); p.y+=((p.ty||p.y)-p.y)*Math.min(1,dt*10); }
    parts.forEach(function(f){ f.t+=dt; f.x+=f.vx*dt; f.y+=f.vy*dt; f.vx*=0.9; f.vy*=0.9; });
    parts = parts.filter(function(f){ return f.t<f.life; });
    if(shake>0.2) shake*=0.86; else shake=0;
  }

  function desenha(){
    var shx=(Math.random()-0.5)*shake, shy=(Math.random()-0.5)*shake;
    ctx.setTransform(DPR,0,0,DPR, shx*DPR, shy*DPR);
    // fundo ambiente
    var g=ctx.createRadialGradient(WC/2,HC*0.4,60, WC/2,HC*0.4, Math.max(WC,HC)*0.8);
    g.addColorStop(0,COL.bg2); g.addColorStop(1,COL.bg); ctx.fillStyle=g; ctx.fillRect(0,0,WC,HC);
    desenhaChao();
    // objetos (com culling)
    var lista = (me.role==='seeker' && phase==='seeking') ? render.props : render.props;
    for(var id in lista){ var d=lista[id]; var sx=w2sx(d.x), sy=w2sy(d.y);
      if(sx<-80||sx>WC+80||sy<-80||sy>HC+80) continue;
      var al = d.sprite==='camaleao' ? alphaCam(d.tint, d.x, d.y) : 1;
      blitSprite(d.sprite||'barril', d.tint, sx, sy, R*ZOOM, false, al); }
    // outros caçadores
    for(var sid in render.seekers){ var s=render.seekers[sid]; var ssx=w2sx(s.x),ssy=w2sy(s.y);
      if(ssx>-70&&ssx<WC+70&&ssy>-70&&ssy<HC+70) desenhaCacador(ssx,ssy,R*ZOOM,false); }
    // meu avatar
    var mx=w2sx(me.x), my=w2sy(me.y);
    if(me.role==='seeker'){ desenhaCacador(mx,my,R*ZOOM,true); }
    else if(snap&&snap.you&&snap.you.alive){
      // ESCONDER: você se vê como o item+cor o tempo todo (provador de fantasia); CAÇAR: item só camuflado, andando = camaleão exposto
      if(phase==='hiding' || camoAtivo()){
        var mal = skinSel==='camaleao' ? alphaCam(meuTint, me.x, me.y) : 1;
        blitSprite(skinSel||'barril', meuTint, mx, my, R*ZOOM, true, mal);
      } else { desenhaCamaleao(mx,my,R*ZOOM,false); }
    }
    // particulas
    parts.forEach(function(f){ var a=1-f.t/f.life; ctx.save(); ctx.globalAlpha=Math.max(0,a);
      ctx.fillStyle=f.col; ctx.beginPath(); ctx.arc(w2sx(f.x),w2sy(f.y),f.r*ZOOM,0,7); ctx.fill(); ctx.restore(); });
    // cooldown ring
    if(me.role==='seeker' && Date.now()<cooldownUntil){ var cd=cooldownUntil-Date.now(); ctx.save();
      ctx.strokeStyle=COL.red; ctx.lineWidth=3; ctx.globalAlpha=0.85; ctx.beginPath();
      ctx.arc(mx,my,R*ZOOM+8,-1.57,-1.57+6.28*(cd/3500)); ctx.stroke(); ctx.restore(); }
    // vinheta
    ctx.setTransform(DPR,0,0,DPR,0,0);
    var vg=ctx.createRadialGradient(WC/2,HC/2,Math.min(WC,HC)*0.42, WC/2,HC/2,Math.max(WC,HC)*0.72);
    vg.addColorStop(0,'rgba(0,0,0,0)'); vg.addColorStop(1,'rgba(0,0,0,0.55)'); ctx.fillStyle=vg; ctx.fillRect(0,0,WC,HC);
    minimapa();
  }

  function desenhaChao(){
    var g=68*ZOOM; if(g<8) g=8;
    var x0=w2sx(0), y0=w2sy(0), x1=w2sx(scene.w), y1=w2sy(scene.h);
    ctx.save(); ctx.beginPath(); ctx.rect(Math.max(0,x0),Math.max(0,y0),Math.min(WC,x1)-Math.max(0,x0),Math.min(HC,y1)-Math.max(0,y0)); ctx.clip();
    ctx.strokeStyle=COL.grid; ctx.lineWidth=1; ctx.globalAlpha=0.5; ctx.beginPath();
    var sx0=Math.floor(cam.x/68)*68;
    for(var wx=sx0-68; wx<cam.x+WC/ZOOM; wx+=68){ var px=w2sx(wx); ctx.moveTo(px,y0); ctx.lineTo(px,y1); }
    var sy0=Math.floor(cam.y/68)*68;
    for(var wy=sy0-68; wy<cam.y+HC/ZOOM; wy+=68){ var py=w2sy(wy); ctx.moveTo(x0,py); ctx.lineTo(x1,py); }
    ctx.stroke(); ctx.globalAlpha=1; ctx.restore();
    // zonas de cor (muros grafitados/faixas/tapetes — onde o camaleão pintado some)
    (scene.zones||[]).forEach(function(z){ var c=PALETTE[z.tint]; if(!c) return;
      var zx=w2sx(z.x), zy=w2sy(z.y), zw=z.w*ZOOM, zh=z.h*ZOOM;
      if(zx>WC||zy>HC||zx+zw<0||zy+zh<0) return;
      ctx.fillStyle='rgba('+c[0]+','+c[1]+','+c[2]+',0.30)'; rr(ctx,zx,zy,zw,zh,6*ZOOM); ctx.fill();
      ctx.strokeStyle='rgba('+c[0]+','+c[1]+','+c[2]+',0.55)'; ctx.lineWidth=2; ctx.stroke(); });
    // paredes / conteineres
    (scene.walls||[]).forEach(function(w){ var wx=w2sx(w.x),wy=w2sy(w.y),ww=w.w*ZOOM,wh=w.h*ZOOM;
      if(wx>WC||wy>HC||wx+ww<0||wy+wh<0) return;
      ctx.fillStyle='#241a4d'; rr(ctx,wx,wy,ww,wh,4*ZOOM); ctx.fill();
      ctx.strokeStyle=COL.ro; ctx.lineWidth=2; ctx.shadowColor=COL.ro; ctx.shadowBlur=8; ctx.stroke(); ctx.shadowBlur=0; });
    // borda neon da arena
    ctx.strokeStyle=COL.ci; ctx.lineWidth=3; ctx.shadowColor=COL.ci; ctx.shadowBlur=14;
    ctx.strokeRect(x0,y0,scene.w*ZOOM,scene.h*ZOOM); ctx.shadowBlur=0;
  }

  // ── sprites (desenhados em codigo; centrados em x,y, raio r) ──
  function desenhaForma(c, type, r){                           // desenha a FORMA centrada em (0,0); caller ja transladou; base do cache
    c.shadowColor='rgba(34,211,238,.5)'; c.shadowBlur=4; c.lineWidth=1.6; c.lineJoin='round';
    if(type==='barril'){                                       // tambor de aço
      c.fillStyle=lin(c,-r,0,r,0,'#9aa2bd','#464e6a'); c.strokeStyle='#cdd5ec'; rr(c,-r*0.6,-r*0.98,r*1.2,r*1.96,r*0.16); c.fill(); c.stroke();
      c.shadowBlur=0; c.fillStyle='#bcc4db'; c.beginPath(); c.ellipse(0,-r*0.94,r*0.6,r*0.19,0,0,7); c.fill();
      c.strokeStyle='#3b4058'; c.lineWidth=r*0.12; c.beginPath(); c.moveTo(-r*0.6,-r*0.35); c.lineTo(r*0.6,-r*0.35); c.moveTo(-r*0.6,r*0.4); c.lineTo(r*0.6,r*0.4); c.stroke();
    } else if(type==='caixa'){                                 // caixote de madeira
      c.fillStyle=lin(c,-r,-r,r,r,'#c68a46','#8a5626'); c.strokeStyle='#5a3a18'; rr(c,-r*0.86,-r*0.86,r*1.72,r*1.72,r*0.08); c.fill(); c.stroke();
      c.shadowBlur=0; c.strokeStyle='#6b431d'; c.lineWidth=r*0.1; c.beginPath();
      c.moveTo(-r*0.86,-r*0.3); c.lineTo(r*0.86,-r*0.3); c.moveTo(-r*0.86,r*0.3); c.lineTo(r*0.86,r*0.3); c.moveTo(0,-r*0.86); c.lineTo(0,r*0.86); c.stroke();
      c.strokeStyle='#4a2f14'; c.lineWidth=r*0.13; c.strokeRect(-r*0.86,-r*0.86,r*1.72,r*1.72);
    } else if(type==='planta'){                                // vaso com planta
      c.shadowBlur=6; c.fillStyle='#3fb56b'; leaf(c,0,-r*0.4,r*1.05); leaf(c,-r*0.5,-r*0.05,r*0.85); leaf(c,r*0.5,-r*0.05,r*0.85);
      c.fillStyle='#57c77e'; leaf(c,-r*0.26,-r*0.28,r*0.62); leaf(c,r*0.26,-r*0.28,r*0.62); leaf(c,0,-r*0.62,r*0.6);
      c.shadowBlur=0; c.fillStyle=lin(c,0,r*0.2,0,r,'#c1673a','#8a4526'); c.strokeStyle='#6a3319'; c.lineWidth=1.6; c.beginPath();
      c.moveTo(-r*0.5,r*0.25); c.lineTo(r*0.5,r*0.25); c.lineTo(r*0.38,r); c.lineTo(-r*0.38,r); c.closePath(); c.fill(); c.stroke();
      c.fillStyle='#d4794a'; rr(c,-r*0.56,r*0.14,r*1.12,r*0.2,r*0.06); c.fill();
    } else if(type==='extintor'){                              // extintor
      c.fillStyle=lin(c,-r,0,r,0,'#e83043','#9c1020'); c.strokeStyle='#ff8895'; rr(c,-r*0.4,-r*0.6,r*0.8,r*1.6,r*0.24); c.fill(); c.stroke();
      c.shadowBlur=0; c.fillStyle='#e9e4d6'; rr(c,-r*0.3,-r*0.05,r*0.6,r*0.6,r*0.06); c.fill();
      c.fillStyle='#1c1c22'; rr(c,-r*0.16,-r*0.98,r*0.32,r*0.44,r*0.06); c.fill(); c.fillStyle='#333'; rr(c,-r*0.34,-r*0.78,r*0.5,r*0.14,r*0.05); c.fill();
    } else if(type==='cone'){                                  // cone de trânsito
      c.fillStyle=lin(c,0,-r,0,r*0.7,'#ff8420','#d8560e'); c.strokeStyle='#ffb066'; c.beginPath(); c.moveTo(0,-r); c.lineTo(r*0.66,r*0.66); c.lineTo(-r*0.66,r*0.66); c.closePath(); c.fill(); c.stroke();
      c.shadowBlur=0; c.fillStyle='#f7f3e8'; c.beginPath(); c.moveTo(-r*0.34,-r*0.06); c.lineTo(r*0.34,-r*0.06); c.lineTo(r*0.44,r*0.18); c.lineTo(-r*0.44,r*0.18); c.closePath(); c.fill();
      c.fillStyle=lin(c,0,r*0.6,0,r,'#e06414','#a8480c'); rr(c,-r*0.92,r*0.6,r*1.84,r*0.32,r*0.1); c.fill();
    } else if(type==='tv'){                                    // TV / monitor
      c.fillStyle=lin(c,0,-r,0,r,'#333a4c','#1a1e2a'); c.strokeStyle='#5a6480'; rr(c,-r*0.94,-r*0.72,r*1.88,r*1.4,r*0.12); c.fill(); c.stroke();
      c.shadowColor=COL.ci; c.shadowBlur=8; c.fillStyle=lin(c,0,-r*0.55,0,r*0.45,'#39e6ff','#0d6a80'); rr(c,-r*0.72,-r*0.52,r*1.28,r*1.0,r*0.06); c.fill();
      c.shadowBlur=0; c.strokeStyle='rgba(255,255,255,.12)'; c.lineWidth=1; for(var s=-2;s<=2;s++){ c.beginPath(); c.moveTo(-r*0.7,s*r*0.18); c.lineTo(r*0.56,s*r*0.18); c.stroke(); }
      c.fillStyle='#2a2f3e'; c.beginPath(); c.arc(r*0.78,0,r*0.14,0,7); c.fill();
      c.strokeStyle='#4a5064'; c.lineWidth=r*0.08; c.beginPath(); c.moveTo(-r*0.4,r*0.7); c.lineTo(-r*0.55,r*0.98); c.moveTo(r*0.4,r*0.7); c.lineTo(r*0.55,r*0.98); c.stroke();
    } else if(type==='pneu'){                                  // pneu
      c.fillStyle='#22222a'; c.strokeStyle='#40404e'; c.beginPath(); c.arc(0,0,r*0.94,0,7); c.fill(); c.stroke();
      c.shadowBlur=0; c.strokeStyle='#101018'; c.lineWidth=r*0.16; for(var a=0;a<6.28;a+=0.45){ c.beginPath(); c.moveTo(Math.cos(a)*r*0.6,Math.sin(a)*r*0.6); c.lineTo(Math.cos(a)*r*0.92,Math.sin(a)*r*0.92); c.stroke(); }
      c.fillStyle='#5a5f70'; c.beginPath(); c.arc(0,0,r*0.42,0,7); c.fill(); c.fillStyle='#2c2f3a'; c.beginPath(); c.arc(0,0,r*0.24,0,7); c.fill();
    } else if(type==='lixeira'){                               // lixeira
      c.fillStyle=lin(c,-r,0,r,0,'#828a98','#484e5c'); c.strokeStyle='#aab0be'; c.beginPath(); c.moveTo(-r*0.52,-r*0.5); c.lineTo(r*0.52,-r*0.5); c.lineTo(r*0.4,r*0.95); c.lineTo(-r*0.4,r*0.95); c.closePath(); c.fill(); c.stroke();
      c.shadowBlur=0; c.strokeStyle='rgba(0,0,0,.25)'; c.lineWidth=r*0.06; for(var v=-2;v<=2;v++){ c.beginPath(); c.moveTo(v*r*0.18,-r*0.4); c.lineTo(v*r*0.16,r*0.85); c.stroke(); }
      c.fillStyle='#9aa0ac'; rr(c,-r*0.62,-r*0.72,r*1.24,r*0.26,r*0.06); c.fill(); c.strokeStyle='#6a707c'; c.lineWidth=1.4; c.strokeRect(-r*0.62,-r*0.72,r*1.24,r*0.26);
      c.fillStyle='#7a808c'; rr(c,-r*0.1,-r*0.9,r*0.2,r*0.2,r*0.05); c.fill();
    } else if(type==='camaleao'){                              // camaleão parado (jogadores pintados E estátuas do beco)
      c.shadowBlur=0;
      c.strokeStyle='#6a7a6e'; c.lineWidth=r*0.2; c.lineCap='round'; c.beginPath(); c.arc(r*0.68,r*0.22,r*0.46,-1.0,2.4); c.stroke();
      c.fillStyle='#7c8c80'; c.beginPath(); c.ellipse(-r*0.38,r*0.55,r*0.17,r*0.11,0,0,7); c.ellipse(r*0.3,r*0.58,r*0.17,r*0.11,0,0,7); c.fill();
      c.fillStyle=lin(c,0,-r*0.7,0,r*0.7,'#96a89a','#5e6e62'); c.strokeStyle='#43503f';
      c.beginPath(); c.ellipse(0,0,r*0.95,r*0.68,0,0,7); c.fill(); c.stroke();
      c.fillStyle='#8a9a8e'; for(var ci=-1;ci<=1;ci++){ c.beginPath(); c.moveTo(ci*r*0.3-r*0.13,-r*0.58); c.lineTo(ci*r*0.3,-r*0.94); c.lineTo(ci*r*0.3+r*0.13,-r*0.58); c.closePath(); c.fill(); }
      c.fillStyle='#e8ece6'; c.beginPath(); c.arc(-r*0.46,-r*0.14,r*0.3,0,7); c.fill();
      c.fillStyle='#20261e'; c.beginPath(); c.arc(-r*0.46,-r*0.14,r*0.13,0,7); c.fill();
      c.strokeStyle='#20261e'; c.lineWidth=1.6; c.beginPath(); c.arc(-r*0.1,r*0.12,r*0.3,0.15,1.2); c.stroke();
    } else { c.fillStyle=COL.ro; c.beginPath(); c.arc(0,0,r*0.8,0,7); c.fill(); }
    c.shadowBlur=0;
  }
  // fade do camaleão pintado: cor casa com a ZONA sob ele = derrete no fundo (determinístico e público — seeker e hider calculam igual)
  function zonaTintEm(x,y){ var zs=scene&&scene.zones; if(!zs) return -1;
    for(var i=0;i<zs.length;i++){ var z=zs[i]; if(x>=z.x&&x<=z.x+z.w&&y>=z.y&&y<=z.y+z.h) return z.tint; } return -1; }
  function alphaCam(tint,x,y){ var zt=zonaTintEm(x,y); if(zt<0||tint==null||tint<0) return 1;
    var d=Math.abs(tint-zt); return d===0?0.3:(d===1?0.62:1); }
  // cache offscreen por (type,tint): desenha a forma UMA vez, recorta a tinta na silhueta (source-atop, preserva o volume) -> no loop vira blit
  function spriteBmp(type, tint){
    var key = type+'|'+(tint==null||tint<0?'x':tint), cv=sprCache[key];
    if(cv) return cv;
    cv=document.createElement('canvas'); cv.width=CS; cv.height=CS;
    var c=cv.getContext('2d'); c.translate(CS/2, CS/2); desenhaForma(c, type, CR);
    if(tint!=null && tint>=0 && PALETTE[tint]){
      c.globalCompositeOperation='source-atop'; c.globalAlpha=0.55;
      c.fillStyle='rgb('+PALETTE[tint][0]+','+PALETTE[tint][1]+','+PALETTE[tint][2]+')';
      c.fillRect(-CS/2,-CS/2,CS,CS); c.globalAlpha=1; c.globalCompositeOperation='source-over';
    }
    sprCache[key]=cv; return cv;
  }
  function blitSprite(type, tint, sx, sy, r, glow, alpha){
    if(alpha==null) alpha=1;
    ctx.save(); ctx.globalAlpha=0.3*alpha; ctx.fillStyle='#000'; ctx.beginPath(); ctx.ellipse(sx,sy+r*0.82,r*0.8,r*0.26,0,0,7); ctx.fill(); ctx.restore();
    var bmp=spriteBmp(type,tint), sz=(r/CR)*CS;
    ctx.save(); ctx.globalAlpha=alpha;
    if(glow){ ctx.shadowColor=COL.gr; ctx.shadowBlur=(alpha<0.7?5:14); }   // fundido = quase sem brilho (senão o glow entrega)
    ctx.drawImage(bmp, sx-sz/2, sy-sz/2, sz, sz); ctx.restore();
  }
  function lin(c,x0,y0,x1,y1,a,b){ var g=c.createLinearGradient(x0,y0,x1,y1); g.addColorStop(0,a); g.addColorStop(1,b); return g; }
  function rr(c,x,y,w,h,rad){ rad=Math.min(rad,w/2,h/2); c.beginPath(); c.moveTo(x+rad,y); c.arcTo(x+w,y,x+w,y+h,rad); c.arcTo(x+w,y+h,x,y+h,rad); c.arcTo(x,y+h,x,y,rad); c.arcTo(x,y,x+w,y,rad); c.closePath(); }
  function leaf(c,x,y,s){ c.beginPath(); c.ellipse(x,y,s*0.26,s*0.5,0,0,7); c.fill(); }

  function desenhaCamaleao(x,y,r,camo){
    var wig = camo?0:Math.sin(Date.now()/110)*2;
    ctx.save(); ctx.translate(x,y+wig);
    ctx.save(); ctx.globalAlpha=0.3; ctx.fillStyle='#000'; ctx.beginPath(); ctx.ellipse(0,r*0.85,r*0.85,r*0.3,0,0,7); ctx.fill(); ctx.restore();
    ctx.globalAlpha=camo?0.5:1; ctx.shadowColor=COL.ro2; ctx.shadowBlur=camo?4:16;
    // cauda enrolada
    ctx.strokeStyle=camo?'#7a5a8e':COL.ro2; ctx.lineWidth=r*0.22; ctx.lineCap='round'; ctx.beginPath(); ctx.arc(r*0.7,r*0.25,r*0.5,-1.0,2.4); ctx.stroke();
    // pes
    ctx.fillStyle=COL.ro; ctx.beginPath(); ctx.ellipse(-r*0.4,r*0.55,r*0.18,r*0.12,0,0,7); ctx.ellipse(r*0.3,r*0.6,r*0.18,r*0.12,0,0,7); ctx.fill();
    // corpo
    var g=ctx.createRadialGradient(-r*0.2,-r*0.2,r*0.2,0,0,r); g.addColorStop(0, camo?'#8a6a9e':'#ff9ecf'); g.addColorStop(1, camo?'#5a3a6e':COL.ro2);
    ctx.fillStyle=g; ctx.beginPath(); ctx.ellipse(0,0,r*0.98,r*0.72,0,0,7); ctx.fill();
    // crista
    ctx.fillStyle=COL.ro; for(var i=-1;i<=1;i++){ ctx.beginPath(); ctx.moveTo(i*r*0.3-r*0.14,-r*0.62); ctx.lineTo(i*r*0.3,-r*1.02); ctx.lineTo(i*r*0.3+r*0.14,-r*0.62); ctx.closePath(); ctx.fill(); }
    // olho giratorio
    ctx.shadowBlur=0; ctx.fillStyle='#fff'; ctx.beginPath(); ctx.arc(-r*0.48,-r*0.16,r*0.34,0,7); ctx.fill();
    var dx=clamp((me.tx-me.x)/80,-1,1)*r*0.14, dy=clamp((me.ty-me.y)/80,-1,1)*r*0.14;
    ctx.fillStyle='#07060f'; ctx.beginPath(); ctx.arc(-r*0.48+dx,-r*0.16+dy,r*0.15,0,7); ctx.fill();
    ctx.fillStyle='#fff'; ctx.globalAlpha=0.8; ctx.beginPath(); ctx.arc(-r*0.55+dx,-r*0.22+dy,r*0.05,0,7); ctx.fill(); ctx.globalAlpha=1;
    // sorriso
    ctx.strokeStyle='#07060f'; ctx.lineWidth=2; ctx.beginPath(); ctx.arc(-r*0.12,r*0.12,r*0.32,0.1,1.25); ctx.stroke();
    ctx.restore(); ctx.globalAlpha=1; ctx.shadowBlur=0;
  }
  function desenhaCacador(x,y,r,mine){
    ctx.save(); ctx.translate(x,y);
    if(mine){ var lg=ctx.createRadialGradient(0,0,r,0,0,r*5); lg.addColorStop(0,'rgba(34,211,238,.16)'); lg.addColorStop(1,'rgba(34,211,238,0)'); ctx.fillStyle=lg; ctx.beginPath(); ctx.arc(0,0,r*5,0,7); ctx.fill(); }
    ctx.save(); ctx.globalAlpha=0.3; ctx.fillStyle='#000'; ctx.beginPath(); ctx.ellipse(0,r*0.8,r*0.8,r*0.28,0,0,7); ctx.fill(); ctx.restore();
    ctx.shadowColor=COL.ci; ctx.shadowBlur=mine?16:8;
    var g=ctx.createRadialGradient(-r*0.2,-r*0.3,r*0.2,0,0,r); g.addColorStop(0, mine?'#7ff0ff':'#4aa8c0'); g.addColorStop(1, mine?'#1287a0':'#1c5666');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(0,0,r*0.82,0,7); ctx.fill();
    // visor/lanterna
    ctx.shadowBlur=6; ctx.fillStyle=COL.ink; rr(ctx,-r*0.4,-r*0.62,r*0.8,r*0.3,r*0.12); ctx.fill();
    ctx.shadowBlur=0; ctx.fillStyle='#07060f'; ctx.beginPath(); ctx.arc(-r*0.26,-r*0.05,r*0.13,0,7); ctx.arc(r*0.26,-r*0.05,r*0.13,0,7); ctx.fill();
    ctx.restore(); ctx.shadowBlur=0;
  }

  // ── minimapa (canto superior direito) ──
  function minimapa(){
    if(!scene) return;
    var mw=88, mh=mw*(scene.h/scene.w); if(mh>150){ mh=150; mw=mh*(scene.w/scene.h); }
    var px=WC-mw-12, py=12;
    ctx.save(); ctx.globalAlpha=0.85;
    ctx.fillStyle='rgba(7,6,15,.7)'; rr(ctx,px-4,py-4,mw+8,mh+8,8); ctx.fill();
    ctx.strokeStyle=COL.ro; ctx.lineWidth=1.5; ctx.stroke();
    var sxr=mw/scene.w, syr=mh/scene.h;
    (scene.zones||[]).forEach(function(z){ var c=PALETTE[z.tint]; if(!c) return;
      ctx.fillStyle='rgba('+c[0]+','+c[1]+','+c[2]+',.55)'; ctx.fillRect(px+z.x*sxr,py+z.y*syr,Math.max(1,z.w*sxr),Math.max(1,z.h*syr)); });
    (scene.walls||[]).forEach(function(w){ ctx.fillStyle='rgba(168,85,247,.4)'; ctx.fillRect(px+w.x*sxr,py+w.y*syr,Math.max(1,w.w*sxr),Math.max(1,w.h*syr)); });
    // caçadores (perigo p/ o hider; posicao dos colegas p/ o seeker)
    for(var sid in render.seekers){ var s=render.seekers[sid]; ctx.fillStyle=COL.ci; ctx.beginPath(); ctx.arc(px+s.x*sxr,py+s.y*syr,2.5,0,7); ctx.fill(); }
    // eu
    ctx.fillStyle = me.role==='seeker'?COL.ci:COL.ro2; ctx.shadowColor=ctx.fillStyle; ctx.shadowBlur=6;
    ctx.beginPath(); ctx.arc(px+me.x*sxr,py+me.y*syr,3.5,0,7); ctx.fill(); ctx.shadowBlur=0;
    ctx.restore();
  }

  document.body.addEventListener('touchstart', function(){ if(actx&&actx.state==='suspended') actx.resume(); }, {passive:true, once:true});
})();
