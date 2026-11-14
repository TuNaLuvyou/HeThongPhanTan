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
- Bind port thất bại (port đã bị chiếm hoặc IP trong `config.json` không phải IP máy đang chạy) thì `start_node()` trả `None` và node thoát ngay kèm thông báo lỗi; không mở giao diện chạy mà không có kết nối.

## 5. Giao thức
- Mỗi gói tin là một chuỗi JSON trên một dòng, kết thúc bằng `\n`.
- Mọi gói tin có trường `type` và `from` (ID node gửi).
- `from` là căn cứ định danh node gửi ở tầng mạng: node nhận lấy `from` của gói tin, không suy ID người gửi từ IP kết nối (nhiều node chạy chung một máy thì trùng IP; suy từ IP chỉ còn dùng để ghi log).

## 6. Các loại gói tin
- `ELECTION`
- `ANSWER`
- `COORDINATOR`
- `HEARTBEAT`
- `HEARTBEAT_ACK`
- `REQUEST_LOCK`
- `GRANT_LOCK`
- `LOCK_QUEUED`
- `RELEASE_LOCK`
- `LOCK_QUEUED` là loại mới so với danh sách đã chốt ở mục 8: coordinator trả cho node vừa bị đưa vào hàng đợi FIFO ("đã nhận đơn, đang xếp hàng"), để node xin quyền phân biệt "đang chờ tới lượt" với "coordinator đã chết" (FLOWS mục 2b). `GRANT_LOCK` và `LOCK_QUEUED` đều được tính là "coordinator có phản hồi".
- **Cấu trúc trường của từng loại** (đang ở trạng thái CẦN CHỐT — xem 9a): bảng dưới đây là đề xuất theo đúng code đang phát, cần cả nhóm họp xác nhận chứ chưa chốt một mình.

| `type` | Cấu trúc gói tin | Ai gửi → ai |
|---|---|---|
| `ELECTION` | `{"type": "ELECTION", "from": 2}` | Node bắt đầu bầu cử → các node có ID lớn hơn |
| `ANSWER` | `{"type": "ANSWER", "from": 4, "to": 2}` | Chỉ node có ID lớn hơn node khởi động bầu cử → node khởi động |
| `COORDINATOR` | `{"type": "COORDINATOR", "from": 4}` | Node thắng cử broadcast cho tất cả; coordinator đang tại vị cũng trả riêng cho node ID nhỏ hơn khi nhận `ELECTION` (FLOWS mục 2) |
| `HEARTBEAT` | `{"type": "HEARTBEAT", "from": 4}` | Coordinator → từng peer, mỗi `HEARTBEAT_INTERVAL` |
| `HEARTBEAT_ACK` | `{"type": "HEARTBEAT_ACK", "from": 2, "to": 4}` | Peer (không phải coordinator) → coordinator |
| `REQUEST_LOCK` | `{"type": "REQUEST_LOCK", "from": 2}` | Node có giao dịch → coordinator |
| `GRANT_LOCK` | `{"type": "GRANT_LOCK", "from": 4}` | Coordinator → node được cấp quyền |
| `LOCK_QUEUED` | `{"type": "LOCK_QUEUED", "from": 4}` | Coordinator → node vừa được đưa vào hàng đợi FIFO |
| `RELEASE_LOCK` | `{"type": "RELEASE_LOCK", "from": 2}` | Node đang giữ quyền → coordinator |

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
- **a.** Cấu trúc trường JSON của từng loại gói tin: code đã có đáp án, bảng đề xuất ở mục 6. Cần cả nhóm xác nhận để chuyển thành ĐÃ CHỐT, đồng thời sửa dòng "Khoá tập trung tại coordinator (`REQUEST_LOCK` / `GRANT_LOCK` / `RELEASE_LOCK`)" ở mục 8 thành có thêm `LOCK_QUEUED`.
- **b.** Tham số thời gian: CẦN CHỐT. Code hiện đang dùng (đơn vị giây):
  - `ELECTION_ANSWER_TIMEOUT = 5.0` (`core/bully.py`) — chờ `ANSWER` khi bầu cử.
  - `HEARTBEAT_INTERVAL = 2.0` (`core/bully.py`) — chu kỳ coordinator gửi `HEARTBEAT` và chu kỳ vòng canh kiểm tra.
  - `HEARTBEAT_TIMEOUT = 3.0` (`core/bully.py`) — peer không trả `HEARTBEAT_ACK` quá ngần này bị coi là offline (chỉ để ghi log, không kích hoạt bầu cử).
  - `COORDINATOR_SILENCE_TIMEOUT = 6.0` (`core/bully.py`) — node phụ phát hiện coordinator im lặng, bầu lại (FLOWS mục 2a).
  - `LOCK_REPLY_TIMEOUT = 10.0` (`main.py`) — chờ `GRANT_LOCK`/`LOCK_QUEUED` sau khi gửi `REQUEST_LOCK`; quá hạn thì bầu lại (FLOWS mục 2b).
  - Đề xuất: chốt nguyên các giá trị trên nếu cả nhóm đồng ý; phương án khác: nới `COORDINATOR_SILENCE_TIMEOUT` ≥ 3 lần `HEARTBEAT_INTERVAL` để dung sai mạng ZeroTier.
- **c.** Xử lý khi node đang giữ quyền bị crash: CẦN CHỐT. Code hiện chưa xử lý — coordinator giữ nguyên `_lock_holder` cho tới khi có `RELEASE_LOCK`; các node khác nhận `LOCK_QUEUED` và chờ vô hạn (coordinator vẫn sống nên không kích hoạt bầu lại). Đề xuất cũ vẫn hợp thời gian: quyền có thời hạn (lease), coordinator thu hồi khi quá hạn; phương án khác: coordinator phát `GRANT_LOCK` lại cho node kế tiếp sau `LOCK_REPLY_TIMEOUT` mà không thấy `RELEASE_LOCK`.
- **d.** Xử lý khi đổi coordinator giữa chừng: CẦN CHỐT phần còn lại. Code hiện đã làm: coordinator cũ xoá holder + hàng đợi FIFO khi hạ vai trò (`BankService.reset_lock()`, log `LOCK_RESET`), coordinator mới bắt đầu với hàng đợi trống; node đang chờ mà phát hiện đổi coordinator thì đơn xin bị huỷ và báo người dùng bấm lại (`main.py::update_status`). Còn bỏ ngỏ: code KHÔNG tự động gửi lại `REQUEST_LOCK` như đề xuất cũ của mục này — cần chốt giữa "huỷ + báo bấm lại" (đang dùng) với "tự gửi lại" (đề xuất cũ).
- **e.** Có thêm kiểm tra phiên bản (version) khi ghi DB làm lớp bảo vệ phụ hay không.
- **f.** Driver DB (psycopg2-binary hay psycopg 3) và có tách file `core/db.py` hay không. (Ghi nhận: code hiện dùng `psycopg2`.)
- **g.** Chức năng demo bổ sung: công tắc "tắt khoá" (minh hoạ mất cập nhật), nút giả lập crash, độ trễ mô phỏng xử lý khi nạp/rút.
