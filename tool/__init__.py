"""Bộ phận TOOL (Thiên Công) của CROWN AGENT.

Biến một mẫu (video, prompt, tệp, thư mục) thành công cụ chạy được, phát hành vào kho chung
để mọi Agent đã có trong danh bạ tìm và gọi. Chỉ dùng thư viện chuẩn Python.

Thư mục dữ liệu (theo lệnh Chủ tịch 2026-11-05):
  inbox/      mẫu gửi vào          workbench/  đặc tả, mã, bản thử theo mã việc
  catalog/    tool đã phát hành    skills/     skill riêng của bộ phận TOOL
  agents/     hồ sơ, bộ nhớ Agent  runs/       lịch sử chạy, lỗi, bằng chứng
"""
__version__ = "1.0.0"
