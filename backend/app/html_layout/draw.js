// Trusted browser renderer. Model output only enters the DOM as validated geometry, colors or text.
payload => {
  "use strict";
  const {theme, pages, width, pictures = {}, logo} = payload;
  const number = (v, fallback, lo=0, hi=2000) => {
    const n = Number(v ?? fallback);
    if (!Number.isFinite(n) || n < lo || n > hi) throw Error("Invalid dimension/value: " + v);
    return n;
  };
  const color = (value, fallback="ink") => {
    const chosen = value || fallback;
    const c = theme[chosen] || chosen;
    if (!/^#[a-f0-9]{6}$/i.test(c)) throw Error("Expected a theme color or #RRGGBB: " + chosen);
    return c;
  };
  const font = name => name === "display" ? theme.display : name === "mono" && !theme.lockFont ? "Consolas" : theme.font || "Arial";
  const bool = x => x === "true" || x === "1";
  const align = value => ["left","right","center"].includes(value) ? value : "left";
  const flexAlign = value => ({start:"flex-start",end:"flex-end",between:"space-between",center:"center",stretch:"stretch"})[value];
  const el = (tag, parent, styles={}) => {
    const result = document.createElement(tag);
    Object.assign(result.style, styles);
    parent.append(result);
    return result;
  };
  const px = v => v + "px";
  function cssLength(value) {
    const token = String(value).trim();
    if (token.endsWith("%")) return number(token.slice(0, -1), 0, 0, 100) + "%";
    return px(number(token.replace(/px$/, ""), 0));
  }
  let serial = 0;
  function mark(node, type) {
    node.dataset.node = type;
    node.id = "obj-" + ++serial;
    return node;
  }
  const pos = (x,y,w,h) => ({position:"absolute",left:px(x),top:px(y),width:px(w),height:px(h)});
  function shape(parent,x,y,w,h,fill,form="rect") {
    const node = mark(el("div",parent,{...pos(x,y,w,h),backgroundColor:color(fill)}),"shape");
    node.dataset.shape = form;
    if (form === "round" || form === "ellipse") node.style.borderRadius = form === "round" ? "8px" : "50%";
    const points = {triangle:[[.5,0],[1,1],[0,1]],diamond:[[.5,0],[1,.5],[.5,1],[0,.5]]}[form];
    if (points) clip(node,points);
    return node;
  }
  function clip(node,points) {
    node.style.clipPath = "polygon(" + points.map(([x,y])=>x*100+"% "+y*100+"%").join(",") + ")";
  }
  function polygon(parent,points,fill) {
    const xs = points.map(p=>p[0]), ys = points.map(p=>p[1]);
    const x=Math.min(...xs), y=Math.min(...ys), w=Math.max(...xs)-x, h=Math.max(...ys)-y;
    if (!(w>0 && h>0)) throw Error("Polygon has zero area");
    const node = shape(parent,x,y,w,h,fill);
    const normalized = points.map(p=>[(p[0]-x)/w,(p[1]-y)/h]);
    node.dataset.points = JSON.stringify(normalized);
    clip(node,normalized);
  }
  function line(p,x1,y1,x2,y2,c,head=false,weight=1.5) {
    const dx=x2-x1,dy=y2-y1,len=Math.hypot(dx,dy);
    if (!len) throw Error("Zero-length line");
    const w=Math.max(1,Math.abs(dx)),h=Math.max(1,Math.abs(dy));
    const l=(x1+x2-w)/2,t=(y1+y2-h)/2;
    const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
    p.append(svg); mark(svg,"line");
    Object.assign(svg.style,{...pos(l,t,w,h),color:color(c),overflow:"visible"});
    svg.dataset.direction = !dy ? (dx>0?"right":"left") : !dx ? (dy>0?"down":"up") :
      dx>0 ? (dy>0?"diagonal":"diagonal-up") : (dy>0?"diagonal-down-left":"diagonal-up-left");
    svg.dataset.weight=weight; svg.dataset.arrows=head?"end":"none";
    const part = (name,attributes) => {
      const obj = document.createElementNS(svg.namespaceURI,name);
      for(const [key,value] of Object.entries(attributes)) obj.setAttribute(key,value);
      svg.append(obj);
    };
    part("line",{x1:x1-l,y1:y1-t,x2:x2-l,y2:y2-t,stroke:"currentColor","stroke-width":weight/.75});
    if(head) {
      const x=x2-l,y=y2-t,ux=dx/len,uy=dy/len;
      part("polygon",{fill:"currentColor",points:[[x,y],[x-ux*9-uy*4,y-uy*9+ux*4],[x-ux*9+uy*4,y-uy*9-ux*4]].map(v=>v.join(",")).join(" ")});
    }
    return svg;
  }
  // The native text contract supports 8–100 pt. CSS uses 96 px/in and PowerPoint uses 72 pt/in.
  const textStyle = a => ({fontFamily:font(a.font),fontSize:px(Math.max(11,Math.min(125,number(a.size,19,1,500)))),
    lineHeight:"1.17",fontWeight:bool(a.bold)?"700":"400",textAlign:align(a.align),
    color:color(a.color),whiteSpace:"pre-wrap",overflowWrap:"anywhere"});
  function label(p,x,y,w,h,value,a={}) {
    if (!(w>0 && h>0)) throw Error("Empty label bounds");
    const holder = el("div",p,{...pos(x,y,w,h),display:"flex",flexDirection:"column",justifyContent:"center"});
    const n = mark(el("div",holder,textStyle(a)),"text"); n.textContent=value;
    let size=parseFloat(n.style.fontSize);
    while(n.offsetHeight > h+1 && size>11) n.style.fontSize=px(--size);
    if (n.offsetHeight>h+3) throw Error("Label needs more room: "+value);
  }
  function drawing(p,children,w,h) {
    const nodes = new Map(), attachments = [];
    for(const {tag,a,t} of children) {
      const fraction = k=>number(String(a[k] ?? 0).replace(/%$/, ""),0,0,100)/100;
      const X = k=>fraction(k)*w, Y = k=>fraction(k)*h;
      if(tag==="box") {
        const n=shape(p,X("x"),Y("y"),X("w"),Y("h"),a.bg||"surface",a.form||"rect");
        if(a.id) nodes.set(a.id,n.id);
      } else if(tag==="label") label(p,X("x"),Y("y"),X("w"),Y("h"),t,a);
      else if(tag==="arrow") {
        const n=line(p,X("x1"),Y("y1"),X("x2"),Y("y2"),a.color||"accent",a.head!=="false");
        attachments.push([n,a]);
      } else if(tag==="poly") {
        const points=(a.points||"").trim().split(/\s+/).map(pair=>pair.split(","));
        if(points.length<3 || points.some(pt=>pt.length!==2)) throw Error("Invalid polygon points");
        polygon(p,points.map(([x,y])=>[number(x,0,0,100)*w/100,number(y,0,0,100)*h/100]),a.bg||"accent");
      }
    }
    for(const [n,a] of attachments) for(const end of ["from","to"]) if(a[end]) {
      const [id,side]=a[end].split(":");
      if(!nodes.has(id)||!["top","bottom","left","right"].includes(side)) throw Error("Unknown drawing anchor "+a[end]);
      n.dataset[end]=nodes.get(id)+":"+side;
    }
  }
  function matrix(p,rows,a,w,h) {
    const n=rows[0].children.length;
    if(rows.length<2 || rows.length>12 || n>8 || rows.some(r=>r.children.length!==n))
      throw Error("Matrix needs 2–12 uniform rows and 1–8 columns");
    const table=mark(el("table",p,{width:"100%",height:"100%",borderCollapse:"collapse",tableLayout:"fixed",
      fontFamily:font("body"),fontSize:px(number(a.size,18,12,36)),lineHeight:"1.18"}),"table");
    if(a.widths) {
      const widths=a.widths.split(",").map(v=>number(v,1,0.1,100));
      if(widths.length!==n) throw Error("Matrix width count differs from column count");
      const sum=widths.reduce((a,b)=>a+b,0), group=el("colgroup",table);
      widths.forEach(v=>el("col",group,{width:v*100/sum+"%"}));
    }
    const body=el("tbody",table);
    rows.forEach((r,i)=>{
      const tr=el("tr",body);
      r.children.forEach(({a:cell,t})=>{
        const td=el("td",tr,{padding:"4px 8px",verticalAlign:"middle",textAlign:align(cell.align),
          backgroundColor:color(cell.bg||(!i?"ink":"surface")),color:color(cell.color||(!i?"background":"ink")),
          fontWeight:bool(cell.bold)||!i?"700":"400",borderBottom:"1px solid "+color("muted"),
          overflowWrap:"anywhere",whiteSpace:"pre-wrap"});
        td.textContent=t;
      });
    });
    let size=parseFloat(table.style.fontSize);
    while(table.offsetHeight>h+2 && size>12) table.style.fontSize=px(--size);
    if(table.offsetHeight>h+3) throw Error("Matrix is too dense for the allocated height");
  }
  function waterfall(p,bars,a,w,h) {
    let running=0;
    const values=bars.map(({a:b},i)=>{
      const v=number(b.value,NaN,-1e12,1e12),total=b.kind==="total";
      if(i && total && Math.abs(running-v)>1e-7*Math.max(1,Math.abs(v))) throw Error("Waterfall total does not reconcile: "+b.label);
      const start=total?0:running; running=total?v:start+v;
      return {start,end:running,v,total,b};
    });
    const low=Math.min(0,...values.flatMap(v=>[v.start,v.end])),high=Math.max(0,...values.flatMap(v=>[v.start,v.end]));
    const y=v=>37+(high-v)/(high-low||1)*(h-108),step=w/values.length,bw=step*.64;
    line(p,2,y(0),w-2,y(0),"muted",false,.65);
    values.forEach(({start,end,v,total,b},i)=>{
      const x=step*(i+.18),top=y(Math.max(start,end)),bottom=y(Math.min(start,end));
      shape(p,x,top,bw,Math.max(1,bottom-top),b.color||(total?"ink":v<0?"muted":"accent"));
      if(i<values.length-1) line(p,x+bw,y(end),x+step,y(end),"muted",false,.75);
      const str=(v<0?"−":!total?"+":"")+(a.unit||"")+Math.abs(v).toFixed(number(a.decimals,0,0,6))+(a.suffix||"");
      label(p,i*step,top-32,step,30,str,{size:19,bold:"true",align:"center"});
      label(p,i*step+2,h-64,step-4,58,b.label,{size:16,align:"center"});
    });
  }
  function gantt(p,tasks,a,w,h) {
    const periods=number(a.periods,12,1,365), left=w*.37, cell=(w-left-8)/periods, top=48;
    const rowH=(h-top-4)/tasks.length, titleW=left-12;
    const stride=Math.max(1,Math.ceil(periods/12));
    for(let i=0;i<periods;i+=stride)
      label(p,left+i*cell,2,Math.min(stride,periods-i)*cell,36,String(i+1),{size:14,align:"center",color:"muted"});
    label(p,0,2,titleW,36,a.unit||"Period",{size:14,bold:"true",color:"muted"});
    tasks.forEach(({a:t},i)=>{
      const start=number(t.start,NaN,1,periods),end=number(t.end,NaN,start,periods);
      const y=top+i*rowH;
      line(p,1,y,w-2,y,"muted",false,.5);
      label(p,0,y+2,titleW,rowH-4,t.label+(t.milestone?"\n"+t.milestone:""),{size:17});
      const x=left+(start-1)*cell, bw=(end-start+1)*cell;
      shape(p,x,y+rowH*.32,bw,rowH*.34,t.color||"accent");
    });
  }
  function sankey(p,flows,a,w,h) {
    const nodes=new Map(), edges=[];
    const get=name=>{
      if(!name) throw Error("Sankey flow missing endpoint");
      if(!nodes.has(name)) nodes.set(name,{name,ins:[],outs:[],rank:0});
      return nodes.get(name);
    };
    flows.forEach(({a:f})=>{
      const edge={s:get(f.from),t:get(f.to),v:number(f.value,NaN,.000001,1e12),c:f.color||"accent"};
      edge.s.outs.push(edge);edge.t.ins.push(edge);edges.push(edge);
    });
    const sum=xs=>xs.reduce((n,e)=>n+e.v,0);
    const pending=[...nodes.values()].filter(n=>!n.ins.length), done=[];
    while(pending.length) {
      const n=pending.shift(); done.push(n);
      for(const {t} of n.outs) {
        t.rank=Math.max(t.rank,n.rank+1);
        if(t.ins.every(e=>done.includes(e.s)) && !pending.includes(t)) pending.push(t);
      }
    }
    if(done.length!==nodes.size) throw Error("Sankey must be acyclic");
    const layers=[];
    for(const n of nodes.values()) {
      const input=sum(n.ins),output=sum(n.outs);
      if(input && output && Math.abs(input-output)>1e-7*Math.max(1,input)) throw Error("Flow does not balance at "+n.name);
      n.v=Math.max(input,output); (layers[n.rank]??=[]).push(n);
    }
    const cap=58,margin=5,step=(w-32)/(layers.length-1),scale=Math.min(...layers.map(layer=>
      (h-2*margin-layer.length*cap)/layer.reduce((v,n)=>v+n.v,0)));
    if(scale<=0) throw Error("Sankey needs more height for its labels");
    layers.forEach((layer,i)=>{
      const packed=layer.reduce((v,n)=>v+n.v*scale+cap,0);let y=(h-packed)/2;
      for(const n of layer) {
        n.x=9+i*step;n.y=y+cap;n.height=n.v*scale;n.src=n.dst=n.y;y+=cap+n.height;
      }
    });
    for(const e of edges) {
      const x1=e.s.x+10,x2=e.t.x, y1=e.s.src,y2=e.t.dst,th=e.v*scale;
      const upper=[];
      for(let i=0;i<=9;i++) {const t=i/9;upper.push([x1+(x2-x1)*t,y1+(y2-y1)*t*t*(3-2*t)]);}
      polygon(p,[...upper,...upper.slice().reverse().map(([x,y])=>[x,y+th])],e.c);
      e.s.src+=th;e.t.dst+=th;
    }
    for(const n of nodes.values()) {
      shape(p,n.x,n.y,10,n.height,"ink");
      const tw=Math.min(step-5,w*.28),x=Math.max(0,Math.min(w-tw,n.x+5-tw/2));
      label(p,x,n.y-cap,tw,cap-3,n.name+"\n"+n.v.toLocaleString("en-US"),{align:"center",size:15,bold:"true"});
    }
  }
  function sequence(p,messages,a,w,h) {
    const names=(a.actors||"").split("|").map(s=>s.trim());
    if(names.length<2 || new Set(names).size!==names.length) throw Error("Sequence needs distinct actors separated by |");
    const col=w/names.length, posX=name=>{const i=names.indexOf(name);if(i<0) throw Error("Unknown actor "+name);return (i+.5)*col;};
    names.forEach((name,i)=>{
      shape(p,i*col+4,0,col-8,40,"surface"); label(p,i*col+6,0,col-12,40,name,{align:"center",bold:"true",size:16});
      line(p,posX(name),41,posX(name),h-3,"muted",false,.7);
    });
    const row=(h-46)/messages.length;
    messages.forEach(({a:m},i)=>{
      const x=posX(m.from),end=posX(m.to),y=44+(i+1)*row-4;
      if(x!==end) {
        label(p,Math.min(x,end)+2,y-row+2,Math.abs(x-end)-4,row-3,m.label,{align:"center",size:15});
        line(p,x,y,end,y,"accent",true);
      } else {
        const d=x>w/2?-1:1,span=col*.35;
        line(p,x,y-10,x+d*span,y-10,"accent");line(p,x+d*span,y-10,x+d*span,y,"accent");
        line(p,x+d*span,y,x,y,"accent",true);
        label(p,Math.max(0,Math.min(w-col*1.6,x-col*.8)),y-row,col*1.6,row-11,m.label,{align:"center",size:14});
      }
    });
  }
  function picture(parent, children, attrs, width, height) {
    const asset = pictures[attrs.asset];
    if (!asset) throw Error("Picture is unavailable");
    const scale = Math.min(width / asset.width, height / asset.height);
    const w = asset.width * scale, h = asset.height * scale;
    const image = mark(el("img", parent, pos((width-w)/2, (height-h)/2, w, h)), "image");
    image.dataset.asset = attrs.asset;
    image.alt = attrs.alt || "";
    image.src = asset.src;
  }
  const widgets={drawing,matrix,waterfall,gantt,sankey,sequence,picture,
    exhibit:box=>mark(el("div",box,{position:"absolute",inset:"0"}),"exhibit")};
  // Unlike ordinary text, a drawing has no intrinsic CSS size before it is painted.
  // Let an unsized flex ancestor share available space when its exhibit would collapse.
  function expandCollapsedPanel(panel, slide) {
    for (const dimension of ["width", "height"]) {
      if (panel.getBoundingClientRect()[dimension] >= 16) continue;
      for (let box = panel; box !== slide && !box.style[dimension]; box = box.parentElement) {
        const horizontal = box.parentElement.style.flexDirection === "row";
        if (horizontal === (dimension === "width")) {
          if (!box.style.flexGrow) box.style.flex = "1 1 0px";
        } else {
          box.style.alignSelf = "stretch";
        }
      }
    }
  }
  function render(node,parent,pending) {
    const {tag,a,t,children}=node;
    const style={position:"relative",minWidth:"0",minHeight:"0"};
    if(a.grow !== undefined) style.flex=number(a.grow,1,0,100)+" 1 0px";
    if(a.w) style.width=cssLength(a.w);
    if(a.h) style.height=cssLength(a.h);
    if(tag==="page") Object.assign(style,{height:"100%",width:"100%"});
    const box=el("div",parent,style);
    if (a.bg) box.dataset.surface=color(a.bg);
    if(a.bg && tag!=="page" && tag!=="rule") {
      const bg=mark(el("div",box,{position:"absolute",inset:"0",backgroundColor:color(a.bg)}),"shape");
      bg.dataset.shape="rect";
    }
    if(["page","row","col"].includes(tag)) {
      Object.assign(box.style,{display:"flex",flexDirection:tag==="row"?"row":"column",padding:px(number(a.pad,tag==="page"?36:0)),
        gap:px(number(a.gap,tag==="page"?14:8)),alignItems:flexAlign(a.align)||"stretch",justifyContent:flexAlign(a.justify)||"flex-start"});
      if (tag==="page" && (payload.hasExhibit || logo)) box.style.paddingBottom=px(Math.max(logo?52:32,parseFloat(box.style.paddingBottom)));
      children.forEach(c=>render(c,box,pending));
    } else if(tag==="text") {
      const copy=mark(el("div",box,{...textStyle(a),position:"relative"}),"text");copy.textContent=t;
      if(!a.grow && !a.h) box.style.flexShrink="0";
    } else if(tag==="rule") {
      if(!a.h) box.style.height="2px";
      box.style.flexShrink="0"; mark(box,"shape");box.style.backgroundColor=color(a.color||a.bg||"muted");
    } else if(widgets[tag]) {
      if(!a.grow && !a.h) box.style.flex="1 1 0px";
      pending.push({box, paint:()=>{
        const {width:w,height:h}=box.getBoundingClientRect();
        if(w<15||h<16) throw Error(tag+" needs more space; assign a larger height or grow=1 to its parent");
        if(tag==="drawing") drawing(box,children,w,h); else widgets[tag](box,children,a,w,h);
      }});
    }
  }
  for(const {body,title} of pages) {
    const slide=el("section",document.querySelector("main"),{backgroundColor:color(body.a.bg||"background")});
    slide.className="slide";slide.dataset.title=title;
    const pending=[];
    render(body,slide,pending);
    for (const {box} of pending) {
      if (box.offsetWidth < 15 || box.offsetHeight < 16) expandCollapsedPanel(box, slide);
    }
    for (const panel of pending) panel.paint();
    if (logo) {
      const footer=el("div",slide,pos(width-94,674,62,32));
      picture(footer,[],{asset:logo,alt:"Brand logo"},62,32);
    }
    const rect=slide.getBoundingClientRect();
    for(const n of slide.querySelectorAll("[data-node]")) {
      const r=n.getBoundingClientRect();
      if(!r.width || !r.height || r.left<rect.left-.05||r.top<rect.top-.05||r.right>rect.right+.05||r.bottom>rect.bottom+.05)
        throw Error("Object outside canvas or collapsed: "+(n.textContent.slice(0,90)||n.dataset.node));
    }
  }
}
