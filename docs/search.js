
(function(){
  var q = document.getElementById('q');
  if(!q) return;
  var base = window.location.pathname.replace(/[^/]*$/, '');
  function esc(s){ return s.replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function run(kw){
    var idx = window.WEBBOOK_INDEX;
    var box = document.getElementById('results');
    if(!idx || !box) return;
    if(!kw){ box.innerHTML=''; return; }
    var hits=[], total=0;
    var re; try{ re = new RegExp(kw.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi'); }catch(e){ return; }
    for(var i=0;i<idx.length && hits.length<30;i++){
      var p=idx[i], m, positions=[];
      re.lastIndex=0;
      while((m=re.exec(p.text))!==null){ positions.push(m.index); if(positions.length>200) break; }
      if(!positions.length) continue;
      total += positions.length;
      var snips=[];
      for(var k=0;k<Math.min(positions.length,3);k++){
        var s=Math.max(0,positions[k]-40), e=Math.min(p.text.length,positions[k]+kw.length+60);
        snips.push('…'+esc(p.text.slice(s,e)).replace(re,function(x){return '<mark>'+x+'</mark>';})+'…');
      }
      hits.push('<div class="hit"><a href="'+base+p.file+'#'+p.anchor+'">'+esc(p.title)+'</a><div>'+snips.join('<br>')+'</div></div>');
    }
    box.innerHTML = hits.length ? ('<p>共 '+total+' 处提及，显示前 '+hits.length+' 个条目：</p>'+hits.join('<hr>'))
                               : ('<p>未找到「'+esc(kw)+'」。</p>');
  }
  function ensureIndex(cb){
    if(window.WEBBOOK_INDEX){ cb(); return; }
    var s=document.createElement('script'); s.src=base+'searchindex.js';
    s.onload=cb; document.head.appendChild(s);
  }
  var timer=null;
  q.addEventListener('input', function(){
    clearTimeout(timer);
    var kw=q.value.trim();
    timer=setTimeout(function(){ ensureIndex(function(){ run(kw); }); }, 250);
  });
  var pm = /[?&]q=([^&]+)/.exec(window.location.search);
  if(pm){ q.value = decodeURIComponent(pm[1].replace(/\+/g,' '));
    ensureIndex(function(){ run(q.value.trim()); }); }
})();
