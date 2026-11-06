# CNC APS Planner — V4 Heuristic Baseline

## Objective
Create auditable draft schedules from approved APS jobs and explicitly qualified machine lists.

## Required preconditions
- Work order and operation exist and are linked.
- Required minutes are reviewed by production planning.
- Eligible machine codes are confirmed by manufacturing/process engineering.
- `PLANT_TIMEZONE` is set to the plant's IANA timezone.

## Current objective function
Priority and due date rank work-order groups; within each work order, operation sequence is preserved. Each task is assigned to the eligible machine with the earliest feasible finish under the configured workday/shift/break calendar and known approved schedule occupancy.

## Human approval gate
Every result is a draft. The authorized planner reviews machine suitability, fixture/tool/material/personnel availability, setup assumptions, delivery impact and maintenance windows. Approval rechecks machine time overlap before writing a V2 `SchedulePlan`.

## Prohibited actions
No automatic schedule approval, no CNC program download, no machine start/stop/reset, no PLC write, no parameter/tool-offset modification.

## Known constraints not yet modeled
Alternative routings, sequence-dependent setup, fixture/holder compatibility, tool availability/life, operator skill matrices, material kitting, subcontract operations, maintenance calendars, multi-shift and holiday exceptions, finite buffer/WIP constraints, and optimality proof.
