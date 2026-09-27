/* Raposa Caçadora — Premium operational UI */
(function(){
  function panel(id,title,body){return '<div class="panel form premium-panel" id="'+id+'"><div class="section-head"><h3>'+title+'</h3></div>'+body+'</div>'}
  function grid(items){return '<div class="premium-grid">'+items.map(function(x){return '<div><b>'+x[0]+'</b><span>'+x[1]+'</span></div>'}).join('')+'</div>'}
  function addPremiumPanels(){
    const config=document.getElementById('config');
    if(config&&!document.getElementById('premiumConfigExtra')){
      const box=document.createElement('div');box.id='premiumConfigExtra';box.className='premium-extra';
      box.innerHTML=panel('cfgIntegracoes','🤖 Integrações',grid([['Manus','Criação e processamento dos carrosséis'],['Telegram','Aprovação, comandos e notificações'],['Supabase','Fila, estados e registros'],['Storage','Imagens e arquivos'],['Instagram','Publicação dos conteúdos']]))+
      panel('cfgAutomacao','⚡ Automação',grid([['Ativar / pausar','Controle geral da automação'],['Intervalo','Tempo entre produtos'],['Produtos por ciclo','Quantidade processada em cada ciclo'],['Limite diário','Quantidade máxima por dia'],['Horário de operação','Defina quando o worker pode trabalhar']]))+
      panel('cfgProdutos','📦 Critérios de produtos',grid([['Avaliação mínima','Filtro por avaliação'],['Vendas mínimas','Filtro por volume de vendas'],['Desconto mínimo','Filtro por desconto'],['Preço mínimo / máximo','Controle da faixa de preço'],['Categorias permitidas','Categorias que podem entrar'],['Categorias bloqueadas','Categorias que devem ser ignoradas']]))+
      panel('cfgConteudo','✍️ Conteúdo',grid([['Quantidade de slides','Defina o tamanho padrão do carrossel'],['Modelo de legenda','Estrutura usada pelo Manus'],['CTA','Chamada para ação'],['Hashtags','Padrão de hashtags']]))+
      panel('cfgNotificacoes','🔔 Notificações',grid([['Carrossel pronto','Avisar quando estiver pronto para revisão'],['Erro','Avisar quando houver falha'],['Publicação','Confirmar publicação concluída'],['Telegram','Enviar somente eventos importantes']]))+
      config.appendChild(box);
    }
    const stats=document.getElementById('estatisticas');
    if(stats&&!document.getElementById('premiumStatsExtra')){
      const box=document.createElement('div');box.id='premiumStatsExtra';box.className='premium-extra';
      box.innerHTML=panel('statsPerformance','📈 Performance',grid([['Taxa de aprovação','Percentual de carrosséis aprovados'],['Taxa de reprovação','Percentual de carrosséis rejeitados'],['Taxa de publicação','Percentual que chegou à publicação'],['Taxa de erro','Percentual que exigiu intervenção'],['Tempo médio','Tempo entre entrada e conclusão'],['Produção diária','Volume produzido por dia']]))+
      panel('statsManus','🤖 Manus',grid([['Tarefas concluídas','Total de tarefas finalizadas'],['Tarefas com erro','Tarefas que falharam'],['Tempo médio da tarefa','Duração média do processamento'],['Última tarefa','Identificação da tarefa mais recente']]))+
      stats.appendChild(box);
    }
    addHealth();addNotificationBell();
  }
  function addHealth(){
    if(document.getElementById('premiumHealth'))return;
    const dash=document.getElementById('dashboard');if(!dash)return;
    const box=document.createElement('div');box.id='premiumHealth';box.className='premium-extra';
    box.innerHTML=panel('healthCard','🩺 Saúde do sistema','<div class="health-list"><div><i>●</i> Telegram <b>Monitorado</b></div><div><i>●</i> Supabase <b>Monitorado</b></div><div><i>●</i> Manus <b>Monitorado</b></div><div><i>●</i> Worker <b>Monitorado</b></div><div><i>●</i> Storage <b>Monitorado</b></div><div><i>●</i> Instagram <b>Monitorado</b></div></div>');dash.appendChild(box);
  }
  function addNotificationBell(){
    if(document.getElementById('premiumBell'))return;
    const top=document.querySelector('.topbar')||document.querySelector('header');if(!top)return;
    const b=document.createElement('button');b.id='premiumBell';b.className='premium-bell';b.title='Notificações';b.textContent='🔔';top.appendChild(b);
  }
  const style=document.createElement('style');style.textContent='.premium-extra{display:grid;gap:14px;margin-top:14px}.premium-panel{padding:18px}.premium-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.premium-grid>div{padding:14px;border:1px solid rgba(255,255,255,.07);border-radius:14px;background:rgba(255,255,255,.025)}.premium-grid b{display:block;font-size:13px;margin-bottom:5px}.premium-grid span{display:block;color:#858c99;font-size:11px;line-height:1.5}.health-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.health-list div{padding:12px;border-radius:12px;background:rgba(255,255,255,.025);font-size:11px}.health-list i{color:#6de2a0;font-style:normal;margin-right:5px}.health-list b{float:right;color:#858c99;font-weight:600}.premium-bell{margin-left:auto;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);border-radius:12px;padding:9px 12px;cursor:pointer}@media(max-width:650px){.premium-grid,.health-list{grid-template-columns:1fr}.premium-panel{padding:14px}}';document.head.appendChild(style);
  const observer=new MutationObserver(function(){addPremiumPanels()});observer.observe(document.body,{subtree:true,childList:true});addPremiumPanels();
})();
