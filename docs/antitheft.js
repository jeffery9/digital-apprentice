
(function(){
  // 水印追踪：在页面中嵌入用户标识
  function addWatermark() {
    var canvas = document.createElement('canvas');
    canvas.width = 400; canvas.height = 300;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = 'rgba(0,0,0,0.03)';
    ctx.font = '20px serif';
    ctx.rotate(-30 * Math.PI / 180);
    // 水印内容：时间戳 + 随机ID（可追溯）
    var ts = new Date().toISOString().slice(0,16).replace(/[T:-]/g,'');
    var rid = Math.random().toString(36).slice(2,8).toUpperCase();
    var text = '数字见习生·' + ts + '·' + rid;
    ctx.fillText(text, 50, 150);
    var div = document.createElement('div');
    div.className = 'watermark';
    div.style.backgroundImage = 'url(' + canvas.toDataURL() + ')';
    div.style.backgroundSize = '400px 300px';
    document.body.appendChild(div);
  }
  // 禁止复制和右键
  function disableCopy() {
    document.addEventListener('contextmenu', function(e) { e.preventDefault(); });
    document.addEventListener('selectstart', function(e) { e.preventDefault(); });
    document.addEventListener('copy', function(e) { e.preventDefault(); });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { addWatermark(); disableCopy(); });
  } else {
    addWatermark(); disableCopy();
  }
})();
