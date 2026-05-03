"""
Face lookup endpoint.

GET /users/{user_id}/face
    - Searches host and recv devices for the user's enrolled face photo
    - Returns raw JPG of the first face found
    - Response headers tell you which device it came from

GET /users/{user_id}/face?device=host   → only check host
GET /users/{user_id}/face?device=recv   → only check recv
GET /users/{user_id}/face?device=both   → check both, return JSON with base64
"""
import base64
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app.core.dahua_client import DahuaClient
from app.core.device_pool import DevicePool
from app.dependencies import get_pool, require_auth

router = APIRouter(prefix="/users", tags=["face-lookup"])
log = logging.getLogger(__name__)


@router.get("/{user_id}/face")
async def get_user_face_any_device(
    user_id: str,
    device: Optional[str] = Query(
        None,
        description=(
            "Which device to search. "
            "'host' = only host, 'recv' = only recv, "
            "'both' = check both and return JSON with base64, "
            "omit = check host first then recv, return first found as JPG"
        ),
    ),
    pool: DevicePool = Depends(get_pool),
):
    """
    Look up a user's enrolled face photo across one or both devices.

    Default behaviour (no ?device= param):
        Checks host first. If not found, checks recv.
        Returns raw JPG bytes with header X-Face-Device telling you which device had it.

    ?device=both:
        Checks both devices and returns a JSON object:
        {
          "user_id": "EMP1001",
          "host": { "found": true,  "image_base64": "/9j/4AAQ..." },
          "recv": { "found": false, "image_base64": null }
        }

    ?device=host or ?device=recv:
        Only checks that device. Returns raw JPG if found, 404 if not.
    """
    from app.config import get_settings
    settings = get_settings()
    known = {d.name for d in settings.devices}

    async def _fetch(dev_name: str) -> Optional[bytes]:
        """Return first face photo bytes for user on dev_name, or None."""
        if dev_name not in known:
            return None
        try:
            def _get(c: DahuaClient):
                photos = c.get_face_bytes(user_id)
                return photos[0] if photos else None
            return await pool.run(dev_name, _get)
        except Exception as e:
            log.warning("face_fetch_error", extra={
                "user_id": user_id, "device": dev_name, "error": str(e)})
            return None

    # ---- ?device=both → JSON with base64 for both devices ----------------
    if device == "both":
        host_jpg = await _fetch("host")
        recv_jpg = await _fetch("recv")

        if not host_jpg and not recv_jpg:
            raise HTTPException(
                status_code=404,
                detail=f"User '{user_id}' has no face photo on either device.",
            )

        return JSONResponse({
            "user_id": user_id,
            "host": {
                "found": host_jpg is not None,
                "image_base64": (
                    base64.b64encode(host_jpg).decode() if host_jpg else None
                ),
                "size_bytes": len(host_jpg) if host_jpg else 0,
            },
            "recv": {
                "found": recv_jpg is not None,
                "image_base64": (
                    base64.b64encode(recv_jpg).decode() if recv_jpg else None
                ),
                "size_bytes": len(recv_jpg) if recv_jpg else 0,
            },
        })

    # ---- ?device=host or ?device=recv → single device --------------------
    if device in ("host", "recv"):
        if device not in known:
            raise HTTPException(
                status_code=400,
                detail=f"Device '{device}' is not configured.",
            )
        jpg = await _fetch(device)
        if not jpg:
            raise HTTPException(
                status_code=404,
                detail=f"User '{user_id}' has no face photo on '{device}'.",
            )
        return Response(
            content=jpg,
            media_type="image/jpeg",
            headers={"X-Face-Device": device,
                     "X-User-ID": user_id},
        )

    # ---- no ?device= → try host first, then recv -------------------------
    if device is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid device='{device}'. "
                   f"Valid values: 'host', 'recv', 'both', or omit.",
        )

    # Try host first
    jpg = await _fetch("host")
    if jpg:
        log.info("face_found", extra={"user_id": user_id, "device": "host"})
        return Response(
            content=jpg,
            media_type="image/jpeg",
            headers={"X-Face-Device": "host",
                     "X-User-ID": user_id},
        )

    # Fall back to recv
    jpg = await _fetch("recv")
    if jpg:
        log.info("face_found", extra={"user_id": user_id, "device": "recv"})
        return Response(
            content=jpg,
            media_type="image/jpeg",
            headers={"X-Face-Device": "recv",
                     "X-User-ID": user_id},
        )

    raise HTTPException(
        status_code=404,
        detail=(
            f"User '{user_id}' has no face photo on either device. "
            "The user may not exist, or may not have a face enrolled."
        ),
    )
