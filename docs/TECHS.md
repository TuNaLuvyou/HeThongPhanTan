# Kiến trúc Kỹ thuật & Giao thức (TECHS)

## 1. Công nghệ
- Python 3
- Tkinter (desktop, không web)
- Socket TCP, threading
- JSON
- Supabase Postgres
- ZeroTier (mạng ảo giữa các máy)

## 2. Kiến trúc
- Mọi node chạy cùng một mã nguồn, khác nhau ở tham số `--id`.
- Mỗi node vừa là server (lắng nghe) vừa là client (gửi).
- Coordinator không giữ số dư; số dư nằm trong DB, coordinator chỉ cấp quyền truy cập (phương pháp cấp quyền sử dụng).

## 3. Module và người phụ trách
- `core/network.py`: Thành viên 3 (Linux 1)
- `core/bully.py`: Thành viên 2 (macOS ARM)
- `core/bank_service.py` + `db/schema.sql`: Thành viên 4 (Linux 2)
- `ui/app_window.py`: Thành viên 5 (Windows 1)
- `main.py`: Trưởng nhóm (macOS ARM)

## 4. Cấu hình
- `config.json` liệt kê 6 node (`id`, `ip`, `port`, `os`).
- Port của node = giá trị trong config theo `--id`, tự bind khi khởi động.

## 5. Giao thức
- Mỗi gói tin là một chuỗi JSON trên một dòng, kết thúc bằng `\n`.
- Mọi gói tin có trường `type` và `from` (ID node gửi).

## 6. Các loại gói tin
- `ELECTION`
- `ANSWER`
- `COORDINATOR`
- `HEARTBEAT`
- `HEARTBEAT_ACK`
- `REQUEST_LOCK`
- `GRANT_LOCK`
- `RELEASE_LOCK`
- **Các trường chi tiết của từng loại**: CẦN CHỐT (trưởng nhóm đề xuất).

## 7. CSDL (đề xuất lược đồ)
- Bảng `fund` (`id`, `balance bigint`, `updated_at`): có một dòng duy nhất.
- Bảng `transactions` (`id`, `node_id`, `type` DEPOSIT/WITHDRAW, `amount`, `created_at`).
- Kết nối qua Supabase Shared pooler chế độ session (cổng 5432) vì mạng nhà có thể chỉ có IPv4.
- Project free của Supabase bị tạm dừng sau khoảng 7 ngày không hoạt động, cần kiểm tra trước buổi demo.
- Chuỗi kết nối lưu trong `.env`, không commit.

## 8. ĐÃ CHỐT
- Python + Tkinter.
- ID 0 đến 5.
- Port tự gán theo ID.
- JSON phân tách bằng `\n`.
- Bully với `ELECTION` / `ANSWER` / `COORDINATOR` + heartbeat.
- ID lớn hơn thắng.
- Khoá tập trung tại coordinator (`REQUEST_LOCK` / `GRANT_LOCK` / `RELEASE_LOCK`).
- Số dư trong DB Supabase.

## 9. CẦN CHỐT (sau khi các thành viên báo cáo)
- **a.** Cấu trúc trường JSON của từng loại gói tin.
- **b.** Tham số thời gian: chu kỳ heartbeat, timeout chờ ANSWER, timeout phát hiện coordinator lỗi.
- **c.** Xử lý khi node đang giữ quyền bị crash (đề xuất: quyền có thời hạn, coordinator thu hồi khi quá hạn).
- **d.** Xử lý khi đổi coordinator giữa chừng (đề xuất: coordinator mới xoá trạng thái khoá cũ và chờ quyền cũ hết hạn rồi mới cấp quyền đầu tiên; node đang chờ gửi lại REQUEST_LOCK).
- **e.** Có thêm kiểm tra phiên bản (version) khi ghi DB làm lớp bảo vệ phụ hay không.
- **f.** Driver DB (psycopg2-binary hay psycopg 3) và có tách file `core/db.py` hay không.
- **g.** Chức năng demo bổ sung: công tắc "tắt khoá" (minh hoạ mất cập nhật), nút giả lập crash, độ trễ mô phỏng xử lý khi nạp/rút.
