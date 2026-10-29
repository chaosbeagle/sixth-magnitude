'use strict';
const visualState=state=>({pending:'Needs confirmation',matched:'Overlay confirmed',mismatch:'Overlay mismatch',unavailable:'Visual check unavailable'})[state]||state.replaceAll('_',' ');
const reviewState=state=>({not_reviewed:'Awaiting review',deferred:'Review deferred',rejected:'Review rejected',physician_reviewed:'Physician reviewed',reviewer_accepted:'Reviewed',adjudicated_reference:'Adjudicated reference'})[state]||state.replaceAll('_',' ');

const $=id=>document.getElementById(id), clone=x=>structuredClone(x);
const S={run:null, caseId:null, source:null, state:null, plans:[], uid:null, planId:null, geom:null, obs:null,
  evidence:null, dirty:false, sync:false, epoch:0, loading:false, timer:null};
let definition, labels, defaults;
const fieldNames={tumour_evidence:'Tumour evidence',tumour_distribution:'Distribution',pattern_assessment_state:'Pattern assessment',patterns_present:'Growth patterns',components_assessment_state:'Component assessment',components_present:'Tissue components',architecture_assessment_state:'Architecture assessment',architecture_evidence:'Architecture',assessability:'Assessability',uncertainty_level:'Uncertainty',note:'Notes',tumour_relative_location:'Position relative to tumour',boundary_assessment_state:'Boundary assessment',boundary_types:'Boundaries',local_context_relation:'Focus and context',assessment_state:'Assessment',artifact_flags:'Image artefacts',candidates:'Candidate events'};
const valueNames={suspected_present:'Suspected tumour',no_evidence_in_view:'No evidence in this view',not_assessed:'Not assessed',not_applicable:'Not applicable',peripheral_only:'Peripheral only',complex_glandular:'Complex glandular',collapsed_alveoli_candidate:'Possible collapsed alveoli',nerve_candidate:'Possible nerve',fibrovascular_core_candidate:'Possible fibrovascular core',cribriform_candidate:'Possible cribriform structure',fused_glands_candidate:'Possible fused glands',tumour_nontumour_interface:'Tumour / non-tumour interface',tumour_nontumour:'Tumour / non-tumour',epithelial_stromal:'Epithelial / stromal',vessel_stromal:'Vessel / stromal',airway_alveolar:'Airway / alveolar',intratumour:'Within tumour',extratumour:'Outside tumour',STAS:'STAS candidate',LVI:'Lymphovascular invasion candidate',VPI:'Visceral pleural invasion candidate',invasion_boundary:'Invasion boundary candidate'};
const displayValue=value=>valueNames[value]||value.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());

const viewer=OpenSeadragon({id:'viewer',showNavigationControl:false,showNavigator:true,navigatorPosition:'BOTTOM_LEFT',
  animationTime:.15,blendTime:0,imageLoaderLimit:2,maxZoomPixelRatio:4,
  gestureSettingsMouse:{clickToZoom:false,dblClickToZoom:false}});
const anno=AnnotoriousOSD.createOSDAnnotator(viewer,{drawingEnabled:false,autoSave:true});
function status(message,error=false){$('status').textContent=message;document.querySelector('footer').classList.toggle('error',error);}
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await r.json();if(!r.ok){let message=result.error||r.statusText;for(const [from,to] of [['標註名稱不得空白','Enter a region label.'],['有選項時請將評估狀態改為已評估或不確定','Choose assessed or indeterminate when findings are selected.'],['腫瘤證據與分布狀態矛盾','Tumour evidence and distribution conflict.'],['有選項時請更新評估狀態','Update the assessment state when findings are selected.']])message=message.replace(from,to);throw Error(message);}return result;}
function safe(fn){return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>{console.error(e.stack||e.message);status(e.message,true);});}
function actor(){const id=$('actor').value.trim(),role=$('role').value;if(!id)throw Error('Enter an operator ID.');
  if(role==='physician'&&/^gpt|^astra|^ai[-_]/i.test(id))throw Error('A physician must enter their own ID.');return {id,role};}
const draftKey=()=>`sixth:${S.run.run_id}:${S.caseId}`;
function drafts(){const raw=JSON.parse(localStorage.getItem(draftKey())||'{}');return raw.uid?{[raw.uid]:raw}:raw;}
function cacheDraft(){if(S.caseId&&S.geom&&S.obs&&S.dirty){const items=drafts();items[S.uid]={uid:S.uid,geometry:S.geom,observation:S.obs,
  planId:S.planId,baseHash:S.state.annotations[S.uid]?.content_hash||null,updated:Date.now()};localStorage.setItem(draftKey(),JSON.stringify(items));}}
function clearDraft(){const items=drafts();delete items[S.uid];localStorage.setItem(draftKey(),JSON.stringify(items));}
async function restoreDraft(draft){const latest=S.state.annotations[draft.uid];
  if((latest?.content_hash||null)!==draft.baseHash){$('json-input').value=JSON.stringify(draft.observation,null,2);status('The saved region changed. Your draft is retained in Structured input for comparison.',true);return false;}
  cacheDraft();S.uid=draft.uid;S.planId=draft.planId||null;S.geom=clone(draft.geometry);fillForm(draft.observation);S.dirty=true;
  geometryFields();redraw();listRegions();fov(2048);await prepare();status('Draft restored.');return true;}
function changed(){if(S.sync)return;S.dirty=true;cacheDraft();updateState();}
function imagesReady(){return S.evidence&&['focus','context'].every(k=>{const i=$(k+'-image');return i.complete&&i.naturalWidth>0&&i.dataset.evidence===S.evidence.evidence_id;});}
function current(){return S.state?.annotations[S.uid];}
function orderedAnnotations(){const order=new Map(S.plans.map((p,i)=>[p.id,i]));return Object.values(S.state.annotations).filter(a=>!a.deleted)
  .sort((a,b)=>(order.get(a.planned_roi_id)??Infinity)-(order.get(b.planned_roi_id)??Infinity)||a.created_utc.localeCompare(b.created_utc)||a.id.localeCompare(b.id));}
function updateState(){const a=current();$('saved-state').textContent=S.dirty?'Unsaved changes':a?visualState(a.visual.state):'Unsaved';
  $('visual').disabled=!!(S.dirty||S.loading||!a||!imagesReady()||a.evidence_id!==S.evidence?.evidence_id);
  $('save').disabled=!S.geom||S.loading;
  $('review-state').textContent=S.dirty?'Save changes before confirmation or review.':a?`${visualState(a.visual.state)} · ${reviewState(a.review.state)}`:'Unsaved observation';}
function buildForms(){
  for(const group of ['focus','context','relationship','quality','special']){
    const section=document.createElement('section');section.className='form-section';section.id='form-'+group;section.hidden=group!=='focus';
    for(const [key,field] of Object.entries(definition.properties[group].properties)){
      const wrap=document.createElement('div');wrap.className='field';
      const label=document.createElement('label');label.textContent=fieldNames[key]||displayValue(key);label.htmlFor=`${group}-${key}`;wrap.append(label);
      let input;
      if(field.type==='array'){
        input=document.createElement('div');input.className='choices';input.id=`${group}-${key}`;
        for(const value of field.items.enum){const choice=document.createElement('label');choice.className='choice';const box=document.createElement('input');box.type='checkbox';box.value=value;
          box.setAttribute('aria-label',`${displayValue(group)} ${labels[value]||displayValue(value)}`);
          box.onchange=()=>{S.obs[group][key]=Array.from(input.querySelectorAll('input:checked'),x=>x.value);
            const stateKey=({patterns_present:'pattern_assessment_state',components_present:'components_assessment_state',architecture_evidence:'architecture_assessment_state',boundary_types:'boundary_assessment_state'})[key]||'assessment_state';
            if(S.obs[group][key].length&&['not_assessed','not_applicable','unassessable'].includes(S.obs[group][stateKey])){
              S.obs[group][stateKey]='assessed';$(`${group}-${stateKey}`).value='assessed';}
            changed();};choice.append(box,document.createTextNode(labels[value]||value));input.append(choice);}
      }else if(field.enum){input=document.createElement('select');input.id=`${group}-${key}`;
        for(const value of field.enum){const option=document.createElement('option');option.value=value;option.textContent=labels[value]||value;input.append(option);}
        input.onchange=()=>{S.obs[group][key]=input.value;changed();};
      }else{input=document.createElement('textarea');input.id=`${group}-${key}`;input.maxLength=1000;input.oninput=()=>{S.obs[group][key]=input.value||null;changed();};}
      wrap.append(input);section.append(wrap);
    }$('forms').append(section);
  }
}
function fillForm(observation){S.obs=clone(observation);S.sync=true;$('annotation-label').value=S.obs.label;
  for(const group of ['focus','context','relationship','quality','special'])for(const [key,value] of Object.entries(S.obs[group])){
    const el=$(`${group}-${key}`);if(!el)continue;
    if(Array.isArray(value))for(const box of el.querySelectorAll('input'))box.checked=value.includes(box.value);else el.value=value??'';
  }S.sync=false;}
function geometryFields(){if(!S.geom)return;const [x0,y0,x1,y1]=S.geom.bbox_level0;
  $('cx').value=(x0+x1)/2;$('cy').value=(y0+y1)/2;$('width').value=Number(((x1-x0)*S.source.mpp_x).toFixed(6));$('height').value=Number(((y1-y0)*S.source.mpp_y).toFixed(6));
  $('context-side').value=S.geom.context_side_um;$('geometry-role').value=S.geom.role;
  $('geometry-status').textContent=`${$('width').value} × ${$('height').value} µm`;
  const m=current()?.measurement;$('measurement-info').textContent=m?`Region ${m.code} · Full tumour coverage: ${m.section_complete?'declared':'not declared'} · Boundary: ${m.boundary_assessable?'assessable':'not assessed'}. Edit these settings in the pathology workbench.`:'';
}
function shape(uid,geometry){const [x0,y0,x1,y1]=geometry.bbox_level0,bounds={minX:x0,minY:y0,maxX:x1,maxY:y1};
  return {id:uid,bodies:[],target:{annotation:uid,selector:{type:geometry.type==='polygon'?'POLYGON':'RECTANGLE',geometry:geometry.type==='polygon'?{points:geometry.points,bounds}:{x:x0,y:y0,w:x1-x0,h:y1-y0,rot:0,bounds}}}};}
function redraw(){S.sync=true;const items=Object.values(S.state.annotations).filter(a=>!a.deleted&&a.id!==S.uid).map(a=>shape(a.id,a.geometry));
  if(S.geom)items.push(shape(S.uid,S.geom));anno.setAnnotations(items);if(S.geom)anno.setSelected(S.uid);S.sync=false;}
function fov(um){if(!S.geom)return;const [x0,y0,x1,y1]=S.geom.bbox_level0,cx=(x0+x1)/2,cy=(y0+y1)/2;
  viewer.viewport.fitBounds(viewer.viewport.imageToViewportRectangle(cx-um/S.source.mpp_x/2,cy-um/S.source.mpp_y/2,um/S.source.mpp_x,um/S.source.mpp_y),true);}
async function showEvidence(data,epoch){if(epoch!==S.epoch)return;S.evidence=data;
  await Promise.all(['focus','context'].map(kind=>new Promise((resolve,reject)=>{const img=$(kind+'-image'),meta=data.views[kind];
    img.hidden=false;img.dataset.evidence=data.evidence_id;img.onload=()=>{resolve();updateState();};img.onerror=()=>reject(Error('The evidence image could not be loaded.'));
    const url=`/evidence/${data.render_evidence_id||data.evidence_id}/${data.files[kind+'_overlay'].file}`;img.src=url;$(kind+'-link').href=url;
    $(kind+'-scale').textContent=`${meta.output_mpp_x.toFixed(2)} µm/px${meta.boundary_padded?' · Padded edge':''}`;
  })));if(epoch===S.epoch){S.loading=false;updateState();}}
async function prepare(){if(!S.geom)return;const epoch=++S.epoch;S.loading=true;S.evidence=null;updateState();
  try{const data=await api('/api/prepare',{case_id:S.caseId,source_key:S.source.source_key,geometry:S.geom});await showEvidence(data,epoch);}
  catch(e){if(epoch===S.epoch){S.loading=false;updateState();}throw e;}}
async function selectAnnotation(id){cacheDraft();const a=S.state.annotations[id];if(!a||a.deleted)return;
  const draft=drafts()[id];if(draft&&(a.content_hash||null)===draft.baseHash)return restoreDraft(draft);
  S.uid=id;S.planId=a.planned_roi_id||null;S.geom=clone(a.geometry);fillForm(a.observation);S.dirty=false;geometryFields();redraw();listRegions();fov(Math.max(...[$('width').value,$('height').value].map(Number))*1.6);
  const epoch=++S.epoch;S.loading=true;updateState();await showEvidence(await api('/api/evidence/'+a.evidence_id),epoch);}
async function newWindow(plan=null){cacheDraft();S.uid=crypto.randomUUID();S.planId=plan?.id||null;let cx,cy;
  if(plan){S.geom={type:'rectangle',role:'sampling_window',bbox_level0:clone(plan.bbox_level0),context_side_um:plan.context_side_um,points:null};}
  else{const point=viewer.viewport.viewportToImageCoordinates(viewer.viewport.getCenter(true));cx=point.x;cy=point.y;
    const w=512/S.source.mpp_x,h=512/S.source.mpp_y;cx=Math.min(S.source.width-w/2,Math.max(w/2,cx));cy=Math.min(S.source.height-h/2,Math.max(h/2,cy));
    S.geom={type:'rectangle',role:'sampling_window',bbox_level0:[cx-w/2,cy-h/2,cx+w/2,cy+h/2],context_side_um:2048,points:null};}
  fillForm(defaults);S.dirty=false;geometryFields();redraw();listRegions();fov(2048);await prepare();status('Region ready. Inspect the images and enter your observation.');}
function listRegions(){const el=$('regions');el.replaceChildren();let count=0;
  for(const a of orderedAnnotations()){count++;const b=document.createElement('button');b.className='roi-button'+(a.id===S.uid?' active':'');
    b.append(document.createTextNode(`${count}. ${a.observation.label}`));const small=document.createElement('small');small.textContent=`${visualState(a.visual.state)}${a.review.state==='not_reviewed'?'':` · ${reviewState(a.review.state)}`}`;b.append(small);b.onclick=safe(()=>selectAnnotation(a.id));el.append(b);}
  for(const draft of Object.values(drafts())){const b=document.createElement('button');b.className='roi-button';
    b.textContent=`Draft · ${draft.observation.label||'Untitled region'}`;b.onclick=safe(()=>restoreDraft(draft));el.append(b);}
  $('roi-count').textContent=count;}
async function listCases(){const run=await api('/api/run');S.run=run;$('run-badge').textContent=`${run.queue.length} ${run.queue.length===1?'slide':'slides'}${run.purpose==='engineering'?' · Demo':''}`;
  $('cases').replaceChildren();for(const c of run.queue){const button=document.createElement('button');button.className='case-button'+(c.case_id===S.caseId?' active':'');
    const strong=document.createElement('strong');strong.textContent=c.case_id;const small=document.createElement('small');small.textContent=`${c.count} regions · ${c.pending_visual} to confirm`;
    button.append(strong,small);button.onclick=safe(()=>openCase(c.case_id));$('cases').append(button);}
  return run;}
async function loadState(){const data=await api('/api/case/'+S.caseId);S.state=data.state;listRegions();await listCases();return data;}
async function openCase(caseId){if(S.timer)await stopTimer();cacheDraft();++S.epoch;S.loading=true;S.evidence=null;S.geom=null;S.uid=null;S.caseId=caseId;updateState();
  status(`Opening ${caseId}…`);$('viewer-loading').hidden=false;
  for(const kind of ['focus','context']){$(kind+'-image').hidden=true;$(kind+'-image').removeAttribute('src');}
  const data=await api('/api/case/'+caseId);S.source=data.source;S.state=data.state;S.plans=data.planned_rois;
  $('case-name').textContent=caseId;$('source-badge').textContent=`${S.source.mpp_x} µm/px`;
  // Annotorious can merge an empty initial clear with an absent history entry.
  S.sync=true;if(anno.getAnnotations().length)anno.clearAnnotations();S.sync=false;
  const top=S.source.downsamples.length-1,source=S.source;
  await new Promise((resolve,reject)=>{const opened=()=>{viewer.removeHandler('open-failed',failed);$('viewer-loading').hidden=true;resolve();};
    const failed=()=>{viewer.removeHandler('open',opened);reject(Error('The slide could not be opened.'));};viewer.addOnceHandler('open',opened);viewer.addOnceHandler('open-failed',failed);
    viewer.open({width:source.width,height:source.height,tileSize:512,minLevel:0,maxLevel:top,
      getLevelScale:l=>1/source.downsamples[top-l],getNumTiles:l=>new OpenSeadragon.Point(Math.ceil(source.level_dimensions[top-l][0]/512),Math.ceil(source.level_dimensions[top-l][1]/512)),
      getTileUrl:(l,x,y)=>`/tiles/${caseId}/${source.source_key}/${top-l}/${x}/${y}/tile.png`});});
  $('plans').replaceChildren();for(const [i,p] of S.plans.entries()){const b=document.createElement('button');b.className='plan-button';b.textContent=`Location ${i+1}`;
    b.onclick=safe(()=>{const existing=orderedAnnotations().find(a=>a.planned_roi_id===p.id);return existing?selectAnnotation(existing.id):newWindow(p);});$('plans').append(b);}
  await listCases();listRegions();const pending=Object.values(drafts()).sort((a,b)=>(b.updated||0)-(a.updated||0));
  if(pending.length&&await restoreDraft(pending[0]))return;
  const first=orderedAnnotations()[0];
  if(first)await selectAnnotation(first.id);else await newWindow(S.plans[0]||null);
  status(`${caseId} loaded.`);}
async function action(payload){const body={case_id:S.caseId,source_key:S.source.source_key,base_revision:S.state.revision,
  operation_id:crypto.randomUUID(),actor:actor(),...payload};
  // Keep the exact request available across a network retry; do not create a new ID.
  const key=`sixth:pending:${S.run.run_id}`;sessionStorage.setItem(key,JSON.stringify(body));
  let result;
  try{result=await api('/api/action',body);}catch(e){
    if(e instanceof TypeError){result=await api('/api/action',body);}else throw e;}
  sessionStorage.removeItem(key);await loadState();return result;}
async function save(){if(!S.geom)throw Error('Select a region first.');if(S.loading)throw Error('Wait for the images to load.');
  // Read the final Annotorious geometry, including a drag still selected on the canvas.
  const currentShape=anno.getAnnotationById(S.uid);if(currentShape)receiveShape(currentShape,false);
  status('Saving observation…');const result=await action({action:'save',annotation_id:S.uid,planned_roi_id:S.planId,geometry:S.geom,observation:S.obs});
  S.dirty=false;clearDraft();listRegions();const a=current();S.evidence=null;const epoch=++S.epoch;S.loading=true;
  await showEvidence(await api('/api/evidence/'+a.evidence_id),epoch);updateState();status('Saved. Inspect the overlay before confirming.');}
async function visual(){const finalShape=anno.getAnnotationById(S.uid);if(finalShape)receiveShape(finalShape);
  if($('visual').disabled)throw Error('Save and load both evidence views first.');const a=current();
  await action({action:'visual',annotation_id:a.id,content_hash:a.content_hash,evidence_id:a.evidence_id,state:'matched'});updateState();status('Overlay confirmed. Physician review is still pending.');}
async function review(decision){if(S.dirty)throw Error('Save your changes first.');const a=current();if(!a)throw Error('Save the observation first.');
  await action({action:'review',annotation_id:a.id,content_hash:a.content_hash,decision});updateState();status('Physician review saved for this observation.');}
async function startTimer(){if(actor().role!=='physician')throw Error('Select the Physician role first.');const sid=crypto.randomUUID();
  await action({action:'review_start',review_session_id:sid});S.timer=sid;$('actor').disabled=true;$('review-timer').textContent='Pause review timer';status('Review timer running.');}
async function stopTimer(){if(!S.timer)return;await action({action:'review_stop',review_session_id:S.timer});S.timer=null;$('actor').disabled=false;$('review-timer').textContent='Start review timer';status('Review timer paused.');}
function receiveShape(a,dirty=true){if(S.sync||a.id!==S.uid)return;const selector=a.target.selector,g=selector.geometry;
  let bbox,points=null;if(selector.type==='RECTANGLE')bbox=[g.x,g.y,g.x+g.w,g.y+g.h];else{points=g.points;bbox=[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];}
  if(JSON.stringify(bbox)===JSON.stringify(S.geom.bbox_level0)&&JSON.stringify(points)===JSON.stringify(S.geom.points))return;
  const span=Math.max((bbox[2]-bbox[0])*S.source.mpp_x,(bbox[3]-bbox[1])*S.source.mpp_y);
  S.geom={type:points?'polygon':'rectangle',role:points?'tissue_region':S.geom.role,bbox_level0:bbox,points,context_side_um:Math.max(S.geom.context_side_um,span*2)};
  geometryFields();if(dirty){changed();status('Region changed. Save to update its evidence.');}}
anno.on('updateAnnotation',a=>receiveShape(a));
anno.on('createAnnotation',safe(async a=>{if(S.sync)return;cacheDraft();S.uid=a.id;S.planId=null;S.geom={bbox_level0:[0,0,1,1],context_side_um:2048,points:null,role:'tissue_region'};
  fillForm(defaults);anno.setDrawingEnabled(false);receiveShape(a);geometryFields();changed();await prepare();status('Region drawn. Add an observation and save.');}));
async function applyGeometry(){if(!S.geom)return;const x=+$('cx').value,y=+$('cy').value,w=+$('width').value/S.source.mpp_x,h=+$('height').value/S.source.mpp_y;
  const old=S.geom.bbox_level0,bbox=[x-w/2,y-h/2,x+w/2,y+h/2];
  const points=S.geom.points?.map(([px,py])=>[bbox[0]+(px-old[0])/(old[2]-old[0])*w,bbox[1]+(py-old[1])/(old[3]-old[1])*h])||null;
  S.geom={type:points?'polygon':'rectangle',role:$('geometry-role').value,bbox_level0:bbox,points,context_side_um:+$('context-side').value};
  changed();geometryFields();redraw();await prepare();status('Geometry updated. Save to keep this change.');}
function validateShape(value,template,path=''){if(template.type==='object'){if(!value||Array.isArray(value)||typeof value!=='object')throw Error(`${path} must be an object`);
  for(const key of Object.keys(value))if(!(key in template.properties))throw Error(`Unknown field ${path}.${key}`);
  for(const key of template.required){if(!(key in value))throw Error(`Missing field ${path}.${key}`);validateShape(value[key],template.properties[key],path+'.'+key);}}
  else if(template.type==='array'){if(!Array.isArray(value)||value.some(x=>!template.items.enum.includes(x)))throw Error(`Invalid choice at ${path}`);}
  else if(template.enum&&!template.enum.includes(value))throw Error(`Invalid code at ${path}`);
  else if(template.const&&template.const!==value)throw Error(`Schema mismatch at ${path}`);
  else if(Array.isArray(template.type)&&value!==null&&typeof value!=='string')throw Error(`Expected text at ${path}`);
  else if(template.type==='string'&&typeof value!=='string')throw Error(`Expected text at ${path}`);}
for(const button of document.querySelectorAll('[data-tab]'))button.onclick=()=>{for(const b of document.querySelectorAll('[data-tab]'))b.classList.toggle('active',b===button);
  for(const g of ['focus','context','relationship','quality','special'])$('form-'+g).hidden=g!==button.dataset.tab;};
for(const button of document.querySelectorAll('[data-fov]'))button.onclick=()=>fov(+button.dataset.fov);
$('home').onclick=()=>viewer.viewport.goHome();$('annotation-label').oninput=()=>{S.obs.label=$('annotation-label').value;changed();};
$('save').onclick=safe(save);$('visual').onclick=safe(visual);$('new').onclick=safe(()=>newWindow());$('refresh').onclick=safe(()=>openCase(S.caseId));
$('apply-geometry').onclick=safe(applyGeometry);$('center').onclick=safe(async()=>{const p=viewer.viewport.viewportToImageCoordinates(viewer.viewport.getCenter(true));$('cx').value=p.x;$('cy').value=p.y;await applyGeometry();});
$('polygon').onclick=()=>{anno.cancelSelected();anno.setDrawingTool('polygon');anno.setDrawingEnabled(true);status('Click along the tissue boundary, then enter your observation.');};
$('geometry-role').onchange=()=>{S.geom.role=$('geometry-role').value;changed();};
$('copy-json').onclick=()=>{$('json-input').value=JSON.stringify(S.obs,null,2);status('Current observation loaded into Structured input.');};
function mergeObservation(base,patch,template,path=''){if(!patch||Array.isArray(patch)||typeof patch!=='object')throw Error(`${path} must be an object`);
  const value=clone(base);for(const [key,item] of Object.entries(patch)){const field=template.properties[key];if(!field)throw Error(`Unknown field ${path}.${key}`);
    value[key]=field.type==='object'?mergeObservation(value[key],item,field,path+'.'+key):item;}return value;}
$('load-json').onclick=safe(()=>{const obs=mergeObservation(S.obs,JSON.parse($('json-input').value),definition);validateShape(obs,definition);fillForm(obs);changed();status('JSON applied. Save to validate the observation.');});
$('role').onchange=safe(async()=>{if(S.timer){$('role').value='physician';throw Error('Pause the review timer before changing roles.');}$('review-controls').hidden=$('role').value!=='physician';
  if($('role').value==='physician'){$('actor').value='';$('actor').placeholder='Physician ID';}else $('actor').value=$('role').value==='engineering'?'engineering-test':'ai-operator';});
$('review-timer').onclick=safe(()=>S.timer?stopTimer():startTimer());$('accept').onclick=safe(()=>review('accept'));$('defer').onclick=safe(()=>review('defer'));$('reject').onclick=safe(()=>review('reject'));
$('delete').onclick=safe(async()=>{const a=current();if(!a)throw Error('This region has not been saved.');await action({action:'delete',annotation_id:a.id,content_hash:a.content_hash});S.dirty=false;clearDraft();await openCase(S.caseId);});
$('finish').onclick=safe(async()=>{if(S.dirty)throw Error('Save your changes first.');if(S.timer)await stopTimer();await action({action:'case_status',status:'completed_planned_sampling',note:'Planned sampling completed; whole-slide coverage is not declared.'});status('Planned sampling marked complete.');});
$('next-case').onclick=safe(()=>{const ids=S.run.queue.map(c=>c.case_id),i=ids.indexOf(S.caseId);if(i+1<ids.length)return openCase(ids[i+1]);status('This is the last slide.');});
$('export').onclick=safe(async()=>{const result=await api('/api/export',{});$('export-result').textContent=result.directory;status(`Exported ${result.regions} regions and ${result.observations} observations; ${result.research_labels} physician-reviewed records.`);});
$('usage').onclick=safe(async()=>{const data=await api('/api/usage');$('export-result').textContent=JSON.stringify(data);status(data.available?'Usage updated.':'No session usage is available.');});
for(const kind of ['focus','context'])$(kind+'-raw').onclick=()=>{if(!S.evidence)return;const img=$(kind+'-image');const raw=img.src.endsWith('/'+S.evidence.files[kind].file);
  const url=`/evidence/${S.evidence.render_evidence_id||S.evidence.evidence_id}/${S.evidence.files[raw?kind+'_overlay':kind].file}`;img.src=url;$(kind+'-link').href=url;$(kind+'-raw').textContent=raw?'Original':'Overlay';};
window.addEventListener('beforeunload',cacheDraft);
safe(async()=>{const s=await api('/api/schema');definition=s.schema;labels=Object.fromEntries(Object.keys(s.labels).map(code=>[code,displayValue(code)]));defaults=s.defaults;buildForms();fillForm(defaults);await listCases();
  if(S.run.purpose==='engineering'){$('role').value='engineering';$('actor').value='engineering-test';}
  if(S.run.queue.length)await openCase(S.run.queue.find(c=>c.status!=='completed_planned_sampling')?.case_id||S.run.queue[0].case_id);
})()
