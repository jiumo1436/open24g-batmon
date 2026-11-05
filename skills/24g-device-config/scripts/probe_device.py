#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""列出本机 HID 接口，并抓取指定设备的真实收发帧。

适配一台新设备最难的第一步是「这台接收器到底是什么」，以前要人去设备管理器
一层层翻 VID/PID，还要另外去软件里抓帧。这个脚本把这两步都变成一条命令：

用法：
    # 1) 先看本机有哪些接口（厂商私有接口打 ✓）
    python probe_device.py

    # 2) 只看厂商私有接口，最省事的一屏
    python probe_device.py --vendor

    # 3) 锁定一台，抓它的真实帧（默认监听 6 秒）
    python probe_device.py --vid 0x3554 --pid 0xF503 --frames 8

    # 4) 机器可读，方便别的脚本接着处理
    python probe_device.py --vendor --json

    # 5) 不知道 VID，机器上又只插了一个 2.4G 接收器
    python probe_device.py --vendor --frames 8

输出里直接给好可以粘进配置的 match 片段，VID/PID、usage_page、报文长度一次到位。
仅 Windows；只读，不改任何设备状态，也不需要管理员权限。
"""
import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WIN = os.name == "nt"
VENDOR_PAGE = 0xFF00          # 这个值以上的 usage_page 是厂商自定义接口
# usage_page 是标准页（0x0001 键鼠、0x000C 消费类…）的接口只用来收发输入，
# 电量报文一定在厂商私有页上。跟程序内「捕获报文」同一套判断。
# 候选报告 ID：设备的描述符没声明 value caps 时（不少国产接收器就这样），
# 按这张表挨个试，比只试 0~7 更容易碰上真实的 ID。
CANDIDATE_RIDS = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                  0x08, 0x0A, 0x10, 0x13, 0x1E, 0x5A)

# ---------------- Win32 常量 ----------------
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_DIGCF_PRESENT = 0x2
_DIGCF_DEVICEINTERFACE = 0x10
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0 = 0
_HIDP_STATUS_SUCCESS = 0x00110000

if IS_WIN:
    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID),
                    ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

    class _SP_DEV_INTERFACE_DETAIL(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", wintypes.WCHAR * 1024)]

    class _HIDD_ATTR(ctypes.Structure):
        _fields_ = [("Size", wintypes.DWORD), ("VendorID", wintypes.USHORT),
                    ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]

    class _HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT),
            ("UsagePage", wintypes.USHORT),
            ("InputReportByteLength", wintypes.USHORT),
            ("OutputReportByteLength", wintypes.USHORT),
            ("FeatureReportByteLength", wintypes.USHORT),
            ("Reserved", wintypes.USHORT * 17),
            ("NumberLinkCollectionNodes", wintypes.USHORT),
            ("NumberInputButtonCaps", wintypes.USHORT),
            ("NumberInputValueCaps", wintypes.USHORT),
            ("NumberInputDataIndices", wintypes.USHORT),
            ("NumberOutputButtonCaps", wintypes.USHORT),
            ("NumberOutputValueCaps", wintypes.USHORT),
            ("NumberOutputDataIndices", wintypes.USHORT),
            ("NumberFeatureButtonCaps", wintypes.USHORT),
            ("NumberFeatureValueCaps", wintypes.USHORT),
            ("NumberFeatureDataIndices", wintypes.USHORT),
        ]

    class _VALUE_CAPS(ctypes.Structure):
        _fields_ = [
            ("UsagePage", wintypes.USHORT), ("ReportID", ctypes.c_ubyte),
            ("IsAlias", ctypes.c_ubyte), ("BitField", wintypes.USHORT),
            ("LinkCollection", wintypes.USHORT), ("LinkUsage", wintypes.USHORT),
            ("LinkUsagePage", wintypes.USHORT), ("IsRange", ctypes.c_ubyte),
            ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
            ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
            ("Reserved", ctypes.c_ubyte), ("BitSize", wintypes.USHORT),
            ("ReportCount", wintypes.USHORT), ("Reserved2", wintypes.USHORT * 5),
            ("UnitsExp", wintypes.ULONG), ("Units", wintypes.ULONG),
            ("LogicalMin", ctypes.c_long), ("LogicalMax", ctypes.c_long),
            ("PhysicalMin", ctypes.c_long), ("PhysicalMax", ctypes.c_long),
            ("u", wintypes.ULONG * 3),
        ]

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    _HID = ctypes.WinDLL("hid.dll")
    _SETUP = ctypes.WinDLL("setupapi.dll")
    _K = ctypes.WinDLL("kernel32.dll", use_last_error=True)

    _HID.HidD_GetHidGuid.restype = None
    _HID.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
    _HID.HidD_GetAttributes.restype = ctypes.c_byte
    _HID.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTR)]
    _HID.HidD_GetPreparsedData.restype = ctypes.c_byte
    _HID.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _HID.HidD_FreePreparsedData.restype = ctypes.c_byte
    _HID.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _HID.HidP_GetCaps.restype = ctypes.c_int
    _HID.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
    _HID.HidP_GetValueCaps.restype = ctypes.c_int
    _HID.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(_VALUE_CAPS),
                                       ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p]
    _HID.HidD_GetProductString.restype = ctypes.c_byte
    _HID.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    _HID.HidD_GetFeature.restype = ctypes.c_byte
    _HID.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    _HID.HidD_GetInputReport.restype = ctypes.c_byte
    _HID.HidD_GetInputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    _SETUP.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    _SETUP.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), ctypes.c_wchar_p,
                                            wintypes.HANDLE, wintypes.DWORD]
    _SETUP.SetupDiEnumDeviceInterfaces.restype = ctypes.c_int
    _SETUP.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                  ctypes.POINTER(_GUID), wintypes.DWORD,
                                                  ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
    _SETUP.SetupDiGetDeviceInterfaceDetailW.restype = ctypes.c_int
    _SETUP.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.POINTER(_SP_DEV_INTERFACE_DETAIL), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _SETUP.SetupDiDestroyDeviceInfoList.restype = ctypes.c_int
    _SETUP.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    _K.CreateFileW.restype = wintypes.HANDLE
    _K.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                               wintypes.HANDLE]
    _K.CloseHandle.restype = ctypes.c_int
    _K.CloseHandle.argtypes = [wintypes.HANDLE]
    _K.ReadFile.restype = ctypes.c_int
    _K.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
    _K.CreateEventW.restype = wintypes.HANDLE
    _K.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _K.WaitForSingleObject.restype = wintypes.DWORD
    _K.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _K.GetOverlappedResult.restype = ctypes.c_int
    _K.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_int]
    _K.CancelIo.restype = ctypes.c_int
    _K.CancelIo.argtypes = [wintypes.HANDLE]


# ---------------- 基础 I/O ----------------
def _open_meta(path):
    """只读元数据：访问权限 0，最不容易被系统/别的程序拒绝。"""
    h = _K.CreateFileW(ctypes.c_wchar_p(path), 0,
                       _FILE_SHARE_READ | _FILE_SHARE_WRITE, None, _OPEN_EXISTING, 0, None)
    return h if h and h != _INVALID_HANDLE else None


def _open_rw(path):
    """读写句柄（带 OVERLAPPED，方便加超时）。"""
    args = (ctypes.c_wchar_p(path), _GENERIC_READ | _GENERIC_WRITE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE, None, _OPEN_EXISTING)
    h = _K.CreateFileW(*args, _FILE_FLAG_OVERLAPPED, None)
    if not h or h == _INVALID_HANDLE:
        h = _K.CreateFileW(*args, 0, None)
    return h if h and h != _INVALID_HANDLE else None


def _read_overlapped(h, size, timeout_ms):
    buf = ctypes.create_string_buffer(size)
    ov = _OVERLAPPED()
    ov.hEvent = _K.CreateEventW(None, 1, 0, None)
    if not ov.hEvent or ov.hEvent == _INVALID_HANDLE:
        return None
    nread = wintypes.DWORD(0)
    got = None
    try:
        if _K.ReadFile(h, buf, size, ctypes.byref(nread), ctypes.byref(ov)):
            got = buf.raw[:nread.value]
        elif ctypes.get_last_error() == _ERROR_IO_PENDING:
            if _K.WaitForSingleObject(ov.hEvent, timeout_ms) == _WAIT_OBJECT_0:
                if _K.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(nread), False):
                    got = buf.raw[:nread.value]
            else:
                _K.CancelIo(h)
                _K.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(nread), True)
    finally:
        if ov.hEvent and ov.hEvent != _INVALID_HANDLE:
            _K.CloseHandle(ov.hEvent)
    return got


# ---------------- 枚举 ----------------
def enumerate_hid():
    """返回本机全部 HID 接口：path / vid / pid / usage_page / in/out/feat 长度 / 产品名。"""
    out = []
    g = _GUID()
    _HID.HidD_GetHidGuid(ctypes.byref(g))
    hdev = _SETUP.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                       _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
    if hdev == _INVALID_HANDLE:
        return out
    did = _SP_DEVICE_INTERFACE_DATA()
    did.cbSize = ctypes.sizeof(did)
    i = 0
    try:
        while _SETUP.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(g), i,
                                                 ctypes.byref(did)):
            i += 1
            needed = wintypes.DWORD()
            _SETUP.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), None, 0,
                                                    ctypes.byref(needed), None)
            if needed.value <= 0:
                continue
            # 按结构体实际大小分配：DevicePath 是 1024 个 WCHAR，缓冲区小了会越界
            buf = ctypes.create_string_buffer(
                max(needed.value, ctypes.sizeof(_SP_DEV_INTERFACE_DETAIL)))
            detail = ctypes.cast(buf, ctypes.POINTER(_SP_DEV_INTERFACE_DETAIL))[0]
            detail.cbSize = 8
            if not _SETUP.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did),
                                                           ctypes.byref(detail),
                                                           needed.value, None, None):
                continue
            h = _open_meta(detail.DevicePath)
            if h is None:
                continue
            try:
                attr = _HIDD_ATTR()
                attr.Size = ctypes.sizeof(attr)
                if not _HID.HidD_GetAttributes(h, ctypes.byref(attr)):
                    continue
                up = inl = outl = featl = 0
                pp = ctypes.c_void_p()
                if _HID.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    caps = _HIDP_CAPS()
                    if _HID.HidP_GetCaps(pp, ctypes.byref(caps)) == _HIDP_STATUS_SUCCESS:
                        up = int(caps.UsagePage)
                        inl = int(caps.InputReportByteLength)
                        outl = int(caps.OutputReportByteLength)
                        featl = int(caps.FeatureReportByteLength)
                    _HID.HidD_FreePreparsedData(pp)
                prod = ctypes.create_unicode_buffer(128)
                pname = prod.value if _HID.HidD_GetProductString(h, prod, 256) else ""
                out.append({"path": detail.DevicePath, "vid": int(attr.VendorID),
                            "pid": int(attr.ProductID), "usage_page": up,
                            "in_len": inl, "out_len": outl, "feat_len": featl,
                            "product": pname})
            finally:
                _K.CloseHandle(h)
    finally:
        _SETUP.SetupDiDestroyDeviceInfoList(hdev)
    return out


def report_ids(path):
    """从描述符里读真实报告 ID。厂商接口的 ID 常是 0x06/0x08/0x13 这种自定义值，
    按 0 发帧会被系统直接拒掉（错误 87），等于白发。"""
    inp, outp, feat = set(), set(), set()
    h = _open_meta(path)
    if h is None:
        return inp, outp, feat
    try:
        pp = ctypes.c_void_p()
        if not _HID.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            return inp, outp, feat
        try:
            caps = _HIDP_CAPS()
            if _HID.HidP_GetCaps(pp, ctypes.byref(caps)) != _HIDP_STATUS_SUCCESS:
                return inp, outp, feat
            for kind, n, dst in ((0, caps.NumberInputValueCaps, inp),
                                 (1, caps.NumberOutputValueCaps, outp),
                                 (2, caps.NumberFeatureValueCaps, feat)):
                if not n:
                    continue
                arr = (_VALUE_CAPS * n)()
                ln = wintypes.USHORT(n)
                if _HID.HidP_GetValueCaps(kind, arr, ctypes.byref(ln), pp) != _HIDP_STATUS_SUCCESS:
                    continue
                for k in range(ln.value):
                    dst.add(arr[k].ReportID)
        finally:
            _HID.HidD_FreePreparsedData(pp)
    finally:
        _K.CloseHandle(h)
    return inp, outp, feat


# ---------------- 抓帧 ----------------
def _fmt(data):
    return " ".join(f"{b:02X}" for b in data)


def _hint(data):
    """标出值在 2~100 之间的字节 —— 电量通常就在这些位置里（0/1 多是状态位，不算）。"""
    idx = [str(i) for i, b in enumerate(data) if 2 <= b <= 100]
    if not idx:
        return ""
    if len(idx) > 10:
        return f"   <- 2~100 的字节[{','.join(idx[:10])}…]"
    return f"   <- 2~100 的字节[{','.join(idx)}]"


def _send(h, data, out_len):
    """发一帧。走 SetOutputReport：厂商接口基本都是这条路。"""
    size = max(len(data), out_len or 0, 8)
    buf = ctypes.create_string_buffer(bytes(data).ljust(size, b"\x00"), size)
    return _HID.HidD_SetOutputReport(h, buf, size) != 0


def dump_frames(dev, seconds, sends=None):
    """抓这台接口的 feature / input / 被动上报帧。

    sends 不为空时先按顺序发这些帧，再收回应 —— 很多接收器不发查询帧就不肯回话。
    设备休眠时可能什么都抓不到。
    """
    res = {"report_ids": {}, "feature": [], "input": [], "passive": [], "sends": []}
    ins, outs, feats = report_ids(dev["path"])
    res["report_ids"] = {"in": sorted(ins), "out": sorted(outs), "feat": sorted(feats)}

    h = _open_rw(dev["path"])
    if h is None:
        res["error"] = "打不开句柄（可能被别的程序独占）"
        return res
    try:
        # feature 报文：feat_len 为 0 的设备（描述符没报）按 64 试
        flen = dev["feat_len"] or 64
        for rid in (sorted(feats) if feats else CANDIDATE_RIDS):
            buf = ctypes.create_string_buffer(max(flen, 8))
            buf[0] = rid & 0xFF
            if _HID.HidD_GetFeature(h, buf, len(buf)):
                data = buf.raw[:len(buf)]
                if any(b != 0 for b in data[1:]):
                    res["feature"].append(data)
        # input 报文：直接发起读请求
        ilen = dev["in_len"] or 64
        for rid in (sorted(ins) if ins else CANDIDATE_RIDS):
            buf = ctypes.create_string_buffer(ilen)
            buf[0] = rid & 0xFF
            if _HID.HidD_GetInputReport(h, buf, ilen):
                data = buf.raw[:ilen]
                if any(b != 0 for b in data[1:]):
                    res["input"].append(data)
        # 主动查询：发一帧，然后收回应
        for frame in (sends or []):
            got = []
            ok = _send(h, frame, dev["out_len"])
            deadline = time.time() + max(seconds, 0.4)
            seen = set()
            while time.time() < deadline:
                reply = _read_overlapped(h, ilen, 250)
                if reply and any(b != 0 for b in reply) and bytes(reply) not in seen:
                    seen.add(bytes(reply))
                    got.append(reply)
            res["sends"].append({"sent": bytes(frame), "ok": ok, "replies": got})
        # 被动监听：很多接收器会周期性主动上报
        seen = set()
        deadline = time.time() + max(seconds, 0)
        while time.time() < deadline:
            frame = _read_overlapped(h, ilen, 300)
            if frame and any(b != 0 for b in frame):
                key = bytes(frame)
                if key not in seen:
                    seen.add(key)
                    res["passive"].append(frame)
    finally:
        _K.CloseHandle(h)
    return res


# ---------------- 输出 ----------------
def _match_snippet(d):
    return ('"match": { "vid": "0x%04X", "pid": "0x%04X", "usage_page": "0x%04X", '
            '"min_in": %d, "min_out": %d }'
            % (d["vid"], d["pid"], d["usage_page"], d["in_len"], d["out_len"]))


def print_table(devs):
    # 厂商私有接口排在前面，同 VID/PID 的接口聚在一起 —— 一屏就能看出哪几行属于同一台
    devs = sorted(devs, key=lambda d: (d["usage_page"] < VENDOR_PAGE, d["vid"], d["pid"],
                                       d["usage_page"], d["in_len"]))
    vendor = [d for d in devs if d["usage_page"] >= VENDOR_PAGE]
    print(f"本机 HID 接口 {len(devs)} 个，其中厂商私有接口 {len(vendor)} 个"
          f"（usage_page ≥ 0x{VENDOR_PAGE:04X}）\n")
    head = "  #  私有  VID      PID      usagePage  in   out  feat  产品名"
    print(head)
    print("  " + "-" * (len(head) - 2))
    for i, d in enumerate(devs, 1):
        mark = " ✓ " if d["usage_page"] >= VENDOR_PAGE else "   "
        print(f"  {i:<3}{mark}  0x{d['vid']:04X}   0x{d['pid']:04X}   "
              f"0x{d['usage_page']:04X}     {d['in_len']:<3}  {d['out_len']:<3}  "
              f"{d['feat_len']:<4}  {d['product'] or '(无产品名)'}")
    print()
    if not vendor:
        print("没有厂商私有接口。常见原因：接收器没插好、设备没切到 2.4G 档、设备在休眠。\n")
        return
    # 按 VID:PID 去重，每台接收器给一条照抄就能用的命令
    uniq = []
    for d in vendor:
        pair = (d["vid"], d["pid"])
        if pair not in uniq:
            uniq.append(pair)
    print("下一步（挑你要的那台照抄）：")
    for v, p in uniq[:6]:
        print(f"  python probe_device.py --vid 0x{v:04X} --pid 0x{p:04X} --frames 8")
    print()
    print("不确定是哪台：拔掉接收器再跑一次这个脚本，清单里少掉的那几行就是它。")
    print("机器上只插了一个 2.4G 接收器，也可以一次抓全部厂商接口：")
    print("  python probe_device.py --vendor --frames 8")


def print_dump(dev, res, seconds):
    print(f"\n=== 抓帧 0x{dev['vid']:04X}:0x{dev['pid']:04X}  "
          f"usagePage=0x{dev['usage_page']:04X}  in={dev['in_len']} out={dev['out_len']}  "
          f"| {dev['product'] or '(无产品名)'} ===")
    if res.get("error"):
        print("  " + res["error"])
        return
    ids = res["report_ids"]
    _hx = lambda s: " ".join(f"{x:02X}" for x in s) if s else "无"
    print(f"  报告ID：输入=[{_hx(ids['in'])}]  输出=[{_hx(ids['out'])}]  "
          f"特征=[{_hx(ids['feat'])}]")
    for item in res["sends"]:
        flag = "" if item["ok"] else "（发送失败，可能被系统拒了）"
        print(f"  发送: {_fmt(item['sent'])}{flag}")
        if item["replies"]:
            for frame in item["replies"]:
                print(f"  回应: {_fmt(frame)}{_hint(frame)}")
        else:
            print("  回应: 没有")
    if not any((res["feature"], res["input"], res["passive"], res["sends"])):
        print(f"  {seconds} 秒内没有抓到任何非零帧。设备可能休眠 / 没开机 / 没切 2.4G 档。")
        print("  很多接收器不发查询帧就不回话：用 --send 带一帧试试（见 references/recipes.md）。")
        return
    for tag, key in (("feat", "feature"), ("in  ", "input")):
        for frame in res[key]:
            print(f"  {tag}: {_fmt(frame)}{_hint(frame)}")
    if res["passive"]:
        print(f"  passive（{seconds} 秒内被动收到 {len(res['passive'])} 种不同帧）：")
        for frame in res["passive"]:
            print(f"         {_fmt(frame)}{_hint(frame)}")
    print()
    print("  可粘进配置的 match：")
    print("  " + _match_snippet(dev))


def parse_hex(text):
    """'13 4A 01' / '134A01' / '0x13,0x4A' 都认。"""
    raw = str(text).strip()
    for junk in ("0x", "0X"):
        raw = raw.replace(junk, " ")
    for sep in (",", ":", "-", "\t", "\n", "\r", "|"):
        raw = raw.replace(sep, " ")
    parts = [p for p in raw.split(" ") if p]
    out = bytearray()
    for p in parts:
        if len(p) % 2 or any(c not in "0123456789abcdefABCDEF" for c in p):
            raise ValueError(f"不是合法十六进制：{p!r}")
        out += bytes.fromhex(p)
    if not out:
        raise ValueError("空帧")
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(
        description="列出本机 HID 接口并抓取指定设备的真实帧（仅 Windows）")
    ap.add_argument("--vid", action="append", default=[],
                    help="只处理这个 VID，如 0x3554；可重复")
    ap.add_argument("--pid", action="append", default=[],
                    help="只处理这个 PID，如 0xF503；可重复")
    ap.add_argument("--vendor", action="store_true",
                    help="只处理厂商私有接口（usage_page ≥ 0xFF00）")
    ap.add_argument("--frames", type=float, default=None, metavar="秒",
                    help="抓帧时的被动监听时长，默认 6 秒；给 0 表示只列报告 ID")
    ap.add_argument("--send", action="append", default=[], metavar="十六进制",
                    help="先发这一帧再收回应，如 --send \"13 4A 01 00 ...\"；可重复")
    ap.add_argument("--json", action="store_true", help="输出 JSON，便于脚本处理")
    args = ap.parse_args()

    if not IS_WIN:
        print("这个脚本只能在 Windows 上跑（要调 hid.dll / setupapi.dll）。")
        return 2

    devs = enumerate_hid()
    if not devs:
        print("一个 HID 接口都没枚举到。可能被安全软件拦了，或者当前不是常规桌面会话。")
        return 1

    vid = {int(v, 16) for v in args.vid}
    pid = {int(p, 16) for p in args.pid}
    try:
        sends = [parse_hex(s) for s in args.send]
    except ValueError as e:
        print(f"--send 参数有问题：{e}")
        return 2
    if sends and args.frames is None:
        args.frames = 2.0          # 发查询帧总得留点时间收回应
    picked = devs
    if vid or pid:
        picked = [d for d in devs
                  if (not vid or d["vid"] in vid) and (not pid or d["pid"] in pid)]
    # 只处理厂商私有页：标准页（键鼠 / 消费类）上没有电量报文，
    # 而且标准接口和厂商接口混在一起抓，容易让 hid.dll 在进程退出时崩掉。
    targets = [d for d in picked if d["usage_page"] >= VENDOR_PAGE]
    n_std = len(picked) - len(targets)

    if args.json:
        payload = {"all": devs, "targets": targets}
        if args.frames is not None:
            payload["frames"] = [
                {"device": d, "capture": dump_frames(d, args.frames, sends)} for d in targets]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not (vid or pid or args.vendor):
        print_table(devs)
        return 0

    if not picked:
        print("没有匹配到这个 VID/PID。先跑一次不带参数的，看看清单里到底有什么：")
        print("  python probe_device.py")
        return 1

    if not targets:
        print(f"这台的 {n_std} 个接口都是标准页（usage_page < 0x{VENDOR_PAGE:04X}），"
              "没有厂商私有接口。")
        print("电量大概率不走 HID：可能是串口设备，或者只能用厂商软件读。")
        return 1

    for d in targets:
        print(f"[目标] 0x{d['vid']:04X}:0x{d['pid']:04X} usagePage=0x{d['usage_page']:04X} "
              f"in={d['in_len']} out={d['out_len']} feat={d['feat_len']} "
              f"| {d['product'] or '(无产品名)'}")
    if n_std:
        print(f"（另有 {n_std} 个标准页接口已跳过：键鼠 / 消费类接口不承载电量报文）")
    print()

    if args.frames is None:
        # 没要求抓帧：只给报告 ID 和 match 片段，最快
        for d in targets:
            ins, outs, feats = report_ids(d["path"])
            _hx = lambda s: " ".join(f"{x:02X}" for x in s) if s else "无"
            print(f"0x{d['vid']:04X}:0x{d['pid']:04X} 报告ID 输入=[{_hx(ins)}] "
                  f"输出=[{_hx(outs)}] 特征=[{_hx(feats)}]")
            print("  " + _match_snippet(d))
        print("\n要抓真实帧，加 --frames 8（监听 8 秒）。")
        return 0

    for d in targets:
        print_dump(d, dump_frames(d, args.frames, sends), args.frames)
    print("\n拿上面的帧去跑 analyze_frames.py，用 --pct 标上厂商软件显示的百分比，"
          "电量字节和校验方式会一起算出来。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
