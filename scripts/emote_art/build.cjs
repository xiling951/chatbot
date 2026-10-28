/* Original project geometry, not copied third-party characters or images.
   Development only: npm ci, then npm run build in this directory.
   resvg renders our editable SVG sources; no external images are fetched. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Resvg } = require('@resvg/resvg-js');

const output = path.resolve(__dirname, '../../plugins/astrbot_plugin_companion_core/assets/emotes');
fs.mkdirSync(output, { recursive: true });
const definitions = [
  ['greeting','你好呀','wave','smile','#D9EFC8'],
  ['joy','开心','spark','happy','#FFE7AA'],
  ['thanks','谢谢你','flower','smile','#F8D7C9'],
  ['celebrate','好耶','confetti','happy','#FFE5A8'],
  ['praise','很棒呀','star','happy','#EADDF9'],
  ['approval','赞一个','thumb','smile','#CDEAE1'],
  ['playful','嘿嘿','wink','wink','#F6D6C9'],
  ['laugh','哈哈哈','laugh','laugh','#FFE2A8'],
  ['curious','嗯？','question','curious','#D5E7F1'],
  ['thinking','想一想','dots','thinking','#DBE0F5'],
  ['idea','有主意啦','bulb','happy','#F8EDB7'],
  ['acknowledge','收到','check','smile','#D5ECD6'],
  ['agree','嗯嗯','nod','smile','#D2E9E2'],
  ['gentle_no','我想想','tilt','thinking','#EADFF0'],
  ['apology','抱歉呀','bow','sorry','#E3E5EE'],
  ['quiet','安静一会','quiet','calm','#D9E9EE'],
  ['comfort','慢慢来','blanket','calm','#F7DDD1'],
  ['rest','歇一会','cup','calm','#E5EACD'],
  ['goodnight','晚安','moon','sleep','#DDDFF2'],
  ['morning','早安','sun','happy','#FFE4A6'],
  ['welcome','回来啦','wave2','happy','#D2EBD8'],
  ['goodbye','下次聊','wave3','smile','#E5E1F3'],
  ['music','听首歌','notes','calm','#D8E9ED'],
  ['reading','翻一页','book','calm','#EADFC8'],
  ['rain','听雨','rain','calm','#D9E9F0'],
  ['sunshine','晴朗一点','sun2','smile','#F5E6AB'],
  ['appreciation','送你小花','flowers','smile','#F4DED9'],
  ['support','给你打气','heart','calm','#F4D8D8'],
  ['ready','准备好啦','flag','happy','#D5EBD8'],
  ['patience','不着急','sprout','calm','#DFEBCF'],
  ['surprise','哇哦','stars','surprised','#E2DDF4'],
  ['small_win','小小进步','medal','happy','#FFE3B1'],
];
const ink = '#34514B';
function line(d, extra='') { const color = /stroke="([^"]+)"/.exec(extra)?.[1] || ink; return `<path d="${d}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>`; }
function star(x,y,r=12,color='#F8C65E') {
  const points = Array.from({length:10}, (_,i) => {
    const angle = -Math.PI/2 + Math.PI*i/5, size = i%2 ? r*.47 : r;
    return `${x+Math.cos(angle)*size},${y+Math.sin(angle)*size}`;
  }).join(' ');
  return `<polygon points="${points}" fill="${color}" stroke="${ink}" stroke-width="3" stroke-linejoin="round"/>`;
}
function heart(x,y) { return `<path d="M${x},${y+18} C${x-30},${y} ${x-20},${y-22} ${x},${y-7} C${x+20},${y-22} ${x+30},${y} ${x},${y+18}Z" fill="#E99C9C" stroke="${ink}" stroke-width="3"/>`; }
function flower(x,y) { return `<g stroke="${ink}" stroke-width="2.5">${[0,72,144,216,288].map(angle=>`<ellipse cx="${x}" cy="${y-8}" rx="6" ry="10" fill="#F3B9BE" transform="rotate(${angle} ${x} ${y})"/>`).join('')}<circle cx="${x}" cy="${y}" r="5" fill="#F9D471"/></g>`; }
function extras(kind) {
  const decor = {
    wave: line('M 45 131 Q 22 116 27 97 M 22 91 L 18 81 M 32 87 L 32 76'),
    wave2: line('M 46 135 Q 20 110 30 94 M 25 84 L 19 76 M 35 82 L 36 72')+star(214,51,10),
    wave3: line('M 210 130 Q 237 112 229 93 M 234 83 L 240 75 M 224 81 L 223 71'),
    spark: star(41,62,13)+star(213,55,11)+line('M 216 84 L 224 80'),
    confetti: star(34,59,12)+star(219,53,12)+line('M 70 29 L 66 18 M 193 29 L 200 18 M 22 107 L 15 102')+`<circle cx="125" cy="20" r="4" fill="#D98C8C"/><circle cx="229" cy="94" r="4" fill="#D98C8C"/>`,
    star: star(204,155,28)+star(36,61,11),
    thumb: `<path d="M181 145 L184 120 Q184 111 191 112 L195 127 L212 127 Q221 127 219 137 L215 155 L184 155Z" fill="#FBE4AC" stroke="${ink}" stroke-width="4" stroke-linejoin="round"/>`,
    wink: star(213,71,13),
    laugh: line('M 27 87 L 20 79 M 226 85 L 235 78')+`<path d="M42 119 Q31 136 42 137 Q51 136 42 119" fill="#85B9CC"/>`,
    question: `<text x="205" y="68" font-family="Microsoft YaHei" font-size="40" fill="${ink}" transform="rotate(10 205 68)">?</text>`,
    dots: [199,213,227].map(x=>`<circle cx="${x}" cy="70" r="4" fill="${ink}"/>`).join(''),
    bulb: `<path d="M184 54 C170 28 210 18 211 42 Q212 51 204 56 L185 56Z" fill="#F7D474" stroke="${ink}" stroke-width="3"/>`+line('M188 64 L201 64 M194 15 L194 8 M173 22 L167 16 M217 25 L225 21'),
    check: `<circle cx="204" cy="146" r="24" fill="#7DB699" stroke="${ink}" stroke-width="3"/>`+line('M192 146 L200 154 L215 137','stroke="white"'),
    nod: line('M 215 60 L 215 83 M 207 76 L 215 84 L 223 76'),
    tilt: line('M 200 65 Q 213 58 225 65 M 204 75 Q 213 69 221 75'),
    bow: line('M 43 71 L 34 64 M 214 74 L 224 66')+`<path d="M194 80 Q186 97 195 96 Q204 95 194 80" fill="#9CC8D4"/>`,
    quiet: `<path d="M189 136 Q200 127 204 139 L202 157 Q191 168 187 156Z" fill="#E6EBE5" stroke="${ink}" stroke-width="3"/>`,
    blanket: `<path d="M58 148 Q128 163 198 145 L185 177 Q126 202 72 177Z" fill="#D59E90" stroke="${ink}" stroke-width="4"/>`+line('M82 160 L80 173 M104 165 L103 179 M129 167 L129 182 M154 164 L156 179 M177 159 L181 171','stroke="#F6D9CA"'),
    cup: `<path d="M106 145 L147 145 L143 175 Q126 184 111 175Z M147 150 Q168 147 162 162 Q158 170 146 166" fill="#FEF8E8" stroke="${ink}" stroke-width="4"/>`+line('M119 134 Q111 125 119 119 M136 134 Q128 125 137 118'),
    moon: `<path d="M211 30 Q182 46 210 68 Q178 69 178 47 Q178 27 211 30Z" fill="#F0C969" stroke="${ink}" stroke-width="3"/>`+star(44,59,8),
    sun: `<circle cx="206" cy="47" r="17" fill="#F1C86E" stroke="${ink}" stroke-width="3"/>`+line('M206 20 L206 13 M206 74 L206 81 M179 47 L172 47 M233 47 L240 47'),
    sun2: `<circle cx="47" cy="53" r="19" fill="#E8B65A" stroke="${ink}" stroke-width="3"/>`+line('M47 24 L47 17 M18 53 L11 53 M69 31 L75 25 M69 75 L75 81'),
    notes: line('M33 74 L33 48 L50 43 L50 68 M207 72 L207 45 L225 40 L225 67')+`<ellipse cx="27" cy="75" rx="7" ry="5" fill="${ink}"/><ellipse cx="44" cy="69" rx="7" ry="5" fill="${ink}"/><ellipse cx="201" cy="73" rx="7" ry="5" fill="${ink}"/><ellipse cx="219" cy="68" rx="7" ry="5" fill="${ink}"/>`,
    book: `<path d="M86 147 Q109 143 128 155 Q149 143 171 148 L170 179 Q147 175 128 185 Q108 175 86 179Z" fill="#FAF4DF" stroke="${ink}" stroke-width="4"/>`+line('M128 156 L128 183 M97 157 L115 162 M141 161 L160 156'),
    rain: [34,52,205,222].map((x,i)=>`<path d="M${x} ${49+i%2*21} l-5 13" stroke="#7FAABB" stroke-width="4" stroke-linecap="round"/>`).join(''),
    flower: flower(205,137)+line('M205 150 L198 176'),
    flowers: flower(192,141)+flower(216,150)+flower(205,128)+line('M191 151 L203 181 M216 161 L203 181 M205 141 L203 181'),
    heart: heart(205,145)+line('M175 156 L187 151'),
    flag: line('M198 177 L198 105')+`<path d="M198 105 L231 115 L198 128Z" fill="#DEB66A" stroke="${ink}" stroke-width="3"/>`,
    sprout: line('M205 177 L205 145')+`<path d="M205 155 Q181 159 184 139 Q202 137 205 155 M205 147 Q204 128 225 132 Q226 151 205 147" fill="#86B391" stroke="${ink}" stroke-width="3"/>`,
    stars: star(37,61,14)+star(211,48,16)+star(227,102,9),
    medal: `<path d="M192 127 L186 113 L201 110 L210 129" fill="#D8978E" stroke="${ink}" stroke-width="3"/>`+`<circle cx="202" cy="148" r="24" fill="#F0CC79" stroke="${ink}" stroke-width="4"/>`+star(202,148,13,'#FFF0B7'),
  };
  return decor[kind] || '';
}
function face(expression) {
  let eyes;
  if (['happy','laugh'].includes(expression)) eyes=line('M86 104 Q96 91 106 104 M146 104 Q156 91 166 104');
  else if (['calm','sleep'].includes(expression)) eyes=line('M85 105 Q96 113 107 105 M145 105 Q156 113 167 105');
  else if (expression==='wink') eyes=`<ellipse cx="96" cy="105" rx="4.8" ry="7" fill="${ink}"/>`+line('M146 101 L158 107 L146 113');
  else eyes=`<ellipse cx="96" cy="105" rx="4.8" ry="7" fill="${ink}"/><ellipse cx="156" cy="105" rx="4.8" ry="7" fill="${ink}"/>`;
  let mouth;
  if (expression==='laugh') mouth=`<path d="M108 127 Q126 155 144 127Z" fill="#9E6867" stroke="${ink}" stroke-width="4" stroke-linejoin="round"/>`;
  else if (expression==='surprised'||expression==='curious') mouth=`<ellipse cx="127" cy="131" rx="6" ry="9" fill="#9E6867" stroke="${ink}" stroke-width="3"/>`;
  else if (expression==='sorry') mouth=line('M118 134 Q127 127 136 134')+line('M85 88 L104 92 M149 92 L168 88');
  else if (expression==='thinking') mouth=line('M121 132 L134 132')+line('M148 88 L166 84');
  else if (expression==='sleep') mouth=line('M122 131 L132 131');
  else mouth=line('M115 128 Q127 141 139 128');
  return `<ellipse cx="77" cy="122" rx="13" ry="7" fill="#E9A699" opacity=".58"/><ellipse cx="175" cy="122" rx="13" ry="7" fill="#E9A699" opacity=".58"/>`+eyes+mouth;
}

const body='M62 156 C38 141 41 112 58 96 C54 67 75 47 100 53 C120 32 153 42 162 64 C192 57 216 81 207 108 C230 131 211 160 189 164 C173 188 145 184 128 171 C106 190 79 181 62 156Z';
const entries = definitions.map(([intent,caption,kind,expression,color], index) => {
  const id = `E${String(index+1).padStart(2,'0')}`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><title>${caption}</title><ellipse cx="128" cy="187" rx="65" ry="8" fill="#34514B" opacity=".08"/><path d="${body}" fill="${color}" stroke="white" stroke-width="16"/><path d="${body}" fill="${color}" stroke="${ink}" stroke-width="5" stroke-linejoin="round"/>${face(expression)}${extras(kind)}<text x="128" y="227" font-family="Microsoft YaHei" font-weight="700" font-size="22" fill="${ink}" text-anchor="middle" stroke="white" stroke-width="5" paint-order="stroke">${caption}</text></svg>`;
  // Explicit font supplied from the local OS; no font file is redistributed.
  const png = new Resvg(svg, {font:{fontFiles:['C:/Windows/Fonts/msyh.ttc'],loadSystemFonts:false,defaultFontFamily:'Microsoft YaHei'}}).render().asPng();
  fs.writeFileSync(path.join(output, `${id}.svg`), svg+'\n');
  fs.writeFileSync(path.join(output, `${id}.png`), png);
  return {id,intent,caption,file:`${id}.png`,source:`${id}.svg`,sha256:crypto.createHash('sha256').update(png).digest('hex'),width:256,height:256,bytes:png.length};
});
fs.writeFileSync(path.join(output,'manifest.json'), JSON.stringify({format:1,origin:'Original parametric vector drawings authored for this project; no third-party pictures, characters or brand marks.',generator:'scripts/emote_art/build.cjs',renderer:'@resvg/resvg-js@2.6.2',font:'Local Microsoft YaHei used for rendering only; font files are not distributed.',assets:entries},null,2)+'\n');
console.log(JSON.stringify({assets:entries.length,maximum_bytes:Math.max(...entries.map(e=>e.bytes))}));
