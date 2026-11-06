from datetime import datetime
from sqlalchemy import String,Integer,Float,DateTime,ForeignKey,Text,Boolean
from sqlalchemy.orm import Mapped,mapped_column,relationship
from .db import Base
class Machine(Base):
 __tablename__="machines"
 id:Mapped[int]=mapped_column(primary_key=True)
 code:Mapped[str]=mapped_column(String(40),unique=True,index=True)
 name:Mapped[str]=mapped_column(String(120))
 controller:Mapped[str]=mapped_column(String(80),default="unknown")
 status:Mapped[str]=mapped_column(String(30),default="unknown")
 enabled:Mapped[bool]=mapped_column(Boolean,default=True)
class WorkOrder(Base):
 __tablename__="work_orders"
 id:Mapped[int]=mapped_column(primary_key=True)
 order_no:Mapped[str]=mapped_column(String(50),unique=True,index=True)
 product_code:Mapped[str]=mapped_column(String(80))
 product_name:Mapped[str]=mapped_column(String(160))
 drawing_revision:Mapped[str]=mapped_column(String(20),default="A")
 planned_qty:Mapped[int]=mapped_column(Integer)
 good_qty:Mapped[int]=mapped_column(Integer,default=0)
 scrap_qty:Mapped[int]=mapped_column(Integer,default=0)
 status:Mapped[str]=mapped_column(String(30),default="released")
 created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
 operations:Mapped[list["Operation"]]=relationship(back_populates="order",cascade="all,delete-orphan")
class Operation(Base):
 __tablename__="operations"
 id:Mapped[int]=mapped_column(primary_key=True)
 work_order_id:Mapped[int]=mapped_column(ForeignKey("work_orders.id"))
 sequence:Mapped[int]=mapped_column(Integer)
 operation_code:Mapped[str]=mapped_column(String(40))
 operation_name:Mapped[str]=mapped_column(String(120))
 machine_id:Mapped[int|None]=mapped_column(ForeignKey("machines.id"),nullable=True)
 standard_minutes:Mapped[float]=mapped_column(Float,default=0)
 status:Mapped[str]=mapped_column(String(30),default="pending")
 order:Mapped[WorkOrder]=relationship(back_populates="operations")
class Report(Base):
 __tablename__="production_reports"
 id:Mapped[int]=mapped_column(primary_key=True)
 work_order_id:Mapped[int]=mapped_column(ForeignKey("work_orders.id"))
 operation_id:Mapped[int]=mapped_column(ForeignKey("operations.id"))
 machine_id:Mapped[int|None]=mapped_column(ForeignKey("machines.id"),nullable=True)
 employee_code:Mapped[str]=mapped_column(String(40))
 good_qty:Mapped[int]=mapped_column(Integer)
 scrap_qty:Mapped[int]=mapped_column(Integer)
 labor_minutes:Mapped[int]=mapped_column(Integer)
 created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
class Inspection(Base):
 __tablename__="quality_inspections"
 id:Mapped[int]=mapped_column(primary_key=True)
 work_order_id:Mapped[int]=mapped_column(ForeignKey("work_orders.id"))
 inspection_type:Mapped[str]=mapped_column(String(30))
 inspector_code:Mapped[str]=mapped_column(String(40))
 sample_qty:Mapped[int]=mapped_column(Integer)
 pass_qty:Mapped[int]=mapped_column(Integer)
 fail_qty:Mapped[int]=mapped_column(Integer)
 characteristic:Mapped[str]=mapped_column(String(100),default="")
 measured_value:Mapped[float|None]=mapped_column(Float,nullable=True)
 spec_min:Mapped[float|None]=mapped_column(Float,nullable=True)
 spec_max:Mapped[float|None]=mapped_column(Float,nullable=True)
 disposition:Mapped[str]=mapped_column(String(30),default="pending")
class Tool(Base):
 __tablename__="tools"
 id:Mapped[int]=mapped_column(primary_key=True)
 tool_code:Mapped[str]=mapped_column(String(50),unique=True)
 tool_name:Mapped[str]=mapped_column(String(120))
 tool_type:Mapped[str]=mapped_column(String(60))
 max_life_minutes:Mapped[int]=mapped_column(Integer)
 used_life_minutes:Mapped[int]=mapped_column(Integer,default=0)
 status:Mapped[str]=mapped_column(String(30),default="available")
class Material(Base):
 __tablename__="materials"
 id:Mapped[int]=mapped_column(primary_key=True)
 material_code:Mapped[str]=mapped_column(String(60),unique=True)
 material_name:Mapped[str]=mapped_column(String(160))
 lot_no:Mapped[str]=mapped_column(String(60),default="")
 unit:Mapped[str]=mapped_column(String(20),default="pcs")
 qty_on_hand:Mapped[float]=mapped_column(Float,default=0)
 safety_stock:Mapped[float]=mapped_column(Float,default=0)
class InventoryTxn(Base):
 __tablename__="inventory_transactions"
 id:Mapped[int]=mapped_column(primary_key=True)
 material_id:Mapped[int]=mapped_column(ForeignKey("materials.id"))
 txn_type:Mapped[str]=mapped_column(String(20))
 qty:Mapped[float]=mapped_column(Float)
 reference_no:Mapped[str]=mapped_column(String(60))
 operator_code:Mapped[str]=mapped_column(String(40))
 created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
class MachineEvent(Base):
 __tablename__="machine_events"
 id:Mapped[int]=mapped_column(primary_key=True)
 machine_id:Mapped[int]=mapped_column(ForeignKey("machines.id"))
 event_type:Mapped[str]=mapped_column(String(30))
 event_value:Mapped[str]=mapped_column(String(120))
 source:Mapped[str]=mapped_column(String(40),default="manual")
 event_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
class Alert(Base):
 __tablename__="alerts"
 id:Mapped[int]=mapped_column(primary_key=True)
 severity:Mapped[str]=mapped_column(String(20))
 category:Mapped[str]=mapped_column(String(40))
 title:Mapped[str]=mapped_column(String(160))
 detail:Mapped[str]=mapped_column(Text)
 status:Mapped[str]=mapped_column(String(20),default="open")
class PieceRate(Base):
 __tablename__="piece_rates"
 id:Mapped[int]=mapped_column(primary_key=True)
 operation_code:Mapped[str]=mapped_column(String(40))
 grade:Mapped[str]=mapped_column(String(20),default="A")
 rate:Mapped[float]=mapped_column(Float)
