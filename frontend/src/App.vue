<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
const API = `${window.location.protocol}//${window.location.hostname}:8000`
const token = ref(localStorage.getItem('mes_token') || '')
const username = ref(localStorage.getItem('mes_user') || '')
const role = ref(localStorage.getItem('mes_role') || '')
const loginName = ref('admin'), loginPassword = ref('')
const loginError = ref(''), busy = ref(false), error = ref(''), notice = ref('')
const active = ref('overview'), rows = ref<any>(null), metrics = ref<any>({}), routeCode = ref('PART-001')
const payload = ref(''), postResult = ref<any>(null), oeeMachineId = ref<number>(1)
const apsRunId = ref<number>(1), apsAssignments = ref<any[]>([])
const traceMode = ref('work_order'), traceKey = ref('1'), traceRows = ref<any[]>([])
const refData = ref<{orders:any[];machines:any[];employees:any[]}>({orders:[],machines:[],employees:[]})
const repForm = ref({work_order_id:'',operation_id:'',machine_id:'',employee_code:'',good_qty:1,scrap_qty:0,labor_minutes:0})
const woForm = ref({order_no:'',product_code:'',product_name:'',planned_qty:100})
const opForm = ref({work_order_id:'',sequence:10,operation_code:'',operation_name:'',machine_id:'',standard_minutes:0})
const apsForm = ref({job_no:'',work_order_id:'',operation_id:'',priority:5,due_at:'',required_minutes:60,machines:[] as string[]})
const pages = [
  { id:'report', label:'生产报工', icon:'✓', path:'/api/v1/production/reports', method:'POST', noGet:true },
  { id:'apsjobs', label:'APS排程任务', icon:'▦', path:'/api/v4/aps/jobs', method:'GET', post:'/api/v4/aps/jobs', sample:{job_no:'APS-JOB-001',work_order_id:1,operation_id:1,priority:8,due_at:'2026-11-10T17:00:00',release_at:'2026-11-06T08:00:00',required_minutes:120,eligible_machine_codes:['CNC-01']} },
  { id:'apsgenerate', label:'APS智能排程计算', icon:'▦', path:'/api/v4/aps/runs', method:'GET', post:'/api/v4/aps/generate', sample:{horizon_start:'2026-11-09T08:00:00',horizon_days:5,shift_start:'08:00',shift_end:'17:00',break_start:'12:00',break_end:'13:00',workdays:[0,1,2,3,4,5],max_jobs:200} },
  { id:'apsruns', label:'APS排程运行批次', icon:'▤', path:'/api/v4/aps/runs', method:'GET' },
  { id:'aiinsights', label:'AI生产分析 Agent', icon:'✧', path:'/api/v4/ai/production-analysis/latest', method:'GET', post:'/api/v4/ai/production-analysis/run?days=7', sample:{} },
  { id:'maintpred', label:'预测性维护评估', icon:'◌', path:'/api/v4/maintenance/predictions', method:'GET' },
  { id:'maintthreshold', label:'维护阈值配置', icon:'⚙', path:'/api/v4/maintenance/thresholds', method:'GET', post:'/api/v4/maintenance/thresholds', sample:{machine_code:'CNC-01',point_name:'spindle_temp_c',warning_value:60,critical_value:75,direction:'high',unit:'°C',enabled:true} },
  { id:'traceability', label:'生产全流程追溯', icon:'⌁', path:'/api/v4/dashboard', method:'GET', post:'/api/v4/traceability/events', sample:{event_no:'TRACE-2026-0001',event_type:'operation_complete',work_order_id:1,operation_id:1,machine_id:1,employee_code:'EMP-001',product_code:'PART-001',material_code:'AL6061',material_lot:'LOT-2026-001',tool_code:'T-001',good_qty:10,reject_qty:1,reference_no:'REPORT-001',metadata:{inspection:'first-article-pass'}} },
  { id:'monitor', label:'实时设备监控', icon:'◉', path:'/api/v3/monitor/overview', method:'GET' },
  { id:'oee', label:'设备 OEE', icon:'▥', path:'/api/v3/oee/machines/{machine_id}', method:'GET' },
  { id:'alarms3', label:'设备报警事件', icon:'⚠', path:'/api/v3/alarms/events', method:'GET' },
  { id:'gateways', label:'边缘网关状态', icon:'⌘', path:'/api/v3/edge/gateways', method:'GET' },
  { id:'alarmrules', label:'报警规则', icon:'⚑', path:'/api/v3/alarms/rules', method:'GET', post:'/api/v3/alarms/rules', sample:{rule_code:'SPINDLE-LOAD-HI',machine_code:'',point_name:'spindle_load_pct',operator:'>',threshold:90,severity:'high',title:'主轴负载过高',detail_template:'{machine} 主轴负载 {value}% 超过 {threshold}%',cooldown_seconds:300,enabled:true} },
  { id:'overview', label:'经营总览', icon:'◫', path:'/api/v2/analytics/production', method:'GET' },
  { id:'machines', label:'设备台账', icon:'▣', path:'/api/v1/machines', method:'GET' },
  { id:'orders', label:'生产工单', icon:'▤', path:'/api/v1/work-orders', method:'GET' },
  { id:'employees', label:'员工与技能', icon:'♙', path:'/api/v2/employees', method:'GET', post:'/api/v2/employees', sample:{employee_code:'EMP-003',full_name:'新员工',department:'CNC一组',skill_level:'L1',active:true} },
  { id:'products', label:'产品主数据', icon:'◇', path:'/api/v2/products', method:'GET', post:'/api/v2/products', sample:{product_code:'PART-002',product_name:'精密零件',drawing_revision:'A',material_code:'AL6061',description:'',active:true} },
  { id:'routing', label:'工艺路线', icon:'⌁', path:'/api/v2/routings/', method:'GET', post:'/api/v2/routings', sample:{product_code:'PART-001',revision:'A',operations:[{sequence:10,operation_code:'OP10',operation_name:'CNC粗铣',work_center:'CNC',standard_cycle_seconds:270,setup_minutes:20,preferred_machine_code:'CNC-01'}]} },
  { id:'ncr', label:'NCR不合格管理', icon:'⚑', path:'/api/v2/quality/ncr', method:'GET', post:'/api/v2/quality/ncr', sample:{ncr_no:'NCR-2026-001',work_order_id:1,operation_id:1,defect_code:'DIM-OUT',defect_description:'孔径超差',affected_qty:2,owner_code:'EMP-002'} },
  { id:'planning', label:'排程与产能', icon:'▦', path:'/api/v2/planning/plans', method:'GET', post:'/api/v2/planning/plans', sample:{work_order_id:1,machine_id:1,start_at:'2026-11-06T08:00:00',end_at:'2026-11-06T10:00:00',planned_minutes:120,sequence_no:10} },
  { id:'devices', label:'设备数据采集', icon:'⌁', path:'/api/v2/devices/readings/latest', method:'GET', post:'/api/v2/devices/readings', sample:{machine_id:1,point_name:'spindle_load_pct',numeric_value:45.2,quality:'good',source:'edge-gateway'} },
  { id:'analysis', label:'生产分析', icon:'▥', path:'/api/v2/analytics/production', method:'GET' },
  { id:'audit', label:'操作审计', icon:'≡', path:'/api/v2/audit/logs', method:'GET' },
  { id:'tools', label:'刀具管理', icon:'⌖', path:'/api/v1/tools', method:'GET' },
  { id:'materials', label:'物料库存', icon:'▧', path:'/api/v1/materials', method:'GET' },
  { id:'alerts', label:'异常预警', icon:'⚠', path:'/api/v1/alerts', method:'GET' },
]
let monitorTimer: number | undefined
const page = computed(() => pages.find(p => p.id === active.value) || pages[0])
const isArray = computed(() => Array.isArray(rows.value))
const columns = computed(() => {
  if (!isArray.value || !rows.value?.length) return []
  return Object.keys(rows.value[0]).filter(k => !['password_hash'].includes(k)).slice(0, 10)
})
async function request(path:string, options:RequestInit={}) {
  const headers:any = {'Content-Type':'application/json', ...(options.headers || {})}
  if (token.value) headers.Authorization = `Bearer ${token.value}`
  const res = await fetch(API + path, {...options, headers})
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
  return body
}
async function login() {
  loginError.value=''; busy.value=true
  try {
    const r = await fetch(API+'/api/v2/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:loginName.value,password:loginPassword.value})})
    const body = await r.json()
    if (!r.ok) throw new Error(body.detail || '登录失败')
    token.value=body.access_token; username.value=body.username; role.value=body.role
    localStorage.setItem('mes_token',token.value); localStorage.setItem('mes_user',username.value); localStorage.setItem('mes_role',role.value)
    await load()
  } catch(e:any) { loginError.value=e.message } finally { busy.value=false }
}
function logout(){token.value='';username.value='';role.value='';localStorage.removeItem('mes_token');localStorage.removeItem('mes_user');localStorage.removeItem('mes_role');rows.value=null}
function example(){ payload.value=JSON.stringify(page.value.sample || {},null,2) }
async function ensureRefs(){
  if(refData.value.orders.length && refData.value.machines.length && refData.value.employees.length) return
  const [orders,machines,employees]=await Promise.all([
    request('/api/v1/work-orders'),request('/api/v1/machines'),request('/api/v2/employees')
  ])
  refData.value={orders:orders as any[],machines:machines as any[],employees:employees as any[]}
}
function woOptions(woId:any){return refData.value.orders.find((o:any)=>Number(o.id)===Number(woId))?.operations||[]}
async function submitReport(){
  try{
    const r=await request('/api/v1/production/reports',{method:'POST',body:JSON.stringify({
      work_order_id:Number(repForm.value.work_order_id),operation_id:Number(repForm.value.operation_id),
      machine_id:repForm.value.machine_id?Number(repForm.value.machine_id):null,employee_code:repForm.value.employee_code,
      good_qty:Number(repForm.value.good_qty)||0,scrap_qty:Number(repForm.value.scrap_qty)||0,labor_minutes:Number(repForm.value.labor_minutes)||0
    })})
    notice.value=`报工已接受：累计合格 ${r.good_qty}，累计不良 ${r.scrap_qty}，工单状态 ${r.status}`
    await load()
  }catch(e:any){error.value=e.message}
}
async function submitWorkOrder(){
  try{await request('/api/v1/work-orders',{method:'POST',body:JSON.stringify(woForm.value)});notice.value='工单已创建';await load();await ensureRefs()}
  catch(e:any){error.value=e.message}
}
async function submitOperation(){
  try{
    const wid=Number(opForm.value.work_order_id)
    await request(`/api/v1/work-orders/${wid}/operations`,{method:'POST',body:JSON.stringify({
      sequence:Number(opForm.value.sequence)||10,operation_code:opForm.value.operation_code,operation_name:opForm.value.operation_name,
      machine_id:opForm.value.machine_id?Number(opForm.value.machine_id):null,standard_minutes:Number(opForm.value.standard_minutes)||0
    })})
    notice.value='工序已添加';await load();await ensureRefs()
  }catch(e:any){error.value=e.message}
}
async function submitApsJob(){
  try{
    await request('/api/v4/aps/jobs',{method:'POST',body:JSON.stringify({
      job_no:apsForm.value.job_no,work_order_id:Number(apsForm.value.work_order_id),operation_id:Number(apsForm.value.operation_id),
      priority:Number(apsForm.value.priority)||5,due_at:apsForm.value.due_at||undefined,
      required_minutes:Number(apsForm.value.required_minutes)||60,eligible_machine_codes:apsForm.value.machines
    })})
    notice.value='APS任务已加入排队池，请在“APS智能排程计算”中生成草案';await load()
  }catch(e:any){error.value=e.message}
}
async function load(){
  if(!token.value)return
  error.value=''; notice.value=''; postResult.value=null
  try {
    if(active.value==='report'){ await ensureRefs(); rows.value=null; if(!payload.value)example(); return }
    if(active.value==='orders'||active.value==='apsjobs') ensureRefs().catch(()=>{})
    let path=page.value.path
    if(active.value==='oee') {
      const monitor:any=await request('/api/v3/monitor/overview')
      if(!monitor.machines?.length) throw new Error('尚无设备台账，请先创建设备')
      if(!monitor.machines.some((m:any)=>Number(m.machine_id)===oeeMachineId.value)) oeeMachineId.value=Number(monitor.machines[0].machine_id)
      path=`/api/v3/oee/machines/${oeeMachineId.value}`
    }
    if(active.value==='routing') path += encodeURIComponent(routeCode.value)
    rows.value=await request(path)
    if(active.value==='overview' || active.value==='analysis') metrics.value=rows.value
    if(active.value==='monitor') metrics.value=rows.value
    if(active.value==='planning') {
      const cap=await request('/api/v2/planning/capacity')
      metrics.value={...metrics.value, capacity:cap}
    }
    if(!payload.value) example()
  }catch(e:any){error.value=e.message}
}
async function submit(){
  try {
    const data=JSON.parse(payload.value)
    const path=page.value.post
    if(!path) throw new Error('当前模块没有配置新增接口')
    postResult.value=await request(path,{method:'POST',body:JSON.stringify(data)})
    notice.value='提交成功'; await load()
  }catch(e:any){error.value=e.message}
}
async function approve(id:number){
  try { postResult.value=await request(`/api/v2/planning/plans/${id}/approve`,{method:'POST'});notice.value='排程审批成功';await load() }
  catch(e:any){error.value=e.message}
}
async function acknowledgeAlarm(id:number){
  try {postResult.value=await request(`/api/v3/alarms/events/${id}/acknowledge`,{method:'POST'});notice.value='报警已确认';await load()}
  catch(e:any){error.value=e.message}
}
async function disposeNcr(id:number, action:string){
  try {postResult.value=await request(`/api/v2/quality/ncr/${id}/disposition`,{method:'POST',body:JSON.stringify({action,owner_code:username.value})});notice.value='NCR处置完成';await load()}
  catch(e:any){error.value=e.message}
}
async function seed(){try{await request('/api/v1/demo/seed',{method:'POST'});await request('/api/v2/demo/seed',{method:'POST'});notice.value='演示主数据初始化完成';await load()}catch(e:any){error.value=e.message}}
async function loadAPSAssignments(){
  try { apsAssignments.value=await request(`/api/v4/aps/runs/${apsRunId.value}/assignments`) }
  catch(e:any){error.value=e.message}
}
async function approveAPSAssignment(id:number){
  try { postResult.value=await request(`/api/v4/aps/assignments/${id}/approve`,{method:'POST'});notice.value='APS分配已审批并写入正式排程';await loadAPSAssignments();await load() }
  catch(e:any){error.value=e.message}
}
async function rejectAPSAssignment(id:number){
  try { postResult.value=await request(`/api/v4/aps/assignments/${id}/reject?note=planner_review`,{method:'POST'});notice.value='APS草案已驳回，任务已退回待排队列';await loadAPSAssignments();await load() }
  catch(e:any){error.value=e.message}
}
async function searchTraceability(){
  try { const path=traceMode.value==='work_order'?`/api/v4/traceability/work-orders/${encodeURIComponent(traceKey.value)}`:`/api/v4/traceability/material-lots/${encodeURIComponent(traceKey.value)}`;traceRows.value=await request(path) }
  catch(e:any){error.value=e.message}
}
function changePage(id:string){active.value=id;rows.value=null;payload.value='';apsAssignments.value=[];traceRows.value=[];load()}
onMounted(()=>{if(token.value)load(); monitorTimer=window.setInterval(()=>{if(token.value && active.value==='monitor')load()},15000)})
onUnmounted(()=>{if(monitorTimer)window.clearInterval(monitorTimer)})
</script>
<template>
  <div v-if="!token" class="login-shell">
    <div class="login-card"><div class="brand-mark">M</div><p class="eyebrow">SMART FACTORY · PRIVATE MES</p><h1>CNC 制造执行平台</h1><p class="muted">V4.0 · 私有化部署 · 角色权限控制</p>
      <label>用户名</label><input v-model="loginName" autocomplete="username" />
      <label>密码</label><input v-model="loginPassword" type="password" autocomplete="current-password" @keyup.enter="login" />
      <p v-if="loginError" class="error">{{loginError}}</p><button class="primary full" :disabled="busy" @click="login">{{busy?'登录中…':'登录系统'}}</button>
      <small>首次登录账号由 MES_ADMIN_USER / MES_ADMIN_PASSWORD 环境变量初始化。</small>
    </div>
  </div>
  <div v-else class="shell">
    <aside class="sidebar"><div class="logo"><div class="brand-mark small">M</div><div><b>CNC MES</b><small>SMART FACTORY V4.0</small></div></div>
      <div class="menu-label">制造运营</div><button v-for="p in pages" :key="p.id" :class="['nav-item',{selected:active===p.id}]" @click="changePage(p.id)"><span>{{p.icon}}</span>{{p.label}}</button>
      <div class="sidebar-foot"><span class="online-dot"></span>私有网络运行中<br/><small>OT接口：只读采集</small></div>
    </aside>
    <main class="workspace"><header class="topbar"><div><span class="breadcrumb">MES / {{page.label}}</span><h1>{{page.label}}</h1></div><div class="user-box"><div class="avatar">{{username.slice(0,1).toUpperCase()}}</div><div><b>{{username}}</b><small>{{role}}</small></div><button class="quiet" @click="logout">退出</button></div></header>
      <div class="toolbar"><div class="muted">数据更新时间以服务器响应为准 · API: {{page.path}}</div><div class="toolbar-actions"><button class="secondary" @click="seed">初始化演示数据</button><button class="primary" @click="load">↻ 刷新数据</button></div></div>
      <div v-if="error" class="banner error">{{error}}</div><div v-if="notice" class="banner success">{{notice}}</div>
      <section v-if="['apsjobs','apsgenerate','apsruns','aiinsights','maintpred','maintthreshold','traceability'].includes(active)" class="banner">V4.0 模块：启发式 APS 与可解释规则分析。阈值需由工艺/设备负责人校准；排程草案必须人工审核后审批。</section>
      <section v-if="active==='overview' || active==='analysis'" class="metric-grid">
        <article class="metric"><small>计划数量</small><strong>{{metrics.planned_qty??'—'}}</strong><span>当前工单计划总量</span></article>
        <article class="metric"><small>累计合格</small><strong>{{metrics.good_qty??'—'}}</strong><span>生产报工合格数量</span></article>
        <article class="metric"><small>生产良率</small><strong>{{metrics.yield_pct??'—'}}<i>%</i></strong><span>合格数 / 已报工数</span></article>
        <article class="metric"><small>工单完成率</small><strong>{{metrics.completion_pct??'—'}}<i>%</i></strong><span>已报工数 / 计划数</span></article>
        <article class="metric"><small>未关闭 NCR</small><strong>{{metrics.open_ncr??'—'}}</strong><span>待质量处置</span></article>
        <article class="metric"><small>人工工时</small><strong>{{metrics.labor_minutes??'—'}}</strong><span>分钟 · 以报工数据统计</span></article>
      </section>
      <section v-if="active==='monitor' && rows?.machines" class="panel"><div class="panel-head"><div><h2>设备实时状态</h2><p>每15秒刷新；在线判定：90秒内收到有效采集数据</p></div><span class="tag">{{rows.online_machines}} / {{rows.total_machines}} ONLINE</span></div><div class="machine-grid"><article v-for="m in rows.machines" :key="m.machine_id" class="machine-card"><div class="machine-card-head"><b>{{m.machine_code}}</b><span :class="['state-pill',m.online?'is-online':'is-offline']">{{m.online?'在线':'离线'}}</span></div><strong>{{m.state}}</strong><small>{{m.name}} · {{m.controller}}</small><div class="machine-meta"><span>未关闭报警：{{m.open_alarms}}</span><span>{{m.latest_point||'暂无点位'}}：{{m.latest_value??'—'}}</span></div><small>最后数据：{{m.last_seen_at||'尚未接入'}}</small></article></div></section>
      <section v-if="active==='apsruns'" class="panel">
        <div class="panel-head"><div><h2>排程草案审核</h2><p>先生成 APS 批次，再载入该批次的设备分配；审批时服务端会二次检查冲突。</p></div></div>
        <div class="inline-filter"><label>运行批次 ID</label><input v-model="apsRunId" type="number" min="1"/><button class="secondary" @click="loadAPSAssignments">载入分配</button></div>
        <div v-if="apsAssignments.length" class="table-wrap"><table><thead><tr><th>任务编号</th><th>设备</th><th>开始</th><th>结束</th><th>分钟</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="a in apsAssignments" :key="a.id"><td>{{a.job_no}}</td><td>{{a.machine_code}}</td><td>{{a.start_at}}</td><td>{{a.end_at}}</td><td>{{a.planned_minutes}}</td><td>{{a.status}}</td><td><button v-if="a.status==='draft'" class="tiny primary" @click="approveAPSAssignment(a.id)">审批</button> <button v-if="a.status==='draft'" class="tiny danger" @click="rejectAPSAssignment(a.id)">驳回</button></td></tr></tbody></table></div>
        <div v-else class="empty">选择批次 ID 并载入分配记录</div>
      </section>
      <section v-if="active==='traceability'" class="panel">
        <div class="panel-head"><div><h2>追溯查询</h2><p>可按工单编号对应的数据库 ID，或材料批次号查询事件。</p></div></div>
        <div class="inline-filter"><label>查询类型</label><select v-model="traceMode"><option value="work_order">工单 ID</option><option value="material_lot">材料批次</option></select><input v-model="traceKey"/><button class="secondary" @click="searchTraceability">查询事件</button></div>
        <pre v-if="traceRows.length">{{JSON.stringify(traceRows,null,2)}}</pre><div v-else class="empty">输入工单 ID 或材料批次后查询</div>
      </section>
      <section v-if="active==='report'" class="panel form-panel">
        <div class="panel-head"><div><h2>生产报工</h2><p>按工单/工序提交合格数、不良数与人工工时；提交会记入审计</p></div></div>
        <div v-if="!refData.orders.length" class="empty">暂无工单，请先在“生产工单”页创建工单与工序</div>
        <template v-else>
        <div class="form-grid">
          <label>工单<select v-model="repForm.work_order_id"><option value="">选择工单</option><option v-for="w in refData.orders" :key="w.id" :value="w.id">{{w.order_no}} · {{w.product_name}}</option></select></label>
          <label>工序<select v-model="repForm.operation_id"><option value="">选择工序</option><option v-for="op in woOptions(repForm.work_order_id)" :key="op.id" :value="op.id">{{op.sequence}} {{op.operation_code}} {{op.operation_name}}</option></select></label>
          <label>设备<select v-model="repForm.machine_id"><option value="">未指定</option><option v-for="mc in refData.machines" :key="mc.id" :value="mc.id">{{mc.code}}（{{mc.name}}）</option></select></label>
          <label>报工人<select v-model="repForm.employee_code"><option value="">选择员工</option><option v-for="e in refData.employees" :key="e.employee_code" :value="e.employee_code">{{e.employee_code}} · {{e.full_name}}</option></select></label>
          <label>合格数量<input type="number" min="0" v-model.number="repForm.good_qty"/></label>
          <label>不良数量<input type="number" min="0" v-model.number="repForm.scrap_qty"/></label>
          <label>人工工时（分钟）<input type="number" min="0" v-model.number="repForm.labor_minutes"/></label>
        </div>
        <div class="form-actions"><button class="primary" :disabled="!repForm.work_order_id||!repForm.operation_id||!repForm.employee_code" @click="submitReport">提交报工</button></div>
        </template>
      </section>
      <section v-if="active==='orders'" class="panel form-panel">
        <div class="panel-head"><div><h2>新建工单</h2><p>创建生产工单与工序；报工需要已存在的工单/工序/员工/设备</p></div></div>
        <div class="form-grid">
          <label>工单号<input v-model="woForm.order_no" placeholder="MO-2026-0002"/></label>
          <label>产品编码<input v-model="woForm.product_code" placeholder="PART-001"/></label>
          <label>产品名称<input v-model="woForm.product_name" placeholder="精密铝合金支架"/></label>
          <label>计划数量<input type="number" min="1" v-model.number="woForm.planned_qty"/></label>
        </div>
        <div class="form-actions"><button class="primary" :disabled="!woForm.order_no||!woForm.product_code||!woForm.product_name" @click="submitWorkOrder">创建工单</button></div>
        <div class="panel-head form-sub"><div><h2>添加工序</h2><p>为已有工单追加/修正工序步骤</p></div></div>
        <div class="form-grid">
          <label>工单 ID<input type="number" min="1" v-model.number="opForm.work_order_id"/></label>
          <label>顺序号<input type="number" min="1" step="10" v-model.number="opForm.sequence"/></label>
          <label>工序编码<input v-model="opForm.operation_code" placeholder="OP10"/></label>
          <label>工序名称<input v-model="opForm.operation_name" placeholder="CNC粗铣"/></label>
          <label>指定设备<select v-model="opForm.machine_id"><option value="">不指定</option><option v-for="mc in refData.machines" :key="mc.id" :value="mc.id">{{mc.code}}</option></select></label>
          <label>标准工时（分钟）<input type="number" min="0" v-model.number="opForm.standard_minutes"/></label>
        </div>
        <div class="form-actions"><button class="primary" :disabled="!opForm.work_order_id||!opForm.operation_code||!opForm.operation_name" @click="submitOperation">添加工序</button></div>
      </section>
      <section v-if="active==='apsjobs'" class="panel form-panel">
        <div class="panel-head"><div><h2>新建 APS 排程任务</h2><p>任务进入排队池后，在“APS智能排程计算”中生成草案并人工审批</p></div></div>
        <div v-if="!refData.orders.length" class="empty">暂无工单，请先创建工单与工序</div>
        <template v-else>
        <div class="form-grid">
          <label>任务编号<input v-model="apsForm.job_no" placeholder="APS-JOB-002"/></label>
          <label>工单<select v-model="apsForm.work_order_id"><option value="">选择工单</option><option v-for="w in refData.orders" :key="w.id" :value="w.id">{{w.order_no}} · {{w.product_name}}</option></select></label>
          <label>工序<select v-model="apsForm.operation_id"><option value="">选择工序</option><option v-for="op in woOptions(apsForm.work_order_id)" :key="op.id" :value="op.id">{{op.operation_code}} {{op.operation_name}}</option></select></label>
          <label>优先级（1-10）<input type="number" min="1" max="10" v-model.number="apsForm.priority"/></label>
          <label>交期（可选）<input type="datetime-local" v-model="apsForm.due_at"/></label>
          <label>所需分钟<input type="number" min="1" v-model.number="apsForm.required_minutes"/></label>
        </div>
        <div class="form-grid"><label>可排设备（勾选合格设备）</label>
          <div class="check-row"><label v-for="mc in refData.machines" :key="mc.code" class="check-item"><input type="checkbox" :value="mc.code" v-model="apsForm.machines"/> {{mc.code}}（{{mc.name}}）</label></div>
        </div>
        <div class="form-actions"><button class="primary" :disabled="!apsForm.job_no||!apsForm.work_order_id||!apsForm.operation_id||!apsForm.machines.length" @click="submitApsJob">加入排程队列</button></div>
        </template>
      </section>
      <section v-if="active==='oee'" class="panel inline-filter"><label>设备 ID</label><input v-model="oeeMachineId" type="number" min="1"/><button class="secondary" @click="load">计算 OEE</button></section>
      <section v-if="active==='oee' && rows?.machine_code" class="metric-grid"><article class="metric"><small>综合设备效率 OEE</small><strong>{{rows.oee_pct??'—'}}<i>%</i></strong><span>{{rows.data_complete?'采集项齐全，班次口径待校准':'采集数据尚不完整'}}</span></article><article class="metric"><small>时间开动率</small><strong>{{rows.availability_pct}}<i>%</i></strong><span>运行时间 / 计划生产时间</span></article><article class="metric"><small>性能效率</small><strong>{{rows.performance_pct??'—'}}<i>%</i></strong><span>理想节拍 × 总产量 / 运行时间</span></article><article class="metric"><small>质量合格率</small><strong>{{rows.quality_pct??'—'}}<i>%</i></strong><span>合格计数 / 总计数</span></article><article class="metric"><small>合格数量</small><strong>{{rows.good_qty}}</strong><span>累计计数器窗口增量</span></article><article class="metric"><small>不合格数量</small><strong>{{rows.reject_qty}}</strong><span>累计计数器窗口增量</span></article></section>
      <section v-if="active==='planning' && metrics.capacity" class="panel"><div class="panel-head"><div><h2>设备负荷概览</h2><p>统计窗口 {{metrics.capacity.days}} 天；仅汇总已审批/执行中排程</p></div></div><div class="capacity-grid"><div v-for="m in metrics.capacity.machines" :key="m.machine_id" class="capacity"><b>{{m.machine_code}}</b><strong>{{m.load_pct}}%</strong><div class="bar"><i :style="{width:Math.min(m.load_pct,100)+'%'}"></i></div><small>{{m.booked_minutes}} 分钟已排程 · {{m.status}}</small></div></div></section>
      <section v-if="active==='routing'" class="panel inline-filter"><label>产品编码</label><input v-model="routeCode" @keyup.enter="load"/><button class="secondary" @click="load">查询工艺</button></section>
      <section v-if="active!=='report'" class="panel data-panel"><div class="panel-head"><div><h2>{{page.label}}数据</h2><p>接口返回记录：{{isArray?rows?.length:(rows?'1':'0')}}</p></div><span class="tag">{{page.method}}</span></div>
        <div v-if="isArray && rows?.length" class="table-wrap"><table><thead><tr><th v-for="col in columns" :key="col">{{col}}</th><th v-if="active==='planning'||active==='ncr'||active==='alarms3'">操作</th></tr></thead><tbody><tr v-for="(r,i) in rows" :key="r.id??i"><td v-for="col in columns" :key="col">{{typeof r[col]==='object' && r[col]!==null?JSON.stringify(r[col]):r[col]}}</td><td v-if="active==='planning'"><button v-if="r.status==='draft'" class="tiny primary" @click="approve(r.id)">审批</button></td><td v-if="active==='alarms3'"><button v-if="r.status==='open'" class="tiny secondary" @click="acknowledgeAlarm(r.id)">确认</button></td><td v-if="active==='ncr'"><button v-if="r.status==='open'" class="tiny secondary" @click="disposeNcr(r.id,'rework')">返工</button> <button v-if="r.status==='open'" class="tiny danger" @click="disposeNcr(r.id,'scrap')">报废</button></td></tr></tbody></table></div>
        <pre v-else-if="rows!==null">{{JSON.stringify(rows,null,2)}}</pre><div v-else class="empty">暂无数据或正在加载</div>
      </section>
      <section v-if="page.post" class="panel editor-panel"><div class="panel-head"><div><h2>快速录入 / API 操作</h2><p>按接口数据结构提交 JSON；写操作会记录在操作审计中</p></div></div><textarea v-model="payload" spellcheck="false"></textarea><div class="editor-actions"><button class="secondary" @click="example">载入示例</button><button class="primary" @click="submit">提交数据</button></div><pre v-if="postResult">{{JSON.stringify(postResult,null,2)}}</pre></section>
      <footer>© CNC-MES Smart Factory V4.0 · 内网私有部署 · 设备数据只读采集</footer>
    </main>
  </div>
</template>
