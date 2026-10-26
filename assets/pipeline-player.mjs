// A bounded, user-controlled illustrative animation. No manufacturing computation.
async function start(){
  const {STEPS,DURATION,frameAt,renderPipeline}=await import('./pipeline-scene.mjs');
  const $=id=>document.getElementById(id);
  const scene=$('scene'),scroll=$('scene-scroll'),play=$('play'),slider=$('timeline');
  const buttons=[...document.querySelectorAll('.steps button')];
  const gaps=[...document.querySelectorAll('.gap')];
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  let time=0,playing=false,last=0,lastPaint=0,request=0,previousStage=-1;
  const actions=['几何特征 ↔ 制造要求','选工序 / 装夹 / 刀具','算刀路 → 后处理','按 NC 改变材料','对照原目标与公差'];
  function draw(force=false){
    const {stage,progress}=frameAt(time),failed=$('failed').checked;
    scene.innerHTML=renderPipeline({stage,progress,failed});
    slider.value=String(Math.round(time));
    if(stage!==previousStage||force){
      buttons.forEach((b,i)=>i===stage?b.setAttribute('aria-current','step'):b.removeAttribute('aria-current'));
      gaps.forEach((g,i)=>g.classList.toggle('selected',i===stage));
      $('input').textContent=STEPS[stage].input;
      $('owner').textContent=STEPS[stage].owner;
      $('action').textContent=actions[stage];
      $('output').textContent=failed&&stage===4?'偏差证据 → Agent':STEPS[stage].output;
      $('counter').value=`0${stage+1} / 05`;
      const width=scene.getBoundingClientRect().width;
      const center=(155+250*stage)/1320*width;
      scroll.scrollTo({left:Math.max(0,center-scroll.clientWidth/2),behavior:'instant'});
      previousStage=stage;
    }
  }
  function stop(){
    playing=false;cancelAnimationFrame(request);request=0;
    play.textContent=time>=DURATION?'↺ 再看一遍':'▶ 播放流程';
    play.setAttribute('aria-pressed','false');
  }
  function tick(now){
    if(!playing)return;
    time=Math.min(DURATION,time+Math.max(0,Math.min(now-last,200)));
    last=now;
    if(now-lastPaint>=80||time===DURATION){draw();lastPaint=now;}
    if(time===DURATION)stop();else request=requestAnimationFrame(tick);
  }
  function resume(){
    if(time>=DURATION)time=0;
    playing=true;play.textContent='Ⅱ 暂停';play.setAttribute('aria-pressed','true');
    last=performance.now();lastPaint=0;draw(true);request=requestAnimationFrame(tick);
  }
  function seek(value){stop();time=Math.max(0,Math.min(DURATION,value));draw(true);stop();}
  function select(index){if(Number.isInteger(index)&&index>=0&&index<5)seek(index*5000+2500);}
  play.addEventListener('click',()=>playing?stop():resume());
  $('restart').addEventListener('click',()=>seek(0));
  slider.addEventListener('input',()=>seek(Number(slider.value)));
  buttons.forEach((b,i)=>{
    b.disabled=false;b.addEventListener('click',()=>select(i));
    b.addEventListener('keydown',e=>{
      let next;if(e.key==='ArrowRight')next=(i+1)%5;else if(e.key==='ArrowLeft')next=(i+4)%5;
      if(next!==undefined){e.preventDefault();buttons[next].focus();select(next);}
    });
  });
  scene.addEventListener('click',e=>{const station=e.target.closest('[data-step]');if(station)select(Number(station.dataset.step));});
  $('failed').addEventListener('change',()=>seek(22500));
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  if('IntersectionObserver' in window)new IntersectionObserver(entries=>{if(!entries[0].isIntersecting)stop();},{threshold:.05}).observe(document.querySelector('.player'));
  reduced.addEventListener('change',()=>stop());
  // Start paused, including for reduced-motion users. An explicit Play runs once.
  play.disabled=$('restart').disabled=slider.disabled=$('failed').disabled=false;
  draw(true);stop();
  window.genlawPipeline=Object.freeze({get state(){return {time,playing,...frameAt(time),failed:$('failed').checked,illustrative:true};},seek});
}
start().catch(error=>{
  console.error('Pipeline illustration could not start',error);
  document.getElementById('load-error').hidden=false;
});
