// Convert the trusted renderer's DOM into the studio's editable slide geometry.
namespace => {
  const slide = document.querySelector(".slide");
  const page = slide.getBoundingClientRect();
  const hex = css => {
    const rgb = /^rgb\((\d+), (\d+), (\d+)\)$/.exec(css);
    if (!rgb) throw Error("Use an opaque RGB color");
    return "#" + rgb.slice(1).map(n => Number(n).toString(16).padStart(2, "0")).join("");
  };
  const nodes = [...slide.querySelectorAll("[data-node]")];
  if (!nodes.length || nodes.length > 100) throw Error("A slide needs 1–100 editable objects");
  const elements = nodes.map((el, z) => {
    const r = el.getBoundingClientRect(), style = getComputedStyle(el);
    const type = el.dataset.node;
    const common = {type, id: namespace + "-" + el.id, z, x:(r.x-page.x)/page.width,
      y:(r.y-page.y)/page.height, w:r.width/page.width, h:r.height/page.height};
    if (type === "shape") return {...common, shape:el.dataset.shape || "rect",
      fill:hex(style.backgroundColor), polygon:JSON.parse(el.dataset.points || "null")};
    if (type === "image") return {...common, asset_id:el.dataset.asset, alt:el.alt};
    if (type === "line") {
      const anchor = value => {
        if (!value) return null;
        const [id,side] = value.split(":");
        return {shape_id:namespace + "-" + id, side};
      };
      return {...common,color:hex(style.color),weight:Number(el.dataset.weight),direction:el.dataset.direction,
        arrows:el.dataset.arrows, start_anchor:anchor(el.dataset.from),end_anchor:anchor(el.dataset.to)};
    }
    const face = style.fontFamily.split(",")[0].replace(/["']/g, "").trim();
    const size = parseFloat(style.fontSize)*.75;
    if (type === "text") {
      const range = document.createRange();
      range.selectNodeContents(el);
      const fragments = [...range.getClientRects()].filter(line => line.width > 0);
      for (const line of fragments) {
        if (line.x < r.x-1 || line.right > r.right+1 || line.bottom > r.bottom+3)
          throw Error("Text exceeds its frame: " + el.innerText.slice(0,100));
      }
      const single = fragments.length > 0 && !el.innerText.includes("\n") &&
        fragments.every(line => Math.abs(line.top-fragments[0].top)<1);
      return {...common,text:el.innerText,font:face,size,color:hex(style.color),
        bold:Number(style.fontWeight)>=600,align:style.textAlign,single_line:single,
        line_spacing:parseFloat(style.lineHeight)/parseFloat(style.fontSize)};
    }
    if (type !== "table") throw Error("Unsupported editable object " + type);
    const cells = [...el.rows].map(row=>[...row.cells]);
    const grid = fn => cells.map(row=>row.map(cell=>fn(cell,getComputedStyle(cell))));
    return {...common,font:face,size,rows:grid(cell=>cell.innerText),
      header_fill:hex(getComputedStyle(cells[0][0]).backgroundColor),
      row_rule:hex(getComputedStyle(cells[0][0]).borderBottomColor),
      cell_fills:grid((_,s)=>hex(s.backgroundColor)), cell_inks:grid((_,s)=>hex(s.color)),
      css:{columns:cells[0].map(c=>c.getBoundingClientRect().width/r.width),
        rows:[...el.rows].map(row=>row.getBoundingClientRect().height/r.height),
        align:grid((_,s)=>s.textAlign),bold:grid((_,s)=>Number(s.fontWeight)>=600)}};
  });
  return {id:namespace,title:slide.dataset.title,background:hex(getComputedStyle(slide).backgroundColor),elements};
}
