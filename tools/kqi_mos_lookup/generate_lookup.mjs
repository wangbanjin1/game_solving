import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../..");
function option(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}
const OUT = path.resolve(REPO_ROOT, option("--output") || "outputs/9_20_1/KQI_MOS带宽查表_v1.xlsx");
const PREVIEW = path.resolve(REPO_ROOT, option("--preview-dir") || "outputs/9_20_1/KQI_MOS带宽查表_v1_previews");
const wb = Workbook.create();

const COLORS = {navy:"#17365D", blue:"#1F4E78", cyan:"#DDEBF7", pale:"#EAF3F8", green:"#E2F0D9", amber:"#FFF2CC", red:"#FCE4D6", gray:"#E7E6E6", white:"#FFFFFF", ink:"#1F2937"};
const headers = ["业务","方向","阶段","带宽点(Mbps)","KQI映射","KQI档位","时延范围(ms)","时延保守","时延典型","时延乐观","丢包范围","丢包保守","丢包典型","丢包乐观","分辨率集合","分辨率保守(p)","分辨率典型(p)","分辨率乐观(p)","卡顿范围","卡顿保守","卡顿典型","卡顿乐观","首缓范围(s)","首缓保守(ms)","首缓典型(ms)","首缓乐观(ms)","合法动作","MOS保守","MOS典型","MOS乐观","MOS等级(典型)","说明"];

const kqi = {
  live: {
    差:{rtt:[150,300,"(150,300]"], loss:[0.005,0.05,"(0.5%,5%]"], stall:[0.3,1,"(30%,100%]"], res:[480,480,480,"480p"]},
    一般:{rtt:[80,150,"(80,150]"], loss:[0.0015,0.005,"(0.15%,0.5%]"], stall:[0.1,0.3,"(10%,30%]"], res:[540,720,720,"540p、720p"]},
    好:{rtt:[0,80,"[0,80]"], loss:[0,0.0015,"[0,0.15%]"], stall:[0,0.1,"[0,10%]"], res:[1080,1080,1080,"1080p"]}
  },
  video: {
    差:{rtt:[200,500,"(200,500]"], loss:[0.005,0.05,"(0.5%,5%]"], stall:[0.3,1,"(30%,100%]"], res:[360,480,480,"360p、480p"], buffer:[2,10,"(2,10]"]},
    一般:{rtt:[100,200,"(100,200]"], loss:[0.0015,0.005,"(0.15%,0.5%]"], stall:[0.1,0.3,"(10%,30%]"], res:[540,720,720,"540p、720p"], buffer:[1,2,"(1,2]"]},
    好:{rtt:[0,100,"[0,100]"], loss:[0,0.0015,"[0,0.15%]"], stall:[0,0.1,"[0,10%]"], res:[1080,1080,2160,"1080p、2160p"], buffer:[0,1,"[0,1]"]}
  },
  meeting: {
    差:{rtt:[150,300,"(150,300]"], loss:[0.005,0.05,"(0.5%,5%]"], stall:[0.3,1,"(30%,100%]"], res:[270,270,270,"270p"]},
    一般:{rtt:[80,150,"(80,150]"], loss:[0.0015,0.005,"(0.15%,0.5%]"], stall:[0.1,0.3,"(10%,30%]"], res:[360,360,360,"360p"]},
    好:{rtt:[0,80,"[0,80]"], loss:[0,0.0015,"[0,0.15%]"], stall:[0,0.1,"[0,10%]"], res:[720,720,720,"720p"]}
  },
  game: {
    差:{rtt:[100,460,"(100,460]"], loss:[0.005,0.05,"(0.5%,5%]"], stall:[0.3,1,"(30%,100%]"], jitter:[20,50,"(20,50]"]},
    一般:{rtt:[50,100,"(50,100]"], loss:[0.0015,0.005,"(0.15%,0.5%]"], stall:[0.1,0.3,"(10%,30%]"], jitter:[10,20,"(10,20]"]},
    好:{rtt:[0,50,"[0,50]"], loss:[0,0.0015,"[0,0.15%]"], stall:[0,0.1,"[0,10%]"], jitter:[0,10,"[0,10]"]}
  }
};

const models = {
  openlive:{name:"开直播", sheet:"开直播", directions:["UL"], phases:["稳态"], max:6, source:kqi.live, weights:[.25,.05,.25,.10], type:"A", legal:{480:[.3,1.5],540:[.5,2.5],720:[.8,4],1080:[1.5,6]}},
  video:{name:"视频", sheet:"视频", directions:["DL"], phases:["稳态","首播"], max:20, source:kqi.video, weights:[.04,.25,.04,.25], type:"A", legal:{360:[.2,1.5],480:[.3,2.5],540:[.5,3.5],720:[.8,6],1080:[1.5,10],2160:[6,20]}},
  cloudgame:{name:"云游", sheet:"云游", directions:["DL"], phases:["稳态"], max:20, source:kqi.live, weights:[.25,.05,.05,.25], type:"B", legal:{480:[1,6],540:[1.5,10],720:[3,15],1080:[6,20]}},
  meeting:{name:"视频会议", sheet:"视频会议", directions:["UL","DL"], phases:["稳态"], max:6, source:kqi.meeting, weights:[.25,.05,.05,.25], type:"A", legal:{270:[.2,1.5],360:[.3,2.5],720:[.8,6]}},
  voip:{name:"视频通话", sheet:"视频通话", directions:["UL","DL"], phases:["稳态"], max:6, source:kqi.meeting, weights:[0,0,.15,.15], type:"B", call:true, legal:{270:[.2,1.5],360:[.3,2.5],720:[.8,6]}},
  game:{name:"手游", sheet:"手游", directions:["UL+DL"], phases:["稳态"], max:.1, source:kqi.game, weights:[0,0,.25,.04], type:"B", game:true, legal:{}}
};

function mid(a,b, geometric=false){ if (geometric && a>0) return Math.sqrt(a*b); return (a+b)/2; }
function gradeForRate(id, mbps){
  if (["openlive","meeting","voip"].includes(id)) return mbps<=.7?"差":mbps<=1.5?"一般":"好";
  if (id==="cloudgame") return mbps<=3?"差":mbps<=6?"一般":"好";
  return null;
}
function gradeFormula(cell){ return `=IF(${cell}<2,"很差",IF(${cell}<3,"差",IF(${cell}<3.5,"一般",IF(${cell}<4.5,"良好","优秀"))))`; }
function generalFormula(r, model, scenario){
  const rate=`$D${r}*1000`, res=scenario==="worst"?`$P${r}`:scenario==="typ"?`$Q${r}`:`$R${r}`;
  const delay=scenario==="worst"?`$H${r}`:scenario==="typ"?`$I${r}`:`$J${r}`;
  const loss=scenario==="worst"?`$L${r}`:scenario==="typ"?`$M${r}`:`$N${r}`;
  const stall=scenario==="worst"?`$T${r}`:scenario==="typ"?`$U${r}`:`$V${r}`;
  const buffer=scenario==="worst"?`$X${r}`:scenario==="typ"?`$Y${r}`:`$Z${r}`;
  const [w1,w2,g1,g2]=model.weights;
  const sb=`(5/(1+EXP(-(${rate})/928.984)))`;
  const sr=`(5/(1+EXP(-(${res})/410)))`;
  const q=model.game?"4.5":`MAX(1,MIN(5,5-4*${w1}*(5-${sb})-4*${w2}*(5-${sr})))`;
  const phase=`$C${r}`;
  const i=model===models.video?`IF(${phase}="首播",1+4*EXP(-0.0003*${buffer}),1+4*EXP(-0.0035*${delay}))`:`1+4*EXP(-0.0035*${delay})`;
  const sl=`1+4*EXP(-180.94*${loss})`, ss=`5-4*${stall}`;
  const v=`MAX(1,MIN(5,5-4*${g1}*(5-(${sl}))-4*${g2}*(5-(${ss}))))`;
  const a=`(0.1*(1+2*EXP(-(${i})/2)))`, b=`(0.1*(1+2*EXP(-(${v})/2)))`;
  const factor=model.type==="A"?`((${a})*((${i})-1)+(${b})*((${v})-1))/(4*((${a})+(${b})))`:`1-(${a})*(5-(${i}))-(${b})*(5-(${v}))`;
  return `=MAX(1,MIN(5,1+((${q})-1)*(${factor})))`;
}
function callFormula(r, scenario){
  const rate=`$D${r}*1000`, res=scenario==="worst"?`$P${r}`:scenario==="typ"?`$Q${r}`:`$R${r}`;
  const delay=scenario==="worst"?`$H${r}`:scenario==="typ"?`$I${r}`:`$J${r}`;
  const loss=scenario==="worst"?`$L${r}`:scenario==="typ"?`$M${r}`:`$N${r}`;
  const stall=scenario==="worst"?`$T${r}`:scenario==="typ"?`$U${r}`:`$V${r}`;
  const pixels=`IF(${res}=270,480*270,IF(${res}=360,640*360,IF(${res}=720,1280*720,1920*1080)))`;
  const d=`0.0975*POWER(30,1.2667)*POWER(${pixels},0.3177)`;
  const sb=`1+4.1192-4.1192/(1+POWER((${rate})/(${d}),2.1276))`;
  const sr=`1-0.6571+0.6571/(1+POWER((${pixels})/232000,-1.295))`;
  const q=`MAX(1,MIN(5,(${sb})*(${sr})))`;
  const i=`MAX(1,MIN(5,1+3.615-3.615/(1+POWER((${delay})/396.6+0.256,-2.016))))`;
  const sl=`MAX(1,MIN(5,5*EXP(-100*${loss}/1.383)))`, ss=`5-4*${stall}`;
  const v=`MAX(1,MIN(5,5-4*0.15*(5-(${sl}))-4*0.15*(5-(${ss}))))`;
  const a=`0.1*(1+2*EXP(-(${i})/2))`, b=`0.1*(1+2*EXP(-(${v})/2))`;
  const factor=`1-(${a})*(5-(${i}))-(${b})*(5-(${v}))`;
  return `=MAX(1,MIN(5,1+((${q})-1)*(${factor})))`;
}
function styleTitle(sheet, title, subtitle, endCol="AF"){
  sheet.mergeCells(`A1:${endCol}1`); sheet.getRange("A1").values=[[title]];
  sheet.getRange(`A1:${endCol}1`).format={fill:COLORS.navy,font:{bold:true,color:COLORS.white,size:16},rowHeight:30,verticalAlignment:"center"};
  sheet.mergeCells(`A2:${endCol}2`); sheet.getRange("A2").values=[[subtitle]];
  sheet.getRange(`A2:${endCol}2`).format={fill:COLORS.cyan,font:{color:COLORS.ink,italic:true},wrapText:true,rowHeight:34,verticalAlignment:"center"};
  sheet.showGridLines=false;
}
function styleHeader(range){ range.format={fill:COLORS.blue,font:{bold:true,color:COLORS.white},wrapText:true,verticalAlignment:"center",horizontalAlignment:"center",borders:{preset:"all",style:"thin",color:"#B4C6E7"},rowHeight:34}; }
function styleBody(range){ range.format={font:{color:COLORS.ink,size:9},verticalAlignment:"center",borders:{preset:"all",style:"thin",color:"#D9E2F3"}}; }

const summary=wb.worksheets.add("总览");
const ranges=wb.worksheets.add("KQI区间");
const grades=wb.worksheets.add("MOS分档");
for (const m of Object.values(models)) wb.worksheets.add(m.sheet);

styleTitle(summary,"带宽—KQI—MOS 查表设计（首版）","主索引采用带宽/码率（0.1 Mbps）；MOS 是由该带宽点、分辨率和KQI区间计算出的结果范围。黄色内容是实验映射假设，正式用于博弈前需要实测或规则标定。","N");
summary.getRange("A4:N4").values=[["业务","代码","重要性","媒体方向","带宽范围(Mbps)","网格/场景","映射策略","当前公式使用的KQI","MOS最小","MOS典型最小","MOS典型最大","MOS最大","审计状态","备注"]]; styleHeader(summary.getRange("A4:N4"));

styleTitle(ranges,"KQI 原始区间与公式使用情况","区间来自用户给出的分档；代表值按中点计算，丢包非零区间使用几何中点。区间本身不证明带宽变化会导致KQI变化。","M");
ranges.getRange("A4:M4").values=[["业务","指标","单位","KQI档位","下界","下界包含","上界","上界包含","代表值","好坏方向","当前MOS使用","来源","说明"]]; styleHeader(ranges.getRange("A4:M4"));

styleTitle(grades,"MOS 分档与配置差异","首版按用户口径分类；“一般”上界3.5仍待业务确认。当前代码 solver.level_edges 与本表不一致。","H");
grades.getRange("A4:H4").values=[["MOS档位","下界","下界包含","上界","上界包含","用户口径","当前代码边界","处理建议"]]; styleHeader(grades.getRange("A4:H4"));
grades.getRange("A5:H9").values=[
 ["很差",1,true,2,false,"1–2","1–2.5","改为[1,2)"],
 ["差",2,true,3,false,"2–3","2.5–3.5","改为[2,3)"],
 ["一般",3,true,3.5,false,"3–3.5（待确认）","3.5–4","改为[3,3.5)并确认"],
 ["良好",3.5,true,4.5,false,"3.5–4.5","4–4.5","改为[3.5,4.5)"],
 ["优秀",4.5,true,5,true,"4.5以上","4.5–5","保持[4.5,5]"]
]; styleBody(grades.getRange("A5:H9")); grades.getRange("B5:D9").format.numberFormat="0.0";
grades.getRange("A12:H16").values=[
 ["审计项","当前状态","目标状态","影响","首版处理","代码位置","优先级","备注"],
 ["带宽动作粒度","1 kbps","100 kbps","候选/暴搜规模与结果","工作簿使用100 kbps","候选生成器/参考暴搜","高","后续改代码"],
 ["MOS层级边界","[1,2.5,3.5,4,4.5,5]","[1,2,3,3.5,4.5,5]","套餐层级判断","按用户口径展示","solver.level_edges","高","需同步配置"],
 ["带宽联动KQI","未实现","条件映射/实测模型","改变动作后的MOS真实性","同档绑定仅作实验假设","MosModel上游场景模型","高","正式求解前标定"],
 ["未入公式指标","抖动、会议建立时延","明确是否纳入","手游/会议结果","明确标注不影响","models/mos.py","中","需业务确认"]
]; styleHeader(grades.getRange("A12:H12")); styleBody(grades.getRange("A13:H16"));

const rangeRows=[];
function addMetric(business, metric, unit, band, lo, hi, text, better, used, note=""){
  const li=text.startsWith("["), ui=text.endsWith("]");
  rangeRows.push([business,metric,unit,band,lo,li,hi,ui,mid(lo,hi,metric==="丢包率"),better,used,"用户提供的KQI分档",note]);
}
for (const [business,src,extra] of [["开直播",kqi.live,false],["云游",kqi.live,false],["视频会议",kqi.meeting,false],["视频通话",kqi.meeting,false],["视频",kqi.video,true]]){
  for (const band of ["差","一般","好"]){ const x=src[band]; addMetric(business,business==="开直播"||business==="云游"?"交互时延":"端到端时延","ms",band,...x.rtt,"越小越好",true); addMetric(business,"丢包率","比例",band,...x.loss,"越小越好",true); addMetric(business,"卡顿占比","比例",band,...x.stall,"越小越好",true); rangeRows.push([business,"分辨率","p",band,x.res[0],true,x.res[2],true,x.res[1],"越大越好",true,"用户提供的KQI分档",x.res[3]]); if(extra) addMetric(business,"初始缓冲","s",band,...x.buffer,"越小越好",true,"仅视频首播阶段"); }
}
for (const band of ["差","一般","好"]){ const x=kqi.game[band]; addMetric("手游","交互时延","ms",band,...x.rtt,"越小越好",true); addMetric("手游","丢包率","比例",band,...x.loss,"越小越好",true); addMetric("手游","卡顿占比","比例",band,...x.stall,"越小越好",true); addMetric("手游","抖动","ms",band,...x.jitter,"越小越好",false,"当前模型未使用"); }
ranges.getRange(`A5:M${4+rangeRows.length}`).values=rangeRows; styleBody(ranges.getRange(`A5:M${4+rangeRows.length}`)); ranges.getRange(`E5:I${4+rangeRows.length}`).format.numberFormat="0.0000"; ranges.freezePanes.freezeRows(4);

const summaryMeta=[];
for (const [id,model] of Object.entries(models)){
  const sh=wb.worksheets.getItem(model.sheet); styleTitle(sh,`${model.name}：带宽主表`, model.game?"当前模型固定UL/DL各0.1 Mbps；码率和抖动不进入MOS。三行仅展示KQI区间对MOS的影响。":"黄色“实验同档绑定”用于展示带宽区间与KQI区间联动；MOS保守/典型/乐观分别使用区间最差角点、中点、最好角点。","AF");
  sh.getRange("A4:AF4").values=[headers]; styleHeader(sh.getRange("A4:AF4"));
  const values=[], formulas=[];
  const rates=model.game?[.1]:Array.from({length:Math.round(model.max*10)},(_,i)=>(i+1)/10);
  for (const dir of model.directions) for (const phase of model.phases) for (const rate of rates){
    const bands=id==="video"?["差","一般","好"]:model.game?["差","一般","好"]:[gradeForRate(id,rate)];
    for (const band of bands){ const x=model.source[band]; const rw=x.rtt, lw=x.loss, sw=x.stall, rv=x.res||[0,0,0,"—"], bf=x.buffer||[0,0,"—"];
      const legal=model.game?true:(model.legal[rv[1]]?rate>=model.legal[rv[1]][0] && rate<=model.legal[rv[1]][1]:false);
      const mapping=id==="video"?"待标定：独立场景":model.game?"固定配额":"实验同档绑定";
      const note=id==="video"?"无带宽分档；同一带宽列出差/一般/好三种KQI场景":model.game?`抖动${x.jitter[2]}当前不影响MOS`:model.call?"方向MOS；会话MOS取UL/DL最小值":"分配带宽暂按等于应用码率";
      values.push([model.name,dir,phase,rate,mapping,band,rw[2],rw[1],mid(rw[0],rw[1]),rw[0],lw[2],lw[1],mid(lw[0],lw[1],true),lw[0],rv[3],rv[0],rv[1],rv[2],sw[2],sw[1],mid(sw[0],sw[1]),sw[0],bf[2],bf[1]*1000,mid(bf[0],bf[1])*1000,bf[0]*1000,legal?"是":"否",null,null,null,null,note]);
      const rr=4+values.length; const fn=model.call?callFormula:((r,s)=>generalFormula(r,model,s));
      formulas.push({r:rr, arr:[fn(rr,"worst"),fn(rr,"typ"),fn(rr,"best"),gradeFormula(`AC${rr}`)]});
    }
  }
  const end=4+values.length; sh.getRange(`A5:AF${end}`).values=values; styleBody(sh.getRange(`A5:AF${end}`));
  for (const f of formulas) sh.getRange(`AB${f.r}:AE${f.r}`).formulas=[f.arr];
  sh.getRange(`D5:D${end}`).format.numberFormat="0.0"; sh.getRange(`L5:N${end}`).format.numberFormat="0.000%"; sh.getRange(`T5:V${end}`).format.numberFormat="0.0%"; sh.getRange(`AB5:AD${end}`).format.numberFormat="0.000";
  sh.getRange(`E5:E${end}`).format.fill=COLORS.amber; sh.getRange(`AB5:AE${end}`).format.fill=COLORS.green; sh.getRange(`AA5:AA${end}`).format.horizontalAlignment="center";
  sh.getRange(`AB5:AD${end}`).conditionalFormats.add("colorScale",{colors:["#F8696B","#FFEB84","#63BE7B"],thresholds:["min","50%","max"]});
  sh.freezePanes.freezeRows(4); sh.freezePanes.freezeColumns(6);
  const widths=[10,8,8,12,18,10,16,11,11,11,16,11,11,11,20,13,13,13,16,11,11,11,15,14,14,14,10,12,12,12,16,34];
  widths.forEach((w,i)=>sh.getRangeByIndexes(0,i,end,1).format.columnWidth=w);
  sh.getRange(`A5:AF${end}`).format.wrapText=true;
  const importance={openlive:"中",video:"低",cloudgame:"中",meeting:"高",voip:"高",game:"中"}[id];
  const mapText=id==="video"?"无带宽阈值；每点列3档KQI":model.game?"固定0.1 Mbps":"按带宽档同档绑定KQI";
  summaryMeta.push({id,model,end,importance,mapText,count:values.length});
}

const svals=[];
for (const m of summaryMeta){ const mdl=m.model; const media=mdl.game?"固定UL+DL":mdl.directions.join("/"); const br=mdl.game?"0.1固定":`0.1–${mdl.max.toFixed(1)}`; svals.push([mdl.name,m.id,m.importance,media,br,m.count,m.mapText,mdl.game?"RTT/丢包/卡顿":"码率/分辨率/RTT(或首缓)/丢包/卡顿",null,null,null,null,m.id==="video"?"待标定":m.id==="game"?"模型限制":"实验假设",m.id==="meeting"||m.id==="voip"?"会话MOS=min(UL,DL)":m.id==="video"?"缺少带宽→KQI分档规则":""]); }
summary.getRange(`A5:N${4+svals.length}`).values=svals; styleBody(summary.getRange(`A5:N${4+svals.length}`));
summaryMeta.forEach((m,i)=>{ const r=5+i, sh=`'${m.model.sheet}'`; summary.getRange(`I${r}:L${r}`).formulas=[[`=MIN(${sh}!AB5:AB${m.end})`,`=MIN(${sh}!AC5:AC${m.end})`,`=MAX(${sh}!AC5:AC${m.end})`,`=MAX(${sh}!AD5:AD${m.end})`]]; });
summary.getRange(`I5:L${4+svals.length}`).format.numberFormat="0.000"; summary.getRange(`G5:G${4+svals.length}`).format.fill=COLORS.amber; summary.getRange(`I5:L${4+svals.length}`).format.fill=COLORS.green;
summary.getRange("A13:N20").values=[
 ["设计结论","内容","", "", "", "", "", "", "", "", "", "", "", ""],
 ["主索引","整数bandwidth_kbps最稳妥；本表显示为Mbps并按0.1递增。MOS不是唯一主键。","", "", "", "", "", "", "", "", "", "", "", ""],
 ["MOS范围","保守/典型/乐观分别使用KQI区间的最差角点、中点、最好角点；典型不是概率期望。","", "", "", "", "", "", "", "", "", "", "", ""],
 ["因果边界","现有区间只描述KQI好坏，不能证明带宽改变会自动改变RTT、丢包或卡顿；黄色映射需后续实测标定。","", "", "", "", "", "", "", "", "", "", "", ""],
 ["视频缺口","视频没有码率分档，所以每个0.1 Mbps点并列三种KQI场景，不能直接作为唯一求解动作结果。","", "", "", "", "", "", "", "", "", "", "", ""],
 ["手游限制","当前手游固定100 kbps，码率、分辨率、抖动不进入MOS；若要参与带宽博弈，需新增带宽→网络KQI模型。","", "", "", "", "", "", "", "", "", "", "", ""],
 ["双向业务","视频会议和视频通话按UL/DL分别计算，最终会话MOS取两个方向的最小值。","", "", "", "", "", "", "", "", "", "", "", ""],
 ["业务覆盖","用户资料覆盖6类；仓库另有观看直播watchlive、短视频shortvideo，因缺少本轮KQI区间暂未生成。","", "", "", "", "", "", "", "", "", "", "", ""]
];
summary.mergeCells("B14:N14"); summary.mergeCells("B15:N15"); summary.mergeCells("B16:N16"); summary.mergeCells("B17:N17"); summary.mergeCells("B18:N18"); summary.mergeCells("B19:N19"); summary.mergeCells("B20:N20"); styleHeader(summary.getRange("A13:N13")); styleBody(summary.getRange("A14:N20")); summary.getRange("A14:A20").format={fill:COLORS.cyan,font:{bold:true,color:COLORS.blue},borders:{preset:"all",style:"thin",color:"#D9E2F3"}}; summary.getRange("B14:N20").format.wrapText=true; summary.getRange("B14:N20").format.rowHeight=34;
summary.freezePanes.freezeRows(4); summary.getRange("A:N").format.columnWidth=14; summary.getRange("G:G").format.columnWidth=24; summary.getRange("H:H").format.columnWidth=28; summary.getRange("N:N").format.columnWidth=30;
ranges.getRange("A:M").format.columnWidth=13; ranges.getRange("L:L").format.columnWidth=22; ranges.getRange("M:M").format.columnWidth=28; ranges.getRange("A:M").format.wrapText=true;
grades.getRange("A:H").format.columnWidth=18; grades.getRange("D:D").format.columnWidth=18; grades.getRange("H:H").format.columnWidth=28; grades.getRange("A:H").format.wrapText=true;

await wb.recalculate();
const inspection=await wb.inspect({kind:"workbook,sheet",maxChars:6000});
console.log(inspection.ndjson || inspection);
const errors=await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:100},summary:"formula errors"});
console.log(errors.ndjson || errors);
for (const m of summaryMeta){ const chk=await wb.inspect({kind:"region",sheetId:m.model.sheet,range:`A4:AF${Math.min(m.end,10)}`,maxChars:2500}); console.log(chk.ndjson || chk); }
await fs.mkdir(PREVIEW,{recursive:true});
for (const name of ["总览","KQI区间","MOS分档",...Object.values(models).map(x=>x.sheet)]){
  const img=await wb.render({sheetName:name,range:"A1:N25",scale:.8,format:"png"});
  await fs.writeFile(`${PREVIEW}/${name}.png`,new Uint8Array(await img.arrayBuffer()));
}
await fs.mkdir(path.dirname(OUT),{recursive:true});
const file=await SpreadsheetFile.exportXlsx(wb); await file.save(OUT);
console.log(`SAVED ${OUT}`);
