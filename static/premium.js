/* Raposa Caçadora — Premium UI behavior */
(function(){
  function updateDashboardBlocks(){
    document.querySelectorAll('.dash-block').forEach(function(card){
      const value=card.querySelector('b');
      if(value && (value.textContent==='—' || value.textContent==='')) card.classList.add('loading');
      else card.classList.remove('loading');
    });
  }

  function addPremiumPanels(){
    const config=document.getElementById('config');
    if(config && !document.getElementById('premiumConfigExtra')){
      const box=document.createElement('div'); box.id='premiumConfigExtra'; box.className='premium-extra';
      box.innerHTML=`<div class="panel form premium-panel"><div class="section-head"><h3>🤖 Integrações</h3><span class="status-dot">● Conectividade</span></div><div class="premium-grid"><div><b>Manus</b><span>Automação e criação dos carrosséis</span></div><div><b>Telegram</b><span>Aprovação e notificações</span></div><div><b>Supabase</b><span>Fila, estados e registros</span></div><div><b>Storage</b><span>Imagens e arquivos</span></div><div><b>Instagram</b><span>Publicação dos conteúdos</span></div></div></div><div class="panel form premium-panel"><div class="section-head"><h3>⚙️ Regras de conteúdo</h3></div><div class="premium-grid"><div><b>Avaliação mínima</b><span>Defina o mínimo aceito para produtos</span></div><div><b>Vendas mínimas</b><span>Filtre produtos por volume de vendas</span></div><div><b>Desconto mínimo</b><span>Controle o percentual mínimo de desconto</span></div><div><b>Limite diário</b><span>Controle quantos produtos entram na automação</span></div></div></div><div class="panel form premium-panel"><div class="section-head"><h3>🔔 Notificações</h3></div><div class="premium-grid"><div><b>Carrossel pronto</b><span>Avisar quando estiver disponível para revisão</span></div><div><b>Erro de processamento</b><span>Avisar somente quando houver falha</span></div><div><b>Publicação concluída</b><span>Confirmar publicação no Telegram</span></div></div></div>`;
      config.appendChild(box);
    }

    const stats=document.getElementById('estatisticas');
    if(stats && !document.getElementById('premiumStatsExtra')){
      const box=document.createElement('div'); box.id='premiumStatsExtra'; box.className='premium-extra';
      box.innerHTML=`<div class="panel form premium-panel"><div class="section-head"><h3>📈 Indicadores operacionais</h3><span>Atualização automática</span></div><div class="premium-grid"><div><b>Taxa de aprovação</b><span>Relação entre revisão e aprovação</span></div><div><b>Taxa de publicação</b><span>Carrosséis que chegaram à publicação</span></div><div><b>Taxa de erro</b><span>Itens que exigiram intervenção</span></div><div><b>Tempo médio</b><span>Tempo entre entrada e conclusão</span></div></div></div><div class="panel form premium-panel"><div class="section-head"><h3>🧭 Saúde operacional</h3><span>Visão rápida</span></div><div class="health-list"><div><i>●</i> Telegram <b>Monitorado</b></div><div><i>●</i> Supabase <b>Monitorado</b></div><div><i>●</i> Manus <b>Monitorado</b></div><div><i>●</i> Worker <b>Monitorado</b></div><div><i>●</i> Storage <b>Monitorado</b></div><div><i>●</i> Instagram <b>Monitorado</b></div></div></div>`;
      stats.appendChild(box);
    }
  }

  const style=document.createElement('style');
  style.textContent=`.premium-extra{display:grid;gap:14px;margin-top:14px}.premium-panel{padding:18px}.premium-panel .section-head{margin-top:0}.premium-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.premium-grid>div{padding:14px;border:1px solid rgba(255,255,255,.06);border-radius:14px;background:rgba(255,255,255,.02)}.premium-grid b{display:block;font-size:13px;margin-bottom:5px}.premium-grid span{display:block;color:#858c99;font-size:11px;line-height:1.5}.status-dot{color:#6de2a0;font-size:10px}.health-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.health-list div{padding:12px;border-radius:12px;background:rgba(255,255,255,.025);font-size:11px}.health-list i{color:#6de2a0;font-style:normal;margin-right:5px}.health-list b{float:right;color:#858c99;font-weight:600}@media(max-width:650px){.premium-grid,.health-list{grid-template-columns:1fr}.premium-panel{padding:14px}}`;
  document.head.appendChild(style);

  const observer=new MutationObserver(function(){updateDashboardBlocks();addPremiumPanels();});
  observer.observe(document.body,{subtree:true,childList:true,characterData:true});
  updateDashboardBlocks();
  addPremiumPanels();
})();
