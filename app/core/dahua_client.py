"""
DahuaClient — synchronous SDK wrapper.

All methods are blocking. Must be called via DevicePool.run() to offload
to the thread pool.
"""
from ctypes import (POINTER, byref, c_void_p, cast, create_string_buffer,
                    memmove, pointer, sizeof, string_at)
from datetime import datetime
from typing import Iterator

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Enum import (EM_A_NET_EM_ACCESS_CTL_FACE_SERVICE,
                             EM_A_NET_EM_ACCESS_CTL_USER_SERVICE,
                             EM_LOGIN_SPAC_CAP_TYPE, EM_NET_RECORD_TYPE)
from NetSDK.SDK_Struct import (NET_A_FIND_RECORD_ACCESSCTLCARD_CONDITION,
                               NET_ACCESS_FACE_INFO, NET_ACCESS_USER_INFO,
                               NET_FIND_RECORD_ACCESSCTLCARDREC_CONDITION_EX,
                               NET_IN_ACCESS_FACE_SERVICE_GET,
                               NET_IN_ACCESS_FACE_SERVICE_INSERT,
                               NET_IN_ACCESS_FACE_SERVICE_REMOVE,
                               NET_IN_ACCESS_USER_SERVICE_GET,
                               NET_IN_ACCESS_USER_SERVICE_INSERT,
                               NET_IN_ACCESS_USER_SERVICE_REMOVE,
                               NET_IN_FIND_NEXT_RECORD_PARAM,
                               NET_IN_FIND_RECORD_PARAM,
                               NET_OUT_ACCESS_FACE_SERVICE_GET,
                               NET_OUT_ACCESS_FACE_SERVICE_INSERT,
                               NET_OUT_ACCESS_FACE_SERVICE_REMOVE,
                               NET_OUT_ACCESS_USER_SERVICE_GET,
                               NET_OUT_ACCESS_USER_SERVICE_INSERT,
                               NET_OUT_ACCESS_USER_SERVICE_REMOVE,
                               NET_OUT_FIND_NEXT_RECORD_PARAM,
                               NET_OUT_FIND_RECORD_PARAM,
                               NET_RECORDSET_ACCESS_CTL_CARD,
                               NET_RECORDSET_ACCESS_CTL_CARDREC, NET_TIME,
                               C_ENUM)

WAIT_MS = 5000
MAX_PHOTO_BYTES = 200 * 1024
MAX_PHOTOS = 5


def _make_net_time(dt: datetime) -> NET_TIME:
    t = NET_TIME()
    t.dwYear, t.dwMonth, t.dwDay = dt.year, dt.month, dt.day
    t.dwHour, t.dwMinute, t.dwSecond = dt.hour, dt.minute, dt.second
    return t


class DahuaClient:
    def __init__(self, ip, port, username, password, label=""):
        self.ip, self.port = ip, port
        self.username, self.password = username, password
        self.label = label or ip
        self.sdk = NetClient()
        self.sdk.InitEx()
        self.login_id = 0

    def login(self):
        login_id, _, err = self.sdk.LoginEx2(
            self.ip, self.port, self.username, self.password,
            EM_LOGIN_SPAC_CAP_TYPE.TCP, None)
        if login_id == 0:
            raise RuntimeError(f"[{self.label}] Login failed: {err}")
        self.login_id = login_id

    def logout(self):
        if self.login_id:
            self.sdk.Logout(self.login_id)
            self.login_id = 0

    # ---------- USER ----------
    def get_user(self, user_id: str):
        in_p = NET_IN_ACCESS_USER_SERVICE_GET()
        in_p.dwSize = sizeof(NET_IN_ACCESS_USER_SERVICE_GET)
        in_p.nUserNum = 1
        in_p.szUserID = user_id.encode()
        out_p = NET_OUT_ACCESS_USER_SERVICE_GET()
        out_p.dwSize = sizeof(NET_OUT_ACCESS_USER_SERVICE_GET)
        out_p.nMaxRetNum = 1
        user_buf = (NET_ACCESS_USER_INFO * 1)()
        fail_buf = (C_ENUM * 1)()
        out_p.pUserInfo = cast(user_buf, POINTER(NET_ACCESS_USER_INFO))
        out_p.pFailCode = cast(fail_buf, POINTER(C_ENUM))
        ok = self.sdk.OperateAccessUserService(
            self.login_id,
            EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_GET,
            in_p, out_p, WAIT_MS)
        if not ok or fail_buf[0] != 0:
            return None
        u = user_buf[0]
        if not (u.szName.strip(b"\x00").strip() or u.nDoorNum > 0
                or u.stuValidBeginTime.dwYear > 0 or u.stuValidEndTime.dwYear > 0):
            return None
        return u

    def insert_user(self, user_id, name, department="", phone="",
                    doors=(1,), valid_from=None, valid_to=None, password=""):
        u = NET_ACCESS_USER_INFO()
        u.szUserID = user_id.encode()
        u.szName = name.encode("utf-8")[:31]
        if department: u.szDepartment = department.encode("utf-8")[:127]
        if phone: u.szPhoneNumber = phone.encode()[:31]
        if password: u.szPsw = password.encode()[:63]
        u.nDoorNum = len(doors)
        for i, d in enumerate(doors[:32]):
            u.nDoors[i] = int(d)
        valid_from = valid_from or datetime.now()
        valid_to = valid_to or datetime(valid_from.year + 10, valid_from.month, valid_from.day)
        u.stuValidBeginTime = _make_net_time(valid_from)
        u.stuValidEndTime = _make_net_time(valid_to)
        return self.insert_user_struct(u)

    def insert_user_struct(self, user_info):
        arr = (NET_ACCESS_USER_INFO * 1)()
        memmove(byref(arr[0]), byref(user_info), sizeof(NET_ACCESS_USER_INFO))
        in_p = NET_IN_ACCESS_USER_SERVICE_INSERT()
        in_p.dwSize = sizeof(NET_IN_ACCESS_USER_SERVICE_INSERT)
        in_p.nInfoNum = 1
        in_p.pUserInfo = cast(arr, POINTER(NET_ACCESS_USER_INFO))
        out_p = NET_OUT_ACCESS_USER_SERVICE_INSERT()
        out_p.dwSize = sizeof(NET_OUT_ACCESS_USER_SERVICE_INSERT)
        out_p.nMaxRetNum = 1
        fail_buf = (C_ENUM * 1)()
        out_p.pFailCode = cast(fail_buf, POINTER(C_ENUM))
        ok = self.sdk.OperateAccessUserService(
            self.login_id,
            EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_INSERT,
            in_p, out_p, WAIT_MS)
        return bool(ok) and fail_buf[0] == 0

    def remove_user(self, user_id):
        in_p = NET_IN_ACCESS_USER_SERVICE_REMOVE()
        in_p.dwSize = sizeof(NET_IN_ACCESS_USER_SERVICE_REMOVE)
        in_p.nUserNum = 1
        in_p.szUserID = user_id.encode()
        out_p = NET_OUT_ACCESS_USER_SERVICE_REMOVE()
        out_p.dwSize = sizeof(NET_OUT_ACCESS_USER_SERVICE_REMOVE)
        out_p.nMaxRetNum = 1
        fail_buf = (C_ENUM * 1)()
        out_p.pFailCode = cast(fail_buf, POINTER(C_ENUM))
        ok = self.sdk.OperateAccessUserService(
            self.login_id,
            EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_REMOVE,
            in_p, out_p, WAIT_MS)
        return bool(ok) and fail_buf[0] == 0

    # ---------- FACE ----------
    def get_face_bytes(self, user_id):
        in_p = NET_IN_ACCESS_FACE_SERVICE_GET()
        in_p.dwSize = sizeof(NET_IN_ACCESS_FACE_SERVICE_GET)
        in_p.nUserNum = 1
        in_p.szUserID = user_id.encode()
        out_p = NET_OUT_ACCESS_FACE_SERVICE_GET()
        out_p.dwSize = sizeof(NET_OUT_ACCESS_FACE_SERVICE_GET)
        out_p.nMaxRetNum = 1
        face_buf = (NET_ACCESS_FACE_INFO * 1)()
        photo_bufs = [create_string_buffer(MAX_PHOTO_BYTES) for _ in range(MAX_PHOTOS)]
        for i, b in enumerate(photo_bufs):
            face_buf[0].pFacePhoto[i] = cast(b, c_void_p).value
            face_buf[0].nInFacePhotoLen[i] = MAX_PHOTO_BYTES
        fail_buf = (C_ENUM * 1)()
        out_p.pFaceInfo = cast(face_buf, POINTER(NET_ACCESS_FACE_INFO))
        out_p.pFailCode = cast(fail_buf, POINTER(C_ENUM))
        ok = self.sdk.OperateAccessFaceService(
            self.login_id,
            EM_A_NET_EM_ACCESS_CTL_FACE_SERVICE.NET_EM_ACCESS_CTL_FACE_SERVICE_GET,
            in_p, out_p, WAIT_MS)
        if not ok or fail_buf[0] != 0:
            return []
        photos = []
        for i in range(face_buf[0].nFacePhoto):
            actual = face_buf[0].nOutFacePhotoLen[i]
            if actual > 0:
                photos.append(string_at(photo_bufs[i], actual))
        return photos

    def insert_face(self, user_id, photos):
        if not photos:
            return False
        n = min(len(photos), MAX_PHOTOS)
        face_arr = (NET_ACCESS_FACE_INFO * 1)()
        face_arr[0].szUserID = user_id.encode()
        face_arr[0].nFacePhoto = n
        face_arr[0].nFaceData = 0
        face_arr[0].bFaceDataExEnable = 0
        bufs = []
        for i in range(n):
            b = create_string_buffer(photos[i], len(photos[i]))
            bufs.append(b)
            face_arr[0].pFacePhoto[i] = cast(b, c_void_p).value
            face_arr[0].nInFacePhotoLen[i] = len(photos[i])
            face_arr[0].nOutFacePhotoLen[i] = len(photos[i])
        in_p = NET_IN_ACCESS_FACE_SERVICE_INSERT()
        in_p.dwSize = sizeof(NET_IN_ACCESS_FACE_SERVICE_INSERT)
        in_p.nFaceInfoNum = 1
        in_p.pFaceInfo = cast(face_arr, POINTER(NET_ACCESS_FACE_INFO))
        out_p = NET_OUT_ACCESS_FACE_SERVICE_INSERT()
        out_p.dwSize = sizeof(NET_OUT_ACCESS_FACE_SERVICE_INSERT)
        out_p.nMaxRetNum = 1
        fail_buf = (C_ENUM * 1)()
        out_p.pFailCode = cast(fail_buf, POINTER(C_ENUM))
        ok = self.sdk.OperateAccessFaceService(
            self.login_id,
            EM_A_NET_EM_ACCESS_CTL_FACE_SERVICE.NET_EM_ACCESS_CTL_FACE_SERVICE_INSERT,
            in_p, out_p, WAIT_MS)
        result = bool(ok) and fail_buf[0] == 0
        del bufs
        return result

    def remove_face(self, user_id):
        in_p = NET_IN_ACCESS_FACE_SERVICE_REMOVE()
        in_p.dwSize = sizeof(NET_IN_ACCESS_FACE_SERVICE_REMOVE)
        in_p.nUserNum = 1
        in_p.szUserID = user_id.encode()
        out_p = NET_OUT_ACCESS_FACE_SERVICE_REMOVE()
        out_p.dwSize = sizeof(NET_OUT_ACCESS_FACE_SERVICE_REMOVE)
        out_p.nMaxRetNum = 1
        fail_buf = (C_ENUM * 1)()
        out_p.pFailCode = cast(fail_buf, POINTER(C_ENUM))
        ok = self.sdk.OperateAccessFaceService(
            self.login_id,
            EM_A_NET_EM_ACCESS_CTL_FACE_SERVICE.NET_EM_ACCESS_CTL_FACE_SERVICE_REMOVE,
            in_p, out_p, WAIT_MS)
        return bool(ok) and fail_buf[0] == 0

    # ---------- ENUMERATION ----------
    def list_all_card_records(self, page_size: int = 100) -> Iterator:
        cond = NET_A_FIND_RECORD_ACCESSCTLCARD_CONDITION()
        cond.dwSize = sizeof(NET_A_FIND_RECORD_ACCESSCTLCARD_CONDITION)
        in_p = NET_IN_FIND_RECORD_PARAM()
        in_p.dwSize = sizeof(NET_IN_FIND_RECORD_PARAM)
        in_p.emType = EM_NET_RECORD_TYPE.ACCESSCTLCARD
        in_p.pQueryCondition = cast(pointer(cond), c_void_p)
        out_p = NET_OUT_FIND_RECORD_PARAM()
        out_p.dwSize = sizeof(NET_OUT_FIND_RECORD_PARAM)
        if not self.sdk.FindRecord(self.login_id, in_p, out_p, WAIT_MS):
            return
        h = out_p.lFindeHandle
        try:
            while True:
                buf = (NET_RECORDSET_ACCESS_CTL_CARD * page_size)()
                for i in range(page_size):
                    buf[i].dwSize = sizeof(NET_RECORDSET_ACCESS_CTL_CARD)
                nin = NET_IN_FIND_NEXT_RECORD_PARAM()
                nin.dwSize = sizeof(NET_IN_FIND_NEXT_RECORD_PARAM)
                nin.lFindeHandle = h
                nin.nFileCount = page_size
                nout = NET_OUT_FIND_NEXT_RECORD_PARAM()
                nout.dwSize = sizeof(NET_OUT_FIND_NEXT_RECORD_PARAM)
                nout.pRecordList = cast(buf, c_void_p)
                nout.nMaxRecordNum = page_size
                if not self.sdk.FindNextRecord(nin, nout, WAIT_MS):
                    break
                got = nout.nRetRecordNum
                for i in range(got):
                    yield buf[i]
                if got < page_size:
                    break
        finally:
            self.sdk.FindRecordClose(h)

    def list_access_records(self, start: datetime, end: datetime, page_size: int = 50) -> Iterator:
        cond = NET_FIND_RECORD_ACCESSCTLCARDREC_CONDITION_EX()
        cond.dwSize = sizeof(NET_FIND_RECORD_ACCESSCTLCARDREC_CONDITION_EX)
        cond.bTimeEnable = 1
        cond.stStartTime = _make_net_time(start)
        cond.stEndTime = _make_net_time(end)
        in_p = NET_IN_FIND_RECORD_PARAM()
        in_p.dwSize = sizeof(NET_IN_FIND_RECORD_PARAM)
        in_p.emType = EM_NET_RECORD_TYPE.ACCESSCTLCARDREC_EX
        in_p.pQueryCondition = cast(pointer(cond), c_void_p)
        out_p = NET_OUT_FIND_RECORD_PARAM()
        out_p.dwSize = sizeof(NET_OUT_FIND_RECORD_PARAM)
        if not self.sdk.FindRecord(self.login_id, in_p, out_p, WAIT_MS):
            return
        h = out_p.lFindeHandle
        try:
            while True:
                buf = (NET_RECORDSET_ACCESS_CTL_CARDREC * page_size)()
                for i in range(page_size):
                    buf[i].dwSize = sizeof(NET_RECORDSET_ACCESS_CTL_CARDREC)
                nin = NET_IN_FIND_NEXT_RECORD_PARAM()
                nin.dwSize = sizeof(NET_IN_FIND_NEXT_RECORD_PARAM)
                nin.lFindeHandle = h
                nin.nFileCount = page_size
                nout = NET_OUT_FIND_NEXT_RECORD_PARAM()
                nout.dwSize = sizeof(NET_OUT_FIND_NEXT_RECORD_PARAM)
                nout.pRecordList = cast(buf, c_void_p)
                nout.nMaxRecordNum = page_size
                if not self.sdk.FindNextRecord(nin, nout, WAIT_MS):
                    break
                got = nout.nRetRecordNum
                for i in range(got):
                    yield buf[i]
                if got < page_size:
                    break
        finally:
            self.sdk.FindRecordClose(h)
