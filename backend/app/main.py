from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel,Field
from .db import Base,engine,get_db,Session
from . import models as m
from . import models_v2 as m2
from . import models_v3 as m3
from . import models_v4 as m4
from .auth import hash_password, decode_token
from .api_v2 import router as router_v2
from .api_v3 import router as router_v3
from .api_v4 import router as router_v4
import os
if os.getenv("AUTO_CREATE_SCHEMA", "false").lower() == "true":
    Base.metadata.create_all(bind=engine)
app=FastAPI(title="CNC-MES Smart Factory API",version="4.0.0")
app.include_router(router_v2)
app.include_router(router_v3)
app.include_router(router_v4)
# Bootstrap administrator once. Replace MES_ADMIN_PASSWORD before first production start.
with Session() as _db:
    if not _db.query(m2.User).filter_by(username=os.getenv("MES_ADMIN_USER", "admin")).first():
        _password = os.getenv("MES_ADMIN_PASSWORD", "")
        if len(_password) >= 12:
            _db.add(m2.User(username=os.getenv("MES_ADMIN_USER", "admin"), password_hash=hash_password(_password), role="admin"))
            _db.commit()

@app.middleware("http")
async def protect_legacy_v1(request: Request, call_next):
    # V1 compatibility routes are also protected; V2 login endpoint remains public.
    if request.url.path.startswith("/api/v1/") and os.getenv("V1_AUTH_REQUIRED", "true").lower() == "true":
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"detail": "需要 Bearer Token"})
        try:
            payload = decode_token(auth.split(" ", 1)[1])
            with Session() as _db:
                _user = _db.query(m2.User).filter_by(username=payload.get("sub"), active=True).first()
                if not _user:
                    return JSONResponse(status_code=401, content={"detail": "用户不存在或已停用"})
                # V1 遗留端点统一要求业务角色：只读/网关角色不可执行变更操作
                if _user.role in {"viewer", "gateway"} and request.method in {"POST", "PUT", "DELETE", "PATCH"}:
                    return JSONResponse(status_code=403, content={"detail": "当前角色无权执行此操作"})
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "登录凭证无效或已过期"})
    return await call_next(request)
app.add_middleware(CORSMiddleware,allow_origins=os.getenv("CORS_ORIGINS","http://localhost:5173").split(","),allow_methods=["*"],allow_headers=["*"])
class MachineIn(BaseModel):
 code:str; name:str; controller:str="unknown"; status:str="unknown"
class OrderIn(BaseModel):
 order_no:str; product_code:str; product_name:str; drawing_revision:str="A"; planned_qty:int=Field(gt=0)
class OperationIn(BaseModel):
 sequence:int; operation_code:str; operation_name:str; machine_id:int|None=None; standard_minutes:float=0
class ReportIn(BaseModel):
 work_order_id:int; operation_id:int; machine_id:int|None=None; employee_code:str
 good_qty:int=Field(ge=0); scrap_qty:int=Field(ge=0); labor_minutes:int=Field(ge=0)
class InspectIn(BaseModel):
 work_order_id:int; inspection_type:str; inspector_code:str; sample_qty:int=Field(gt=0)
 pass_qty:int=Field(ge=0); fail_qty:int=Field(ge=0); characteristic:str=""
 measured_value:float|None=None; spec_min:float|None=None; spec_max:float|None=None
class ToolIn(BaseModel):
 tool_code:str; tool_name:str; tool_type:str; max_life_minutes:int=Field(gt=0)
class MaterialIn(BaseModel):
 material_code:str; material_name:str; lot_no:str=""; unit:str="pcs"; qty_on_hand:float=0; safety_stock:float=0
class IssueIn(BaseModel):
 material_id:int; qty:float=Field(gt=0); reference_no:str; operator_code:str
@app.get("/health")
def health(): return {"status":"ok"}
@app.get("/api/v1/dashboard")
def dashboard(db:Session=Depends(get_db)):
 return {"work_orders_total":db.query(m.WorkOrder).count(),"work_orders_open":db.query(m.WorkOrder).filter(m.WorkOrder.status!="closed").count(),"machines_total":db.query(m.Machine).count(),"machines_running":db.query(m.Machine).filter(m.Machine.status=="running").count(),"good_qty_total":db.query(func.coalesce(func.sum(m.WorkOrder.good_qty),0)).scalar(),"alerts_open":db.query(m.Alert).filter(m.Alert.status=="open").count()}
@app.get("/api/v1/machines")
def machines(db:Session=Depends(get_db)): return db.query(m.Machine).all()
@app.post("/api/v1/machines")
def add_machine(x:MachineIn,db:Session=Depends(get_db)):
 if db.query(m.Machine).filter_by(code=x.code).first(): raise HTTPException(409,"设备编号重复")
 o=m.Machine(**x.model_dump());db.add(o);db.commit();db.refresh(o);return o
@app.post("/api/v1/machines/events")
def machine_event(machine_id:int,event_type:str,event_value:str,source:str="gateway",db:Session=Depends(get_db)):
 o=db.get(m.Machine,machine_id)
 if not o: raise HTTPException(404,"设备不存在")
 db.add(m.MachineEvent(machine_id=machine_id,event_type=event_type,event_value=event_value,source=source))
 if event_type=="status": o.status=event_value
 db.commit();return {"accepted":True}
@app.get("/api/v1/work-orders")
def orders(db:Session=Depends(get_db)): return db.query(m.WorkOrder).order_by(m.WorkOrder.created_at.desc()).all()
@app.post("/api/v1/work-orders")
def add_order(x:OrderIn,db:Session=Depends(get_db)):
 if db.query(m.WorkOrder).filter_by(order_no=x.order_no).first(): raise HTTPException(409,"工单号重复")
 o=m.WorkOrder(**x.model_dump());db.add(o);db.commit();db.refresh(o);return o
@app.get("/api/v1/work-orders/{wid}")
def get_order(wid:int,db:Session=Depends(get_db)):
  o=db.get(m.WorkOrder,wid)
  if not o: raise HTTPException(404,"工单不存在")
  ops=db.query(m.Operation).filter_by(work_order_id=wid).order_by(m.Operation.sequence).all()
  return {"work_order":o,"operations":ops}
@app.post("/api/v1/work-orders/{wid}/operations")
def add_operation(wid:int,x:OperationIn,db:Session=Depends(get_db)):
 if not db.get(m.WorkOrder,wid): raise HTTPException(404,"工单不存在")
 o=m.Operation(work_order_id=wid,**x.model_dump());db.add(o);db.commit();db.refresh(o);return {"id":o.id,"status":o.status}
@app.post("/api/v1/production/reports")
def report(x:ReportIn,db:Session=Depends(get_db)):
 # 行锁串行化同一工单/工序的并发报工，防止 good_qty/scrap_qty 丢失更新
 #（SQLite 上为无操作，PostgreSQL 上生效）
 w=db.get(m.WorkOrder,x.work_order_id,with_for_update=True);op=db.get(m.Operation,x.operation_id,with_for_update=True)
 if not w or not op or op.work_order_id!=w.id: raise HTTPException(400,"工单/工序不匹配")
 if w.good_qty+w.scrap_qty+x.good_qty+x.scrap_qty>w.planned_qty: raise HTTPException(400,"报工数量超过计划数量")
 db.add(m.Report(**x.model_dump()));w.good_qty+=x.good_qty;w.scrap_qty+=x.scrap_qty
 if w.good_qty+w.scrap_qty==w.planned_qty:w.status="reported_complete"
 db.commit();return {"accepted":True,"good_qty":w.good_qty,"scrap_qty":w.scrap_qty,"status":w.status}
@app.get("/api/v1/quality/inspections")
def inspections(db:Session=Depends(get_db)): return db.query(m.Inspection).all()
@app.post("/api/v1/quality/inspections")
def inspect(x:InspectIn,db:Session=Depends(get_db)):
 if x.pass_qty+x.fail_qty>x.sample_qty: raise HTTPException(400,"检验数量超出抽样数量")
 if not db.get(m.WorkOrder,x.work_order_id): raise HTTPException(404,"工单不存在")
 state="pass" if x.fail_qty==0 else "hold"
 o=m.Inspection(**x.model_dump(),disposition=state);db.add(o)
 if x.fail_qty: db.add(m.Alert(severity="high",category="quality",title="质量异常",detail=f"工单 {x.work_order_id} 不合格 {x.fail_qty} 件"))
 db.commit();return {"id":o.id,"disposition":state}
@app.get("/api/v1/tools")
def tools_list(db:Session=Depends(get_db)): return db.query(m.Tool).all()
@app.post("/api/v1/tools")
def add_tool(x:ToolIn,db:Session=Depends(get_db)):
 o=m.Tool(**x.model_dump());db.add(o);db.commit();db.refresh(o);return o
@app.post("/api/v1/tools/{tid}/consume")
def tool_consume(tid:int,minutes:int,db:Session=Depends(get_db)):
 o=db.get(m.Tool,tid,with_for_update=True) # 锁住刀具行，防止并发消耗导致寿命计算错乱
 if not o: raise HTTPException(404,"刀具不存在")
 o.used_life_minutes+=minutes;left=o.max_life_minutes-o.used_life_minutes
 if left<=0:
  o.status="replace";db.add(m.Alert(severity="high",category="tool",title="刀具寿命到期",detail=o.tool_code))
 elif left<=o.max_life_minutes*.15: db.add(m.Alert(severity="medium",category="tool",title="刀具寿命预警",detail=f"{o.tool_code} 剩余 {left} 分钟"))
 db.commit();return {"remaining_minutes":max(0,left),"status":o.status}
@app.get("/api/v1/materials")
def materials(db:Session=Depends(get_db)): return db.query(m.Material).all()
@app.post("/api/v1/materials")
def add_material(x:MaterialIn,db:Session=Depends(get_db)):
 o=m.Material(**x.model_dump());db.add(o);db.commit();db.refresh(o);return o
@app.post("/api/v1/inventory/issue")
def issue(x:IssueIn,db:Session=Depends(get_db)):
 o=db.get(m.Material,x.material_id,with_for_update=True) # 锁住物料行，防止并发出库超发
 if not o: raise HTTPException(404,"物料不存在")
 if o.qty_on_hand<x.qty: raise HTTPException(409,"库存不足")
 o.qty_on_hand-=x.qty;db.add(m.InventoryTxn(material_id=o.id,txn_type="issue",qty=x.qty,reference_no=x.reference_no,operator_code=x.operator_code));db.commit()
 return {"accepted":True,"remaining_qty":o.qty_on_hand}
@app.get("/api/v1/planning/suggestions")
def planning(db:Session=Depends(get_db)):
 ws=db.query(m.WorkOrder).filter(m.WorkOrder.status.in_(["released","planned"])).order_by(m.WorkOrder.created_at).all()
 ms=db.query(m.Machine).filter(m.Machine.enabled==True,m.Machine.status.notin_(["fault","maintenance"])).all()
 return {"method":"FIFO MVP heuristic","requires_planner_approval":True,"suggestions":[{"work_order":w.order_no,"machine":ms[0].code if ms else None,"reason":"按创建时间排序，需计划员确认"} for w in ws]}
@app.get("/api/v1/alerts")
def alerts(db:Session=Depends(get_db)): return db.query(m.Alert).order_by(m.Alert.id.desc()).all()
@app.post("/api/v1/payroll/piecework-preview")
def payroll(operation_code:str,good_qty:int,grade:str="A",db:Session=Depends(get_db)):
 r=db.query(m.PieceRate).filter_by(operation_code=operation_code,grade=grade).first()
 if not r: raise HTTPException(404,"未配置工价")
 return {"good_qty":good_qty,"rate":r.rate,"estimated_amount":round(good_qty*r.rate,2),"warning":"仅供试算，需审批"}
@app.post("/api/v1/erp/inbound/work-order")
def erp_inbound(x:OrderIn,db:Session=Depends(get_db)):
 old=db.query(m.WorkOrder).filter_by(order_no=x.order_no).first()
 if old:return {"accepted":True,"duplicate":True,"id":old.id}
 o=m.WorkOrder(**x.model_dump());db.add(o);db.commit();db.refresh(o);return {"accepted":True,"id":o.id}
@app.post("/api/v1/demo/seed")
def seed(db:Session=Depends(get_db)):
 if not db.query(m.Machine).count(): db.add_all([m.Machine(code="CNC-01",name="立式加工中心01",controller="FANUC",status="running"),m.Machine(code="CNC-02",name="数控车床02",controller="Siemens",status="idle")])
 if not db.query(m.WorkOrder).count():
  w=m.WorkOrder(order_no="MO-2026-0001",product_code="PART-001",product_name="精密铝合金支架",planned_qty=100);db.add(w);db.flush()
  db.add(m.Operation(work_order_id=w.id,sequence=10,operation_code="OP10",operation_name="CNC铣削",standard_minutes=4.5))
 if not db.query(m.Tool).count():db.add(m.Tool(tool_code="T-001",tool_name="硬质合金立铣刀",tool_type="铣刀",max_life_minutes=600,used_life_minutes=520))
 if not db.query(m.Material).count():db.add(m.Material(material_code="AL6061-001",material_name="铝合金6061棒料",lot_no="LOT-01",unit="kg",qty_on_hand=250,safety_stock=50))
 if not db.query(m.PieceRate).count():db.add(m.PieceRate(operation_code="OP10",grade="A",rate=2.5))
 db.commit();return {"seeded":True}
