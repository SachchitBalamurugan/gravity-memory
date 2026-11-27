/* A deterministic orbital view. Each labeled satellite opens a real memory. */
window.drawMemoryField = function(canvas, labels, memories, inspect) {
  const points = [[.22,.36],[.76,.29],[.81,.69],[.27,.79]];
  function paint(){
    const {width:w,height:h}=canvas.getBoundingClientRect();
    if(!w || !h) return;
    const ratio=Math.min(devicePixelRatio||1,2);
    canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);
    const c=canvas.getContext('2d'); c.scale(ratio,ratio);
    const cx=w*.50,cy=h*.54,r=Math.min(w*.108,h*.205);
    let seed=407216;const rand=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/4294967296};
    c.fillStyle='#0a090d';c.fillRect(0,0,w,h);
    const halo=c.createRadialGradient(cx,cy,r*.35,cx,cy,r*3.5);
    halo.addColorStop(0,'#62401f');halo.addColorStop(.41,'#352519');halo.addColorStop(.77,'#151011');halo.addColorStop(1,'#0a090d');
    c.fillStyle=halo;c.fillRect(0,0,w,h);
    for(let i=0;i<w*h/140;i++){
      const x=rand()*w,y=rand()*h,a=rand()**3*.56+.04;
      c.fillStyle='rgba(239,216,186,'+a+')';c.fillRect(x,y,rand()>.97?1.5:.8,.8);
    }
    c.save();c.translate(cx,cy);c.rotate(-.16);
    for(let i=0;i<5;i++){
      c.beginPath();c.setLineDash(i%2?[2,5]:[]);c.strokeStyle=i===2?'#8a674840':'#cfb38621';
      c.ellipse(0,0,r*(1.9+i*.58),r*(.67+i*.33),0,0,Math.PI*2);c.stroke();
    }
    c.setLineDash([]);
    // Backlit accretion disk, individual arcs with a seeded density gradient.
    for(let i=0;i<500;i++){
      const radius=r*(1+Math.pow(rand(),1.6)*2.1), theta=rand()*Math.PI*2;
      c.beginPath();c.strokeStyle='rgba(239,'+Math.round(122+rand()*74)+',73,'+(.025+rand()*.10)+')';
      c.lineWidth=.45+rand()*1.2;c.ellipse(0,0,radius,radius*.18,0,theta,theta+rand()*.85);c.stroke();
    }
    let glow=c.createRadialGradient(0,0,r*.7,0,0,r*1.4);
    glow.addColorStop(0,'rgba(242,171,86,0)');glow.addColorStop(.42,'rgba(251,179,89,.7)');
    glow.addColorStop(.52,'rgba(244,154,62,.45)');glow.addColorStop(1,'rgba(241,150,67,0)');
    c.fillStyle=glow;c.fillRect(-2*r,-2*r,4*r,4*r);
    c.fillStyle='#08070a';c.beginPath();c.arc(0,0,r*.93,0,2*Math.PI);c.fill();
    for(let i=0;i<145;i++){
      const rad=r*(.94+rand()*.25), start=Math.PI*(.98+rand()*.40);
      c.lineWidth=.5+rand()*1.3;c.strokeStyle='rgba(255,'+Math.round(155+rand()*75)+',117,'+(.025+rand()*.24)+')';
      c.beginPath();c.ellipse(0,0,rad,rad*.96,0,start,Math.PI*(1.75+rand()*.32));c.stroke();
    }
    for(let i=0;i<390;i++){
      const radius=r*(.95+Math.pow(rand(),1.6)*2),theta=rand()*Math.PI;
      c.lineWidth=.4+rand()*1.25;
      c.strokeStyle='rgba(255,'+Math.round(143+rand()*77)+','+Math.round(79+rand()*65)+','+(.045+rand()*.22)+')';
      c.beginPath();c.ellipse(0,0,radius,radius*.155,0,theta,Math.min(Math.PI,theta+.25+rand()*.75));c.stroke();
    }
    c.restore();
    if(!memories.length){c.fillStyle='#b4a998';c.font='12px monospace';c.textAlign='center';c.fillText('No memory keys yet',cx,cy+r*1.65)}
  }
  labels.replaceChildren();
  memories.slice(0,4).forEach((m,i)=>{
    const b=document.createElement('button');b.textContent=m.key;
    b.style.left=(points[i][0]*100)+'%';b.style.top=(points[i][1]*100)+'%';
    b.onclick=()=>inspect(m.key);labels.append(b);
  });
  const observer=new ResizeObserver(paint);observer.observe(canvas);paint();
  return ()=>observer.disconnect();
};
