#!/usr/bin/env python3
"""srt-renumber 1.0.0 — đánh lại số thứ tự các đoạn phụ đề SRT, giữ nguyên mọi byte khác.

Cách gọi (hợp đồng tool, PLAYBOOK mục 5):
    python main.py request.json
    request.json = {"inputs": {"input": "in/<tên>.srt"}, "out_dir": "out", "params": {"start": 1}}

Thành công: ghi out/<tên>.srt (cùng tên tệp đầu vào), dòng cuối stdout là JSON báo cáo, mã thoát 0.
Lỗi: mã thoát 2, một dòng tiếng Việt ở stderr, không tạo tệp kết quả.

Cách làm để "giữ nguyên" là thật:
- Tệp được đọc và ghi ở chế độ nhị phân; Python không được đổi kiểu xuống dòng.
- Nội dung phải là UTF-8 hợp lệ (giải mã nghiêm ngặt rồi mã hóa lại cho đúng các byte ban đầu).
- Kết quả được ghép từ các lát cắt của chính văn bản gốc. Chỉ có hai loại thay đổi:
  thay cụm chữ số của dòng số, và chèn dòng số cho đoạn thiếu số.

Chỉ dùng thư viện chuẩn; không mạng, không gọi tiến trình khác.
"""
import errno
import json
import os
import re
import sys

EXIT_ERROR = 2
MAPPING_LIMIT = 200  # báo cáo chỉ liệt kê tối đa 200 cặp (số cũ, số mới) đầu tiên
KNOWN_PARAMS = ("start",)
BOM = "\ufeff"
_OS_HINTS = {errno.EFBIG: "kết quả vượt hạn mức dung lượng", errno.ENOSPC: "hết chỗ trống trên đĩa"}

# Khoảng trắng trong một dòng: khoảng trắng Unicode, và U+FEFF (BOM nằm giữa tệp khi nhiều tệp bị ghép lại).
_WS = r"[\s\ufeff]"
# G:PP:SS,mmm — giờ 1–3 chữ số, phút và giây 2 chữ số, dấu thập phân ',' hoặc '.', 1–3 chữ số phần nghìn.
_TIME = r"[0-9]{1,3}:[0-9]{2}:[0-9]{2}[,.][0-9]{1,3}"
# Dòng timecode: <giờ đầu> --> <giờ cuối>, khoảng trắng quanh '-->' tùy ý, phía sau có thể có thông tin vị trí.
_TIMECODE_RE = re.compile(rf"{_WS}*{_TIME}{_WS}*-->{_WS}*{_TIME}(?:{_WS}.*)?\Z", re.DOTALL)
# Dòng số: chỉ gồm chữ số 0-9, cho phép khoảng trắng hai đầu. Nhóm 1 là cụm chữ số sẽ được thay.
_NUMBER_RE = re.compile(rf"{_WS}*([0-9]+){_WS}*\Z")
_BLANK_RE = re.compile(rf"{_WS}*\Z")
# Ký tự xuống dòng: \n, \r\n, \r\r\n (tệp bị chuyển đổi hai lần) hoặc một dãy \r kiểu Mac cũ.
_EOL_RE = re.compile(r"\r+\n?|\n")


class ToolFail(Exception):
    """Lỗi có thông báo tiếng Việt cho người gọi: mã thoát 2, không tạo tệp kết quả."""


def _lines(text, pos):
    """Sinh (đầu dòng, cuối nội dung, cuối dòng kể cả ký tự xuống dòng) cho từng dòng kể từ vị trí pos."""
    for m in _EOL_RE.finditer(text, pos):
        s, e = m.span()
        if text[e - 1] == "\n":
            yield pos, s, e
        else:  # dãy \r không kèm \n: mỗi \r là một lần xuống dòng
            yield pos, s, s + 1
            for k in range(s + 1, e):
                yield k, k, k + 1
        pos = e
    if pos < len(text):  # dòng cuối không có ký tự xuống dòng
        yield pos, len(text), len(text)


def renumber(text, start=1):
    """Đánh lại số các đoạn phụ đề trong `text` từ `start`. Trả (văn bản mới, báo cáo).

    - Dòng số của một đoạn là dòng chỉ gồm chữ số nằm NGAY TRƯỚC dòng timecode; chỉ cụm chữ số bị thay.
    - Dòng timecode đứng đầu tệp hoặc ngay sau dòng trống là đoạn thiếu số: chèn một dòng số phía trước,
      dùng kiểu xuống dòng của chính dòng timecode đó.
    - Dòng timecode đứng ngay sau một dòng chữ (không trống, không phải số) không mở đầu đoạn nào:
      để nguyên, không đếm vào `cues`, đếm vào `ignored_timecodes`.
    """
    origin = 1 if text.startswith(BOM) else 0  # BOM đầu tệp luôn ở lại đầu tệp
    if _BLANK_RE.match(text, origin):
        raise ToolFail("Tệp phụ đề rỗng hoặc chỉ có khoảng trắng — không có gì để đánh số.")

    edits = []  # (vị trí đầu, vị trí cuối, chuỗi thay thế) trên văn bản gốc, theo thứ tự tăng dần
    mapping = []
    cues = changed = inserted = ignored = 0
    prev = None  # (đầu dòng, cuối nội dung) của dòng liền trước
    last_eol = ""  # ký tự xuống dòng gần nhất đã gặp
    for ls, le, lend in _lines(text, origin):
        if _TIMECODE_RE.match(text, ls, le):
            number = _NUMBER_RE.match(text, prev[0], prev[1]) if prev is not None else None
            if number is None and prev is not None and not _BLANK_RE.match(text, prev[0], prev[1]):
                ignored += 1  # timecode ngay sau một dòng chữ: không mở đầu đoạn nào, để nguyên
            else:
                new = str(start + cues)
                if number is not None:  # có dòng số: thay cụm chữ số nếu khác
                    old = number.group(1)
                    if old != new:
                        edits.append((number.start(1), number.end(1), new))
                        changed += 1
                else:  # đầu tệp hoặc ngay sau dòng trống: chèn dòng số, cùng kiểu xuống dòng với dòng timecode
                    old = None
                    edits.append((ls, ls, new + (text[le:lend] or last_eol or "\n")))
                    inserted += 1
                if len(mapping) < MAPPING_LIMIT:
                    mapping.append([old, start + cues])
                cues += 1
        prev = (ls, le)
        if lend > le:
            last_eol = text[le:lend]

    if cues == 0:
        if ignored:
            raise ToolFail(f"Có {ignored} dòng timecode nhưng không dòng nào mở đầu một đoạn phụ đề "
                           "(phải đứng đầu tệp, ngay sau dòng trống hoặc ngay sau dòng số) — không đánh số được.")
        raise ToolFail("Không tìm thấy dòng timecode nào (dạng 00:00:01,200 --> 00:00:03,900) "
                       "— đây không phải tệp phụ đề SRT.")

    parts, pos = [], 0
    for s, e, repl in edits:
        parts.append(text[pos:s])
        parts.append(repl)
        pos = e
    parts.append(text[pos:])
    report = {"ok": True, "cues": cues, "changed": changed, "inserted": inserted, "mapping": mapping,
              "ignored_timecodes": ignored}
    return "".join(parts), report


def _why(e):
    """Lý do ngắn gọn của một lỗi đọc/ghi, để thông báo nằm trên một dòng."""
    if type(e) is OSError:
        return _OS_HINTS.get(e.errno) or errno.errorcode.get(e.errno) or "OSError"
    return type(e).__name__


def _show(value):
    """Giá trị nhận được, viết gọn trên một dòng để đưa vào thông báo lỗi."""
    s = json.dumps(value, ensure_ascii=False)
    return s if len(s) <= 40 else s[:37] + "..."


def _read_request(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ToolFail(f"Không đọc được tệp yêu cầu '{path}' ({_why(e)}).") from None
    try:
        req = json.loads(raw)
    except ValueError:
        raise ToolFail(f"Tệp yêu cầu '{path}' không phải JSON hợp lệ.") from None
    if not isinstance(req, dict):
        raise ToolFail("Tệp yêu cầu phải là một object JSON có inputs, out_dir, params.")
    return req


def _parse_request(req):
    """Trả (đường dẫn tệp vào, thư mục kết quả, start) sau khi kiểm từng trường."""
    inputs = req.get("inputs")
    src = inputs.get("input") if isinstance(inputs, dict) else None
    if not isinstance(src, str) or not src:
        raise ToolFail("Thiếu đầu vào bắt buộc inputs.input (đường dẫn tệp .srt).")
    if not src.lower().endswith(".srt"):
        raise ToolFail(f"Đầu vào phải là tệp có đuôi .srt (nhận được: {_show(os.path.basename(src))}).")
    out_dir = req.get("out_dir", "out")
    if not isinstance(out_dir, str) or not out_dir:
        raise ToolFail("out_dir phải là đường dẫn thư mục ghi kết quả.")
    params = req.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ToolFail("params phải là một object JSON, ví dụ {\"start\": 1}.")
    unknown = sorted(k for k in params if k not in KNOWN_PARAMS)
    if unknown:
        raise ToolFail(f"Tham số không có: {_show(', '.join(unknown))} (tool chỉ nhận tham số start).")
    start = params.get("start", 1)
    if type(start) is not int or start < 0:  # bool, số thực, chuỗi đều bị từ chối
        raise ToolFail(f"Tham số start phải là số nguyên ≥ 0 (nhận được: {_show(start)}).")
    return src, out_dir, start


def _write(path, data):
    try:
        f = open(path, "wb")
    except OSError as e:
        raise ToolFail(f"Không tạo được tệp kết quả '{path}' ({_why(e)}).") from None
    try:
        with f:
            f.write(data)
    except BaseException as e:
        try:
            os.remove(path)  # không để lại tệp kết quả dở dang
        except OSError:
            pass
        if isinstance(e, OSError):
            raise ToolFail(f"Không ghi được tệp kết quả '{path}' ({_why(e)}).") from None
        raise


def run(argv):
    if len(argv) != 2:
        raise ToolFail("Cách gọi: python main.py request.json")
    src, out_dir, start = _parse_request(_read_request(argv[1]))
    try:
        with open(src, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ToolFail(f"Không đọc được tệp đầu vào '{src}' ({_why(e)}).") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ToolFail(f"Tệp không phải UTF-8 hợp lệ (byte lỗi ở vị trí {e.start}) — tool không đoán bảng mã, "
                       "hãy chuyển tệp sang UTF-8 rồi chạy lại.") from None
    new_text, report = renumber(text, start)
    data = new_text.encode("utf-8")
    dst = os.path.join(out_dir, os.path.basename(src))
    try:
        os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(dst) and os.path.samefile(src, dst):
            raise ToolFail("Thư mục kết quả trùng thư mục chứa tệp đầu vào — tool không ghi đè tệp gốc.")
    except OSError as e:
        raise ToolFail(f"Không dùng được thư mục kết quả '{out_dir}' ({_why(e)}).") from None
    _write(dst, data)
    return report, dst


def _stderr(message):
    line = "Lỗi: " + " ".join(message.split()) + "\n"  # luôn đúng một dòng
    try:
        sys.stderr.buffer.write(line.encode("utf-8"))
        sys.stderr.flush()
    except Exception:
        try:
            sys.stderr.write(line.encode("ascii", "backslashreplace").decode("ascii"))
        except Exception:
            pass  # stderr cũng không ghi được: vẫn phải thoát đúng mã lỗi


def _emit(report):
    """Ghi dòng báo cáo thẳng ra stdout (fd 1), không qua bộ đệm, để lỗi ghi lộ ra ngay tại đây."""
    view = memoryview((json.dumps(report, ensure_ascii=True) + "\n").encode("ascii"))
    while view:
        view = view[os.write(1, view):]


def main(argv):
    try:
        report, dst = run(argv)
    except ToolFail as e:
        _stderr(str(e))
        return EXIT_ERROR
    except Exception as e:  # lỗi ngoài dự kiến cũng phải ra mã 2 và một dòng tiếng Việt
        _stderr(f"Sự cố không lường trước ({type(e).__name__}) — không có kết quả.")
        return EXIT_ERROR
    try:
        _emit(report)
    except OSError as e:  # không báo cáo được thì coi như lượt chạy lỗi: không để lại tệp kết quả
        try:
            os.remove(dst)
        except OSError:
            pass
        _stderr(f"Không ghi được báo cáo ra stdout ({_why(e)}) — đã hủy tệp kết quả.")
        return EXIT_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
