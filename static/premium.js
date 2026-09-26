/* Raposa Caçadora — Premium UI behavior */
(function(){
  function updateDashboardBlocks(){
    document.querySelectorAll(".dash-block").forEach(function(card){
      const value=card.querySelector("b");
      if(value && (value.textContent==="—" || value.textContent==="")) card.classList.add("loading");
      else card.classList.remove("loading");
    });
  }
  const observer=new MutationObserver(updateDashboardBlocks);
  observer.observe(document.body,{subtree:true,childList:true,characterData:true});
  updateDashboardBlocks();
})();
