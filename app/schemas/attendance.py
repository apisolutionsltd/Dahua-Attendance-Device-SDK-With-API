"""Attendance-related Pydantic models."""
from typing import Optional
from pydantic import BaseModel
from app.schemas.user import _decode, _format_net_time

METHOD_LABELS = {
    0: "Unknown", 1: "Password", 2: "Card", 3: "Card+Pwd",
    4: "First-Card", 5: "MultiCard", 6: "Remote",
    7: "Fingerprint", 8: "Pwd+Card+FP", 9: "Pwd+FP",
    10: "Pwd+FP-combo", 11: "Card+FP",
    12: "Face", 13: "Face+Pwd", 14: "Citizen-ID",
    15: "QR-Code", 16: "FaceOrPwd", 17: "FaceOrFingerprint",
    22: "FaceAndPwd", 23: "FingerprintAndPwd",
    25: "FingerprintAndFace", 29: "FingerprintOrFace",
    46: "CitizenID-Face",
}
ATTENDANCE_LABELS = {
    0: "", 1: "SignIn", 2: "GoOut", 3: "GoOut-Return",
    4: "SignOut", 5: "OT-SignIn", 6: "OT-SignOut",
}


class AttendanceRecord(BaseModel):
    time: Optional[str]
    user_id: str
    card_no: str = ""
    method: str
    success: bool
    door: int
    direction: int
    attendance_state: str = ""
    error_code: str = "0x0"
    snap_face_url: str = ""

    @classmethod
    def from_sdk(cls, rec) -> "AttendanceRecord":
        return cls(
            time=_format_net_time(rec.stuTime),
            user_id=_decode(rec.szUserID),
            card_no=_decode(rec.szCardNo),
            method=METHOD_LABELS.get(int(rec.emMethod), f"code-{int(rec.emMethod)}"),
            success=bool(rec.bStatus),
            door=int(rec.nDoor),
            direction=int(rec.emDirection),
            attendance_state=ATTENDANCE_LABELS.get(int(rec.emAttendanceState), ""),
            error_code=hex(rec.nErrorCode),
            snap_face_url=_decode(rec.szSnapFaceURL),
        )
