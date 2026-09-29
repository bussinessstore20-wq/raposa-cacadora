/* Raposa Caçadora — integração operacional do painel */
(function(){
  'use strict';
  const esc=v=>String(v==null?'':v).replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#039;'}[m]));
  const tg=window.Telegram?.WebApp;
  const initData=tg?.initData||'';
  async function request(path,options={}){
    const headers=Object.assign({'Accept':'application/json'},options.headers||{});
    if(initData) headers['X-Telegram-Init-Data']=initData;
    const r=await fetch(path,Object.assign({},options,{headers}));
    const text=await r.text(); let data={};
    try{data=text?JSON.parse(text):{}}catch{data={error:text||('HTTP '+r.status)}}
    if(!r.ok) throw new Error(data.error||data.message||('HTTP '+r.status));
    return data;
  }
  function panel(id,title,body){return '<div class="panel form premium-panel" id="'+id+'"><div class="section-head"><h3>'+title+'</h3></div>'+body+'</div>'}
  function grid(items){return '<div class="premium-grid">'+items.map(x=>'<div><b>'+esc(x[0])+'</b><span>'+esc(x[1])+'</span></div>').join('')+'</div>'}
  function notify(text,type='ok'){
    let n=document.getElementById('premiumNotice');
    if(!n){n=document.createElement('div');n.id='premiumNotice';n.className='premium-notice';document.body.appendChild(n)}
    n.className='premium-notice '+type;n.innerHTML=text;n.style.display='block';clearTimeout(notify.t);notify.t=setTimeout(()=>n.style.display='none',5000);
  }
  function addPanels(){
    const config=document.getElementById('config');
    if(config&&!document.getElementById('premiumConfigExtra')){
      const box=document.createElement('div');box.id='premiumConfigExtra';box.className='premium-extra';
      box.innerHTML=panel('cfgIntegracoes','🤖 Integrações','<div id="integrationGrid" class="health-list"><div>Carregando...</div></div>')+
      panel('cfgAutomacao','⚡ Automação','<div class="settings-form"><label>Intervalo entre produtos (minutos)<input id="cfgIntervalMinutes" type="number" min="1" step="1" value="2"></label><label>Limite diário<input id="cfgDailyLimit" type="number" min="0" step="1" value="0"></label><div class="settings-actions"><button id="cfgSave" class="btn primary">💾 Aplicar</button><button id="cfgPause" class="btn">⏸️ Pausar</button><button id="cfgStart" class="btn">▶️ Iniciar</button><button id="cfgStop" class="btn danger">⏹️ Parar</button></div><small id="cfgStatus" class="setting-status">Sincronizando...</small></div>')+
      panel('cfgProdutos','📦 Critérios de produtos',grid([['Avaliação mínima','Configuração disponível no backend quando habilitada'],['Vendas mínimas','Configuração disponível no backend quando habilitada'],['Desconto mínimo','Configuração disponível no backend quando habilitada'],['Preço mínimo / máximo','Configuração disponível no backend quando habilitada']]))+
      panel('cfgConteudo','✍️ Conteúdo','<div class="setting-status">O prompt editorial do Manus permanece no Supabase e não é alterado pelo painel.</div>')+
      panel('cfgNotificacoes','🔔 Notificações','<div id="notificationPanel" class="setting-status">Carregando eventos...</div>');
      config.appendChild(box);wireSettings();
    }
    const stats=document.getElementById('estatisticas');
    if(stats&&!document.getElementById('premiumStatsExtra')){
      const box=document.createElement('div');box.id='premiumStatsExtra';box.className='premium-extra';
      box.innerHTML=panel('statsPerformance','📈 Performance','<div id="liveStats" class="premium-grid"><div>Sincronizando dados reais...</div></div>');stats.appendChild(box);
    }
    addHealth();addNotificationBell();
  }
  async function loadHealth(){
    const target=document.getElementById('integrationGrid');if(!target)return;
    try{
      const d=await request('/api/health');
      const items=d.services||d.integrations||d.checks||d;
      const names=['Telegram','Supabase','Manus','Worker','Storage','Instagram'];
      target.innerHTML=names.map(name=>{
        const key=name.toLowerCase();const raw=items?.[name]??items?.[key];
        let ok=raw===true||raw==='ok'||raw==='healthy'||raw?.ok===true||raw?.status==='ok'||raw?.status==='healthy';
        return '<div class="health-live '+(ok?'live-ok':'live-err')+'"><i>●</i> '+name+' <b>'+ (raw==null?'Sem resposta':(ok?'OK':'ERRO'))+'</b></div>';
      }).join('');
    }catch(e){target.innerHTML='<div class="live-err"><i>●</i> Saúde do sistema <b>ERRO</b><small>'+esc(e.message)+'</small></div>'}
  }
  async function loadSettings(){
    try{
      const d=await request('/api/settings');
      const s=d.settings||d.config||d||{};
      const interval=s.interval_minutes??s.intervalo_minutos??s.interval??s.intervalo;
      if(interval!=null&&document.getElementById('cfgIntervalMinutes'))document.getElementById('cfgIntervalMinutes').value=Number(interval);
      const daily=s.daily_limit??s.limite_diario;
      if(daily!=null&&document.getElementById('cfgDailyLimit'))document.getElementById('cfgDailyLimit').value=daily;
      const active=s.active??s.ativo??s.automation_active??s.automacao_ativa;
      if(document.getElementById('cfgStatus'))document.getElementById('cfgStatus').textContent='Sincronizado • Automação '+(active===false?'pausada':'ativa');
    }catch(e){if(document.getElementById('cfgStatus'))document.getElementById('cfgStatus').textContent='Não foi possível sincronizar configurações: '+e.message}
  }
  async function saveSettings(){
    const minutes=Number(document.getElementById('cfgIntervalMinutes')?.value||0);
    const daily=Number(document.getElementById('cfgDailyLimit')?.value||0);
    if(!Number.isFinite(minutes)||minutes<1||minutes>1440){notify('❌ Intervalo deve ficar entre 1 e 1440 minutos.','err');return}
    try{
      await request('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({intervalo_minutos:minutes,interval_minutes:minutes,daily_limit:daily,initData})});
      notify('✅ Configurações aplicadas no backend.','ok');loadSettings();
    }catch(e){notify('❌ Não foi possível salvar: '+esc(e.message),'err')}
  }
  async function control(action){
    const routes={start:'/api/automacao/start',resume:'/api/automacao/resume',pause:'/api/automacao/pause',stop:'/api/automacao/stop'};
    const path=routes[action]||'/api/control';
    try{
      await request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,initData,ativo:action==='start'||action==='resume'})});
      const label={start:'iniciada',resume:'retomada',pause:'pausada',stop:'parada'}[action]||action;
      notify('✅ Automação '+label+'.','ok');
      loadSettings();
      if(typeof carregar==='function')carregar();
    }catch(e){notify('❌ Controle não aplicado: '+esc(e.message),'err')}
  }
  function wireSettings(){
    document.getElementById('cfgSave')?.addEventListener('click',saveSettings);
    document.getElementById('cfgPause')?.addEventListener('click',()=>control('pause'));
    document.getElementById('cfgStart')?.addEventListener('click',()=>control('start'));
    document.getElementById('cfgStop')?.addEventListener('click',()=>control('stop'));
    loadSettings();
  }
  function addHealth(){
    const dash=document.getElementById('dashboard');if(!dash||document.getElementById('premiumHealth'))return;
    const box=document.createElement('div');box.id='premiumHealth';box.className='premium-extra';
    box.innerHTML=panel('healthCard','🩺 Saúde do sistema','<div id="dashboardHealth" class="health-list"><div>Verificando integrações...</div></div>');dash.appendChild(box);
  }
  async function refreshDashboardHealth(){
    const t=document.getElementById('dashboardHealth');if(!t)return;
    try{const d=await request('/api/health');const services=d.services||d.integrations||d.checks||d;const names=['Telegram','Supabase','Manus','Worker','Storage','Instagram'];t.innerHTML=names.map(n=>{const r=services?.[n]??services?.[n.toLowerCase()];const ok=r===true||r==='ok'||r?.ok===true||r?.status==='ok'||r?.status==='healthy';return '<div class="health-live '+(ok?'live-ok':'live-err')+'"><i>●</i> '+n+' <b>'+(r==null?'Sem resposta':ok?'OK':'ERRO')+'</b></div>'}).join('')}catch(e){t.innerHTML='<div class="live-err">Falha na verificação: '+esc(e.message)+'</div>'}
  }
  function addNotificationBell(){
    if(document.getElementById('premiumBell'))return;
    const top=document.querySelector('.top')||document.querySelector('header');if(!top)return;
    const b=document.createElement('button');b.id='premiumBell';b.className='premium-bell';b.title='Atualizar status';b.textContent='🔔';b.onclick=()=>{refreshDashboardHealth();loadHealth();loadSettings();notify('🔄 Status atualizado.','ok')};top.appendChild(b);
  }
  function addStyles(){
    const s=document.createElement('style');s.textContent='.premium-extra{display:grid;gap:14px;margin-top:14px}.premium-panel{padding:18px}.premium-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.premium-grid>div{padding:14px;border:1px solid rgba(255,255,255,.07);border-radius:14px;background:rgba(255,255,255,.025)}.premium-grid b{display:block;font-size:13px;margin-bottom:5px}.premium-grid span{display:block;color:#858c99;font-size:11px;line-height:1.5}.health-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.health-list div{padding:12px;border-radius:12px;background:rgba(255,255,255,.025);font-size:11px}.health-list i{color:#6de2a0;font-style:normal;margin-right:5px}.health-list b{float:right;font-weight:700}.live-ok b{color:#6de2a0}.live-err b{color:#ff7070}.live-err i{color:#ff7070}.settings-form label{display:grid;gap:6px;color:#b8bec9;font-size:10px;font-weight:800;margin-bottom:10px}.settings-form input{width:100%;padding:11px;border-radius:10px;border:1px solid rgba(255,255,255,.1);background:#0b0d11;color:#fff}.settings-actions{display:flex;gap:8px;flex-wrap:wrap}.setting-status{display:block;color:#858c99;font-size:10px;line-height:1.5;margin-top:8px}.premium-bell{margin-left:8px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);color:#fff;border-radius:12px;padding:9px 12px;cursor:pointer}.premium-notice{position:fixed;right:16px;bottom:16px;z-index:100;padding:12px 15px;border-radius:12px;background:#10251b;color:#69dda0;border:1px solid rgba(105,221,160,.2);font-size:11px;max-width:380px;box-shadow:0 15px 50px rgba(0,0,0,.4)}.premium-notice.err{background:#2b1214;color:#ff8383}.premium-notice.warn{background:#2b220f;color:#ffd477}@media(max-width:650px){.premium-grid,.health-list{grid-template-columns:1fr}.premium-panel{padding:14px}}';document.head.appendChild(s)
  }
  const observer=new MutationObserver(()=>addPanels());observer.observe(document.body,{subtree:true,childList:true});
  addStyles();addPanels();
  setTimeout(()=>{refreshDashboardHealth();loadHealth();loadSettings()},1200);
  setInterval(()=>{if(document.visibilityState==='visible'){refreshDashboardHealth();loadHealth();loadSettings()}},30000);
})();