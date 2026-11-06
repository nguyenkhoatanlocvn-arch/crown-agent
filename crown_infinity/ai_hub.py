"""CROWN AI HUB — cấu hình provider, ngân sách, cổng chặn và giữ chỗ ngân sách.

Mặc định: ai.enabled = False, mọi ngân sách = 0 → mọi lời gọi bị chặn trước khi chạm mạng.
Chưa có transport gọi model thật được kiểm chứng: call_model() không có transport mặc định.
"""
from __future__ import annotations

import os
import re
import time

from .auth import Actor, audit, require_owner
from .db import tx
from .httpcore import ApiError, bad_request
from .platform import DEFAULT_SETTINGS, get_setting, put_setting
from .util import new_id, now_iso

PROVIDER_KINDS = ("anthropic", "openai", "openrouter", "ollama", "other")
KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _provider_view(r) -> dict:
    d = dict(r)
    d["key_configured"] = bool(r["key_env"] and os.environ.get(r["key_env"]))  # chỉ báo có/không, không trả giá trị
    return d


def usage_summary(conn) -> dict:
    today, month = now_iso()[:10], now_iso()[:7]

    def spent(since):
        return conn.execute("SELECT COALESCE(SUM(COALESCE(cost_usd, reserved_usd)),0) FROM ai_usage "
                            "WHERE ts>=? AND status IN ('RESERVED','SUCCEEDED','FAILED')", (since,)).fetchone()[0]
    calls = conn.execute("SELECT status, COUNT(*) FROM ai_usage WHERE ts>=? GROUP BY status", (month,)).fetchall()
    return {"spent_today_usd": spent(today), "spent_month_usd": spent(month),
            "calls_month": {r[0]: r[1] for r in calls},
            "note": "Chi phí là ước tính nếu provider không trả số liệu (cost_is_estimate=1)."}


def get_hub(conn, actor: Actor) -> dict:
    require_owner(actor)
    return {"enabled": bool(get_setting(conn, "ai.enabled")), "budget": get_setting(conn, "ai.budget"),
            "providers": [_provider_view(r) for r in conn.execute("SELECT * FROM ai_providers ORDER BY name")],
            "usage": usage_summary(conn),
            "notes": ["Mọi lời gọi trả phí TẮT mặc định; ngân sách mặc định 0.",
                      "Khóa API đặt ở biến môi trường phía máy chủ; web không nhận và không hiển thị khóa.",
                      "Tài khoản chat (Claude/ChatGPT) khác API: đăng nhập chat không cho phần mềm gọi API miễn phí.",
                      "Chưa có transport gọi model thật: AI Writer/Optimizer/Test Lab model thật là PLANNED."]}


def put_settings(conn, actor: Actor, data: dict) -> dict:
    require_owner(actor)
    with tx(conn):
        if "enabled" in data:
            put_setting(conn, actor, "ai.enabled", bool(data["enabled"]))
        if "budget" in data:
            b = data["budget"]
            if not isinstance(b, dict):
                raise bad_request("budget phải là đối tượng")
            cur = get_setting(conn, "ai.budget")
            spec = {"daily_usd": (float, 0, 1000), "monthly_usd": (float, 0, 10000), "per_request_usd": (float, 0, 100),
                    "max_output_tokens": (int, 0, 200000), "timeout_seconds": (int, 5, 600), "max_retries": (int, 0, 5),
                    "max_concurrency": (int, 1, 16)}
            for k, v in b.items():
                if k not in spec:
                    raise bad_request(f"Khóa ngân sách lạ: {k}")
                typ, lo, hi = spec[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise bad_request(f"{k} phải là số")
                v = typ(v)
                if not (lo <= v <= hi):
                    raise bad_request(f"{k} phải trong khoảng {lo}–{hi}")
                cur[k] = v
            put_setting(conn, actor, "ai.budget", cur)
        audit(conn, actor, "ai.settings.update", "settings", "ai",
              detail={"fields": sorted(data), "enabled": data.get("enabled")})
    return get_hub(conn, actor)


def upsert_provider(conn, actor: Actor, data: dict, pid: str | None = None) -> dict:
    require_owner(actor)
    with tx(conn):
        cur = None
        if pid:
            cur = conn.execute("SELECT * FROM ai_providers WHERE id=?", (pid,)).fetchone()
            if not cur:
                raise ApiError(404, "NOT_FOUND", "Không tìm thấy provider")
        merged = dict(cur) if cur else {"name": "", "kind": "", "base_url": "", "model": "", "key_env": "",
                                        "paid": 1, "enabled": 0}
        for k in ("name", "kind", "base_url", "model", "key_env"):
            if k in data:
                merged[k] = str(data[k] or "").strip()
        for k in ("paid", "enabled"):
            if k in data:
                merged[k] = 1 if data[k] else 0
        if "api_key" in data or "key" in data:
            raise bad_request("Không nhận khóa API qua web. Đặt khóa vào biến môi trường phía máy chủ và ghi TÊN biến.")
        if not merged["name"] or len(merged["name"]) > 80:
            raise bad_request("Tên provider 1–80 ký tự")
        if merged["kind"] not in PROVIDER_KINDS:
            raise bad_request(f"kind phải thuộc {', '.join(PROVIDER_KINDS)}")
        if merged["key_env"] and not KEY_ENV_RE.match(merged["key_env"]):
            raise bad_request("key_env là TÊN biến môi trường (VD ANTHROPIC_API_KEY), không phải khóa")
        if merged["base_url"] and not re.match(r"^https?://", merged["base_url"]):
            raise bad_request("base_url phải là http(s)")
        ts = now_iso()
        if cur:
            conn.execute("UPDATE ai_providers SET name=?,kind=?,base_url=?,model=?,key_env=?,paid=?,enabled=?,updated_at=? "
                         "WHERE id=?", (merged["name"], merged["kind"], merged["base_url"], merged["model"],
                                        merged["key_env"], merged["paid"], merged["enabled"], ts, pid))
        else:
            pid = new_id("AIP")
            conn.execute("INSERT INTO ai_providers(id,name,kind,base_url,model,key_env,paid,enabled,created_at,updated_at)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?)", (pid, merged["name"], merged["kind"], merged["base_url"],
                                                           merged["model"], merged["key_env"], merged["paid"],
                                                           merged["enabled"], ts, ts))
        audit(conn, actor, "ai.provider.upsert", "ai_provider", pid, detail={"enabled": merged["enabled"]})
    return _provider_view(conn.execute("SELECT * FROM ai_providers WHERE id=?", (pid,)).fetchone())


def gate(conn, provider_id: str | None, est_usd, est_output_tokens: int | None) -> dict:
    """Quyết định có được gọi model không. Không chạm mạng."""
    reasons = []
    budget = get_setting(conn, "ai.budget") or DEFAULT_SETTINGS["ai.budget"]
    if not get_setting(conn, "ai.enabled"):
        reasons.append({"code": "AI_DISABLED", "message": "AI Hub đang TẮT"})
    prov = conn.execute("SELECT * FROM ai_providers WHERE id=?", (provider_id,)).fetchone() if provider_id else None
    if not prov:
        reasons.append({"code": "PROVIDER_NOT_FOUND", "message": "Chưa chọn provider hợp lệ"})
    else:
        if not prov["enabled"]:
            reasons.append({"code": "PROVIDER_DISABLED", "message": "Provider đang tắt"})
        if prov["kind"] != "ollama" and not (prov["key_env"] and os.environ.get(prov["key_env"])):
            reasons.append({"code": "KEY_MISSING", "message": "Chưa có khóa trong biến môi trường phía máy chủ"})
    if est_usd is None:
        reasons.append({"code": "COST_UNKNOWN", "message": "Không ước tính được chi phí nên không gọi"})
    else:
        est_usd = float(est_usd)
        if est_usd > budget["per_request_usd"]:
            reasons.append({"code": "OVER_PER_REQUEST", "message": f"Vượt trần mỗi request {budget['per_request_usd']} USD"})
        s = usage_summary(conn)
        if s["spent_today_usd"] + est_usd > budget["daily_usd"]:
            reasons.append({"code": "OVER_DAILY", "message": f"Vượt trần ngày {budget['daily_usd']} USD"})
        if s["spent_month_usd"] + est_usd > budget["monthly_usd"]:
            reasons.append({"code": "OVER_MONTHLY", "message": f"Vượt trần tháng {budget['monthly_usd']} USD"})
    if est_output_tokens is None or est_output_tokens > budget["max_output_tokens"]:
        reasons.append({"code": "TOKENS_OVER", "message": f"Vượt giới hạn {budget['max_output_tokens']} token đầu ra"})
    running = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE status='RESERVED'").fetchone()[0]
    if running >= budget["max_concurrency"]:
        reasons.append({"code": "CONCURRENCY", "message": "Đã đạt số lời gọi đồng thời tối đa"})
    return {"allowed": not reasons, "reasons": reasons, "budget": budget}


def reserve(conn, provider_id: str, est_usd: float, est_output_tokens: int, purpose: str,
            prompt_version_id: str | None = None) -> int:
    """Kiểm cổng và giữ chỗ trong CÙNG một giao dịch ghi → nhiều worker không cùng tiêu một khoản."""
    with tx(conn):
        g = gate(conn, provider_id, est_usd, est_output_tokens)
        if not g["allowed"]:
            raise ApiError(402, "AI_BLOCKED", "Lời gọi model bị chặn", g["reasons"])
        prov = conn.execute("SELECT model FROM ai_providers WHERE id=?", (provider_id,)).fetchone()
        cur = conn.execute("INSERT INTO ai_usage(ts,provider_id,model,purpose,prompt_version_id,status,reserved_usd) "
                           "VALUES (?,?,?,?,?,'RESERVED',?)", (now_iso(), provider_id, prov["model"], purpose,
                                                                prompt_version_id, float(est_usd)))
        return cur.lastrowid


def finalize(conn, usage_id: int, status: str, *, cost_usd=None, cost_is_estimate=True, input_tokens=None,
             output_tokens=None, latency_ms=None, error: str = "") -> None:
    if status not in ("SUCCEEDED", "FAILED", "RELEASED"):
        raise ValueError(status)
    with tx(conn):
        conn.execute("UPDATE ai_usage SET status=?, cost_usd=?, cost_is_estimate=?, input_tokens=?, output_tokens=?, "
                     "latency_ms=?, error=? WHERE id=? AND status='RESERVED'",
                     (status, cost_usd, 1 if cost_is_estimate else 0, input_tokens, output_tokens, latency_ms,
                      error[:300], usage_id))


def call_model(conn, provider_id: str, payload: dict, *, est_usd, est_output_tokens: int, purpose: str,
               transport=None, prompt_version_id: str | None = None) -> dict:
    """Đường gọi duy nhất. transport(provider_row, payload, timeout) -> dict(text, input_tokens, output_tokens, cost_usd).

    Không có transport mặc định: gọi model thật chưa được triển khai/kiểm chứng.
    """
    usage_id = reserve(conn, provider_id, est_usd, est_output_tokens, purpose, prompt_version_id)
    if transport is None:
        finalize(conn, usage_id, "RELEASED", error="NO_TRANSPORT")
        raise ApiError(501, "NOT_IMPLEMENTED", "Chưa có transport gọi model thật trong phiên bản này")
    prov = conn.execute("SELECT * FROM ai_providers WHERE id=?", (provider_id,)).fetchone()
    budget = get_setting(conn, "ai.budget")
    started = time.monotonic()
    attempts, last_err = 0, ""
    while attempts <= budget["max_retries"]:
        attempts += 1
        try:
            res = transport(prov, payload, budget["timeout_seconds"])
        except Exception as exc:  # lỗi mạng/provider: thử lại hữu hạn
            last_err = type(exc).__name__
            continue
        finalize(conn, usage_id, "SUCCEEDED", cost_usd=res.get("cost_usd", est_usd),
                 cost_is_estimate=res.get("cost_usd") is None, input_tokens=res.get("input_tokens"),
                 output_tokens=res.get("output_tokens"), latency_ms=int((time.monotonic() - started) * 1000))
        return {"usage_id": usage_id, "attempts": attempts, **res}
    finalize(conn, usage_id, "FAILED", cost_usd=None, latency_ms=int((time.monotonic() - started) * 1000),
             error=last_err)
    raise ApiError(502, "AI_CALL_FAILED", f"Gọi model thất bại sau {attempts} lần", {"error": last_err})
