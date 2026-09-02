"""
Workflow Engine API Router

RESTful API for workflow CRUD and execution.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from api.deps import get_context, verify_token

logger = logging.getLogger("echoserve.api.workflow")

router = APIRouter(prefix="/workflow", tags=["Workflow Engine"])


def _get_workflow_service(ctx=Depends(get_context)):
    service = _get_workflow_service()
    return service


# ─── Pydantic Models ────────────────────────────────

class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    variables: dict[str, Any] = {}


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    variables: dict[str, Any] | None = None
    status: str | None = None


class WorkflowExecute(BaseModel):
    session_id: str = Field(..., min_length=1)
    user_id: str = ""
    channel: str = "web"
    variables: dict[str, Any] = {}


class WorkflowResume(BaseModel):
    execution_id: str = Field(..., min_length=1)
    user_input: str = Field(..., min_length=1)


# ─── CRUD Endpoints ─────────────────────────────────

@router.post("/", response_model=dict)
async def create_workflow(
    data: WorkflowCreate,
    service = Depends(_get_workflow_service),
    current_user: dict = Depends(verify_token),
):
    """Create a new workflow (draft status)"""
    try:
        wf = service.create_workflow(
            name=data.name,
            description=data.description,
            nodes=data.nodes,
            edges=data.edges,
            variables=data.variables,
            created_by=current_user.get("username", "system"),
        )
        return {"workflow_id": wf.workflow_id, "status": "created", "version": wf.version}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", response_model=dict)
async def list_workflows(
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    service = Depends(_get_workflow_service),
    current_user: dict = Depends(verify_token),
):
    """List workflows with optional filtering"""

    workflows, total = service.list_workflows(
        status=status, keyword=keyword, offset=offset, limit=limit
    )
    return {
        "items": [wf.to_dict() for wf in workflows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{workflow_id}", response_model=dict)
async def get_workflow(
    workflow_id: str,
    current_user: dict = Depends(verify_token),
):
    """Get workflow by ID"""
    service = _get_workflow_service()

    wf = service.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return wf.to_dict()


@router.put("/{workflow_id}", response_model=dict)
async def update_workflow(
    workflow_id: str,
    data: WorkflowUpdate,
    current_user: dict = Depends(verify_token),
):
    """Update workflow (auto increments version)"""
    service = _get_workflow_service()

    try:
        kwargs = data.model_dump(exclude_unset=True)
        wf = service.update_workflow(workflow_id, **kwargs)
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return wf.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{workflow_id}/publish", response_model=dict)
async def publish_workflow(
    workflow_id: str,
    current_user: dict = Depends(verify_token),
):
    """Publish workflow (draft -> active)"""
    service = _get_workflow_service()

    wf = service.publish_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow_id": wf.workflow_id, "status": wf.status.value, "version": wf.version}


@router.post("/{workflow_id}/duplicate", response_model=dict)
async def duplicate_workflow(
    workflow_id: str,
    new_name: str = "",
    current_user: dict = Depends(verify_token),
):
    """Duplicate a workflow"""
    service = _get_workflow_service()

    new_wf = service.duplicate_workflow(workflow_id, new_name)
    if not new_wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow_id": new_wf.workflow_id, "name": new_wf.name, "status": new_wf.status.value}


@router.delete("/{workflow_id}", response_model=dict)
async def delete_workflow(
    workflow_id: str,
    current_user: dict = Depends(verify_token),
):
    """Delete workflow (templates cannot be deleted)"""
    service = _get_workflow_service()

    success = service.delete_workflow(workflow_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot delete workflow (may be a template)")
    return {"workflow_id": workflow_id, "deleted": True}


# ─── Execution Endpoints ────────────────────────────

@router.post("/{workflow_id}/execute", response_model=dict)
async def execute_workflow(
    workflow_id: str,
    data: WorkflowExecute,
    current_user: dict = Depends(verify_token),
):
    """Execute a workflow synchronously"""
    service = _get_workflow_service()

    try:
        result = await service.execute_workflow(
            workflow_id=workflow_id,
            session_id=data.session_id,
            user_id=data.user_id,
            channel=data.channel,
            variables=data.variables,
        )
        return result.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Workflow execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")


@router.post("/resume", response_model=dict)
async def resume_workflow(
    data: WorkflowResume,
    current_user: dict = Depends(verify_token),
):
    """Resume a workflow from WAITING status"""
    service = _get_workflow_service()

    try:
        result = await service.resume_workflow(
            execution_id=data.execution_id,
            user_input=data.user_input,
        )
        return result.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Workflow resume failed: {e}")
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(e)}")


@router.get("/execution/{execution_id}", response_model=dict)
async def get_execution(
    execution_id: str,
    current_user: dict = Depends(verify_token),
):
    """Get execution record by ID"""
    service = _get_workflow_service()

    record = service.get_execution(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="Execution not found")
    return record


# ─── Templates ──────────────────────────────────────

@router.get("/templates", response_model=dict)
async def list_templates(
    current_user: dict = Depends(verify_token),
):
    """List preset workflow templates"""
    service = _get_workflow_service()

    templates, total = service.list_workflows(is_template=True)
    return {
        "items": [wf.to_dict() for wf in templates],
        "total": total,
    }
